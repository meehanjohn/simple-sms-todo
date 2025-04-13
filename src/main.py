# src/main.py

import os
import logging
import re
import random
from typing import List, Tuple, Optional, Dict, Any, Callable

import functions_framework
from flask import Request

from google.cloud import firestore
from google.api_core.exceptions import NotFound
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.document import DocumentReference # For type hints
from google.cloud.firestore_v1.transaction import Transaction # For type hints

# Vonage Imports
from vonage import Vonage, Auth, VonageError as VonageClientError
from vonage_sms import SmsMessage
from vonage_jwt import verify_signature

# --- Import word lists ---
# Assumes word_lists.py is in the same directory (src/)
try:
    from .word_lists import ADJECTIVES, NOUNS
except ImportError:
    # Fallback for local testing if structure is different
    try:
        from word_lists import ADJECTIVES, NOUNS
    except ImportError:
        logging.error("Could not import word lists. Alias generation will fail.")
        ADJECTIVES = ["default"]
        NOUNS = ["list"]


# --- Import phonenumbers ---
try:
    import phonenumbers
    from phonenumbers import NumberParseException
except ImportError:
    phonenumbers = None # Allow running basic tests without it if needed
    NumberParseException = Exception # Placeholder
    logging.error("phonenumbers library not found. Phone number validation will be basic.")


# --- Configuration ---
VONAGE_API_KEY = os.environ.get('VONAGE_API_KEY')
VONAGE_API_SECRET = os.environ.get('VONAGE_API_SECRET')
VONAGE_SIGNATURE_SECRET = os.environ.get('VONAGE_SIGNATURE_SECRET')
GCP_PROJECT_ID = os.environ.get('GCP_PROJECT_ID')

# --- Initialize Clients ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
    force=True
)

# Check essential config
if not all([VONAGE_API_KEY, VONAGE_API_SECRET]):
    logging.error("Missing Vonage API Key/Secret environment variables.")
if not VONAGE_SIGNATURE_SECRET:
    logging.warning("VONAGE_SIGNATURE_SECRET environment variable not set. Signature verification will be skipped if enabled.")

# Firestore Client
try:
    db = firestore.Client(project=GCP_PROJECT_ID)
    logging.info("Firestore client initialized successfully.")
except Exception as e:
    logging.exception(f"Failed to initialize Firestore client: {e}")
    db = None # Application should fail gracefully if DB is unavailable

# Vonage Client
try:
    if VONAGE_API_KEY and VONAGE_API_SECRET:
        auth = Auth(api_key=VONAGE_API_KEY, api_secret=VONAGE_API_SECRET)
        vonage_client = Vonage(auth=auth)
        logging.info("Vonage client initialized successfully.")
    else:
        vonage_client = None
        logging.error("Cannot initialize Vonage client due to missing API Key/Secret.")
except Exception as e:
    logging.exception(f"Failed to initialize Vonage client: {e}")
    vonage_client = None

# --- Constants ---
LISTS_COLLECTION = 'lists'
USERS_COLLECTION = 'users'

# Command Constants
CMD_ADD = "add"
CMD_DONE = "done"
CMD_LIST = "list"
CMD_CREATE = "create"    
CMD_LISTS = "lists"
CMD_HELP = "help"
CMD_INVITE = "invite"
CMD_REMOVE = "remove"
CMD_LEAVE = "leave"
CMD_RENAME = "rename"

# Commands that modify list membership (used for notification accuracy check)
MEMBER_MODIFYING_COMMANDS = {CMD_INVITE, CMD_REMOVE, CMD_LEAVE}

# --- Custom Exceptions ---
class RequestValidationError(Exception):
    """Custom exception for request validation errors."""
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code

class CommandError(Exception):
    """Custom exception for user-facing errors during command processing."""
    pass

# --- Helper Functions ---

def generate_memorable_alias() -> str:
    """Generates a random, memorable alias using adjective-noun-4digitnumber."""
    adj = random.choice(ADJECTIVES)
    noun = random.choice(NOUNS)
    # Generate a 4-digit number (1000-9999)
    num = random.randint(1000, 9999)
    return f"{adj}-{noun}-{num}"

# Regex parser remains the same
message_parser = re.compile(
    r"^(?:([\w-]+):\s*)?"  # Optional non-capturing group for "alias:", captures alias (letters, numbers, -, _)
    r"(\w+)"               # Captures the command (word characters)
    r"(?:\s+(.*))?$",      # Optional non-capturing group for space + args, captures args
    re.IGNORECASE | re.DOTALL
)

