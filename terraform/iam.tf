# terraform/iam.tf

# --- Service Account for the Cloud Function ---
resource "google_service_account" "function_identity" {
  project      = var.project_id
  account_id   = "${var.function_name}-sa"
  display_name = "Service Account for SMS TODO Cloud Function"
}

# --- Grant Function SA permission to access Secrets (using for_each) ---
resource "google_secret_manager_secret_iam_member" "function_secret_accessors" {
  for_each = google_secret_manager_secret.vonage_secrets # Iterate over the created secrets

  project   = each.value.project # Use the project from the iterated secret resource
  secret_id = each.value.secret_id # Use the secret_id from the iterated secret resource
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.function_identity.email}"
}

# --- Grant Function SA other permissions (Firestore, Logging) - No change here ---
resource "google_project_iam_member" "function_firestore_user" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.function_identity.email}"
}

resource "google_project_iam_member" "function_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.function_identity.email}"
}

# --- Service Account and WIF for GitHub Actions (Terraform Runner) - No change here ---
# ... (google_service_account.github_actions_runner, WIF pool/provider, IAM bindings for runner) ...
# Keep the existing for_each for github_runner_permissions
locals {
  github_runner_roles = [
    "roles/cloudfunctions.developer",   # Create/update/delete functions
    "roles/run.admin",                  # Manage underlying Cloud Run services (for CFv2)
    "roles/iam.serviceAccountUser",     # Impersonate the function's SA during deployment
    "roles/storage.admin",              # Manage TF state bucket and function source bucket
    "roles/secretmanager.admin",        # Manage secrets (needed to create/update secret resources) - Tighten if possible
    "roles/datastore.owner",            # Manage Firestore (can use datastore.user if only R/W needed after creation)
    "roles/serviceusage.serviceUsageAdmin", # Enable APIs
    "roles/cloudresourcemanager.projectIamAdmin" # Potentially needed to set IAM policies on resources - scope down if possible
  ]
}
resource "google_project_iam_member" "github_runner_permissions" {
  for_each = toset(local.github_runner_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.github_actions_runner.email}"
}

# Grant Terraform Runner SA permission to write to the source code bucket
resource "google_storage_bucket_iam_member" "runner_sa_source_writer" {
  bucket = google_storage_bucket.function_source_code.name
  role   = "roles/storage.objectAdmin" # Needs create/overwrite permissions
  member = "serviceAccount:${google_service_account.github_actions_runner.email}"
  depends_on = [
    google_storage_bucket.function_source_code,
    google_service_account.github_actions_runner
  ]
}