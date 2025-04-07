terraform {
  required_version = ">= 1.11.3" # Use a recent version

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.28.0" # Use a recent version
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.7.0"
    }
  }

  # Configure GCS backend for remote state management
  backend "gcs" {
    # Bucket name will be provided via command line or TF Cloud/automation
    # Example: terraform init -backend-config="bucket=your-tf-state-bucket-name"
    prefix = "sms-todo/state"
  }
}