def normalize_phone_number(phone: str, default_region: str = "US") -> Optional[str]:
    """Normalize phone number to E.164 format using phonenumbers library."""
    if not phone: return None
    if not phonenumbers:
        logging.warning("phonenumbers library not available, performing basic normalization.")
        # Fallback to basic US-centric logic if library is missing
        digits = re.sub(r"[^\d+]", "", phone)
        if not digits: return None
        if digits.startswith('+'): return digits
        if len(digits) == 10: return f"+1{digits}"
        if len(digits) == 11 and digits.startswith('1'): return f"+{digits}"
        logging.warning(f"Basic normalization failed for: {phone}")
        return None

    try:
        # Parse the number, using default_region if no country code is present
        parsed_number = phonenumbers.parse(phone, default_region)

        # Check if the number is valid
        if not phonenumbers.is_valid_number(parsed_number):
            logging.warning(f"Invalid phone number provided: {phone}")
            return None

        # Format to E.164
        formatted_number = phonenumbers.format_number(parsed_number, phonenumbers.PhoneNumberFormat.E164)
        return formatted_number

    except NumberParseException as e:
        logging.warning(f"Could not parse phone number '{phone}': {e}")
        return None
    except Exception as e: # Catch unexpected errors during parsing/validation
        logging.exception(f"Unexpected error normalizing phone number '{phone}': {e}")
        return None


def send_sms_reply(recipient: str, sender: str, message: str, dry_run: bool = False):
    """Sends an SMS reply using the Vonage client. Assumes numbers are E.164."""
    # Numbers should be normalized before calling this function
    if not recipient or not sender or not recipient.startswith('+') or not sender.startswith('+'):
        logging.error(f"Invalid E.164 format for SMS. Recipient: {recipient}, Sender: {sender}")
        return False

    if not vonage_client:
        logging.error("Vonage client not initialized. Cannot send SMS.")
        return False

    logging.info(f"Attempting to send SMS from {sender} to {recipient}: '{message[:100]}...'")

    if dry_run:
        logging.info("[DRY RUN] SMS Send Skipped.")
        return True

    try:
        sms_message = SmsMessage(to=recipient, from_=sender, text=message)
        response = vonage_client.sms.send(sms_message)
        first_message_response = response.messages[0] if response.messages else None

        if first_message_response and first_message_response.message_id and first_message_response.status == '0':
             logging.info(f"SMS sent successfully to {recipient}. Message UUID: {first_message_response.message_id}")
             return True
        elif first_message_response:
            logging.error(f"Failed to send SMS to {recipient}. Status: {first_message_response.status}, Error: {first_message_response.error_text}")
            return False
        else:
             logging.error(f"Failed to send SMS to {recipient}. Unexpected response structure: {response}")
             return False
    except VonageClientError as e:
        logging.error(f"Vonage ClientError sending SMS to {recipient}: {e}")
        return False
    except Exception as e:
        logging.exception(f"Unexpected error sending SMS to {recipient}: {e}")
        return False


def notify_group(sender_phone: str, list_id: str, list_alias: str, list_data: Dict[str, Any], message: str, vonage_number: str):
    """Sends a message to all members of a list except the original sender."""
    # Prefixing is now handled centrally before calling send_sms_reply
    notification_prefix = f"[{list_alias}] "
    full_message = notification_prefix + message
    members = list_data.get('members', [])
    logging.info(f"Notifying group for list '{list_alias}' ({list_id}). Members: {members}")

    for member_phone in members:
        if member_phone != sender_phone:
            # Assumes member_phone and vonage_number are already normalized E.164
            send_sms_reply(recipient=member_phone, sender=vonage_number, message=full_message)


def get_user_lists(user_phone: str) -> List[Tuple[str, str]]:
    """Fetches the list IDs and aliases the user is a member of."""
    if not db: return [] # Handle case where DB client failed to initialize
    user_doc_ref = db.collection(USERS_COLLECTION).document(user_phone)
    try:
        user_snap = user_doc_ref.get()
        list_ids = []
        if user_snap.exists:
            list_ids = user_snap.to_dict().get('member_of_lists', [])

        user_lists = []
        if list_ids:
            list_refs = [db.collection(LISTS_COLLECTION).document(lid) for lid in list_ids]
            list_snaps = db.get_all(list_refs)
            for list_snap in list_snaps:
                if list_snap.exists:
                    list_data = list_snap.to_dict()
                    alias = list_data.get('alias', f'Unnamed-{list_snap.id[:4]}') # Fallback alias
                    user_lists.append((list_snap.id, alias)) # (list_id, list_alias)
                else:
                    logging.warning(f"User {user_phone} is member of non-existent list {list_snap.reference.id}. Might need cleanup.")
                    # TODO: Implement cleanup logic if needed (remove dangling refs from user doc)

        logging.info(f"User {user_phone} is member of lists: {user_lists}")
        return user_lists
    except Exception as e:
        logging.exception(f"Error fetching user lists for {user_phone}: {e}")
        return [] # Return empty list on error


def find_list_by_alias(user_phone: str, alias_query: str, user_lists: List[Tuple[str, str]]) -> Optional[Tuple[str, str]]:
    """Finds a list ID and alias from the user's lists matching the alias query (case-insensitive)."""
    alias_query_lower = alias_query.lower()
    for list_id, list_alias in user_lists:
        if list_alias.lower() == alias_query_lower:
            return list_id, list_alias
    return None

def check_alias_uniqueness(user_phone: str, alias_to_check: str, user_lists: List[Tuple[str, str]]) -> bool:
    """Checks if the alias is already used by the user."""
    return find_list_by_alias(user_phone, alias_to_check, user_lists) is None

# --- Firestore Transaction Functions ---

