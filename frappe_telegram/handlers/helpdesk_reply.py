import os

import frappe

from frappe_telegram.handlers.telegram_api import send_document_api, send_message_api


def on_communication_insert(doc, method):
	"""Send agent replies to Telegram when a Communication is created on a Telegram-sourced ticket."""
	if doc.sent_or_received != "Sent":
		return
	if doc.reference_doctype != "HD Ticket":
		return

	# Check if this ticket has a Telegram mapping
	mapping = frappe.db.get_value(
		"Helpdesk Telegram Ticket",
		{"ticket": doc.reference_name, "is_open": 1},
		["telegram_user", "telegram_chat"],
		as_dict=True,
	)
	if not mapping:
		return

	# Get bot token
	try:
		settings = frappe.get_cached_doc("Helpdesk Telegram Settings")
		if not settings.enabled or not settings.bot:
			return
		bot_doc = frappe.get_doc("Telegram Bot", settings.bot)
		token = bot_doc.get_password("api_token")
	except Exception:
		return

	# Get chat_id
	chat_id = frappe.db.get_value("Telegram Chat", mapping.telegram_chat, "chat_id")
	if not chat_id:
		return

	# Strip HTML from content
	plain_text = strip_html(doc.content or "")

	if plain_text.strip():
		msg = f"Reply on Ticket #{doc.reference_name}:\n\n{plain_text}"
		send_message_api(chat_id, token, msg)

	# Forward any attachments on this Communication
	send_communication_attachments(doc, chat_id, token)


def send_communication_attachments(doc, chat_id, token):
	"""Send file attachments from a Communication to the Telegram chat."""
	attachments = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Communication", "attached_to_name": doc.name},
		fields=["name", "file_name", "file_url", "is_private"],
	)
	for attachment in attachments:
		file_url = attachment.file_url
		if not file_url or "/files/" not in file_url:
			continue
		file_path = frappe.get_site_path(
			(("" if "/private/" in file_url else "/public") + file_url).strip("/")
		)
		if os.path.exists(file_path):
			send_document_api(chat_id, token, file_path, attachment.file_name)


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


def strip_html(html_content):
	"""Strip HTML tags from content, returning plain text."""
	try:
		from bs4 import BeautifulSoup
		return BeautifulSoup(html_content, "html.parser").get_text(separator="\n")
	except ImportError:
		import re
		return re.sub(r"<[^>]+>", "", html_content)
