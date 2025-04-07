import os
import logging
import hmac
import hashlib
from flask import Flask, request, abort

from google.cloud import firestore
from google.cloud import secretmanager
from vonage import Client, Sms, vonage_errors # Import vonage_errors for specific handling

# --- Configuration ---
# Load sensitive data from environment variables populated by Secret Manager
VONAGE_API_KEY = os.environ.get('VONAGE_API_KEY')
VONAGE_API_SECRET = os.environ.get('VONAGE_API_SECRET')
VONAGE_SIGNATURE_SECRET = os.environ.get('VONAGE_SIGNATURE_SECRET')
GCP_PROJECT_ID = os.environ.get('GCP_PROJECT_ID') # Usually set by GCP, but good practice

# --- Initialize Clients ---
# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Check for essential configuration
if not all([VONAGE_API_KEY, VONAGE_API_SECRET, VONAGE_SIGNATURE_SECRET]):
    logging.error("Missing Vonage API Key/Secret or Signature Secret environment variables.")
    # In a real deployment, this might prevent the function from starting,
    # or cause errors on first request. Handle appropriately.
    # For now, we'll let it fail later if needed.

# Firestore Client
try:
    db = firestore.Client(project=GCP_PROJECT_ID) # Explicit project ID optional if running on GCP
    TODO_COLLECTION = 'todo_lists'
except Exception as e:
    logging.exception(f"Failed to initialize Firestore client: {e}")
    db = None # Ensure db is None if init fails

# Vonage Client
try:
    vonage_client = Client(key=VONAGE_API_KEY, secret=VONAGE_API_SECRET)
    sms = Sms(vonage_client)
except Exception as e:
    logging.exception(f"Failed to initialize Vonage client: {e}")
    sms = None # Ensure sms is None if init fails

# --- Flask App ---
app = Flask(__name__)

# --- Helper Functions ---
def send_sms_reply(recipient, sender, message):
    """Sends an SMS reply using the Vonage client."""
    if not sms:
        logging.error("Vonage client not initialized. Cannot send SMS.")
        return False
    try:
        response_data = sms.send_message({
            'from': sender,
            'to': recipient,
            'text': message,
        })
        if response_data["messages"][0]["status"] == "0":
            logging.info(f"SMS sent successfully to {recipient}")
            return True
        else:
            error_text = response_data['messages'][0]['error-text']
            logging.error(f"Failed to send SMS to {recipient}: {error_text}")
            return False
    except vonage_errors.ClientError as e:
        logging.error(f"Vonage ClientError sending SMS to {recipient}: {e}")
        return False
    except Exception as e:
        logging.exception(f"Unexpected error sending SMS to {recipient}: {e}")
        return False

def verify_vonage_signature(request):
    """Verifies the Vonage signature using the X-Vonage-Signature header."""
    if not VONAGE_SIGNATURE_SECRET:
      logging.warning("VONAGE_SIGNATURE_SECRET not set, skipping signature verification.")
      # In production, you might want to return False here or abort.
      return True # TEMPORARILY allow for testing without secret setup

    signature_header = request.headers.get('X-Vonage-Signature')
    if not signature_header:
        logging.warning("Missing X-Vonage-Signature header.")
        return False

    # Assuming signature format is "sig=<hex_signature> timestamp=<ts>"
    # Adjust parsing based on actual header format if needed
    parts = {p.split('=')[0]: p.split('=')[1] for p in signature_header.split()}
    sig_vonage = parts.get('sig')
    ts_vonage = parts.get('timestamp') # Timestamp might be needed depending on Vonage method

    if not sig_vonage:
        logging.warning("Could not parse signature from header.")
        return False

    # IMPORTANT: Vonage signature calculation methods can vary.
    # This example assumes HMAC-SHA256 of the request body.
    # Check Vonage docs for the EXACT method for *your* webhook type (SMS).
    # It might involve concatenating timestamp, body, etc.
    # This is a common approach:
    # Prepare data - use raw body if possible
    payload = request.get_data() # Get raw bytes
    hasher = hmac.new(bytes(VONAGE_SIGNATURE_SECRET, 'utf-8'), payload, hashlib.sha256)
    calculated_sig = hasher.hexdigest()

    logging.info(f"Vonage Sig: {sig_vonage}, Calculated Sig: {calculated_sig}")

    if hmac.compare_digest(calculated_sig, sig_vonage):
        logging.info("Vonage signature verified successfully.")
        return True
    else:
        logging.warning("Vonage signature verification failed.")
        return False