@firestore.transactional
def create_list_transaction(transaction: Transaction, user_phone: str, vonage_number: str, alias: Optional[str] = None) -> Tuple[str, str]:
    """
    Creates a new list and adds the user as the first member within a transaction.
    Generates a unique alias if none is provided or if the provided one exists.
    Returns (new_list_id, final_alias).
    """
    # Note: Checking alias uniqueness perfectly within a transaction is hard without
    # reading all user lists inside. We rely on the pre-check done before calling.
    # If no alias provided, generate one. Low collision chance assumed for random.
    final_alias = alias
    if not final_alias:
        # Simple generation, assumes low collision probability.
        # A robust solution might involve more complex reservation or retry outside transaction.
        max_tries = 5
        for _ in range(max_tries):
            final_alias = generate_memorable_alias()
            # Basic check against *all* lists (less efficient but safer if needed)
            # query = db.collection(LISTS_COLLECTION).where('alias', '==', final_alias).limit(1)
            # if not query.get(transaction=transaction): break # Found unique
            # For simplicity, we'll just generate and assume low collision for now.
            break # Remove this break if implementing the check above
        else:
             raise Exception(f"Failed to generate a unique alias after {max_tries} tries.")


    # Create the new list document
    new_list_ref = db.collection(LISTS_COLLECTION).document()
    list_data = {
        'alias': final_alias,
        'members': [user_phone],
        'tasks': [],
        'created_by': user_phone,
        'created_at': firestore.SERVER_TIMESTAMP,
        'vonage_number': vonage_number
    }
    transaction.set(new_list_ref, list_data)

    # Update the user's document
    user_doc_ref = db.collection(USERS_COLLECTION).document(user_phone)
    transaction.set(user_doc_ref, {
        'member_of_lists': firestore.ArrayUnion([new_list_ref.id])
    }, merge=True)

    logging.info(f"Transaction: Created list {new_list_ref.id} with alias '{final_alias}' for user {user_phone}")
    return new_list_ref.id, final_alias

@firestore.transactional
def add_member_transaction(transaction: Transaction, inviter_phone: str, invited_phone: str, list_id: str):
    """Adds a member to a list and updates the invited user's record within a transaction."""
    list_ref = db.collection(LISTS_COLLECTION).document(list_id)
    user_ref = db.collection(USERS_COLLECTION).document(invited_phone)

    list_snap = list_ref.get(transaction=transaction)
    if not list_snap.exists:
        raise ValueError(f"List {list_id} not found.")
    list_data = list_snap.to_dict()
    if inviter_phone not in list_data.get('members', []):
         raise PermissionError(f"User {inviter_phone} is not a member of list {list_id} and cannot invite.")

    # Add member to list
    transaction.update(list_ref, {
        'members': firestore.ArrayUnion([invited_phone])
    })
    # Add list to user's record
    transaction.set(user_ref, {
        'member_of_lists': firestore.ArrayUnion([list_id])
    }, merge=True)
    logging.info(f"Transaction: Added {invited_phone} to list {list_id} by {inviter_phone}")

@firestore.transactional
def remove_member_transaction(transaction: Transaction, remover_phone: str, removed_phone: str, list_id: str):
    """Removes a member from a list and updates the removed user's record within a transaction."""
    list_ref = db.collection(LISTS_COLLECTION).document(list_id)
    user_ref = db.collection(USERS_COLLECTION).document(removed_phone)

    list_snap = list_ref.get(transaction=transaction)
    if not list_snap.exists:
        raise ValueError(f"List {list_id} not found.")
    list_data = list_snap.to_dict()
    if remover_phone not in list_data.get('members', []):
         raise PermissionError(f"User {remover_phone} is not a member of list {list_id} and cannot remove others.")

    if removed_phone not in list_data.get('members', []):
        raise ValueError(f"User {removed_phone} is not a member of list {list_id}.")

    # Remove member from list
    transaction.update(list_ref, {
        'members': firestore.ArrayRemove([removed_phone])
    })
    # Remove list from user's record
    transaction.update(user_ref, {
        'member_of_lists': firestore.ArrayRemove([list_id])
    })
    logging.info(f"Transaction: Removed {removed_phone} from list {list_id} by {remover_phone}")

# --- Help Text ---
HELP_TEXT = {
    CMD_ADD: "Usage: add [item description]\nAdds a task to the current list.",
    CMD_DONE: "Usage: done [item description]\nMarks a task as complete (case-insensitive exact match).",
    CMD_LIST: "Usage: list\nShows all open tasks in the current list.",
    CMD_CREATE: "Usage: create [optional list name]\nCreates a new list. If name is omitted, a random one is generated.",
    CMD_LISTS: "Usage: lists\nShows the names of all lists you are a member of.",
    CMD_HELP: "Usage: help [command]\nShows this help list or details for a specific command.",
    CMD_INVITE: "Usage: invite [phone number]\nAdds another user to the current list (use +1... format).",
    CMD_REMOVE: "Usage: remove [phone number]\nRemoves a user from the current list.",
    CMD_LEAVE: "Usage: leave\nRemoves yourself from the current list.",
    CMD_RENAME: "Usage: rename [new list name]\nRenames the current list (use letters, numbers, -, _).",
}
# Generate the basic help list dynamically
BASIC_HELP_LIST = "Available commands:\n" + "\n".join(sorted(HELP_TEXT.keys())) + "\n\nType 'help [command]' for details."
WELCOME_MESSAGE = "\nWelcome! Try 'add [task]' to add your first item, or 'help' for more commands."

