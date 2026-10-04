config {
  # Child modules are linted directly via --recursive, so there is no need
  # to descend into them from each caller and report the same finding twice.
  call_module_type = "none"
}

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

plugin "azurerm" {
  enabled = true
  version = "0.28.0"
  source  = "github.com/terraform-linters/tflint-ruleset-azurerm"
}

# Variables carry descriptions and types throughout; keep that enforced so
# the generated module documentation stays useful to consumers.
rule "terraform_documented_variables" {
  enabled = true
}

rule "terraform_documented_outputs" {
  enabled = true
}

rule "terraform_typed_variables" {
  enabled = true
}

rule "terraform_naming_convention" {
  enabled = true
  format  = "snake_case"
}

rule "terraform_unused_declarations" {
  enabled = true
}

# The examples are root modules pinned by their own lock files; the shared
# modules under modules/ intentionally express ranges rather than exact
# pins so that consumers remain free to choose a provider version.
rule "terraform_required_providers" {
  enabled = true
}

rule "terraform_required_version" {
  enabled = true
}
