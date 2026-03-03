"""
Centralized notification system for Helpdesk Telegram integration.

Provides three notification channels:
1. Rich Telegram messages to users (HTML formatted, emoji-rich)
2. System Comments on HD Ticket (timeline audit trail)
3. Notification Log entries for management (bell-icon alerts in Frappe desk)
"""

import frappe
from frappe.utils import now_datetime, format_datetime


def _esc(text):
	"""Escape HTML special characters in user-controlled text."""
	return frappe.utils.escape_html(str(text)) if text else ""


# ── Ticket metadata helper ──────────────────────────────────────────


def _get_ticket_metadata(ticket_name):
	"""Fetch ticket metadata used across all notification templates."""
	ticket = frappe.db.get_value(
		"HD Ticket",
		ticket_name,
		["name", "subject", "status", "priority", "ticket_type", "agent_group", "raised_by", "creation"],
		as_dict=True,
	)
	if not ticket:
		return None

	assignments = frappe.get_all(
		"ToDo",
		filters={"reference_type": "HD Ticket", "reference_name": ticket_name, "status": "Open"},
		fields=["allocated_to"],
		limit=1,
	)
	ticket["assigned_agent"] = assignments[0].allocated_to if assignments else "Unassigned"
	if ticket["assigned_agent"] != "Unassigned":
		ticket["assigned_agent_name"] = (
			frappe.db.get_value("User", ticket["assigned_agent"], "full_name") or ticket["assigned_agent"]
		)
	else:
		ticket["assigned_agent_name"] = "Unassigned"

	return ticket


def _get_telegram_user_display(telegram_user_name):
	"""Get display-friendly name for a Telegram user."""
	user = frappe.db.get_value(
		"Telegram User",
		telegram_user_name,
		["full_name", "telegram_username"],
		as_dict=True,
	)
	if not user:
		return "Unknown User"
	name = user.full_name or "Unknown"
	if user.telegram_username:
		name += f" (@{user.telegram_username})"
	return name


# ── Settings + recipients loader ─────────────────────────────────────


def _get_notification_settings():
	"""Load notification settings. Returns None if notifications disabled."""
	try:
		settings = frappe.get_cached_doc("Helpdesk Telegram Settings")
		if not settings.enabled or not getattr(settings, "enable_system_notifications", 0):
			return None
		return settings
	except Exception:
		return None


def _get_notification_recipients(settings):
	"""Get list of Frappe user emails from the notification_recipients child table."""
	recipients = []
	for row in settings.notification_recipients or []:
		if row.user:
			recipients.append(row.user)
	if not recipients:
		recipients = ["Administrator"]
	return recipients


# ── Core notification dispatchers ────────────────────────────────────


def add_system_comment(ticket_name, content):
	"""Add a Comment (type 'Info') to the HD Ticket timeline."""
	try:
		frappe.get_doc({
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": "HD Ticket",
			"reference_name": ticket_name,
			"content": content,
		}).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Telegram Helpdesk: system comment error")


