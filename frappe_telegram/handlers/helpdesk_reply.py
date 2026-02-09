import frappe

from frappe_telegram.handlers.telegram_api import send_message_api


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
	if not plain_text.strip():
		return

	msg = f"Reply on Ticket #{doc.reference_name}:\n\n{plain_text}"
	send_message_api(chat_id, token, msg)


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

	send_message_api(
		chat_id, token,
		f"Your ticket #{doc.name} has been resolved.\nSend /start to create a new ticket.",
	)


def strip_html(html_content):
	"""Strip HTML tags from content, returning plain text."""
	try:
		from bs4 import BeautifulSoup
		return BeautifulSoup(html_content, "html.parser").get_text(separator="\n")
	except ImportError:
		import re
		return re.sub(r"<[^>]+>", "", html_content)
