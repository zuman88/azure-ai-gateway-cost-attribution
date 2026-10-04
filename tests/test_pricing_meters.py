"""Unit tests for Azure meter-name parsing.

Run with:

    python -m unittest discover -s tests -v

No third-party dependencies and no network access: every case below is a real meter name copied
verbatim from the Retail Prices API, so the rules can be verified offline and in CI.

Most of these tests exist because the case they cover was a bug that produced a plausible-looking but
wrong price. A wrong rate is worse than a missing one, because a missing rate is reported by the
gateway as unpriced and raises an alert, whereas a wrong one just quietly under-bills a team.
"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))

from _pricing_meters import (  # noqa: E402
    choose_version,
    classify,
    collect_rates,
    context_threshold,
    extract_context,
    extract_deployment,
    is_excluded,
    match_model,
    normalise_to_million,
    tokenize,
)


def meter(name: str, price: float, unit: str = "1K") -> dict[str, object]:
    return {"meterName": name, "retailPrice": price, "unitOfMeasure": unit}


class TestTokenize(unittest.TestCase):
    def test_separators_are_interchangeable(self):
        """Azure uses spaces and hyphens for the same meter, so both must tokenize identically."""
        self.assertEqual(
            tokenize("gpt 4o 0806 Inp glbl Tokens"),
            tokenize("gpt-4o-0806-Inp-glbl Tokens"),
        )

    def test_run_together_words_are_split(self):
        self.assertEqual(tokenize("mini0718"), ["mini", "0718"])
        self.assertEqual(tokenize("BatchOutp"), ["batchoutp"])

    def test_decimal_versions_split_into_digits(self):
        self.assertEqual(tokenize("GPT 5.1"), ["gpt", "5", "1"])


class TestModelMatching(unittest.TestCase):
    def test_model_matches_both_spellings(self):
        for name in ("gpt 4o 1120 Inp glbl Tokens", "gpt-4o-0806-Inp-glbl Tokens"):
            status, tail = match_model(tokenize(name), tokenize("gpt-4o"))
            self.assertEqual(status, "match", name)
            self.assertIsNotNone(tail)

    def test_shorter_model_does_not_absorb_longer_one(self):
        """gpt-4o must not inherit gpt-4o-mini's much cheaper rates."""
        status, tail = match_model(
            tokenize("gpt 4o mini 0718 Inp glbl Tokens"), tokenize("gpt-4o")
        )
        self.assertEqual(status, "unrecognised")
        self.assertEqual(tail[0], "mini")

    def test_point_release_is_a_different_model(self):
        """gpt-5 must not inherit gpt-5.1's rates via the stray "1" token."""
        status, _ = match_model(tokenize("GPT 5.1 opt Gl 1M Tokens"), tokenize("gpt-5"))
        self.assertEqual(status, "unrecognised")

    def test_point_release_matches_itself(self):
        status, tail = match_model(tokenize("GPT 5.1 opt Gl 1M Tokens"), tokenize("gpt-5.1"))
        self.assertEqual(status, "match")
        self.assertEqual(classify(tail), "output")

    def test_unrelated_model_does_not_match(self):
        status, _ = match_model(tokenize("Phi-4-Mini-Input Tokens"), tokenize("gpt-4o"))
        self.assertEqual(status, "no")


class TestClassify(unittest.TestCase):
    def test_every_output_spelling(self):
        """'outpt' was missing once and silently dropped GPT-5's output rate."""
        for tail in (
            ["outp", "glbl", "tokens"],
            ["outpt", "glbl", "1", "m", "tokens"],
            ["output", "global", "tokens"],
            ["opt", "gl", "1", "m", "tokens"],
        ):
            self.assertEqual(classify(tail), "output", tail)

    def test_every_input_spelling(self):
        for tail in (["inp", "glbl", "tokens"], ["inpt", "glbl", "tokens"], ["input", "global", "tokens"]):
            self.assertEqual(classify(tail), "input", tail)

    def test_cached_beats_plain_input(self):
        """Every cached meter also contains the word 'Inp', so ordering is load-bearing."""
        for tail in (
            ["cached", "inp", "glbl", "tokens"],
            ["cchd", "inpt", "glbl", "1", "m", "tokens"],
            ["cd", "inp", "gl", "1", "m", "tokens"],
        ):
            self.assertEqual(classify(tail), "cachedInput", tail)

    def test_cache_write_beats_cache_read(self):
        self.assertEqual(classify(["cache", "write", "glbl", "tokens"]), "cacheWrite")

    def test_undirected_meter_is_input(self):
        """Embedding models publish one undirected token rate and bill only on input."""
        self.assertEqual(classify(["glbl", "tokens"]), "input")


