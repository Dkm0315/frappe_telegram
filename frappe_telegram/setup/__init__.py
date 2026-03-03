import frappe

from .notification import add_telegram_notification_channel


def after_install():
    add_telegram_notification_channel()


def after_migrate():
    add_telegram_notification_channel()
    _ensure_notification_defaults()


def _ensure_notification_defaults():
    """Set default values for notification fields on existing Helpdesk Telegram Settings.

    When new Check fields are added to an existing SingleDocType, their values
    default to 0/NULL instead of the JSON-declared default. This function
    ensures the notification toggle fields are set to 1 on first migration.
    """
    if not frappe.db.exists("DocType", "Helpdesk Telegram Settings"):
        return

    notification_fields = [
        "enable_system_notifications",
        "notify_on_ticket_creation",
        "notify_on_status_change",
        "notify_on_user_response",
        "notify_on_agent_response",
        "notify_on_ticket_reopen",
    ]

    settings = frappe.get_doc("Helpdesk Telegram Settings")
    changed = False

    for field in notification_fields:
        # Only set to 1 if the field has never been explicitly saved (NULL in tabSingles)
        existing = frappe.db.get_value(
            "Singles", {"doctype": "Helpdesk Telegram Settings", "field": field}, "value"
        )
        if existing is None:
            settings.set(field, 1)
            changed = True

    if changed:
        settings.save(ignore_permissions=True)
        frappe.db.commit()
