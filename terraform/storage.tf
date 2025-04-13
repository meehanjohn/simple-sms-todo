# Bucket for Terraform state is configured in the backend block (versions.tf)
# Ensure that bucket exists before running `terraform init`
# Example gcloud command: gsutil mb -p YOUR_PROJECT_ID -l YOUR_REGION gs://your-unique-tf-state-bucket-name

# Bucket to store the zipped source code for Cloud Function deployment
resource "google_storage_bucket" "function_source_code" {
  name          = var.function_source_code_bucket_name # Ensure this is globally unique
  project       = var.project_id
  location      = var.region # Store source code near the function
  uniform_bucket_level_access = true

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = 7 # Keep old source versions for a week (adjust as needed)
    }
  }

  # Optional: If you delete the function, delete the source bucket too
  # force_destroy = true
}

# --- IAM for Source Code Bucket (needed by Function Build & Terraform Runner) ---

# Allow Cloud Build service account (used internally by CF deployment) to read the source
data "google_project" "project_num" { # Using separate data source to get project number
  project_id = var.project_id
}

resource "google_storage_bucket_iam_member" "build_service_account_source_reader" {
  bucket = google_storage_bucket.function_source_code.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${data.google_project.project_num.number}@cloudbuild.gserviceaccount.com"
}

# The Terraform Runner SA (defined in iam.tf) also needs to write here
# This binding is added in iam.tf to avoid circular dependency issues