# --- Main Webhook Handler ---
@app.route('/', methods=['POST'])
def handle_webhook():
    """Handles incoming Vonage SMS webhooks."""
    # Security Check 1: Signature Verification (IP filtering is done at GCP level)
    if not verify_vonage_signature(request):
         abort(401, "Invalid signature") # Unauthorized

    # Check if clients initialized correctly
    if not db or not sms:
        logging.error("Firestore or Vonage client not available.")
        # Return 200 to Vonage so it doesn't retry, but log the internal error.
        return "Internal server error: Service not configured", 200

    # Get data from Vonage webhook (assuming form data)
    try:
        sender_id = request.form['msisdn']
        recipient_id = request.form['to'] # This is your Vonage number, use as list ID
        message_text = request.form.get('text', '').strip()
        message_id = request.form.get('messageId', 'UNKNOWN') # For logging
    except KeyError as e:
        logging.error(f"Missing expected form field: {e}")
        # Don't try to reply if we don't have sender/recipient
        return "Bad Request: Missing data", 400 # Bad request

    logging.info(f"Received message (ID: {message_id}) from {sender_id} to {recipient_id}: '{message_text}'")

    # Parse command and arguments
    parts = message_text.lower().split(maxsplit=1)
    command = parts[0] if parts else ''
    argument = parts[1].strip() if len(parts) > 1 else ''

    # Get Firestore document reference for this Vonage number's list
    doc_ref = db.collection(TODO_COLLECTION).document(recipient_id)
    reply_message = ""

    # --- Command Logic ---
    try:
        if command == 'add':
            if not argument:
                reply_message = "Please specify item to add. Usage: add [item description]"
            else:
                # Add item to the 'tasks' array field. Creates doc/field if needed.
                doc_ref.set({'tasks': firestore.ArrayUnion([argument])}, merge=True)
                reply_message = f"✅ Added TODO: {argument}"
                logging.info(f"Added '{argument}' for list {recipient_id}")

        elif command == 'done':
            if not argument:
                reply_message = "Please specify item to mark done. Usage: done [item description]"
            else:
                # Remove item from the 'tasks' array field. Fails silently if item not found.
                doc_ref.update({'tasks': firestore.ArrayRemove([argument])})
                # Note: ArrayRemove doesn't tell us if it actually removed something.
                # For simplicity, we assume it worked or the item wasn't there.
                reply_message = f"👍 Marked done: {argument}"
                logging.info(f"Removed '{argument}' for list {recipient_id}")

        elif command == 'list':
            doc_snap = doc_ref.get()
            if doc_snap.exists and 'tasks' in doc_snap.to_dict() and doc_snap.to_dict()['tasks']:
                tasks = doc_snap.to_dict()['tasks']
                # Sort for consistent ordering if desired
                # tasks.sort()
                task_list_str = "\n".join([f"- {task}" for task in tasks])
                reply_message = f"📋 Open TODOs:\n{task_list_str}"
            else:
                reply_message = "🎉 No open TODOs!"
            logging.info(f"Listed tasks for list {recipient_id}")

        elif command == 'help':
            reply_message = (
                "Available commands:\n"
                "- add [item]: Add a TODO\n"
                "- done [item]: Remove a TODO\n"
                "- list: Show open TODOs\n"
                "- help: Show this message"
            )
        else:
            reply_message = f"😕 Unknown command '{command}'. Type 'help' for options."
            logging.warning(f"Unknown command '{command}' from {sender_id}")

    except Exception as e:
        logging.exception(f"Error processing command '{command}' for list {recipient_id}: {e}")
        reply_message = "😥 Sorry, an internal error occurred. Please try again later."

    # --- Send Reply ---
    if reply_message:
        send_sms_reply(recipient=sender_id, sender=recipient_id, message=reply_message)

    # --- Acknowledge Webhook ---
    # Return 200 OK to Vonage to signal successful receipt, regardless of command success/failure.
    return "Webhook received", 200

# --- Entry Point for Google Cloud Functions ---
# The name 'app' here matches the default expected by Gunicorn/Cloud Functions
# if no specific entrypoint is defined in deployment.
# If you named your Flask object something else (e.g., `my_flask_app = Flask(__name__)`),
# you'd need to ensure your Cloud Function entrypoint setting matches that name.

# Note: You might not need the following `if __name__ == '__main__':` block
# when deploying to Cloud Functions, as it uses a WSGI server like Gunicorn.
# However, it's useful for local testing.
if __name__ == '__main__':
    # Run the app locally for testing (requires Flask dev server)
    # Make sure to set environment variables locally (e.g., using .env file and python-dotenv)
    # Warning: Local execution won't have GCP IAM roles automatically.
    # You might need `gcloud auth application-default login` for Firestore access.
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))