# --- Command Handler Functions ---
# Define type for command handler functions context dict
CommandHandlerContext = Dict[str, Any]
# Define type for command handler function signature
CommandHandlerResult = Tuple[str, bool, str, Optional[str]] # reply, notify, notification_msg, new_alias
CommandHandler = Callable[[CommandHandlerContext], CommandHandlerResult]

def _handle_add(context: CommandHandlerContext) -> CommandHandlerResult:
    """Handles the 'add' command."""
    sender_id = context["sender_id"]
    argument = context["argument"]
    list_ref = context["list_ref"]

    if not argument:
        return HELP_TEXT[CMD_ADD].split('\n')[0], False, "", None # Return only Usage line
    else:
        list_ref.update({'tasks': firestore.ArrayUnion([argument])})
        reply = f"Added: {argument}"
        notification = f"{sender_id} added TODO: {argument}"
        logging.info(f"{sender_id} added task '{argument}' to list {list_ref.id}")
        return reply, True, notification, None # No alias change

def _handle_done(context: CommandHandlerContext) -> CommandHandlerResult:
    """Handles the 'done' command."""
    sender_id = context["sender_id"]
    argument = context["argument"]
    list_ref = context["list_ref"]
    list_data = context["list_data"]

    if not argument:
        return HELP_TEXT[CMD_DONE].split('\n')[0], False, "", None
    else:
        tasks = list_data.get('tasks', [])
        task_to_remove = None
        for task in tasks:
            if task.lower() == argument.lower():
                task_to_remove = task
                break

        if task_to_remove:
            list_ref.update({'tasks': firestore.ArrayRemove([task_to_remove])})
            reply = f"Done: {task_to_remove}"
            notification = f"{sender_id} marked done: {task_to_remove}"
            logging.info(f"{sender_id} removed task matching '{argument}' from list {list_ref.id}")
            return reply, True, notification, None
        else:
            reply = f"Not found: {argument}"
            logging.info(f"Task matching '{argument}' not found in list {list_ref.id}")
            return reply, False, "", None

def _handle_list(context: CommandHandlerContext) -> CommandHandlerResult:
    """Handles the 'list' command."""
    sender_id = context["sender_id"]
    list_ref = context["list_ref"]
    list_data = context["list_data"]

    tasks = list_data.get('tasks', [])
    if tasks:
        task_list_str = "\n".join([f"- {task}" for task in tasks])
        reply = f"Open TODOs:\n{task_list_str}"
    else:
        reply = "No open TODOs!"
    logging.info(f"{sender_id} listed tasks for list {list_ref.id}")
    return reply, False, "", None

def _handle_invite(context: CommandHandlerContext) -> CommandHandlerResult:
    """Handles the '/invite' command."""
    sender_id = context["sender_id"]
    argument = context["argument"]
    list_ref = context["list_ref"]
    list_data = context["list_data"]
    recipient_id = context["recipient_id"] # Vonage number
    target_list_alias = context["target_list_alias"]

    invited_phone_raw = argument
    invited_phone = normalize_phone_number(invited_phone_raw)
    if not invited_phone:
        return HELP_TEXT[CMD_INVITE].split('\n')[0], False, "", None
    if invited_phone == sender_id:
         return "You cannot invite yourself.", False, "", None
    if invited_phone in list_data.get('members', []):
        return f"{invited_phone_raw} is already in the list.", False, "", None

    try:
        add_member_transaction(db.transaction(), sender_id, invited_phone, list_ref.id)
        reply = f"Invited {invited_phone_raw} to the list."

        # --- Welcome Message Logic for Invitee ---
        invitee_lists = get_user_lists(invited_phone)
        welcome_suffix = ""
        if len(invitee_lists) == 1: # They were just added to their first list
            welcome_suffix = WELCOME_MESSAGE

        # Notify the invited user specifically
        send_sms_reply(
            recipient=invited_phone,
            sender=recipient_id,
            message=f"You've been added to the TODO list '[{target_list_alias}]' by {sender_id}.{welcome_suffix}"
        )
        # --- End Welcome Message Logic ---

        notification = f"{sender_id} invited {invited_phone_raw}."
        logging.info(f"{sender_id} invited {invited_phone} to list {list_ref.id}")
        return reply, True, notification, None
    except PermissionError as pe:
         raise CommandError(str(pe))
    except ValueError as ve: # Catch list not found from transaction
         raise CommandError(str(ve))
    except Exception as e:
         logging.exception(f"Error inviting {invited_phone} to {list_ref.id}: {e}")
         raise CommandError("Could not invite user due to an internal error.")

