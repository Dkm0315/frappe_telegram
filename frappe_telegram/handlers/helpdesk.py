import json
import re

import frappe

from frappe_telegram.handlers.telegram_api import send_message_api, answer_callback_query


def process_update(update_data, token, settings):
	"""Process a single Telegram update through the helpdesk state machine."""
	frappe.set_user("Administrator")

	# Extract message or callback_query
	message = update_data.get("message")
	callback_query = update_data.get("callback_query")

	if callback_query:
		callback_data = callback_query.get("data", "")
		user_info = callback_query.get("from", {})
		chat_info = callback_query.get("message", {}).get("chat", {})
		text = ""
		# Acknowledge the callback
		answer_callback_query(callback_query["id"], token)
	elif message:
		callback_data = ""
		user_info = message.get("from", {})
		chat_info = message.get("chat", {})
		text = message.get("text", "")
	else:
		return

	if not user_info.get("id") or not chat_info.get("id"):
		return

	chat_id = chat_info["id"]

	# Get or create Telegram User + Chat
	telegram_user = get_or_create_telegram_user(user_info)
	telegram_chat = get_or_create_telegram_chat(chat_info, telegram_user)

	# Load or create conversation state
	state = get_or_create_conversation_state(telegram_user.name, telegram_chat.name)

	# Route based on command / callback / current state
	if text == "/start":
		reset_conversation(state)
		send_welcome_menu(chat_id, token, settings)

	elif text == "/newticket" or callback_data == "create_ticket":
		handle_new_ticket(telegram_user, telegram_chat, chat_id, token, settings, state)

	elif callback_data == "my_tickets":
		handle_my_tickets(telegram_user, chat_id, token)

	elif text == "/cancel":
		reset_conversation(state)
		send_message_api(chat_id, token, "Ticket creation cancelled. Send /start to see options.")

	elif state.state == "awaiting_email":
		handle_email_input(text or callback_data, telegram_user, chat_id, token, settings, state)

	elif state.state == "collecting_fields":
		handle_field_input(text or callback_data, telegram_user, telegram_chat, chat_id, token, settings, state)

	else:
		# Not in a conversation — check for follow-up to open ticket
		handle_followup_or_prompt(text, telegram_user, telegram_chat, chat_id, token)


# --- User / Chat management ---

def get_or_create_telegram_user(user_info):
	"""Get or create a Telegram User record from Telegram API user data."""
	user_id = str(user_info["id"])
	existing = frappe.db.get_value("Telegram User", {"telegram_user_id": user_id})
	if existing:
		return frappe.get_doc("Telegram User", existing)

	full_name = user_info.get("first_name", "")
	if user_info.get("last_name"):
		full_name += " " + user_info["last_name"]

	doc = frappe.get_doc({
		"doctype": "Telegram User",
		"telegram_user_id": user_id,
		"telegram_username": user_info.get("username", ""),
		"full_name": full_name.strip() or "Unknown",
		"is_guest": 1,
	})
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc


def get_or_create_telegram_chat(chat_info, telegram_user=None):
	"""Get or create a Telegram Chat record."""
	chat_id = str(chat_info["id"])
	existing = frappe.db.get_value("Telegram Chat", {"chat_id": chat_id})
	if existing:
		return frappe.get_doc("Telegram Chat", existing)

	title = (
		chat_info.get("title")
		or chat_info.get("username")
		or chat_info.get("first_name")
		or str(chat_id)
	)
	doc = frappe.get_doc({
		"doctype": "Telegram Chat",
		"chat_id": chat_id,
		"title": title,
		"type": chat_info.get("type", "private"),
	})
	if telegram_user:
		doc.append("users", {"telegram_user": telegram_user.name})
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc


# --- Conversation state management ---

