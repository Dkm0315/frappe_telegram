import json
import requests

import frappe


def send_message_api(chat_id, token, text, reply_markup=None, parse_mode=None):
	"""Send a text message via Telegram Bot API."""
	payload = {"chat_id": chat_id, "text": text}
	if reply_markup:
		payload["reply_markup"] = json.dumps(reply_markup) if isinstance(reply_markup, dict) else reply_markup
	if parse_mode:
		payload["parse_mode"] = parse_mode

	try:
		response = requests.post(
			f"https://api.telegram.org/bot{token}/sendMessage",
			json=payload,
			timeout=10,
		)
		response.raise_for_status()
		return response.json()
	except Exception as e:
		frappe.log_error(str(e)[:140], "Telegram sendMessage Error")


def answer_callback_query(callback_query_id, token, text=None):
	"""Acknowledge a callback query from an inline keyboard button."""
	payload = {"callback_query_id": callback_query_id}
	if text:
		payload["text"] = text

	try:
		requests.post(
			f"https://api.telegram.org/bot{token}/answerCallbackQuery",
			json=payload,
			timeout=10,
		)
	except Exception as e:
		frappe.log_error(str(e)[:140], "Telegram Callback Error")


def get_updates(token, offset=0, timeout=30):
	"""Poll Telegram for new updates. Returns empty list on 409 (concurrent poll)."""
	try:
		response = requests.get(
			f"https://api.telegram.org/bot{token}/getUpdates",
			params={"offset": offset, "timeout": timeout},
			timeout=timeout + 5,
		)
		if response.status_code == 409:
			# Another polling instance is active — skip silently
			return []
		response.raise_for_status()
		data = response.json()
		if data.get("ok"):
			return data.get("result", [])
		return []
	except Exception as e:
		frappe.log_error(str(e)[:140], "Telegram API Error")
		return []
