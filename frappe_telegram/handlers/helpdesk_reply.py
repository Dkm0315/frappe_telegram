import os

import frappe

from frappe_telegram.handlers.telegram_api import send_document_api, send_message_api


def on_communication_insert(doc, method):
	"""Send agent replies to Telegram when a Communication is created on a Telegram-sourced ticket."""
	if doc.sent_or_received != "Sent":
		return
	if doc.reference_doctype != "HD Ticket":
		return

	target = _get_telegram_target_for_ticket(doc.reference_name)
	if not target:
		return

	chat_id, token = target

	# Strip HTML from content
	plain_text = strip_html(doc.content or "")

	if plain_text.strip():
		msg = f"Reply on Ticket #{doc.reference_name}:\n\n{plain_text}"
		send_message_api(chat_id, token, msg)

	# Forward files already attached to this Communication (uploaded before the reply was sent)
	_send_attached_files(doc.doctype, doc.name, chat_id, token)


def on_file_insert(doc, method):
	"""Forward agent-attached files to the Telegram user.

	Handles two cases:
	  1. File attached to a Communication (agent reply with attachment uploaded after Communication)
	  2. File attached directly to an HD Ticket (agent uploads from the ticket form)
	"""
	ticket_name = None

	if doc.attached_to_doctype == "Communication":
		comm = frappe.db.get_value(
			"Communication",
			doc.attached_to_name,
			["sent_or_received", "reference_doctype", "reference_name"],
			as_dict=True,
		)
		if not comm or comm.sent_or_received != "Sent" or comm.reference_doctype != "HD Ticket":
			return
		ticket_name = comm.reference_name

	elif doc.attached_to_doctype == "HD Ticket":
		ticket_name = doc.attached_to_name

	else:
		return

	if not ticket_name:
		return

	target = _get_telegram_target_for_ticket(ticket_name)
	if not target:
		return

	chat_id, token = target
	_send_file_doc(doc, chat_id, token)


def on_ticket_update(doc, method):
	"""Notify Telegram user when their ticket is resolved and close the mapping."""
	if not doc.has_value_changed("status"):
		return

	# Check if status changed to a resolved category
	if doc.status_category != "Resolved":
		return

	mapping = frappe.db.get_value(
		"Helpdesk Telegram Ticket",
		{"ticket": doc.name, "is_open": 1},
		["name", "telegram_user", "telegram_chat"],
		as_dict=True,
	)
	if not mapping:
		return

	# Close the mapping
	frappe.db.set_value("Helpdesk Telegram Ticket", mapping.name, "is_open", 0)

	# Send notification to user
	try:
		settings = frappe.get_cached_doc("Helpdesk Telegram Settings")
		if not settings.enabled or not settings.bot:
			return
		bot_doc = frappe.get_doc("Telegram Bot", settings.bot)
		token = bot_doc.get_password("api_token")
	except Exception:
		return

	chat_id = frappe.db.get_value("Telegram Chat", mapping.telegram_chat, "chat_id")
	if not chat_id:
		return

	keyboard = {
		"inline_keyboard": [
			[{"text": "Reopen Ticket", "callback_data": f"reopen_ticket_{doc.name}"}],
			[{"text": "Create New Ticket", "callback_data": "create_ticket"}],
		]
	}
	send_message_api(
		chat_id, token,
		f"Your ticket #{doc.name} has been resolved.",
		reply_markup=keyboard,
	)


# --- Helpers ---

def _get_telegram_target_for_ticket(ticket_name):
	"""Return (chat_id, token) for a Telegram-mapped ticket, or None."""
	mapping = frappe.db.get_value(
		"Helpdesk Telegram Ticket",
		{"ticket": ticket_name, "is_open": 1},
		"telegram_chat",
	)
	if not mapping:
		return None

	try:
		settings = frappe.get_cached_doc("Helpdesk Telegram Settings")
		if not settings.enabled or not settings.bot:
			return None
		bot_doc = frappe.get_doc("Telegram Bot", settings.bot)
		token = bot_doc.get_password("api_token")
	except Exception:
		return None

	chat_id = frappe.db.get_value("Telegram Chat", mapping, "chat_id")
	if not chat_id:
		return None

	return chat_id, token


def _send_attached_files(doctype, name, chat_id, token):
	"""Send all file attachments on a given document to a Telegram chat."""
	attachments = frappe.get_all(
		"File",
		filters={"attached_to_doctype": doctype, "attached_to_name": name},
		fields=["name", "file_name", "file_url", "is_private"],
	)
	for attachment in attachments:
		_send_file_doc(attachment, chat_id, token)


def _send_file_doc(file_doc, chat_id, token):
	"""Resolve a File doc's path on disk and send it to a Telegram chat."""
	file_url = file_doc.get("file_url") if hasattr(file_doc, "get") else file_doc.file_url
	file_name = file_doc.get("file_name") if hasattr(file_doc, "get") else file_doc.file_name
	if not file_url or "/files/" not in file_url:
		return
	file_path = frappe.get_site_path(
		(("" if "/private/" in file_url else "/public") + file_url).strip("/")
	)
	if os.path.exists(file_path):
		send_document_api(chat_id, token, file_path, file_name)


def strip_html(html_content):
	"""Strip HTML tags from content, returning plain text."""
	try:
		from bs4 import BeautifulSoup
		return BeautifulSoup(html_content, "html.parser").get_text(separator="\n")
	except ImportError:
		import re
		return re.sub(r"<[^>]+>", "", html_content)