def _handle_remove(context: CommandHandlerContext) -> CommandHandlerResult:
    """Handles the '/remove' command."""
    sender_id = context["sender_id"]
    argument = context["argument"]
    list_ref = context["list_ref"]
    list_data = context["list_data"]
    recipient_id = context["recipient_id"] # Vonage number
    target_list_alias = context["target_list_alias"]

    removed_phone_raw = argument
    removed_phone = normalize_phone_number(removed_phone_raw)
    if not removed_phone:
        return HELP_TEXT[CMD_REMOVE].split('\n')[0], False, "", None
    if removed_phone == sender_id:
        return "Use '/leave' to remove yourself.", False, "", None
    if removed_phone not in list_data.get('members', []):
        return f"{removed_phone_raw} is not in the list.", False, "", None

    try:
        remove_member_transaction(db.transaction(), sender_id, removed_phone, list_ref.id)
        reply = f"Removed {removed_phone_raw} from the list."
        send_sms_reply(recipient=removed_phone, sender=recipient_id, message=f"You've been removed from the TODO list '[{target_list_alias}]' by {sender_id}.")
        notification = f"{sender_id} removed {removed_phone_raw}."
        logging.info(f"{sender_id} removed {removed_phone} from list {list_ref.id}")
        return reply, True, notification, None
    except (PermissionError, ValueError) as ve:
         raise CommandError(str(ve))
    except Exception as e:
         logging.exception(f"Error removing {removed_phone} from {list_ref.id}: {e}")
         raise CommandError("Could not remove user due to an internal error.")

def _handle_leave(context: CommandHandlerContext) -> CommandHandlerResult:
    """Handles the '/leave' command."""
    sender_id = context["sender_id"]
    list_ref = context["list_ref"]
    list_data = context["list_data"]
    target_list_alias = context["target_list_alias"]

    if len(list_data.get('members', [])) <= 1:
        return "You are the last member. To delete the list, use '/delete' (feature not yet implemented).", False, "", None
    try:
        remove_member_transaction(db.transaction(), sender_id, sender_id, list_ref.id)
        # Reply does NOT get prefixed automatically later, so format fully here.
        reply = f"You have left the list '[{target_list_alias}]'."
        notification = f"{sender_id} left the list."
        logging.info(f"{sender_id} left list {list_ref.id}")
        # Return True for notify_others if group notification is desired/implemented accurately
        return reply, True, notification, None # Return None for alias update
    except ValueError as ve: # Catch list not found etc.
        raise CommandError(str(ve))
    except Exception as e:
        logging.exception(f"Error leaving list {list_ref.id}: {e}")
        raise CommandError("Could not leave the list due to an internal error.")

def _handle_rename(context: CommandHandlerContext) -> CommandHandlerResult:
    """Handles the '/rename' command."""
    sender_id = context["sender_id"]
    argument = context["argument"]
    list_ref = context["list_ref"]
    user_lists = context["user_lists"] # Passed from main handler

    new_alias = argument
    target_list_id = list_ref.id

    if not new_alias:
        return HELP_TEXT[CMD_RENAME].split('\n')[0], False, "", None
    if not re.match(r"^[a-zA-Z0-9_-]+$", new_alias):
         return "Error: List name can only contain letters, numbers, hyphens, and underscores.", False, "", None

    is_unique = True
    new_alias_lower = new_alias.lower()
    for l_id, l_alias in user_lists:
        if l_id != target_list_id and l_alias.lower() == new_alias_lower:
            is_unique = False
            break

    if not is_unique:
        return f"Error: You already have a list named '[{new_alias}]'. Choose a different name.", False, "", None

    try:
        list_ref.update({'alias': new_alias})
        reply = f"List renamed to '[{new_alias}]'." # Core reply message
        notification = f"{sender_id} renamed the list to '[{new_alias}]'."
        logging.info(f"{sender_id} renamed list {target_list_id} to '{new_alias}'")
        return reply, True, notification, new_alias # Return the NEW alias
    except Exception as e:
         logging.exception(f"Error renaming list {target_list_id}: {e}")
         raise CommandError("Could not rename the list due to an internal error.")

# --- Command Dispatcher ---
COMMAND_HANDLERS: Dict[str, CommandHandler] = {
    CMD_ADD: _handle_add,
    CMD_DONE: _handle_done,
    CMD_LIST: _handle_list,
    # Global commands (create, lists, help) handled separately
    CMD_INVITE: _handle_invite,
    CMD_REMOVE: _handle_remove,
    CMD_LEAVE: _handle_leave,
    CMD_RENAME: _handle_rename,
}

# --- Core Logic Functions (Refactored) ---

def _validate_request(request: Request):
    """Validates the incoming request (method, signature). Raises RequestValidationError on failure."""
    if request.method != 'POST':
        raise RequestValidationError("Method Not Allowed", 405)

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.lower().startswith("bearer "):
        logging.error("Missing or invalid Authorization header for signature verification.")
        raise RequestValidationError("Unauthorized: Missing signature token", 401)

    token = auth_header.split(maxsplit=1)[1].strip()
    if not VONAGE_SIGNATURE_SECRET:
        logging.warning("VONAGE_SIGNATURE_SECRET not set, SKIPPING signature verification.")
    elif not verify_signature(token, VONAGE_SIGNATURE_SECRET):
        logging.error("Invalid Vonage signature received.")
        raise RequestValidationError("Unauthorized: Invalid signature", 401)
    else:
        logging.info("Vonage signature verified successfully.")


