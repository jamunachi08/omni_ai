import frappe

from omni_ai.utils import SETTINGS, USER_ROLE


def after_install():
	ensure_role()
	ensure_defaults()
	ensure_starter_rules()


def after_migrate():
	ensure_role()
	ensure_defaults()
	ensure_starter_rules()


def ensure_starter_rules():
	"""Create the starter entry checks once. Deleting or changing them later is respected."""
	settings = frappe.get_single(SETTINGS)
	if settings.get("check_rules_seeded"):
		return
	try:
		from omni_ai.checks.defaults import create_starter_rules

		create_starter_rules()
		frappe.db.set_single_value(SETTINGS, "check_rules_seeded", 1)
	except Exception:
		frappe.log_error(title="AI Assistant: starter entry checks could not be created")


def ensure_role():
	if not frappe.db.exists("Role", USER_ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": USER_ROLE, "desk_access": 1}).insert(
			ignore_permissions=True
		)


def ensure_defaults():
	"""Seed role tables once so SQL and Query Report saving start restricted to System Managers."""
	settings = frappe.get_single(SETTINGS)
	changed = False
	for fieldname in ("sql_roles", "report_save_roles"):
		if not settings.get(fieldname):
			settings.append(fieldname, {"role": "System Manager"})
			changed = True
	for fieldname, value in (
		("brand_name", "Neotec AI"),
		("base_url", "https://api.anthropic.com"),
		("model", "claude-opus-5-5"),
	):
		if not settings.get(fieldname):
			settings.set(fieldname, value)
			changed = True
	if settings.get("brand_name") == "Assistant":  # the placeholder name of versions before 1.4
		settings.brand_name = "Neotec AI"
		changed = True
	if changed:
		settings.flags.ignore_permissions = True
		settings.flags.ignore_mandatory = True
		settings.save()
