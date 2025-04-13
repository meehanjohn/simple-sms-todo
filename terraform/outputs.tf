output "function_https_trigger_url" {
  description = "The HTTPS URL trigger for the Cloud Function."
  value       = google_cloudfunctions2_function.default.service_config[0].uri
  sensitive   = false # URL itself isn't typically sensitive
}

output "function_service_account_email" {
  description = "Email of the service account used by the Cloud Function."
  value       = google_service_account.function_identity.email
}

output "github_actions_runner_service_account_email" {
  description = "Email of the service account used by GitHub Actions."
  value       = google_service_account.github_actions_runner.email
}

output "workload_identity_provider_name" {
  description = "The full name of the Workload Identity Provider for GitHub Actions."
  value       = google_iam_workload_identity_pool_provider.github_provider.name
}

output "function_source_code_bucket_name_output" {
 description = "Name of the GCS bucket storing the function source code."
 value = google_storage_bucket.function_source_code.name
}