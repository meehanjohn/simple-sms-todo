# tests/test_main.py

import pytest
from unittest.mock import MagicMock, patch, ANY # ANY helps match arguments flexibly
import random # Import random to allow patching its methods

# Import the module we are testing
from src import main

# --- Fixtures ---

@pytest.fixture
def mock_request(mocker):
    """Fixture for creating a mock Flask request object."""
    mock = MagicMock(spec=main.Request)
    mock.headers = {}
    mock.method = 'POST'
    mock.is_json = False
    mock.form = {}
    mock.get_json.return_value = {}
    mock.get_data.return_value = b'' # Default empty body
    return mock

@pytest.fixture
def mock_db_client(mocker):
    """Fixture for a mock Firestore client."""
    mock_client = MagicMock(spec=main.firestore.Client)
    # Mock the transaction decorator/context manager if needed directly
    # For testing functions *using* transactions, we mock the calls inside them.
    mock_client.transaction.return_value = MagicMock() # Basic mock for transaction context

    # Mock collection().document().get() chain
    mock_doc_ref = MagicMock(spec=main.DocumentReference)
    mock_doc_snap = MagicMock(spec=main.firestore.DocumentSnapshot)
    mock_doc_snap.exists = True
    mock_doc_snap.to_dict.return_value = {}
    mock_doc_snap.id = "mock_doc_id"
    mock_doc_ref.get.return_value = mock_doc_snap
    mock_doc_ref.id = "mock_doc_id" # Set ID on the ref too

    mock_collection_ref = MagicMock()
    mock_collection_ref.document.return_value = mock_doc_ref

    mock_client.collection.return_value = mock_collection_ref

    # Mock get_all for get_user_lists
    mock_client.get_all.return_value = []

    # Patch the global 'db' variable in the main module
    mocker.patch('src.main.db', mock_client)
    return mock_client

@pytest.fixture
def mock_vonage_client_obj(mocker):
    """Fixture providing just the mock Vonage client object *without* patching."""
    mock_client = MagicMock(spec=main.Vonage)
    mock_sms = MagicMock()
    mock_send_response = MagicMock()
    mock_message_status = MagicMock()
    mock_message_status.message_id = "mock-vonage-uuid"
    mock_message_status.status = '0' # Success
    mock_message_status.error_text = None
    mock_send_response.messages = [mock_message_status]
    mock_sms.send.return_value = mock_send_response
    mock_client.sms = mock_sms
    return mock_client

@pytest.fixture(autouse=True)
def mock_dependencies(mocker):
    """Auto-used fixture to mock external libs and globals for all tests."""
    # Mock phonenumbers if installed, otherwise assume it's None
    if main.phonenumbers:
        mocker.patch('src.main.phonenumbers.parse', return_value=MagicMock())
        mocker.patch('src.main.phonenumbers.is_valid_number', return_value=True)
        mocker.patch('src.main.phonenumbers.format_number', return_value='+15551234567') # Example normalized
    else:
        # If not installed, ensure tests don't rely on its specific behavior
        pass

    # Mock random for alias generation
    mocker.patch('random.choice', side_effect=['mock-adj', 'mock-noun'])
    mocker.patch('random.randint', return_value=1234)

    # Mock word lists (can be done here or per-test if needed)
    mocker.patch('src.main.ADJECTIVES', ['mock-adj'])
    mocker.patch('src.main.NOUNS', ['mock-noun'])

    # Mock signature verification to pass by default
    mocker.patch('src.main.verify_signature', return_value=True)

    # Mock transaction functions (we test them separately)
    mocker.patch('src.main.create_list_transaction', return_value=("new_list_id", "new-list-alias"))
    mocker.patch('src.main.add_member_transaction')
    mocker.patch('src.main.remove_member_transaction')

    # Mock send_sms_reply (we test it separately)
    mocker.patch('src.main.send_sms_reply', return_value=True)
    # Mock notify_group (we test it separately)
    mocker.patch('src.main.notify_group')

    # Mock get_user_lists (we test it separately)
    mocker.patch('src.main.get_user_lists', return_value=[]) # Default: user in no lists