def get_or_create_conversation_state(telegram_user_name, telegram_chat_name):
	"""Get or create a conversation state for a Telegram user."""
	existing = frappe.db.get_value(
		"Telegram Conversation State",
		{"telegram_user": telegram_user_name},
	)
	if existing:
		return frappe.get_doc("Telegram Conversation State", existing)

	doc = frappe.get_doc({
		"doctype": "Telegram Conversation State",
		"telegram_user": telegram_user_name,
		"telegram_chat": telegram_chat_name,
		"state": "idle",
		"collected_data": "{}",
		"current_field_index": 0,
	})
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return doc


def reset_conversation(state):
	"""Reset conversation state to idle."""
	state.state = "idle"
	state.collected_data = "{}"
	state.current_field_index = 0
	state.save(ignore_permissions=True)


# --- Welcome menu ---

def send_welcome_menu(chat_id, token, settings):
	"""Send welcome message with inline keyboard buttons."""
	welcome = settings.welcome_message or "Welcome to Support! How can I help you?"
	keyboard = {
		"inline_keyboard": [
			[{"text": "Create Ticket", "callback_data": "create_ticket"}],
			[{"text": "My Tickets", "callback_data": "my_tickets"}],
		]
	}
	send_message_api(chat_id, token, welcome, reply_markup=keyboard)


# --- New ticket flow ---

def handle_new_ticket(telegram_user, telegram_chat, chat_id, token, settings, state):
	"""Start the new ticket creation flow."""
	# Check if email is already stored
	email = state.email
	if email:
		# Skip email collection, start field collection
		init_field_collection(state, settings)
		ask_next_field(state, chat_id, token)
	else:
		# Ask for email
		state.state = "awaiting_email"
		state.telegram_chat = telegram_chat.name
		state.save(ignore_permissions=True)
		send_message_api(chat_id, token, "Please share your registered email to continue.")


def handle_email_input(text, telegram_user, chat_id, token, settings, state):
	"""Validate and store the user's email."""
	if not text or not re.match(r"^.+@.+\..+$", text.strip()):
		send_message_api(
			chat_id, token,
			"That doesn't look like a valid email. Please try again."
		)
		return

	email = text.strip()
	state.email = email
	state.save(ignore_permissions=True)

	# Look up or create Contact
	ensure_contact(email, telegram_user.full_name)

	# Start collecting fields
	init_field_collection(state, settings)
	ask_next_field(state, chat_id, token)


def ensure_contact(email, full_name):
	"""Look up or create a Contact for the given email."""
	existing = frappe.db.get_value("Contact", {"email_id": email})
	if existing:
		return existing

	# Split name
	parts = (full_name or "").split(" ", 1)
	first_name = parts[0] or email
	last_name = parts[1] if len(parts) > 1 else ""

	contact = frappe.get_doc({
		"doctype": "Contact",
		"first_name": first_name,
		"last_name": last_name,
		"email_ids": [{"email_id": email, "is_primary": 1}],
	})
	contact.insert(ignore_permissions=True)
	return contact.name


# --- Template-driven field collection ---

def init_field_collection(state, settings):
	"""Load template fields and prepare the collection state."""
	# Always collect subject + description
	conversation_fields = [
		{
			"key": "subject",
			"label": "Subject",
			"type": "str",
			"required": True,
			"prompt": "What is your issue about? (brief subject line)",
		},
		{
			"key": "description",
			"label": "Description",
			"type": "str",
			"required": True,
			"prompt": "Please describe the issue in detail.",
		},
	]

	# Add template fields if a template is configured
	if settings.ticket_template:
		try:
			from helpdesk.helpdesk.doctype.hd_ticket_template.api import get_fields_meta

			fields_meta = get_fields_meta(settings.ticket_template)
			for f in fields_meta:
				if f.get("hide_from_customer"):
					continue
				if f.get("fieldname") in ("subject", "description"):
					continue
				conversation_fields.append(map_field_to_meta(f))
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Telegram Helpdesk: template field loading")

	state.state = "collecting_fields"
	state.current_field_index = 0
	state.collected_data = json.dumps({"_fields": conversation_fields})
	state.save(ignore_permissions=True)