def _parse_incoming_message(request: Request) -> Tuple[str, str, str, str]:
    """Parses sender, recipient, text, and message ID from request. Raises ValueError on failure."""
    try:
        if request.is_json:
            data = request.get_json()
            sender_id_raw = data.get('from')
            recipient_id_raw = data.get('to')
            message_text = data.get('text', '').strip()
            message_id = data.get('message_uuid', 'UNKNOWN')
        elif request.form:
            data = request.form
            sender_id_raw = data.get('msisdn')
            recipient_id_raw = data.get('to')
            message_text = data.get('text', '').strip()
            message_id = data.get('messageId', 'UNKNOWN')
        else:
            data = request.get_json(force=True, silent=True)
            if data is None:
                raise ValueError("Could not parse request body as JSON or Form.")
            sender_id_raw = data.get('from')
            recipient_id_raw = data.get('to')
            message_text = data.get('text', '').strip()
            message_id = data.get('message_uuid', 'UNKNOWN')

        if not sender_id_raw or not recipient_id_raw:
            raise ValueError("Missing sender ('from'/'msisdn') or recipient ('to') in request.")

        sender_id = normalize_phone_number(sender_id_raw)
        recipient_id = normalize_phone_number(recipient_id_raw)

        if not sender_id or not recipient_id:
            # Include raw numbers in error for debugging
            raise ValueError(f"Could not normalize sender ('{sender_id_raw}') or recipient ('{recipient_id_raw}') phone number.")

        return sender_id, recipient_id, message_text, message_id

    except Exception as e:
        logging.error(f"Error parsing request data: {e}")
        logging.debug(f"Raw request body for error: {request.get_data(as_text=True)}")
        raise ValueError(f"Could not parse data: {e}")


def _parse_command(message_text: str) -> Tuple[Optional[str], str, str]:
    """Parses message text into alias, command, and argument using regex."""
    match = message_parser.match(message_text.strip())
    specified_alias = None
    command = ""
    argument = ""

    if match:
        specified_alias, command_raw, argument = match.groups()
        specified_alias = specified_alias.strip() if specified_alias else None
        command = command_raw.lower() if command_raw else ''
        argument = argument.strip() if argument else ''
        logging.info(f"Parsed: Alias='{specified_alias}', Command='{command}', Argument='{argument}'")
    else:
        command = ""
        argument = ""
        logging.warning(f"Could not parse message via regex: '{message_text}'")

    return specified_alias, command, argument


def _handle_global_commands(command: str, argument: str, sender_id: str, recipient_id: str, user_lists: List[Tuple[str, str]], is_first_list: bool) -> Optional[str]:
    """Handles commands that don't require a specific list context. Returns reply message or None."""
    reply_message = None
    if command == CMD_CREATE:
        try:
            new_alias_request = argument if argument else None
            # Pre-check uniqueness against user's current lists
            if new_alias_request and not check_alias_uniqueness(sender_id, new_alias_request, user_lists):
                reply_message = f"Error: You already have a list with alias '[{new_alias_request}]'. Choose a different name."
            else:
                new_list_id, final_alias = create_list_transaction(db.transaction(), sender_id, recipient_id, new_alias_request)
                reply_message = f"Created new list '{final_alias}'. Invite others with: {final_alias}: invite +1..."

                # --- Welcome Message Logic for Create ---
                if is_first_list:
                    reply_message += WELCOME_MESSAGE

                logging.info(f"User {sender_id} created list {new_list_id} ('{final_alias}')")
        except Exception as e:
            logging.exception(f"Error creating list for {sender_id}: {e}")
            reply_message = "Error: Could not create the list."

    elif command == CMD_LISTS:
        if user_lists:
            list_names = [f"- {alias}" for _, alias in user_lists]
            reply_message = "You are a member of:\n" + "\n".join(list_names)
        else:
            reply_message = "You are not a member of any lists. Create one with '/create [optional name]'."

    elif command == CMD_HELP:
        if argument: # User asked for help on a specific command
            detail = HELP_TEXT.get(argument.lower())
            if detail:
                reply_message = detail
            else:
                reply_message = f"Unknown command '{argument}'.\n\n{BASIC_HELP_LIST}"
        else: # Basic help
            reply_message = BASIC_HELP_LIST

    return reply_message