# --- Test Helper Functions ---

def test_generate_memorable_alias(mocker):
    # Mocks applied by mock_dependencies fixture
    alias = main.generate_memorable_alias()
    assert alias == "mock-adj-mock-noun-1234"

# Test normalize_phone_number (assuming phonenumbers is installed)
@pytest.mark.skipif(main.phonenumbers is None, reason="phonenumbers library not installed")
@pytest.mark.parametrize("raw_phone, expected_normalized", [
    ("555-123-4567", "+15551234567"),
    ("+44 7911 123456", "+447911123456"), # Example UK
    ("invalid number", None),
])
def test_normalize_phone_number_lib(mocker, raw_phone, expected_normalized):
    # Reset mocks specifically for this test if needed, or rely on fixture defaults
    mock_parse = mocker.patch('src.main.phonenumbers.parse')
    mock_is_valid = mocker.patch('src.main.phonenumbers.is_valid_number')
    mock_format = mocker.patch('src.main.phonenumbers.format_number')

    if expected_normalized:
        mock_parsed_obj = MagicMock()
        mock_parse.return_value = mock_parsed_obj
        mock_is_valid.return_value = True
        mock_format.return_value = expected_normalized
    else:
        # Simulate invalid number or parse error
        if raw_phone == "invalid number":
             mock_is_valid.return_value = False
             mock_parse.return_value = MagicMock() # Need to return something parseable
        else: # Simulate parse error
             mock_parse.side_effect = main.NumberParseException("Mock parse error")

    result = main.normalize_phone_number(raw_phone)
    assert result == expected_normalized
    mock_parse.assert_called_once() # Check parse was called

# Test normalize_phone_number fallback (if phonenumbers is NOT installed)
@pytest.mark.skipif(main.phonenumbers is not None, reason="phonenumbers library IS installed")
@pytest.mark.parametrize("raw_phone, expected_normalized", [
    ("5551234567", "+15551234567"),
    ("15551234567", "+15551234567"),
    ("+15551234567", "+15551234567"),
    ("555-123-4567", "+15551234567"), # Basic cleanup
    ("invalid", None),
    ("", None),
    (None, None),
])
def test_normalize_phone_number_fallback(raw_phone, expected_normalized):
     result = main.normalize_phone_number(raw_phone)
     assert result == expected_normalized

def test_send_sms_reply_success(mock_vonage_client_obj, mocker):
    # Explicitly patch the global vonage_client within this test's scope
    mocker.patch('src.main.vonage_client', mock_vonage_client_obj)

    # Optional Debug: Verify the patch worked before calling the function
    # print(f"\n[DEBUG] vonage_client in test: {id(main.vonage_client)}")
    assert main.vonage_client is mock_vonage_client_obj

    # Mock normalization to ensure it returns valid numbers for this test
    sender = "+15551112222"
    recipient = "+15553334444"
    message = "Test message"
    result = main.send_sms_reply(recipient, sender, message)

    assert result is True
    # Assert on the explicitly passed mock object
    mock_vonage_client_obj.sms.send.assert_called_once()
    call_args = mock_vonage_client_obj.sms.send.call_args[0][0] # Get the SmsMessage object
    assert call_args.to == recipient
    assert call_args.from_ == sender
    assert call_args.text == message

def test_send_sms_reply_failure_api(mock_vonage_client_obj, mocker):
    # Patch the global vonage_client
    mocker.patch('src.main.vonage_client', mock_vonage_client_obj)
    assert main.vonage_client is mock_vonage_client_obj # Verify patch
    # Configure mock Vonage client to simulate API failure
    mock_vonage_client_obj.sms.send.side_effect = main.VonageClientError("API Error")
    result = main.send_sms_reply("+15553334444", "+15551112222", "Test")
    assert result is False
    mock_vonage_client_obj.sms.send.assert_called_once()

