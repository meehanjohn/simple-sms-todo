# Zip the application code directory
data "archive_file" "function_source" {
  type        = "zip"
  source_dir  = var.function_source_dir
  output_path = "${path.module}/files/function_source_${timestamp()}.zip" # Temporary zip file location
}

# Upload the zipped code to the GCS bucket
resource "google_storage_bucket_object" "function_source_archive" {
  name   = "${var.function_name}-${data.archive_file.function_source.output_md5}.zip"
  bucket = google_storage_bucket.function_source_code.name
  source = data.archive_file.function_source.output_path # Path to the zipped file
}

# Cloud Function (V2) Resource
resource "google_cloudfunctions2_function" "default" {
  project  = var.project_id
  name     = var.function_name
  location = var.region

  build_config {
    runtime     = var.function_runtime
    entry_point = var.function_entry_point
    source {
      storage_source {
        bucket = google_storage_bucket.function_source_code.name
        object = google_storage_bucket_object.function_source_archive.name
      }
    }
  }

  service_config {
    max_instance_count             = 3
    min_instance_count             = 0
    available_memory               = "256Mi"
    timeout_seconds                = 60
    ingress_settings               = "ALLOW_ALL" # Rely on signature verification in app
    all_traffic_on_latest_revision = true
    service_account_email          = google_service_account.function_identity.email

    # Inject secrets as environment variables using a dynamic block
    dynamic "secret_environment_variables" {
      for_each = var.vonage_secret_config # Iterate over the same map used for secret creation

      content {
        key        = secret_environment_variables.value.env_var # Use env_var from map value
        project_id = var.project_id
        # Reference the secret created in the secrets.tf for_each loop
        secret     = google_secret_manager_secret.vonage_secrets[secret_environment_variables.key].secret_id
        version    = "latest" # Always use the latest version
      }
    }

    # Optionally pass project ID if app needs it and can't autodetect
    # environment_variables = {
    #   GCP_PROJECT_ID = var.project_id
    # }
  }

  # Ensure dependent services/permissions are ready
  depends_on = [
    google_project_service.apis, # Depend on all APIs being enabled
    google_secret_manager_secret_iam_member.function_secret_accessors, # Depend on all secret permissions
    google_project_iam_member.function_firestore_user,
    google_storage_bucket_object.function_source_archive,
    google_service_account.function_identity,
  ]
}

# Allow public HTTPS invocation of the function (required for Vonage webhook)
resource "google_cloudfunctions2_function_iam_member" "invoker" {
  project       = google_cloudfunctions2_function.default.project
  location      = google_cloudfunctions2_function.default.location
  cloud_function = google_cloudfunctions2_function.default.name
  role          = "roles/cloudfunctions.invoker"
  member        = "allUsers" # Makes the function URL publicly accessible
}

# --- Output ---
output "function_url" {
  description = "The HTTPS URL of the deployed Cloud Function."
  value       = google_cloudfunctions2_function.default.service_config[0].uri
}

# --- NOTE ON IP FILTERING ---
# Cloud Functions V2 + Terraform currently lack a straightforward way to apply
# source IP range restrictions directly on the public HTTP endpoint via Terraform resource definition.
# Possible alternatives for stricter network security (beyond this scope):
# 1. Place GCP HTTP(S) Load Balancer + Cloud Armor in front of the function.
# 2. Use API Gateway in front of the function with API Key + Quota/IP rules.
# 3. Implement STRICT signature validation within the `app.py` code (as done in the example).
# For this application's requirements, relying on Vonage Signature Verification
# inside the function (configured via `VONAGE_SIGNATURE_SECRET`) is the
# implemented security measure against unauthorized access.