class TestExclusions(unittest.TestCase):
    def test_batch_and_finetune_are_excluded(self):
        for name in (
            "gpt 4o 1120 Batch Inp glbl Tokens",
            "gpt 4o dev ft inpt glbl Tokens",
        ):
            _, tail = match_model(tokenize(name), tokenize("gpt-4o"))
            self.assertTrue(is_excluded(tail, tokenize(name)), name)

    def test_standard_meter_is_not_excluded(self):
        name = "gpt 4o 1120 Inp glbl Tokens"
        _, tail = match_model(tokenize(name), tokenize("gpt-4o"))
        self.assertFalse(is_excluded(tail, tokenize(name)))


class TestDeployment(unittest.TestCase):
    def test_all_deployment_spellings(self):
        cases = {
            "global": (["inp", "glbl", "tokens"], ["input", "global", "tokens"]),
            "regional": (["inp", "regnl", "tokens"], ["input", "regional", "tokens"]),
            "datazone": (["inp", "data", "zone", "tokens"], ["inp", "dzone", "tokens"], ["inp", "dz", "tokens"]),
        }
        for expected, tails in cases.items():
            for tail in tails:
                self.assertEqual(extract_deployment(tail), expected, tail)


class TestUnitNormalisation(unittest.TestCase):
    def test_per_thousand_scales_to_million(self):
        self.assertAlmostEqual(normalise_to_million(0.0025, "1K"), 2.5)

    def test_per_million_is_unchanged(self):
        self.assertAlmostEqual(normalise_to_million(1.25, "1M"), 1.25)

    def test_unknown_unit_returns_none_rather_than_guessing(self):
        """A factor-of-1000 guess is the worst possible failure mode here."""
        self.assertIsNone(normalise_to_million(1.0, "per hour"))


class TestCollectRates(unittest.TestCase):
    ITEMS = [
        meter("gpt 4o 1120 Inp glbl Tokens", 0.0025),
        meter("gpt 4o 1120 Outp glbl Tokens", 0.01),
        meter("gpt 4o 1120 cached Inp glbl Tokens", 0.00125),
        meter("gpt 4o 1120 Inp regnl Tokens", 0.00275),
        meter("gpt 4o 0513 Input global Tokens", 0.005),
        meter("gpt 4o 0513 Output global Tokens", 0.015),
        meter("gpt 4o 1120 Batch Inp glbl Tokens", 0.00125),
        meter("gpt 4o mini 0718 Inp glbl Tokens", 0.00015),
    ]

    def test_deployment_filter_excludes_other_deployments(self):
        rates, _, _ = collect_rates("gpt-4o", self.ITEMS, "global")
        self.assertTrue(all(rate.deployment in (None, "global") for rate in rates))

    def test_newest_complete_version_is_chosen(self):
        rates, _, _ = collect_rates("gpt-4o", self.ITEMS, "global")
        self.assertEqual(choose_version(rates), "1120")

    def test_rates_are_per_million(self):
        rates, _, _ = collect_rates("gpt-4o", self.ITEMS, "global")
        selected = {r.bucket: r.per_million for r in rates if r.version == "1120"}
        self.assertAlmostEqual(selected["input"], 2.5)
        self.assertAlmostEqual(selected["output"], 10.0)
        self.assertAlmostEqual(selected["cachedInput"], 1.25)

    def test_sibling_model_reported_as_near_miss_not_absorbed(self):
        rates, _, near_misses = collect_rates("gpt-4o", self.ITEMS, "global")
        self.assertIn("mini", near_misses)
        self.assertTrue(all("mini" not in r.meter_name for r in rates))

    def test_version_without_output_loses_to_complete_version(self):
        """A version with only a cached rate is an artefact, not a usable rate card."""
        items = [
            meter("acme m 0101 cached Inp glbl Tokens", 0.001),
            meter("acme m 0100 Inp glbl Tokens", 0.002),
            meter("acme m 0100 Outp glbl Tokens", 0.004),
        ]
        rates, _, _ = collect_rates("acme-m", items, "global")
        self.assertEqual(choose_version(rates), "0100")