def map_field_to_meta(field):
	"""Map an HD Ticket field's metadata to our conversation field format."""
	fieldtype_map = {
		"Data": "str",
		"Small Text": "str",
		"Text": "str",
		"Text Editor": "str",
		"Select": "select",
		"Link": "str",
		"Int": "int",
		"Float": "float",
	}
	meta = {
		"key": field.get("fieldname"),
		"label": field.get("label", field.get("fieldname")),
		"type": fieldtype_map.get(field.get("fieldtype"), "str"),
		"required": bool(field.get("required")),
		"prompt": field.get("placeholder") or f"Please provide {field.get('label', field.get('fieldname'))}",
	}
	if field.get("fieldtype") == "Select" and field.get("options"):
		meta["options"] = field["options"]
	return meta


def ask_next_field(state, chat_id, token):
	"""Ask the user for the next field in the template."""
	data = json.loads(state.collected_data or "{}")
	fields = data.get("_fields", [])

	if state.current_field_index >= len(fields):
		return

	field = fields[state.current_field_index]
	reply_markup = None

	if field.get("type") == "select" and field.get("options"):
		options = [o for o in field["options"].split("\n") if o.strip()]
		if options:
			keyboard = {"inline_keyboard": [[{"text": opt, "callback_data": opt}] for opt in options]}
			reply_markup = keyboard

	optional_hint = "" if field.get("required") else " (optional, send /skip to skip)"
	prompt = f"{field['prompt']}{optional_hint}"

	send_message_api(chat_id, token, prompt, reply_markup=reply_markup)


def handle_field_input(text, telegram_user, telegram_chat, chat_id, token, settings, state):
	"""Process a user's response to a field prompt."""
	data = json.loads(state.collected_data or "{}")
	fields = data.get("_fields", [])
	idx = state.current_field_index

	if idx >= len(fields):
		return

	current_field = fields[idx]

	# Handle /skip for optional fields
	if text == "/skip" and not current_field.get("required"):
		data[current_field["key"]] = ""
		state.current_field_index = idx + 1
		state.collected_data = json.dumps(data)
		state.save(ignore_permissions=True)

		if state.current_field_index >= len(fields):
			create_ticket(data, telegram_user, telegram_chat, chat_id, token, settings, state)
		else:
			ask_next_field(state, chat_id, token)
		return

	# Validate required
	if current_field.get("required") and not text.strip():
		send_message_api(chat_id, token, "This field is required. Please try again.")
		return

	# Validate select
	if current_field.get("type") == "select" and current_field.get("options"):
		valid_options = [o.strip() for o in current_field["options"].split("\n") if o.strip()]
		if text.strip() not in valid_options:
			send_message_api(chat_id, token, "Please select from the options provided.")
			return

	# Validate int/float
	if current_field.get("type") == "int":
		try:
			int(text.strip())
		except ValueError:
			send_message_api(chat_id, token, "Please enter a valid number.")
			return

	if current_field.get("type") == "float":
		try:
			float(text.strip())
		except ValueError:
			send_message_api(chat_id, token, "Please enter a valid number.")
			return

	# Store the value
	data[current_field["key"]] = text.strip()
	state.current_field_index = idx + 1
	state.collected_data = json.dumps(data)
	state.save(ignore_permissions=True)

	# Check if all fields collected
	if state.current_field_index >= len(fields):
		create_ticket(data, telegram_user, telegram_chat, chat_id, token, settings, state)
	else:
		ask_next_field(state, chat_id, token)


# --- Ticket creation ---

