import frappe
from frappe.utils import add_days, now_datetime

from omni_ai.utils import get_settings, to_int


def daily_cleanup():
	"""Apply the retention policy from settings. Does nothing when retention is 0."""
	settings = get_settings()
	days = to_int(settings.retention_days)
	if days <= 0:
		return
	cutoff = add_days(now_datetime(), -days)
	old = frappe.get_all(
		"AI Assistant Conversation",
		filters={"modified": ("<", cutoff), "pinned": 0},
		pluck="name",
		limit=500,
	)
	for name in old:
		delete_conversation_data(name)
	frappe.db.delete("AI Assistant Tool Log", {"creation": ("<", cutoff)})


def delete_conversation_data(name: str):
	frappe.db.delete("AI Assistant Message", {"conversation": name})
	frappe.db.delete("AI Assistant Action", {"conversation": name})
	frappe.db.set_value("AI Assistant Memory", {"source_conversation": name}, "source_conversation", None, update_modified=False)
	frappe.db.set_value("AI Assistant Schedule", {"last_conversation": name}, "last_conversation", None, update_modified=False)
	for file_name in frappe.get_all(
		"File",
		filters={"attached_to_doctype": "AI Assistant Conversation", "attached_to_name": name},
		pluck="name",
	):
		frappe.delete_doc("File", file_name, ignore_permissions=True, force=True)
	frappe.delete_doc("AI Assistant Conversation", name, ignore_permissions=True, force=True)