class TestContextBands(unittest.TestCase):
    """Context-length pricing. Every meter name here is real, copied from the eastus GPT-5.5 meters.

    These exist because GPT-5.5 was priced at one flat rate before context banding was implemented,
    which under-reported long-context requests by 2x on input and 1.5x on output - and did so
    invisibly, because the number looked entirely reasonable.
    """

    def test_bands_are_identified(self):
        self.assertEqual(extract_context(tokenize("5.5 ShortCo inp Gl 1M Tokens")[2:]), "short")
        self.assertEqual(extract_context(tokenize("5.5 LongCo inp Gl 1M Tokens")[2:]), "long")

    def test_unbanded_model_has_no_context(self):
        """The overwhelming majority of models are flat-rate and must stay that way."""
        self.assertIsNone(extract_context(tokenize("gpt 4o 1120 Inp glbl Tokens")[3:]))

    def test_threshold_lookup_is_spelling_insensitive(self):
        for spelling in ("gpt-5.5", "gpt-5-5", "GPT 5 5", "5.5"):
            self.assertEqual(context_threshold(spelling), 272_000, spelling)

    def test_unknown_model_has_no_threshold(self):
        """No threshold must mean no tier, never a guessed one."""
        self.assertIsNone(context_threshold("gpt-4o"))

    def test_short_and_long_rates_are_kept_apart(self):
        items = [
            meter("5.5 ShortCo inp Gl 1M Tokens", 5.0, "1M"),
            meter("5.5 ShortCo opt Gl 1M Tokens", 30.0, "1M"),
            meter("5.5 LongCo inp Gl 1M Tokens", 10.0, "1M"),
            meter("5.5 LongCo opt Gl 1M Tokens", 45.0, "1M"),
        ]
        rates, _, _ = collect_rates("5.5", items, "global")
        short = {r.bucket: r.per_million for r in rates if r.context == "short"}
        long_ = {r.bucket: r.per_million for r in rates if r.context == "long"}
        self.assertEqual(short, {"input": 5.0, "output": 30.0})
        self.assertEqual(long_, {"input": 10.0, "output": 45.0})

    def test_priority_processing_is_excluded(self):
        """'ShortCo PP' is 2.5x the standard rate. Letting it in would overstate every request."""
        items = [
            meter("5.5 ShortCo inp Gl 1M Tokens", 5.0, "1M"),
            meter("5.5 ShortCo PP inp Gl 1M Tokens", 12.5, "1M"),
        ]
        rates, _, _ = collect_rates("5.5", items, "global")
        self.assertEqual([r.per_million for r in rates], [5.0])

    def test_batch_long_context_is_still_excluded(self):
        """Banding must not accidentally readmit batch meters, which are a separate submission path."""
        items = [
            meter("5.5 LongCo Batch inp Gl 1M Tokens", 5.0, "1M"),
            meter("5.5 LongCo inp Gl 1M Tokens", 10.0, "1M"),
        ]
        rates, _, _ = collect_rates("5.5", items, "global")
        self.assertEqual([r.per_million for r in rates], [10.0])

    def test_missing_family_prefix_falls_back(self):
        """GPT-5.5's meters contain no 'gpt', so --alias chat=gpt-5.5 must still resolve."""
        items = [
            meter("5.5 ShortCo inp Gl 1M Tokens", 5.0, "1M"),
            meter("5.5 ShortCo opt Gl 1M Tokens", 30.0, "1M"),
        ]
        rates, notes, _ = collect_rates("gpt-5.5", items, "global")
        self.assertEqual({r.bucket for r in rates}, {"input", "output"})
        self.assertTrue(any("omits the family prefix" in note for note in notes))

    def test_prefix_fallback_does_not_broaden_a_model_that_already_matches(self):
        """The fallback must only fire when the full name matched nothing at all.

        Otherwise 'gpt-4o' would strip to '4o' and start absorbing meters from unrelated families
        that happen to end in the same characters.
        """
        items = [
            meter("gpt 4o 1120 Inp glbl Tokens", 0.0025),
            meter("4o mini Inp glbl Tokens", 0.00015),
        ]
        rates, notes, _ = collect_rates("gpt-4o", items, "global")
        self.assertEqual([r.per_million for r in rates], [2.5])
        self.assertFalse(any("omits the family prefix" in note for note in notes))


if __name__ == "__main__":
    unittest.main()
