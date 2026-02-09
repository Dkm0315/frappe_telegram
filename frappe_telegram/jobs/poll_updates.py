import time

import frappe

from frappe_telegram.handlers.telegram_api import get_updates
from frappe_telegram.handlers.helpdesk import process_update


LOCK_KEY = "telegram_helpdesk_polling"


def poll_telegram_updates():
	"""Scheduled job that polls Telegram for new updates and processes them.

	Runs every minute via scheduler_events cron. Uses continuous long-polling
	within the job for near-instant response times. Frappe Cloud compatible.
	"""
	settings = frappe.get_doc("Helpdesk Telegram Settings")
	if not settings.enabled or not settings.bot:
		return

	# Prevent concurrent polling (Telegram 409 conflict)
	cache = frappe.cache
	if cache.get_value(LOCK_KEY):
		return
	cache.set_value(LOCK_KEY, "1", expires_in_sec=65)

	try:
		_do_poll(settings)
	finally:
		cache.delete_value(LOCK_KEY)


def _do_poll(settings):
	bot_doc = frappe.get_doc("Telegram Bot", settings.bot)
	token = bot_doc.get_password("api_token")
	if not token:
		frappe.log_error("Bot API token not configured", "Telegram Helpdesk")
		return

	# Run continuous polling for ~55 seconds (leave 5s buffer before next cron)
	end_time = time.time() + 55

	while time.time() < end_time:
		# Read offset fresh each iteration
		last_id = frappe.db.get_single_value("Helpdesk Telegram Settings", "last_update_id") or 0
		offset = last_id + 1

		remaining = max(1, int(end_time - time.time()) - 5)
		poll_timeout = min(remaining, 25)

		if poll_timeout < 1:
			break

		updates = get_updates(token, offset=offset, timeout=poll_timeout)

		for update_data in updates:
			try:
				process_update(update_data, token, settings)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					f"Telegram update error"[:140],
				)

			# Persist offset immediately + commit
			frappe.db.set_single_value(
				"Helpdesk Telegram Settings", "last_update_id", update_data["update_id"]
			)
			frappe.db.commit()