def create_ticket(data, telegram_user, telegram_chat, chat_id, token, settings, state):
	"""Create an HD Ticket with the collected data."""
	email = state.email

	ticket_values = {
		"doctype": "HD Ticket",
		"subject": data.get("subject", "Telegram Support Request"),
		"description": data.get("description", data.get("subject", "")),
		"raised_by": email,
		"via_customer_portal": 1,
		"custom_source": "Telegram",
		"custom_telegram_user_id": str(telegram_user.telegram_user_id),
		"custom_telegram_username": telegram_user.telegram_username or "",
	}

	if settings.default_ticket_type:
		ticket_values["ticket_type"] = settings.default_ticket_type
	if settings.default_agent_group:
		ticket_values["agent_group"] = settings.default_agent_group
	if settings.ticket_template:
		ticket_values["template"] = settings.ticket_template

	# Add template-collected fields
	for key, value in data.items():
		if key.startswith("_") or key in ("subject", "description") or not value:
			continue
		# Try direct field, then custom_ prefixed
		if key in frappe.get_meta("HD Ticket").get_fieldnames_with_value():
			ticket_values[key] = value
		elif f"custom_{key}" in frappe.get_meta("HD Ticket").get_fieldnames_with_value():
			ticket_values[f"custom_{key}"] = value

	try:
		ticket_doc = frappe.get_doc(ticket_values)
		ticket_doc.insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Telegram Helpdesk: ticket creation")
		send_message_api(chat_id, token, "Sorry, there was an error creating your ticket. Please try again.")
		reset_conversation(state)
		return

	# Create mapping for two-way communication
	frappe.get_doc({
		"doctype": "Helpdesk Telegram Ticket",
		"telegram_user": telegram_user.name,
		"telegram_chat": telegram_chat.name,
		"ticket": ticket_doc.name,
		"is_open": 1,
	}).insert(ignore_permissions=True)

	# Reset conversation state
	reset_conversation(state)

	# Send confirmation
	try:
		msg = frappe.render_template(
			settings.ticket_created_message or "Ticket #{{ ticket.name }} created: {{ ticket.subject }}",
			{"ticket": ticket_doc},
		)
	except Exception:
		msg = f"Ticket #{ticket_doc.name} created: {ticket_doc.subject}"

	send_message_api(chat_id, token, msg)


# --- My Tickets ---

def handle_my_tickets(telegram_user, chat_id, token):
	"""Show user's open tickets."""
	mappings = frappe.get_all(
		"Helpdesk Telegram Ticket",
		filters={"telegram_user": telegram_user.name, "is_open": 1},
		fields=["ticket"],
	)

	if not mappings:
		send_message_api(chat_id, token, "You have no open tickets. Tap /start to create one.")
		return

	lines = ["Your open tickets:\n"]
	for m in mappings:
		ticket = frappe.db.get_value(
			"HD Ticket", m.ticket,
			["name", "subject", "status"], as_dict=True,
		)
		if ticket:
			lines.append(f"#{ticket.name} - {ticket.subject} ({ticket.status})")

	send_message_api(chat_id, token, "\n".join(lines))


# --- Follow-up messages ---

def handle_followup_or_prompt(text, telegram_user, telegram_chat, chat_id, token):
	"""Handle a message that's not part of a ticket creation conversation."""
	if not text:
		return

	# Check for open ticket mapping
	mapping = frappe.db.get_value(
		"Helpdesk Telegram Ticket",
		{"telegram_user": telegram_user.name, "is_open": 1},
		["name", "ticket"],
		as_dict=True,
	)

	if mapping:
		ticket = frappe.get_doc("HD Ticket", mapping.ticket)
		# Get email for sender
		state = frappe.db.get_value(
			"Telegram Conversation State",
			{"telegram_user": telegram_user.name},
			"email",
		)
		sender = state or telegram_user.full_name

		frappe.get_doc({
			"doctype": "Communication",
			"communication_type": "Communication",
			"content": text,
			"reference_doctype": "HD Ticket",
			"reference_name": mapping.ticket,
			"sender": sender,
			"sent_or_received": "Received",
			"subject": f"Re: {ticket.subject}",
		}).insert(ignore_permissions=True)

		send_message_api(chat_id, token, f"Message added to ticket #{mapping.ticket}")
	else:
		send_message_api(chat_id, token, "No open ticket found. Send /start to see options.")