def send_notification_log(recipients, subject, message, ticket_name):
	"""Create Notification Log entries for recipients via Frappe's enqueue API.

	Uses enqueue_create_notification which:
	- Runs asynchronously via frappe.enqueue (enqueue_after_commit=True)
	- Triggers publish_realtime for the bell icon in Frappe desk
	- Respects per-user notification settings
	- Filters for enabled users only
	"""
	try:
		from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

		enqueue_create_notification(
			recipients,
			{
				"subject": subject,
				"type": "Alert",
				"document_type": "HD Ticket",
				"document_name": ticket_name,
				"from_user": "Administrator",
				"email_content": message,
			},
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Telegram Helpdesk: notification log error")


# ── High-level notification functions ────────────────────────────────


def notify_ticket_created(ticket_name, telegram_user_name):
	"""Management notification when a new ticket is created via Telegram."""
	settings = _get_notification_settings()
	if not settings or not getattr(settings, "notify_on_ticket_creation", 1):
		return

	ticket = _get_ticket_metadata(ticket_name)
	if not ticket:
		return

	tg_user = _esc(_get_telegram_user_display(telegram_user_name))
	recipients = _get_notification_recipients(settings)
	timestamp = format_datetime(now_datetime(), "dd MMM yyyy, hh:mm a")

	comment = (
		f"<b>\U0001f3ab New Ticket Created via Telegram</b><br>"
		f"<b>Created by:</b> {tg_user}<br>"
		f"<b>Subject:</b> {_esc(ticket.subject)}<br>"
		f"<b>Priority:</b> {_esc(ticket.priority) or 'Not set'}<br>"
		f"<b>Type:</b> {_esc(ticket.ticket_type) or 'Not set'}<br>"
		f"<b>Agent Group:</b> {_esc(ticket.agent_group) or 'Not set'}<br>"
		f"<b>Raised by:</b> {_esc(ticket.raised_by) or 'N/A'}<br>"
		f"<b>Time:</b> {timestamp}"
	)
	add_system_comment(ticket_name, comment)

	subject_line = f"\U0001f3ab New Telegram Ticket #{ticket_name}: {_esc(ticket.subject)}"
	send_notification_log(recipients, subject_line, comment, ticket_name)


def notify_status_change(ticket_name, old_status, new_status):
	"""Management notification when a ticket status changes."""
	settings = _get_notification_settings()
	if not settings or not getattr(settings, "notify_on_status_change", 1):
		return

	ticket = _get_ticket_metadata(ticket_name)
	if not ticket:
		return

	recipients = _get_notification_recipients(settings)
	timestamp = format_datetime(now_datetime(), "dd MMM yyyy, hh:mm a")
	actor = frappe.session.user or "System"
	actor_name = _esc(frappe.db.get_value("User", actor, "full_name") or actor)

	comment = (
		f"<b>\U0001f504 Status Changed</b><br>"
		f"<b>From:</b> {_esc(old_status)} <b>\u2192 To:</b> {_esc(new_status)}<br>"
		f"<b>Changed by:</b> {actor_name}<br>"
		f"<b>Ticket:</b> #{ticket_name} - {_esc(ticket.subject)}<br>"
		f"<b>Priority:</b> {_esc(ticket.priority) or 'Not set'}<br>"
		f"<b>Assigned to:</b> {_esc(ticket.assigned_agent_name)}<br>"
		f"<b>Time:</b> {timestamp}"
	)
	add_system_comment(ticket_name, comment)

	subject_line = f"\U0001f504 Ticket #{ticket_name}: {_esc(old_status)} \u2192 {_esc(new_status)}"
	send_notification_log(recipients, subject_line, comment, ticket_name)


def notify_ticket_reopened(ticket_name, telegram_user_name):
	"""Management notification when a Telegram user reopens a resolved ticket."""
	settings = _get_notification_settings()
	if not settings or not getattr(settings, "notify_on_ticket_reopen", 1):
		return

	ticket = _get_ticket_metadata(ticket_name)
	if not ticket:
		return

	tg_user = _esc(_get_telegram_user_display(telegram_user_name))
	recipients = _get_notification_recipients(settings)
	timestamp = format_datetime(now_datetime(), "dd MMM yyyy, hh:mm a")

	comment = (
		f"<b>\U0001f513 Ticket Reopened via Telegram</b><br>"
		f"<b>Reopened by:</b> {tg_user}<br>"
		f"<b>Ticket:</b> #{ticket_name} - {_esc(ticket.subject)}<br>"
		f"<b>Priority:</b> {_esc(ticket.priority) or 'Not set'}<br>"
		f"<b>Assigned to:</b> {_esc(ticket.assigned_agent_name)}<br>"
		f"<b>Time:</b> {timestamp}"
	)
	add_system_comment(ticket_name, comment)

	subject_line = f"\U0001f513 Ticket #{ticket_name} REOPENED by {tg_user}"
	send_notification_log(recipients, subject_line, comment, ticket_name)


def notify_user_response(ticket_name, telegram_user_name, message_preview):
	"""Management notification when a Telegram user sends a follow-up message."""
	settings = _get_notification_settings()
	if not settings or not getattr(settings, "notify_on_user_response", 1):
		return

	ticket = _get_ticket_metadata(ticket_name)
	if not ticket:
		return

	tg_user = _esc(_get_telegram_user_display(telegram_user_name))
	recipients = _get_notification_recipients(settings)
	timestamp = format_datetime(now_datetime(), "dd MMM yyyy, hh:mm a")
	preview = (message_preview[:200] + "...") if len(message_preview) > 200 else message_preview

	comment = (
		f"<b>\U0001f4ac Customer Response via Telegram</b><br>"
		f"<b>From:</b> {tg_user}<br>"
		f"<b>Ticket:</b> #{ticket_name} - {_esc(ticket.subject)}<br>"
		f"<b>Status:</b> {_esc(ticket.status)} | <b>Assigned to:</b> {_esc(ticket.assigned_agent_name)}<br>"
		f"<b>Message:</b> {_esc(preview)}<br>"
		f"<b>Time:</b> {timestamp}"
	)
	add_system_comment(ticket_name, comment)

	subject_line = f"\U0001f4ac Customer response on Ticket #{ticket_name} from {tg_user}"
	send_notification_log(recipients, subject_line, comment, ticket_name)


def notify_agent_response(ticket_name, agent_user, message_preview):
	"""Management notification when an agent sends a reply on a Telegram ticket."""
	settings = _get_notification_settings()
	if not settings or not getattr(settings, "notify_on_agent_response", 1):
		return

	ticket = _get_ticket_metadata(ticket_name)
	if not ticket:
		return

	recipients = _get_notification_recipients(settings)
	timestamp = format_datetime(now_datetime(), "dd MMM yyyy, hh:mm a")
	preview = (message_preview[:200] + "...") if len(message_preview) > 200 else message_preview
	agent_name = _esc(frappe.db.get_value("User", agent_user, "full_name") or agent_user)

	comment = (
		f"<b>\U0001f468\u200d\U0001f4bc Agent Response Sent to Telegram</b><br>"
		f"<b>Agent:</b> {agent_name}<br>"
		f"<b>Ticket:</b> #{ticket_name} - {_esc(ticket.subject)}<br>"
		f"<b>Status:</b> {_esc(ticket.status)}<br>"
		f"<b>Response:</b> {_esc(preview)}<br>"
		f"<b>Time:</b> {timestamp}"
	)
	add_system_comment(ticket_name, comment)

	subject_line = f"\U0001f468\u200d\U0001f4bc Agent {agent_name} replied on Ticket #{ticket_name}"
	send_notification_log(recipients, subject_line, comment, ticket_name)


# ── Rich Telegram message builders (HTML) ────────────────────────────


def build_rich_status_resolved_message(ticket_name):
	"""Rich Telegram message for status resolved."""
	ticket = _get_ticket_metadata(ticket_name)
	if not ticket:
		return f"\u2705 Your ticket #{ticket_name} has been resolved."

	return (
		f"\u2705 <b>Ticket Resolved</b>\n\n"
		f"\U0001f3ab <b>Ticket:</b> #{ticket_name}\n"
		f"\U0001f4cb <b>Subject:</b> {_esc(ticket.subject)}\n"
		f"\U0001f4ca <b>Priority:</b> {_esc(ticket.priority) or 'Standard'}\n"
		f"\U0001f464 <b>Handled by:</b> {_esc(ticket.assigned_agent_name)}\n\n"
		f"\U0001f44d Thank you for contacting support! If you need further assistance, "
		f"you can reopen this ticket or create a new one."
	)


def build_rich_status_reopened_message(ticket_name):
	"""Rich Telegram message for status reopened."""
	ticket = _get_ticket_metadata(ticket_name)
	if not ticket:
		return f"\U0001f504 Your ticket #{ticket_name} has been reopened. You can send follow-up messages."

	return (
		f"\U0001f504 <b>Ticket Reopened</b>\n\n"
		f"\U0001f3ab <b>Ticket:</b> #{ticket_name}\n"
		f"\U0001f4cb <b>Subject:</b> {_esc(ticket.subject)}\n"
		f"\U0001f7e2 <b>Status:</b> Open\n\n"
		f"\U0001f4ac Your ticket has been reopened. You can now send follow-up messages."
	)


def build_rich_status_update_message(ticket_name, new_status):
	"""Rich Telegram message for generic status updates."""
	ticket = _get_ticket_metadata(ticket_name)
	if not ticket:
		return f"\U0001f4e2 Your ticket #{ticket_name} status has been updated to: {new_status}"

	return (
		f"\U0001f4e2 <b>Ticket Status Update</b>\n\n"
		f"\U0001f3ab <b>Ticket:</b> #{ticket_name}\n"
		f"\U0001f4cb <b>Subject:</b> {_esc(ticket.subject)}\n"
		f"\U0001f195 <b>New Status:</b> {_esc(new_status)}\n"
		f"\U0001f4ca <b>Priority:</b> {_esc(ticket.priority) or 'Standard'}\n"
		f"\U0001f464 <b>Assigned to:</b> {_esc(ticket.assigned_agent_name)}"
	)


def build_rich_agent_reply_message(ticket_name, plain_text):
	"""Rich Telegram message for agent replies."""
	ticket = _get_ticket_metadata(ticket_name)
	if not ticket:
		return f"\U0001f4e9 Reply on Ticket #{ticket_name}:\n\n{_esc(plain_text)}"

	return (
		f"\U0001f4e9 <b>Agent Reply</b>\n\n"
		f"\U0001f3ab <b>Ticket:</b> #{ticket_name} - {_esc(ticket.subject)}\n"
		f"\U0001f464 <b>From:</b> {_esc(ticket.assigned_agent_name)}\n\n"
		f"{_esc(plain_text)}"
	)


def build_rich_followup_confirmation(ticket_name):
	"""Rich Telegram message confirming follow-up message was added."""
	ticket = _get_ticket_metadata(ticket_name)
	if not ticket:
		return f"\u2705 Message added to ticket #{ticket_name}"

	return (
		f"\u2705 <b>Message Sent</b>\n\n"
		f"\U0001f3ab <b>Ticket:</b> #{ticket_name}\n"
		f"\U0001f4cb <b>Subject:</b> {_esc(ticket.subject)}\n"
		f"\U0001f4c8 <b>Status:</b> {_esc(ticket.status)}\n\n"
		f"\U0001f552 Your message has been added. An agent will review it shortly."
	)
