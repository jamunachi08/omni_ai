"""Shared helpers: settings access, role checks, version detection, JSON handling."""

import json

import frappe
from frappe import _

SETTINGS = "AI Assistant Settings"
USER_ROLE = "AI Assistant User"
ADMIN_ROLES = ("System Manager",)


def frappe_major() -> int:
	try:
		return int(str(frappe.__version__).split(".")[0])
	except Exception:
		return 15


def get_settings():
	"""Cached settings document (never contains the decrypted API key)."""
	return frappe.get_cached_doc(SETTINGS)


def get_api_key() -> str:
	"""API key from site config (preferred for hosted setups) or from settings."""
	key = frappe.conf.get("ai_assistant_api_key")
	if key:
		return key
	try:
		return get_settings().get_password("api_key", raise_exception=False) or ""
	except Exception:
		return ""


def is_configured() -> bool:
	"""Is there enough configuration to call the AI provider?"""
	try:
		settings = get_settings()
	except Exception:
		return False
	if settings.provider == "OpenAI-compatible":
		# a local model server may need no key, but it does need an address and a model
		return bool((settings.base_url or "").strip() and (settings.model or "").strip())
	return bool(get_api_key())


def agg(function: str, field: str, alias: str):
	"""An aggregate for the `fields` of get_list / get_all that works on v15 (string) and v16 (dict)."""
	if frappe_major() >= 16:
		return {function.upper(): field, "as": alias}
	return f"{function.lower()}({field}) as {alias}"


def doctype_set(fieldname: str) -> set:
	"""DocType names listed in one of the settings' DocType tables."""
	return {row.document_type for row in (get_settings().get(fieldname) or []) if row.document_type}


def provider_is_external() -> bool:
	"""Does the configured AI provider run outside the organisation's own country or servers?"""
	return (get_settings().get("data_location") or "Outside").startswith("Outside")


def setting_roles(fieldname: str, default=ADMIN_ROLES) -> set:
	rows = get_settings().get(fieldname) or []
	roles = {row.role for row in rows if row.role}
	return roles or set(default)


def user_roles(user=None) -> set:
	return set(frappe.get_roles(user or frappe.session.user))


def has_any_role(roles, user=None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(user_roles(user) & set(roles))


def has_access(user=None) -> bool:
	"""Can this user open the assistant at all?"""
	user = user or frappe.session.user
	if not user or user == "Guest":
		return False
	try:
		if not get_settings().enabled:
			return False
	except Exception:
		return False
	return has_any_role((USER_ROLE, *ADMIN_ROLES), user)


def has_app_permission() -> bool:
	return has_access()


def require_access():
	if not has_access():
		frappe.throw(_("You do not have access to the AI assistant."), frappe.PermissionError)


def can_use_sql(user=None) -> bool:
	settings = get_settings()
	return bool(settings.enable_sql) and has_any_role(setting_roles("sql_roles"), user)


def can_save_query_reports(user=None) -> bool:
	settings = get_settings()
	return bool(settings.enable_report_creation) and has_any_role(setting_roles("report_save_roles"), user)


def loads(value, default=None):
	if value in (None, ""):
		return default
	if isinstance(value, (dict, list)):
		return value
	try:
		return json.loads(value)
	except Exception:
		return default


def dumps(value) -> str:
	return json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"))


def clip(text, limit: int) -> str:
	text = "" if text is None else str(text)
	if len(text) <= limit:
		return text
	return text[:limit] + f"… [truncated, {len(text) - limit} more characters]"


def to_int(value, default=0) -> int:
	try:
		return int(value)
	except Exception:
		return default
