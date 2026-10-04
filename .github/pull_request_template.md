# What this changes

<!-- What does this do, and why? Link any related issue. -->

## Type

- [ ] Bug fix
- [ ] New capability
- [ ] Breaking change to a module input or output
- [ ] Documentation only

## Checks

<!-- CI runs all of these, but running them locally first is faster than waiting. -->

- [ ] `terraform fmt -check -recursive`
- [ ] `terraform validate` in every affected module and example
- [ ] `./scripts/check_policies.ps1` — **required if you touched any `.tftpl`**
- [ ] `python -m unittest discover -s tests`
- [ ] `tflint --recursive`

> `terraform validate` cannot see inside policy templates: `templatefile()` is
> evaluated at plan time, so malformed XML validates cleanly and then fails on
> apply against live APIM. `check_policies.ps1` is the only check that catches it.

## Impact on adopters

- [ ] Adds or changes a module input or output — the affected example is updated
- [ ] Changes cost calculation or attribution — reasoning is in the PR or an ADR
- [ ] Changes the authentication or trust boundary — an ADR is included
- [ ] Requires action from someone upgrading, described below

<!-- If a consumer has to change something when they take this, say so plainly. -->

## What you could not verify

<!-- Be specific. "I could not test the private-endpoint path" is genuinely useful;
     silence reads as "fully tested" and usually is not. -->
