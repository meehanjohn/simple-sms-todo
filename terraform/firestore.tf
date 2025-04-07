# Ensure Firestore is initialized in Native mode.
# This resource attempts to create it if it doesn't exist.
# Often, enabling the API (in main.tf) is sufficient if Firestore was already setup manually.
resource "google_firestore_database" "database" {
  project                 = var.project_id
  name                    = "(default)" # Use the default database
  location_id             = var.region  # Or specific multi-region like nam5
  type                    = "FIRESTORE_NATIVE"
  delete_protection_state = "DELETE_PROTECTION_DISABLED" # Or enabled for safety
  # depends_on = [google_project_service.firestore] # Implicit dependency usually sufficient
}