def test_send_sms_reply_failure_invalid_number(mock_vonage_client_obj, mocker):
    result = main.send_sms_reply("invalid", "+15551112222", "Test")
    assert result is False
    mock_vonage_client_obj.sms.send.assert_not_called()

def test_notify_group(mocker):
    mock_send = mocker.patch('src.main.send_sms_reply')
    sender = "+15550001111"
    vonage_num = "+15559998888"
    list_alias = "test-list"
    list_data = {
        "members": [sender, "+15552223333", "+15554445555"]
    }
    message = "Group update"

    main.notify_group(sender, "list_id", list_alias, list_data, message, vonage_num)

    assert mock_send.call_count == 2 # Called for the other two members
    expected_full_message = f"[{list_alias}] {message}"
    # Check calls (order doesn't strictly matter here)
    mock_send.assert_any_call(recipient="+15552223333", sender=vonage_num, message=expected_full_message)
    mock_send.assert_any_call(recipient="+15554445555", sender=vonage_num, message=expected_full_message)

def test_get_user_lists_success(mock_db_client):
    user_phone = "+15551112222"
    list_ids = ["list1", "list2"]
    # Mock user doc
    mock_user_snap = MagicMock()
    mock_user_snap.exists = True
    mock_user_snap.to_dict.return_value = {"member_of_lists": list_ids}
    mock_db_client.collection.return_value.document.return_value.get.return_value = mock_user_snap

    # Mock list docs returned by get_all
    mock_list1_snap = MagicMock()
    mock_list1_snap.exists = True
    mock_list1_snap.id = "list1"
    mock_list1_snap.to_dict.return_value = {"alias": "Alias One"}
    mock_list1_snap.reference.id = "list1" # For logging message

    mock_list2_snap = MagicMock()
    mock_list2_snap.exists = True
    mock_list2_snap.id = "list2"
    mock_list2_snap.to_dict.return_value = {"alias": "Alias Two"}
    mock_list2_snap.reference.id = "list2"

    mock_db_client.get_all.return_value = [mock_list1_snap, mock_list2_snap]

    result = main.get_user_lists(user_phone)

    assert result == [("list1", "Alias One"), ("list2", "Alias Two")]
    mock_db_client.collection.assert_called_with(main.USERS_COLLECTION)
    mock_db_client.collection.return_value.document.assert_called_with(user_phone)
    mock_db_client.get_all.assert_called_once()
    # Check that the refs passed to get_all match the list_ids
    assert len(mock_db_client.get_all.call_args[0][0]) == 2


def test_get_user_lists_no_user_doc(mock_db_client):
    user_phone = "+15551112222"
    mock_user_snap = MagicMock()
    mock_user_snap.exists = False
    mock_db_client.collection.return_value.document.return_value.get.return_value = mock_user_snap

    result = main.get_user_lists(user_phone)
    assert result == []
    mock_db_client.get_all.assert_not_called()

def test_get_user_lists_db_error(mock_db_client):
    user_phone = "+15551112222"
    mock_db_client.collection.return_value.document.return_value.get.side_effect = Exception("Firestore unavailable")
    result = main.get_user_lists(user_phone)
    assert result == [] # Should return empty on error

# --- Test Pure Logic Functions ---

@pytest.mark.parametrize("alias_query, user_lists, expected", [
    ("list1", [("id1", "List1"), ("id2", "List2")], ("id1", "List1")),
    ("LIST1", [("id1", "List1"), ("id2", "List2")], ("id1", "List1")), # Case-insensitive
    ("list3", [("id1", "List1"), ("id2", "List2")], None),
    ("list1", [], None),
])
def test_find_list_by_alias(alias_query, user_lists, expected):
    assert main.find_list_by_alias("any_user", alias_query, user_lists) == expected

