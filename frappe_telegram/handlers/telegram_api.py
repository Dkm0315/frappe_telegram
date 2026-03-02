import json
import requests

import frappe


def send_message_api(chat_id, token, text, reply_markup=None, parse_mode=None):
	"""Send a text message via Telegram Bot API.

	If parse_mode is set and the API call fails (e.g. malformed HTML),
	retries without parse_mode so the message still reaches the user.
	"""
	payload = {"chat_id": chat_id, "text": text}
	if reply_markup:
		payload["reply_markup"] = json.dumps(reply_markup) if isinstance(reply_markup, dict) else reply_markup
	if parse_mode:
		payload["parse_mode"] = parse_mode

	url = f"https://api.telegram.org/bot{token}/sendMessage"
	try:
		response = requests.post(url, json=payload, timeout=10)
		response.raise_for_status()
		return response.json()
	except Exception as e:
		if parse_mode:
			# Retry without parse_mode as plain text fallback
			payload.pop("parse_mode", None)
			try:
				response = requests.post(url, json=payload, timeout=10)
				response.raise_for_status()
				return response.json()
			except Exception:
				pass
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


def send_document_api(chat_id, token, file_path, filename, caption=None):
	"""Send a document to a Telegram chat via Bot API."""
	payload = {"chat_id": chat_id}
	if caption:
		payload["caption"] = caption

	try:
		with open(file_path, "rb") as f:
			response = requests.post(
				f"https://api.telegram.org/bot{token}/sendDocument",
				data=payload,
				files={"document": (filename, f)},
				timeout=30,
			)
		response.raise_for_status()
		return response.json()
	except Exception as e:
		frappe.log_error(str(e)[:140], "Telegram sendDocument Error")


def get_file_info(file_id, token):
	"""Get file path on Telegram servers for a given file_id."""
	try:
		response = requests.post(
			f"https://api.telegram.org/bot{token}/getFile",
			json={"file_id": file_id},
			timeout=10,
		)
		response.raise_for_status()
		data = response.json()
		if data.get("ok"):
			return data["result"].get("file_path")
	except Exception as e:
		frappe.log_error(str(e)[:140], "Telegram getFile Error")
	return None


def download_telegram_file(file_path, token):
	"""Download file bytes from Telegram servers."""
	try:
		response = requests.get(
			f"https://api.telegram.org/file/bot{token}/{file_path}",
			timeout=30,
		)
		response.raise_for_status()
		return response.content
	except Exception as e:
		frappe.log_error(str(e)[:140], "Telegram File Download Error")
	return None


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
