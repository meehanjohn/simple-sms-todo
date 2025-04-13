# terraform/main.tf

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  # Define the list of required APIs
  required_gcp_apis = toset([
    "cloudfunctions.googleapis.com",
    "cloudbuild.googleapis.com",
    "firestore.googleapis.com",
    "secretmanager.googleapis.com",
    "iam.googleapis.com",
    "run.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "storage-component.googleapis.com", # Often needed for storage operations
    "storage-api.googleapis.com",       # Often needed for storage operations
    "eventarc.googleapis.com"
  ])
}

# Enable necessary APIs for the project using for_each
resource "google_project_service" "apis" {
  for_each = local.required_gcp_apis # Iterate over the set of API strings

  project                    = var.project_id
  service                    = each.key # Use the set member as the service name
  disable_dependent_services = false    # Keep default behavior
  disable_on_destroy         = false    # Keep APIs enabled if Terraform destroys infra
}

# Data source to get the project number needed for some IAM bindings
data "google_project" "project" {
  project_id = var.project_id
}

# Ensure API enabling happens before resources that depend on them
# This explicit dependency might be needed if implicit ones aren't enough
# (Usually handled implicitly, but added for clarity if issues arise)
# resource "null_resource" "api_dependency_barrier" {
#   depends_on = [google_project_service.apis]
# }