@pytest.mark.parametrize("alias_to_check, user_lists, expected", [
    ("NewList", [("id1", "List1"), ("id2", "List2")], True),
    ("List1", [("id1", "List1"), ("id2", "List2")], False),
    ("list1", [("id1", "List1"), ("id2", "List2")], False), # Case-insensitive
    ("AnyName", [], True),
])
def test_check_alias_uniqueness(alias_to_check, user_lists, expected):
     assert main.check_alias_uniqueness("any_user", alias_to_check, user_lists) == expected


# --- Test Core Logic / Orchestration Functions ---

def test_validate_request_post_valid_sig(mock_request, mocker):
    mock_request.method = 'POST'
    mock_request.headers = {"Authorization": "Bearer valid_token"}
    mocker.patch('src.main.verify_signature', return_value=True)
    try:
        main._validate_request(mock_request)
    except main.RequestValidationError:
        pytest.fail("Validation should have passed")

def test_validate_request_get_method(mock_request):
    mock_request.method = 'GET'
    with pytest.raises(main.RequestValidationError) as excinfo:
        main._validate_request(mock_request)
    assert excinfo.value.status_code == 405

def test_validate_request_missing_auth(mock_request):
    mock_request.method = 'POST'
    mock_request.headers = {}
    with pytest.raises(main.RequestValidationError) as excinfo:
        main._validate_request(mock_request)
    assert excinfo.value.status_code == 401
    assert "Missing signature token" in str(excinfo.value)

def test_validate_request_invalid_sig(mock_request, mocker):
    mock_request.method = 'POST'
    mock_request.headers = {"Authorization": "Bearer invalid_token"}
    mocker.patch('src.main.verify_signature', return_value=False)
    # Assume VONAGE_SIGNATURE_SECRET is set for this test
    mocker.patch('src.main.VONAGE_SIGNATURE_SECRET', 'a-secret')
    with pytest.raises(main.RequestValidationError) as excinfo:
        main._validate_request(mock_request)
    assert excinfo.value.status_code == 401
    assert "Invalid signature" in str(excinfo.value)

@pytest.mark.parametrize("is_json, form_data, json_data, expected_text", [
    (True, None, {"from": "15551112222", "to": "15559998888", "text": " JSON text "}, "JSON text"),
    (False, {"msisdn": "15551112222", "to": "15559998888", "text": " Form text "}, None, "Form text"),
])
def test_parse_incoming_message_success(mock_request, mocker, is_json, form_data, json_data, expected_text):
    mocker.patch('src.main.normalize_phone_number', side_effect=lambda x, **kw: f"+{x}") # Simple mock normalization
    mock_request.is_json = is_json
    if form_data:
        mock_request.form = form_data
    if json_data:
        mock_request.get_json.return_value = json_data

    sender, recipient, text, msg_id = main._parse_incoming_message(mock_request)
    assert sender == "+15551112222"
    assert recipient == "+15559998888"
    assert text == expected_text
    assert msg_id != "UNKNOWN" # Check it got some ID

def test_parse_incoming_message_failure_missing_data(mock_request, mocker):
     mocker.patch('src.main.normalize_phone_number', side_effect=lambda x, **kw: f"+{x}")
     mock_request.is_json = True
     mock_request.get_json.return_value = {"from": "15551112222"} # Missing 'to'
     with pytest.raises(ValueError, match="Missing sender .* or recipient"):
         main._parse_incoming_message(mock_request)

def test_parse_incoming_message_failure_normalization(mock_request, mocker):
     mocker.patch('src.main.normalize_phone_number', return_value=None) # Simulate normalization failure
     mock_request.is_json = True
     mock_request.get_json.return_value = {"from": "invalid", "to": "15559998888", "text": "T"}
     with pytest.raises(ValueError, match="Could not normalize sender"):
         main._parse_incoming_message(mock_request)

