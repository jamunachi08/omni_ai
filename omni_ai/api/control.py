"""Control Room: what needs attention, what is waiting for approval, what the entry checks caught."""

import frappe
from frappe import _
from frappe.utils import add_days, now_datetime

from omni_ai.utils import ADMIN_ROLES, agg, clip, get_settings, has_any_role, loads, require_access


def _summary(action_type: str, payload: dict) -> list:
	"""A few plain lines that tell the approver what will happen."""
	lines = []
	if action_type == "Send Email":
		lines.append(_("To: {0}").format(", ".join(payload.get("to") or [])))
		if payload.get("cc"):
			lines.append(_("Cc: {0}").format(", ".join(payload["cc"])))
		lines.append(clip(payload.get("body") or "", 600))
	elif payload.get("doctype"):
		kind = (payload.get("kind") or payload.get("action") or "").strip()
		target = f"{payload.get('doctype')} {payload.get('name') or ''}".strip()
		lines.append(f"{kind.capitalize()}: {target}" if kind else target)
		if payload.get("summary"):
			lines.append(clip(payload["summary"], 300))
		values = payload.get("values") or {}
		for key, value in list(values.items())[:8]:
			if isinstance(value, list):
				lines.append(f"{key}: {len(value)} " + _("rows"))
			else:
				lines.append(f"{key}: {clip(str(value), 80)}")
	else:
		# notes, schedules, reports, automations: show the plain values of what was prepared
		for key, value in list(payload.items())[:8]:
			if value in (None, "", [], {}) or key in ("query", "script", "columns", "filters"):
				continue
			text = ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)
			lines.append(f"{key.replace('_', ' ').capitalize()}: {clip(text, 200)}")
	return [line for line in lines if line]


@frappe.whitelist()
def overview(show_clear: int = 0):
	"""Everything the Control Room page shows, for the current user."""
	require_access()
	settings = get_settings()
	if not settings.get("enable_control_room"):
		frappe.throw(_("The Control Room is switched off in the assistant settings."))
	user = frappe.session.user
	from omni_ai.checks.engine import flush_log
	from omni_ai.control.monitors import evaluate

	flush_log()
	pending = []
	for row in frappe.get_all(
		"AI Assistant Action",
		filters={"user": user, "status": "Pending"},
		fields=["name", "title", "action_type", "conversation", "creation", "payload"],
		order_by="creation desc",
		limit=100,
	):
		conversation = frappe.db.get_value("AI Assistant Conversation", row.conversation, ["title", "schedule"], as_dict=True) or {}
		pending.append(
			{
				"name": row.name,
				"title": row.title,
				"action_type": row.action_type,
				"conversation": row.conversation,
				"source": conversation.get("title"),
				"scheduled": bool(conversation.get("schedule")),
				"creation": row.creation,
				"lines": _summary(row.action_type, loads(row.payload, {}) or {}),
			}
		)
	checks = None
	if has_any_role(ADMIN_ROLES, user):
		since = add_days(now_datetime(), -7)
		rows = frappe.get_all(
			"AI Assistant Check Log", filters={"logged_on": (">=", since)}, fields=["rule_title", "outcome", agg("count", "name", "n")],
			group_by="rule_title, outcome", limit=200,
		)  # fmt: skip
		checks = {
			"blocked": sum(r.n for r in rows if r.outcome == "Blocked"),
			"warned": sum(r.n for r in rows if r.outcome == "Warned"),
			"rules": [{"rule": r.rule_title, "outcome": r.outcome, "count": r.n} for r in sorted(rows, key=lambda r: -r.n)[:8]],
			"active_rules": frappe.db.count("AI Assistant Check Rule", {"enabled": 1}),
			"enabled": bool(settings.get("enable_entry_checks")),
		}
	return {
		"monitors": evaluate(user, include_clear=bool(frappe.utils.cint(show_clear))),
		"pending": pending,
		"checks": checks,
		"generated": now_datetime(),
		"brand": settings.brand_name,
	}
