# SMS TODO List Manager

[![Build Status](https://github.com/<YOUR_GITHUB_USERNAME>/<YOUR_REPOSITORY_NAME>/actions/workflows/deploy.yml/badge.svg)](https://github.com/<YOUR_GITHUB_USERNAME>/<YOUR_REPOSITORY_NAME>/actions/workflows/deploy.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT) <!-- Choose your license -->

A simple, cloud-hosted application to manage a shared household TODO list via SMS messages, using Vonage for SMS integration and Google Cloud Platform (GCP) for hosting.

## Description

This application allows users (like you and your wife) to manage a simple TODO list by sending SMS commands to a dedicated Vonage phone number included in a group chat. It leverages serverless technology on GCP for cost-effectiveness and scalability.

## Features

*   **Add Tasks:** Send `add [task description]` to add a new item.
*   **Complete Tasks:** Send `done [task description]` to remove an item (must match exactly, case-insensitive).
*   **List Tasks:** Send `list` to see all open items.
*   **Get Help:** Send `help` to see available commands.

## Architecture

The application uses a serverless architecture hosted on Google Cloud Platform:

1.  A user sends an SMS command (e.g., `add Buy milk`) to the shared Vonage number in a group chat.
2.  Vonage receives the SMS and triggers an HTTP POST webhook to a GCP Cloud Function endpoint.
3.  The GCP Cloud Function (`app.py`) receives the webhook.
4.  It verifies the Vonage signature for security.
5.  It parses the command and interacts with a Google Firestore database to add, remove, or list tasks associated with the Vonage number.
6.  Secrets (Vonage API keys) are securely retrieved from GCP Secret Manager.
7.  The Cloud Function uses the Vonage API to send a confirmation or the task list back to the original sender via SMS.
8.  Infrastructure (Cloud Function, Firestore, Secrets, IAM) is defined using Terraform.
9.  CI/CD is handled by GitHub Actions for Terraform planning (on PRs) and deployment (on merge to `main`).

```mermaid
graph LR
    subgraph User Space
        U[User (You/Wife)] -- SMS --> V[Vonage Number]
    end

    subgraph Vonage Cloud
        V -- Inbound SMS Webhook --> CF[GCP Cloud Function]
        V -- Send SMS API --> U
    end

    subgraph GCP Project
        CF -- Reads/Writes --> FS[Firestore Database]
        CF -- Reads Secret --> SM[Secret Manager]
        GHA[GitHub Actions Runner] -- Deploys --> CF
        GHA -- Manages Infra --> TFState[GCS Bucket for Terraform State]
        GHA -- Reads/Writes --> FS  # Via Terraform
        GHA -- Reads/Writes --> SM  # Via Terraform
        GHA -- Reads/Writes --> OtherGCP[Other GCP Resources via Terraform]
    end

    subgraph GitHub
        GHRepo[GitHub Repository] --> GHA
        UserDev[Developer] -- Pushes Code/Terraform --> GHRepo
    end

    CF -- Logs --> CL[Cloud Logging]
    TFState -- Stores State For --> TerraformInfra[Terraform Managed Infra]
    SM -- Stores --> VonageSecrets[Vonage API Key/Secret/Sig]

    %% Define connections specifically
    Vonage -->|HTTP POST (Webhook)| CF
    CF -->|Python SDK + API Key| Vonage
    CF -->|Firestore Client Lib| FS
    CF -->|Secret Manager Client Lib| SM
    GHA -->|Terraform + gcloud| GCP
```

## Repository Structure

```
.
├── .github/                      # GitHub specific files
│   └── workflows/                # GitHub Actions workflows
│       ├── deploy.yml            # Workflow to deploy on merge to main
│       └── terraform-plan.yml    # Workflow to run terraform plan on PR
│
├── terraform/                    # Terraform Infrastructure as Code
│   ├── main.tf                   # Main config, providers, API enabling
│   ├── variables.tf              # Input variables
│   ├── outputs.tf                # Outputs (function URL)
│   ├── cloud_function.tf         # Cloud Function definition & IAM
│   ├── firestore.tf              # Firestore database setup
│   ├── secrets.tf                # Secret Manager resources
│   ├── iam.tf                    # Service Accounts, WIF setup, IAM bindings
│   ├── storage.tf                # GCS buckets (TF state, function source)
│   └── versions.tf               # Terraform/provider version constraints
│
├── app.py                        # Main Python application logic (Cloud Function)
├── requirements.txt              # Python dependencies
├── .gitignore                    # Files ignored by git
├── LICENSE                       # Project License
└── README.md                     # This file
```

## Prerequisites

Before you begin, ensure you have the following:

1.  **Git:** Installed locally.
2.  **Terraform:** Version >= 1.3 installed ([Download Terraform](https://www.terraform.io/downloads)).
3.  **Google Cloud SDK (`gcloud`):** Installed and configured ([Install gcloud](https://cloud.google.com/sdk/docs/install)).
4.  **GCP Project:** A Google Cloud Platform project with Billing enabled. Note your `Project ID`.
5.  **Vonage Account:**
    *   A Vonage API account ([Vonage Signup](https://dashboard.nexmo.com/sign-up)).
    *   Your Vonage API Key.
    *   Your Vonage API Secret.
    *   Your Vonage **Signature Secret** (from Vonage dashboard settings).
    *   A Vonage phone number capable of sending/receiving SMS. Note the number.
6.  **GitHub Account:** A GitHub account and a repository created for this project.

## Setup

Follow these steps to prepare your environment for deployment:

1.  **Clone the Repository:**
    ```bash
    git clone https://github.com/<YOUR_GITHUB_USERNAME>/<YOUR_REPOSITORY_NAME>.git
    cd <YOUR_REPOSITORY_NAME>
    ```

2.  **Authenticate gcloud:**
    Log in to your Google Cloud account and set application default credentials.
    ```bash
    gcloud auth login
    gcloud auth application-default login
    ```

3.  **Configure gcloud Project:**
    Set your active GCP project.
    ```bash
    gcloud config set project YOUR_PROJECT_ID
    ```

4.  **Create GCS Buckets:**
    Terraform needs buckets for remote state and Cloud Function source code. Create them **before** running Terraform (replace placeholders):
    ```bash
    # Bucket for Terraform state (must be globally unique)
    gsutil mb -p YOUR_PROJECT_ID -l YOUR_REGION gs://your-unique-tf-state-bucket-name

    # Bucket for Cloud Function source code (must be globally unique)
    gsutil mb -p YOUR_PROJECT_ID -l YOUR_REGION gs://your-unique-function-source-bucket-name
    ```
    *Note: Choose an appropriate `YOUR_REGION` (e.g., `us-central1`).*

5.  **Configure Terraform Backend:**
    When you initialize Terraform, you'll link it to the state bucket:
    ```bash
    cd terraform
    terraform init -backend-config="bucket=your-unique-tf-state-bucket-name"
    cd ..
    ```

6.  **Prepare Terraform Variables:**
    Create a file named `terraform/terraform.tfvars` (this file is ignored by `.gitignore`) or prepare to pass variables via the command line. Add the following required variables:
    ```hcl
    # terraform/terraform.tfvars
    project_id     = "YOUR_PROJECT_ID"
    region         = "YOUR_REGION" # e.g., "us-central1"
    github_repo    = "<YOUR_GITHUB_USERNAME>/<YOUR_REPOSITORY_NAME>" # e.g., "myuser/sms-todo-app"
    function_source_code_bucket_name = "your-unique-function-source-bucket-name"

    # Optional: Review and override defaults in terraform/variables.tf if needed
    # vonage_webhook_ips = ["list", "of", "vonage", "ips"] # Update if Vonage IPs change
    # vonage_secret_config = { ... } # Secret names can be changed if desired
    ```

7.  **Configure GitHub Actions Secrets:**
    Terraform creates a Workload Identity Federation setup to allow GitHub Actions to authenticate securely with GCP. After the first Terraform deployment (or by reading the outputs), you need to configure secrets in your GitHub repository (**Settings -> Secrets and variables -> Actions -> New repository secret**):
    *   `GCP_PROJECT_ID`: Your GCP Project ID.
    *   `GCP_WIF_PROVIDER`: The full name of the Workload Identity Provider created by Terraform. Get this from the `workload_identity_provider_name` Terraform output after the first apply.
    *   `GCP_SERVICE_ACCOUNT_EMAIL`: The email address of the service account created for GitHub Actions. Get this from the `github_actions_runner_service_account_email` Terraform output after the first apply.

## Deployment

You can deploy the infrastructure and application manually using Terraform or automatically using the configured GitHub Actions workflow.

**1. Manual Deployment (Terraform CLI):**

   *   Navigate to the Terraform directory:
       ```bash
       cd terraform
       ```
   *   Initialize Terraform (if you haven't already after cloning):
       ```bash
       terraform init -backend-config="bucket=your-unique-tf-state-bucket-name"
       ```
   *   Plan the deployment:
       ```bash
       terraform plan -var-file=terraform.tfvars -out=tfplan
       # Or: terraform plan -var="project_id=..." -var="github_repo=..." ... -out=tfplan
       ```
   *   Apply the plan:
       ```bash
       terraform apply tfplan
       ```
   *   **IMPORTANT: Add Secret Values:** Terraform creates the *secret resources* but not their *values*. After the **first successful `terraform apply`**, you **MUST** add the actual Vonage credentials:
       ```bash
       # Get the exact secret IDs from your terraform/variables.tf (vonage_secret_config map)
       API_KEY_SECRET_ID="vonage-api-key" # Default value
       API_SECRET_SECRET_ID="vonage-api-secret" # Default value
       SIG_SECRET_ID="vonage-signature-secret" # Default value

       echo -n "YOUR_VONAGE_API_KEY" | gcloud secrets versions add $API_KEY_SECRET_ID --data-file=- --project YOUR_PROJECT_ID
       echo -n "YOUR_VONAGE_API_SECRET" | gcloud secrets versions add $API_SECRET_SECRET_ID --data-file=- --project YOUR_PROJECT_ID
       echo -n "YOUR_VONAGE_SIGNATURE_SECRET" | gcloud secrets versions add $SIG_SECRET_ID --data-file=- --project YOUR_PROJECT_ID
       ```
       *(Replace `YOUR_VONAGE_...` and `YOUR_PROJECT_ID` with actual values).*

**2. Automated Deployment (GitHub Actions):**

   *   Ensure you have configured the GitHub Actions secrets (`GCP_PROJECT_ID`, `GCP_WIF_PROVIDER`, `GCP_SERVICE_ACCOUNT_EMAIL`) as described in the **Setup** section.
   *   **Plan on Pull Request:** When you open a Pull Request modifying files in the `terraform/` directory or the application code (`app.py`, `requirements.txt`), the `terraform-plan.yml` workflow will run `terraform plan`.
   *   **Apply on Merge:** When a Pull Request is merged into the `main` branch, the `deploy.yml` workflow will run `terraform apply -auto-approve`, deploying infrastructure changes and the latest application code.
   *   **Remember:** You still need to manually add the Vonage secret *values* via the `gcloud` commands above after the *first* successful deployment creates the secret resources. Subsequent deploys will update the function code but leave the secret values untouched.

## Post-Deployment Configuration

*   **Configure Vonage Webhook:**
    1.  After deployment, get the Cloud Function URL from the Terraform output:
        ```bash
        cd terraform
        terraform output function_https_trigger_url
        ```
    2.  Go to your Vonage Dashboard -> Numbers -> Your Numbers.
    3.  Select your SMS-capable number used for this service.
    4.  In the "Messaging" section (or similar), configure the **Inbound Webhook URL** under "SMS" to the HTTPS URL obtained from the Terraform output.
    5.  Set the webhook method to `POST` (likely `POST-Form` or `POST-JSON`, ensure `app.py` matches). `app.py` currently expects `POST-Form` (`application/x-www-form-urlencoded`).
    6.  Save the changes.

## Usage

1.  Create a group SMS/MMS chat including your phone number, your wife's phone number, and the Vonage phone number you configured.
2.  Send messages to the group chat using the following commands:
    *   `add Buy groceries` - Adds "Buy groceries" to the list.
    *   `done Buy groceries` - Removes "Buy groceries" from the list (case-insensitive match).
    *   `list` - Shows all current TODO items.
    *   `help` - Shows the help message with available commands.
3.  The application will reply within the group chat (sending the reply only back to the original sender of the command).

## Configuration Details

*   **Vonage Credentials:** Stored securely in GCP Secret Manager (`vonage-api-key`, `vonage-api-secret`, `vonage-signature-secret` by default) and accessed by the Cloud Function via environment variables:
    *   `VONAGE_API_KEY`
    *   `VONAGE_API_SECRET`
    *   `VONAGE_SIGNATURE_SECRET`
*   **GCP Project ID:** The function usually detects this automatically when running on GCP, but it can be explicitly set via the `GCP_PROJECT_ID` environment variable if needed.

## Security Considerations

*   **Vonage Signature Verification:** The `app.py` includes code to verify the `X-Vonage-Signature` header using the `VONAGE_SIGNATURE_SECRET`. This is the **primary mechanism** to ensure that only legitimate requests from Vonage trigger the function. Ensure your Signature Secret is correctly set in GCP Secret Manager.
*   **IP Whitelisting:** While the `vonage_webhook_ips` variable exists in Terraform, directly applying IP filtering to Cloud Functions v2 public endpoints via Terraform is not straightforward. The primary security layer remains signature verification.
*   **Secret Management:** Sensitive keys are stored in GCP Secret Manager, not in code. Access is controlled via IAM. Never commit secret values to Git.
*   **IAM Permissions:** Terraform sets up dedicated service accounts for the Cloud Function and GitHub Actions with specific roles. Review the roles in `terraform/iam.tf` to understand the granted permissions.

## Local Development & Testing

You can run the Flask application locally for testing the core logic (requires Python and pip installed):

1.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
2.  **Set Environment Variables:** You need to provide the Vonage credentials locally. You can use environment variables directly or use a tool like `python-dotenv` (install via `pip install python-dotenv`) and create a `.env` file (add `.env` to your `.gitignore`!):
    ```
    # .env
    VONAGE_API_KEY=your_local_vonage_api_key
    VONAGE_API_SECRET=your_local_vonage_api_secret
    VONAGE_SIGNATURE_SECRET=your_local_vonage_signature_secret
    GCP_PROJECT_ID=your_gcp_project_id # Needed for Firestore client
    ```
3.  **Authenticate for GCP Services:** To interact with Firestore locally, authenticate your user credentials:
    ```bash
    gcloud auth application-default login
    ```
4.  **Run the App:**
    ```bash
    # If using python-dotenv, it might load automatically, or you might need to adjust app.py slightly to load it.
    python app.py
    ```
    The app will start (usually on `http://localhost:8080`). You can then use tools like `curl` or Postman to send simulated Vonage webhook POST requests to test command parsing and Firestore interaction. Note that sending actual SMS replies will likely require the full Vonage client setup. Signature verification might need adjustment or temporary disabling for local testing if you can't easily replicate the signature header.

## Cleanup

To remove all deployed resources and avoid ongoing costs, navigate to the Terraform directory and run:

```bash
cd terraform
terraform destroy -var-file=terraform.tfvars # Or pass vars via -var flags
```
Remember to also delete the GCS buckets manually if you don't want them anymore:
```bash
gsutil rm -r gs://your-unique-tf-state-bucket-name
gsutil rm -r gs://your-unique-function-source-bucket-name
```

## License

This project is licensed under the [MIT License](LICENSE). <!-- Update link/text if you choose a different license -->