@pytest.mark.parametrize("message_text, expected_alias, expected_cmd, expected_arg", [
    ("add task one", None, "add", "task one"),
    ("[list1] done Task Two ", "list1", "done", "Task Two"),
    ("/invite +15551234567", None, "/invite", "+15551234567"),
    ("[list 2] /leave", "list 2", "/leave", ""),
    ("list", None, "list", ""),
    ("[Some List] list", "Some List", "list", ""),
    ("", None, "", ""), # Empty message
    ("[onlyalias]", "onlyalias", "", ""), # Only alias
    (" /cmdonly ", None, "/cmdonly", ""), # Command only with spaces
])
def test_parse_command(message_text, expected_alias, expected_cmd, expected_arg):
    alias, cmd, arg = main._parse_command(message_text)
    assert alias == expected_alias
    assert cmd == expected_cmd
    assert arg == expected_arg

# --- Test Command Handlers (Example: _handle_add) ---

def test_handle_add_success(mocker):
    mock_list_ref = MagicMock(spec=main.DocumentReference)
    mock_list_ref.id = "list_abc"
    context = {
        "sender_id": "+1555sender",
        "argument": "New Task Item",
        "list_ref": mock_list_ref,
        "list_data": {"members": [], "tasks": []}, # Provide necessary list_data
        # Add other required context keys if needed by the handler
    }
    # Mock firestore ArrayUnion
    mock_array_union = mocker.patch('src.main.firestore.ArrayUnion')

    reply, notify, notification, new_alias = main._handle_add(context)

    assert reply == "Added: New Task Item"
    assert notify is True
    assert notification == "+1555sender added TODO: New Task Item"
    assert new_alias is None
    mock_array_union.assert_called_once_with(["New Task Item"])
    mock_list_ref.update.assert_called_once_with({'tasks': mock_array_union.return_value})

def test_handle_add_no_argument():
    context = {
        "sender_id": "+1555sender",
        "argument": "", # Empty argument
        "list_ref": MagicMock(),
        "list_data": {},
    }
    reply, notify, notification, new_alias = main._handle_add(context)
    assert "Usage: add" in reply
    assert notify is False

# --- Test Transaction Functions (Example: create_list_transaction) ---

def test_create_list_transaction_success(mocker):
    mock_transaction = MagicMock(spec=main.Transaction)
    mock_db = MagicMock() # Mock the db object used inside the function
    mock_new_list_ref = MagicMock(spec=main.DocumentReference)
    mock_new_list_ref.id = "new_firestore_id"
    mock_user_ref = MagicMock(spec=main.DocumentReference)
    mock_db.collection.side_effect = [
        MagicMock(document=MagicMock(return_value=mock_new_list_ref)), # For LISTS_COLLECTION
        MagicMock(document=MagicMock(return_value=mock_user_ref))      # For USERS_COLLECTION
    ]
    mocker.patch('src.main.db', mock_db) # Patch db used inside transaction func

    # Mock generate_memorable_alias called inside
    mocker.patch('src.main.generate_memorable_alias', return_value="random-alias-1234")
    # Mock firestore constants used inside
    mock_array_union = mocker.patch('src.main.firestore.ArrayUnion')
    mock_server_ts = mocker.patch('src.main.firestore.SERVER_TIMESTAMP')

    user = "+1user"
    vonage = "+1vonage"
    requested_alias = "my-cool-list"

    # We call the function directly, assuming @firestore.transactional handles the execution
    # In a real test, you might need to mock the decorator or the transaction manager
    list_id, final_alias = main.create_list_transaction(mock_transaction, user, vonage, requested_alias)

    assert list_id == "new_firestore_id"
    assert final_alias == requested_alias # Used provided alias

    # Check calls made *on the transaction object*
    mock_transaction.set.assert_any_call(mock_new_list_ref, {
        'alias': requested_alias,
        'members': [user],
        'tasks': [],
        'created_by': user,
        'created_at': mock_server_ts,
        'vonage_number': vonage
    })
    mock_transaction.set.assert_any_call(mock_user_ref, {
        'member_of_lists': mock_array_union.return_value
    }, merge=True)
    mock_array_union.assert_called_once_with([list_id])

