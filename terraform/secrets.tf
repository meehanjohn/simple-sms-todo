# terraform/secrets.tf

resource "google_secret_manager_secret" "vonage_secrets" {
  for_each = var.vonage_secret_config # Iterate over the map keys ("api_key", etc.)

  project   = var.project_id
  secret_id = each.value.secret_id # Get the secret_id from the map value

  replication {
    auto {}
  }
}

# --- IMPORTANT ---
# Manual step to add secret *values* remains the same, but use the correct secret_id:
# After apply, run for each key in var.vonage_secret_config:
#   SECRET_KEY_NAME = lookup(var.vonage_secret_config, "api_key", null).secret_id # Example for api_key
#   echo -n "YOUR_VALUE" | gcloud secrets versions add $SECRET_KEY_NAME --data-file=- --project ${var.project_id}