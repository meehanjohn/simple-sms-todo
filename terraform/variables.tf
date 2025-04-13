variable "project_id" {
  description = "The GCP Project ID to deploy resources into."
  type        = string
}

variable "region" {
  description = "The GCP region to deploy resources into."
  type        = string
  default     = "us-central1" # Choose an appropriate region
}

variable "vonage_webhook_ips" {
  description = "List of allowed Vonage IP ranges for webhook ingress (CIDR format). Find these at https://developer.vonage.com/en/getting-started/concepts/ip-whitelisting#webhook-ip-addresses"
  type        = list(string)
  # Example - Replace with actual Vonage IPs!
  default = [
    "216.147.0.0/18",
    "168.100.64.0/18",
    # Add all relevant IPs from the Vonage documentation for your region/product
  ]
}

variable "github_repo" {
  description = "GitHub repository in 'owner/repo' format for Workload Identity Federation."
  type        = string
  # Example: default = "my-github-username/sms-todo-app"
}

variable "function_name" {
  description = "Name for the Cloud Function."
  type        = string
  default     = "sms-todo-handler"
}

variable "function_source_dir" {
  description = "Path to the directory containing the function's Python code (app.py, requirements.txt)."
  type        = string
  default     = "../src/" # Assumes terraform/ is one level below the app code
}

variable "function_entry_point" {
  description = "The entry point function/object name within your Python code."
  type        = string
  default     = "sms_todo_handler" # Matches the default Flask app object name in the example app.py
}

variable "function_runtime" {
  description = "The Python runtime for the Cloud Function."
  type        = string
  default     = "python311" # Use a supported Python version
}

variable "vonage_secret_config" {
  description = "Configuration for Vonage secrets."
  type = map(object({
    secret_id = string # The name (ID) for the secret in Secret Manager
    env_var   = string # The corresponding environment variable name for the function
  }))
  default = {
    "api_key" = {
      secret_id = "vonage-api-key"
      env_var   = "VONAGE_API_KEY"
    },
    "api_secret" = {
      secret_id = "vonage-api-secret"
      env_var   = "VONAGE_API_SECRET"
    },
    "signature_secret" = {
      secret_id = "vonage-signature-secret"
      env_var   = "VONAGE_SIGNATURE_SECRET"
    }
  }
}

# Note: Terraform state bucket name is configured via the backend block, often passed during init
# variable "terraform_state_bucket" {
#   description = "Name of the GCS bucket for Terraform remote state."
#   type        = string
# }

variable "function_source_code_bucket_name" {
  description = "Name of the GCS bucket to store the zipped function source code."
  type        = string
  # Example: default = "my-app-function-source-code" - Make this unique!
}