def test_create_list_transaction_generates_alias(mocker):
    mock_transaction = MagicMock(spec=main.Transaction)
    mock_db = MagicMock()
    mock_new_list_ref = MagicMock(id="new_id")
    mock_user_ref = MagicMock()
    mock_db.collection.side_effect = [MagicMock(document=MagicMock(return_value=mock_new_list_ref)), MagicMock(document=MagicMock(return_value=mock_user_ref))]
    mocker.patch('src.main.db', mock_db)
    mocker.patch('src.main.generate_memorable_alias', return_value="generated-alias-5678")
    mocker.patch('src.main.firestore.ArrayUnion')
    mocker.patch('src.main.firestore.SERVER_TIMESTAMP')

    # Call without providing an alias
    list_id, final_alias = main.create_list_transaction(mock_transaction, "+1user", "+1vonage", None)

    assert final_alias == "generated-alias-5678"
    # Check alias in the data set on the transaction
    set_call_args = mock_transaction.set.call_args_list[0][0] # Assuming first call is to list ref
    assert set_call_args[1]['alias'] == "generated-alias-5678"


# --- Test Main Handler (Basic Orchestration and Error Handling) ---

def test_sms_todo_handler_empty_message(mock_request, mock_dependencies):
    # mock_dependencies auto-mocks get_user_lists etc.
    mocker.patch('src.main._validate_request') # Assume validation passes
    mocker.patch('src.main._parse_incoming_message', return_value=("+1sender", "+1recipient", "", "msg1"))
    mocker.patch('src.main._parse_command', return_value=(None, "", "")) # Parsed as empty

    response, status_code = main.sms_todo_handler(mock_request)

    assert status_code == 200
    assert "empty message" in response

def test_sms_todo_handler_global_command(mock_request, mock_dependencies):
    mock_validate = mocker.patch('src.main._validate_request')
    mock_parse_msg = mocker.patch('src.main._parse_incoming_message', return_value=("+1sender", "+1recipient", "/help", "msg2"))
    mock_parse_cmd = mocker.patch('src.main._parse_command', return_value=(None, "/help", ""))
    mock_handle_global = mocker.patch('src.main._handle_global_commands', return_value="Help text here")
    mock_send_reply = mocker.patch('src.main._send_reply_and_notifications')

    response, status_code = main.sms_todo_handler(mock_request)

    assert status_code == 200
    mock_handle_global.assert_called_once()
    mock_send_reply.assert_called_once_with("Help text here", False, None, "+1sender", "+1recipient", None, None, None, "/help")

def test_sms_todo_handler_list_command_success(mock_request, mock_dependencies):
    mock_validate = mocker.patch('src.main._validate_request')
    mock_parse_msg = mocker.patch('src.main._parse_incoming_message', return_value=("+1sender", "+1recipient", "add item", "msg3"))
    mock_parse_cmd = mocker.patch('src.main._parse_command', return_value=(None, "add", "item"))
    mock_handle_global = mocker.patch('src.main._handle_global_commands', return_value=None) # Not a global cmd
    mock_get_user_lists = mocker.patch('src.main.get_user_lists', return_value=[("list1", "the_alias")]) # User in one list
    mock_resolve_list = mocker.patch('src.main._resolve_target_list', return_value=("list1", "the_alias", None))
    mock_execute_cmd = mocker.patch('src.main._execute_list_command', return_value=("Added: item", True, "Notification text", "the_alias"))
    mock_send_reply = mocker.patch('src.main._send_reply_and_notifications')
    # Mock DB get for re-fetching list data for notification
    mock_db_client = mock_dependencies # Get the mock db client via fixture if needed, or patch directly
    mock_list_snap = MagicMock()
    mock_list_snap.exists = True
    mock_list_snap.to_dict.return_value = {"members": ["+1sender", "+1other"], "tasks": ["item"]}
    mocker.patch('src.main.db.collection.return_value.document.return_value.get', return_value=mock_list_snap)


    response, status_code = main.sms_todo_handler(mock_request)

    assert status_code == 200
    mock_resolve_list.assert_called_once()
    mock_execute_cmd.assert_called_once()
    mock_send_reply.assert_called_once_with(
        "Added: item", True, "Notification text", "+1sender", "+1recipient",
        "list1", "the_alias", {"members": ["+1sender", "+1other"], "tasks": ["item"]}, "add"
    )