def _resolve_target_list(
        specified_alias: Optional[str], 
        sender_id: str, 
        user_lists: List[Tuple[str, str]], 
        command: str, 
        argument: str
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Resolves the target list based on alias or user's lists. Returns (list_id, list_alias, error_message)."""
    target_list_id = None
    target_list_alias = None
    error_message = None
    num_user_lists = len(user_lists)

    if specified_alias:
        found_list = find_list_by_alias(sender_id, specified_alias, user_lists)
        if found_list:
            target_list_id, target_list_alias = found_list
        else:
            error_message = f"Error: List '{specified_alias}' not found or you are not a member. Use 'lists' to see your lists."
    elif num_user_lists == 1:
        target_list_id, target_list_alias = user_lists[0]
        logging.info(f"User in one list, defaulting to '{target_list_alias}' ({target_list_id})")
    elif num_user_lists > 1:
        # Construct the command example carefully based on the actual command received
        # This requires passing command/argument into this function if we want perfect examples.
        # Simplified error for now:
        error_message = f"Error: You are in multiple lists. Please specify which list (e.g., 'list_alias: {command}{' ' + argument if argument else ''}'). Use 'lists' to see your lists."
    else: # num_user_lists == 0
        error_message = "Error: You are not part of any list. Use 'create [name]' to start one."

    return target_list_id, target_list_alias, error_message


def _execute_list_command(
    command: str,
    context: CommandHandlerContext # Pass the whole context dict
) -> CommandHandlerResult:
    """
    Fetches list data, validates membership, and dispatches to the appropriate command handler.
    Returns: (reply_message, notify_others, notification_message, updated_list_alias)
    Raises CommandError or other exceptions on failure.
    """
    target_list_id = context["target_list_id"]
    target_list_alias = context["target_list_alias"]
    sender_id = context["sender_id"]
    argument = context["argument"]

    list_ref = db.collection(LISTS_COLLECTION).document(target_list_id)
    list_snap = list_ref.get()

    if not list_snap.exists:
        logging.error(f"List {target_list_id} ('{target_list_alias}') not found in DB during command execution.")
        raise CommandError(f"List '{target_list_alias}' seems to be missing.")

    list_data = list_snap.to_dict()
    if sender_id not in list_data.get('members', []):
        logging.warning(f"User {sender_id} lost membership to list {target_list_id} ('{target_list_alias}') before command execution.")
        raise CommandError(f"You are no longer a member of '{target_list_alias}'.")

    # Add list_ref and list_data to the context for handlers
    context["list_ref"] = list_ref
    context["list_data"] = list_data

    # Dispatch to the appropriate handler
    handler = COMMAND_HANDLERS.get(command)
    if handler:
        # Call the handler with the prepared context
        reply_message, notify_others, notification_message, updated_alias = handler(context)
        return reply_message, notify_others, notification_message, updated_alias
    else:
        # Handle unknown commands within a list context more gracefully
        # Check if it looks like an implicit add
        if command and not argument and command not in COMMAND_HANDLERS and command not in [CMD_CREATE, CMD_LISTS, CMD_HELP]:
             # Treat "alias: task description" as implicit add? Let's require 'add' for clarity.
             reply_message = f"Unknown command '{command}'. Did you mean 'add {command}'? Use 'help' for commands."
        elif command:
             reply_message = f"Unknown command '{command}'. Use 'help'."
        else: # Should not happen if parsing is correct
             reply_message = "Invalid input. Use 'help'."
        return reply_message, False, "", None


def _send_reply_and_notifications(
    reply_message: Optional[str],
    notify_others: bool,
    notification_message: Optional[str],
    sender_id: str,
    recipient_id: str, # Vonage #
    target_list_id: Optional[str],
    target_list_alias: Optional[str], # Use the potentially updated alias
    list_data: Optional[Dict[str, Any]], # Potentially updated list data
    command: Optional[str] # Pass command to decide on prefixing for leave
    ):
    """Sends the direct reply (with prefix) and any necessary group notifications."""

    # Add prefix to reply message if needed (unless it's the leave command reply)
    final_reply_message = reply_message
    # Only add prefix if there's a message, a target list context, and it wasn't the leave command
    if reply_message and target_list_alias and command != CMD_LEAVE:
         final_reply_message = f"{target_list_alias}: {reply_message}"
    # Note: _handle_leave formats its own reply fully including the alias.

    if final_reply_message:
        # Assumes sender_id and recipient_id are normalized E.164
        send_sms_reply(recipient=sender_id, sender=recipient_id, message=final_reply_message)

    # Send group notifications if required
    if notify_others and target_list_id and target_list_alias and list_data and notification_message:
         logging.info(f"Sending group notification for list {target_list_alias} ({target_list_id})")
         notify_group(
            sender_phone=sender_id,
            list_id=target_list_id,
            list_alias=target_list_alias, # Use the potentially updated alias
            list_data=list_data, # Use potentially updated list data
            message=notification_message,
            vonage_number=recipient_id
         )


# --- Main Handler Function ---
@functions_framework.http
def sms_todo_handler(request: Request):
    """
    Google Cloud Function triggered by HTTP POST requests from Vonage. (Refactored)
    """
    reply_message: Optional[str] = None
    notify_others: bool = False
    notification_message: Optional[str] = None
    target_list_id: Optional[str] = None
    target_list_alias: Optional[str] = None # Original alias if resolved
    final_list_alias: Optional[str] = None # Potentially updated alias after rename
    list_data: Optional[Dict[str, Any]] = None # Store fetched list data
    sender_id: Optional[str] = None # Store sender_id for final error handling
    recipient_id: Optional[str] = None # Store recipient_id for final error handling
    command: str = "" # Store command for notification logic
    message_id: str = "UNKNOWN" # Store message ID for logging

    try:
        # 1. Validate Request
        _validate_request(request)

        # 2. Check Core Dependencies
        if not db:
            logging.error("FATAL: Firestore client not available.")
            return "Internal Server Error: DB not configured", 500
        if not vonage_client:
             # Log error but try to continue if possible (maybe only listing tasks)
             logging.error("Vonage client not available. SMS replies/notifications will fail.")


        # 3. Parse Incoming Message Data
        sender_id, recipient_id, message_text, message_id = _parse_incoming_message(request)
        logging.info(f"Processing message_id: {message_id} from {sender_id}")

        # 4. Parse Command
        specified_alias, command, argument = _parse_command(message_text)

        # Handle empty message explicitly
        if not command and not argument and not specified_alias:
            logging.info(f"Empty message from {sender_id} (msg_id: {message_id}), no action.")
            return "Webhook processed (empty message)", 200

        # 5. Get User's List Membership
        user_lists = get_user_lists(sender_id)
        is_first_list_scenario = (len(user_lists) == 0)

        # 6. Handle Global Commands
        reply_message = _handle_global_commands(command, argument, sender_id, recipient_id, user_lists, is_first_list_scenario)

        # 7. If not handled globally, resolve and execute list command
        if reply_message is None:
            # 7a. Resolve Target List
            target_list_id, target_list_alias, error_message = _resolve_target_list(specified_alias, sender_id, user_lists, command, argument)

            if error_message:
                reply_message = error_message # Set the error message as the reply
            elif target_list_id and target_list_alias:
                # 7b. Execute List-Specific Command
                try:
                    # Prepare context for the command execution function
                    execution_context = {
                        "command": command,
                        "argument": argument,
                        "sender_id": sender_id,
                        "recipient_id": recipient_id,
                        "target_list_id": target_list_id,
                        "target_list_alias": target_list_alias,
                        "user_lists": user_lists,
                        # list_ref and list_data added inside _execute_list_command
                    }
                    reply_msg_cmd, notify_cmd, notif_msg_cmd, updated_alias = _execute_list_command(
                        command, execution_context
                    )
                    reply_message = reply_msg_cmd
                    notify_others = notify_cmd
                    notification_message = notif_msg_cmd
                    final_list_alias = updated_alias if updated_alias else target_list_alias # Use new alias if rename occurred

                    # 7c. Re-fetch list data if needed for accurate notification
                    # Only fetch if notification is needed AND it was a member-modifying command
                    if notify_others and command in MEMBER_MODIFYING_COMMANDS:
                        logging.info(f"Re-fetching list data for notification after member change (command: {command})")
                        list_snap = db.collection(LISTS_COLLECTION).document(target_list_id).get()
                        if list_snap.exists:
                            list_data = list_snap.to_dict() # Use updated data
                        else:
                           logging.warning(f"List {target_list_id} not found when re-fetching for notification.")
                           notify_others = False # Cancel notification if list is gone
                    elif notify_others:
                        # For non-member changes, use the list_data potentially already fetched by the handler
                        # The handler context now includes list_data, retrieve it if needed
                        list_data = execution_context.get("list_data") # Get data used by handler


                except CommandError as ce:
                    # Handle user-facing errors from command execution
                    logging.warning(f"Command Error for {sender_id} (msg_id: {message_id}, cmd: {command}): {ce}")
                    reply_message = str(ce) # Set reply to the error message
                    notify_others = False # Don't notify on command error
                    # Alias context for error reply will be added by _send_reply_and_notifications

            else:
                 # This case should ideally not be reached if _resolve_target_list is correct
                 logging.error(f"List resolution failed without error message for user {sender_id} (msg_id: {message_id}), command '{command}'")
                 reply_message = "Error: Could not determine the target list."

        # 8. Send Reply and Notifications
        _send_reply_and_notifications(
            reply_message,
            notify_others,
            notification_message,
            sender_id,
            recipient_id,
            target_list_id,
            final_list_alias if final_list_alias else target_list_alias, # Use updated alias
            list_data, # Pass potentially updated list data
            command # Pass command for prefix logic
        )

        # 9. Acknowledge Webhook to Vonage
        logging.info(f"Successfully processed message_id: {message_id}")
        return "Webhook processed", 200

    # --- Exception Handling ---
    except RequestValidationError as rve:
        logging.error(f"Request Validation Error: {rve} (Status: {rve.status_code})")
        return str(rve), rve.status_code
    except ValueError as ve:
        # Catches errors from _parse_incoming_message primarily
        logging.error(f"Data Parsing/Value Error: {ve}")
        return f"Bad Request: {ve}", 200 # Vonage expects 200 or it will retry
    except Exception as e:
        # Catch-all for unexpected internal errors
        logging.exception(f"Unhandled exception in sms_todo_handler (msg_id: {message_id}): {e}")
        # Send a generic error reply if possible
        if sender_id and recipient_id: # Check if basic parsing succeeded
            try:
                # Use basic send_sms_reply directly for generic errors
                send_sms_reply(recipient=sender_id, sender=recipient_id, message="Sorry, an unexpected internal error occurred.")
            except Exception as notify_err:
                logging.error(f"Failed to send error notification: {notify_err}")
        return "Internal Server Error", 200 # Vonage expects 200 or it will retry