def test_sms_todo_handler_resolve_list_error(mock_request, mock_dependencies):
    mocker.patch('src.main._validate_request')
    mocker.patch('src.main._parse_incoming_message', return_value=("+1sender", "+1recipient", "[bad] add item", "msg4"))
    mocker.patch('src.main._parse_command', return_value=("bad", "add", "item"))
    mocker.patch('src.main._handle_global_commands', return_value=None)
    mocker.patch('src.main.get_user_lists', return_value=[]) # User in no lists, or bad alias provided
    mock_resolve_list = mocker.patch('src.main._resolve_target_list', return_value=(None, None, "Error: List not found"))
    mock_execute_cmd = mocker.patch('src.main._execute_list_command')
    mock_send_reply = mocker.patch('src.main._send_reply_and_notifications')

    response, status_code = main.sms_todo_handler(mock_request)

    assert status_code == 200 # Still 200 to Vonage
    mock_resolve_list.assert_called_once()
    mock_execute_cmd.assert_not_called() # Command execution skipped
    mock_send_reply.assert_called_once_with("Error: List not found", False, None, "+1sender", "+1recipient", None, None, None, "add")


def test_sms_todo_handler_command_error(mock_request, mock_dependencies):
    mocker.patch('src.main._validate_request')
    mocker.patch('src.main._parse_incoming_message', return_value=("+1sender", "+1recipient", "/invite invalid", "msg5"))
    mocker.patch('src.main._parse_command', return_value=(None, "/invite", "invalid"))
    mocker.patch('src.main._handle_global_commands', return_value=None)
    mocker.patch('src.main.get_user_lists', return_value=[("list1", "the_alias")])
    mocker.patch('src.main._resolve_target_list', return_value=("list1", "the_alias", None))
    # Simulate the command execution raising a CommandError
    mocker.patch('src.main._execute_list_command', side_effect=main.CommandError("Invalid phone number for invite."))
    mock_send_reply = mocker.patch('src.main._send_reply_and_notifications')

    response, status_code = main.sms_todo_handler(mock_request)

    assert status_code == 200 # Still 200 to Vonage for CommandError
    # Check that the error message was sent back
    mock_send_reply.assert_called_once_with(
        "Invalid phone number for invite.", False, None, "+1sender", "+1recipient",
        "list1", "the_alias", None, "/invite" # list_data might be None here
    )

def test_sms_todo_handler_validation_error(mock_request, mock_dependencies):
    # Simulate _validate_request raising an error
    mocker.patch('src.main._validate_request', side_effect=main.RequestValidationError("Bad Sig", 401))
    mock_parse_msg = mocker.patch('src.main._parse_incoming_message') # Should not be called

    response, status_code = main.sms_todo_handler(mock_request)

    assert status_code == 401
    assert response == "Bad Sig"
    mock_parse_msg.assert_not_called()

def test_sms_todo_handler_unexpected_error(mock_request, mock_dependencies):
    mocker.patch('src.main._validate_request')
    # Simulate parsing raising an unexpected error
    mocker.patch('src.main._parse_incoming_message', side_effect=TypeError("Something unexpected"))
    mock_send_reply = mocker.patch('src.main.send_sms_reply') # Mock basic send for error message

    response, status_code = main.sms_todo_handler(mock_request)

    assert status_code == 500
    assert response == "Internal Server Error"
    # Check if the generic error reply was attempted (best effort)
    mock_send_reply.assert_called_once_with(
        recipient=ANY, # Sender might not be known if error was early
        sender=ANY,    # Recipient might not be known
        message="Sorry, an unexpected internal error occurred."
        )