"""Changes to data: always proposed first, executed only after the user confirms.

`propose_document_action` records what the assistant would like to do and
shows it to the user as a card. `execute_action` runs when the user presses
Confirm, as that user, through the normal document API, so validations,
workflows and permissions behave exactly as in the desk.
"""

import frappe
from frappe.utils import now_datetime

from omni_ai.tools.base import Tool, ToolError, clean_value
from omni_ai.tools.data import get_meta_or_error
from omni_ai.utils import clip, dumps, frappe_major, loads

ACTION_TYPES = {
	"create": "Create Document",
	"update": "Update Document",
	"submit": "Submit Document",
	"cancel": "Cancel Document",
	"delete": "Delete Document",
}
PERMISSION_FOR = {"create": "create", "update": "write", "submit": "submit", "cancel": "cancel", "delete": "delete"}
# Configuration, code and security DocTypes are out of reach: the assistant works on business records.
BLOCKED_MODULES = {"Core", "Custom", "Integrations", "Workflow", "Printing", "Website", "Email", "AI Assistant"}
ALLOWED_IN_BLOCKED_MODULES = set()
# DocTypes in otherwise ordinary modules that hold code, markup or site-wide behaviour.
BLOCKED_DOCTYPES = {
	"System Console", "Bulk Update", "Dashboard Chart", "Dashboard Chart Source", "Number Card", "Workspace",
	"Custom HTML Block", "Form Tour", "Module Onboarding", "Onboarding Step", "Global Search Settings",
	"List View Settings", "Kanban Board", "Auto Repeat", "Assignment Rule", "Milestone Tracker",
	"Energy Point Rule", "Energy Point Settings", "Web Form", "Server Script", "Client Script", "Report",
	"Print Format", "Letter Head", "Address Template", "Notification", "Email Template", "Auto Email Report",
}  # fmt: skip


def create_action(ctx, action_type: str, title: str, payload: dict):
	doc = frappe.get_doc(
		{
			"doctype": "AI Assistant Action",
			"title": clip(title, 130),
			"action_type": action_type,
			"status": "Pending",
			"user": ctx.user,
			"conversation": ctx.conversation,
			"payload": dumps(payload),
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	frappe.db.set_value("AI Assistant Action", doc.name, "owner", ctx.user, update_modified=False)
	ctx.created_actions.append(doc.name)
	return doc


def _check_writable(meta):
	if (meta.module in BLOCKED_MODULES and meta.name not in ALLOWED_IN_BLOCKED_MODULES) or meta.name in BLOCKED_DOCTYPES:
		raise ToolError(
			f"{meta.name} is a system or configuration DocType. The assistant only changes business records; "
			"tell the user to make this change in the desk.",
			denied=True,
		)
	if meta.istable:
		raise ToolError(f"{meta.name} is a child table. Change it through its parent document.")
	if getattr(meta, "is_virtual", 0):
		raise ToolError(f"{meta.name} is a virtual DocType and cannot be changed here.")


def _check_values(meta, values: dict):
	if not isinstance(values, dict) or not values:
		raise ToolError("values must be an object of fieldname: value.")
	for key, value in values.items():
		if key in ("doctype", "name", "docstatus", "owner", "creation", "modified", "modified_by", "idx"):
			raise ToolError(f"'{key}' cannot be set directly.")
		df = meta.get_field(key)
		if not df:
			raise ToolError(f"Field '{key}' does not exist on {meta.name}. Check get_doctype_info.")
		if df.fieldtype in ("Table", "Table MultiSelect"):
			if not isinstance(value, list) or not all(isinstance(r, dict) for r in value):
				raise ToolError(f"'{key}' is a table: pass a list of row objects.")
			child_meta = frappe.get_meta(df.options)
			for row in value:
				for child_key in row:
					if child_key in ("name", "doctype", "parent", "parenttype", "parentfield", "idx"):
						continue
					if not child_meta.get_field(child_key):
						raise ToolError(f"Field '{child_key}' does not exist on {df.options}.")
		elif df.fieldtype == "Password":
			raise ToolError("Password fields cannot be set by the assistant.")


def propose_document_action(args, ctx):
	if not ctx.settings.enable_write_actions:
		raise ToolError(
			"Creating and changing documents is turned off in the assistant settings. Explain the steps so the user can do it.",
			denied=True,
		)
	kind = str(args.get("action") or "").lower()
	if kind not in ACTION_TYPES:
		raise ToolError(f"action must be one of: {', '.join(ACTION_TYPES)}.")
	meta = get_meta_or_error(args.get("doctype"))
	_check_writable(meta)
	name = args.get("name")
	values = args.get("values") or {}

	if kind in ("submit", "cancel") and not meta.is_submittable:
		raise ToolError(f"{meta.name} is not submittable.")
	if kind == "create":
		if meta.issingle:
			raise ToolError(f"{meta.name} is a Single DocType; use action 'update'.")
		_check_values(meta, values)
		if not frappe.has_permission(meta.name, "create"):
			raise ToolError(f"The current user is not allowed to create {meta.name}.", denied=True)
	else:
		name = name or (meta.name if meta.issingle else None)
		if not name:
			raise ToolError("name is required for this action.")
		if not meta.issingle and not frappe.db.exists(meta.name, name):
			raise ToolError(f"{meta.name} '{name}' was not found.")
		doc = frappe.get_doc(meta.name, name)
		if not doc.has_permission(PERMISSION_FOR[kind]):
			raise ToolError(f"The current user is not allowed to {kind} {meta.name} {name}.", denied=True)
		if kind == "update":
			_check_values(meta, values)
			if doc.docstatus == 1 and not all(meta.get_field(k).allow_on_submit for k in values):
				raise ToolError("This document is submitted; only fields allowed after submit can be changed.")
			if doc.docstatus == 2:
				raise ToolError("This document is cancelled and cannot be changed.")
		if kind == "submit" and doc.docstatus != 0:
			raise ToolError("Only draft documents can be submitted.")
		if kind == "cancel" and doc.docstatus != 1:
			raise ToolError("Only submitted documents can be cancelled.")

	summary = clip(args.get("summary") or "", 300)
	verb = {"create": "Create", "update": "Update", "submit": "Submit", "cancel": "Cancel", "delete": "Delete"}[kind]
	title = f"{verb} {meta.name}" + (f" {name}" if name and kind != "create" else "")
	payload = {"action": kind, "doctype": meta.name, "name": name, "values": values, "summary": summary}
	action = create_action(ctx, ACTION_TYPES[kind], title, payload)

	details = []
	for key, value in values.items():
		df = meta.get_field(key)
		if isinstance(value, list):
			for index, row in enumerate(value[:20], start=1):
				text = ", ".join(f"{k}: {clean_value(v)}" for k, v in row.items())
				details.append({"label": f"{df.label} row {index}", "value": clip(text, 300)})
			if len(value) > 20:
				details.append({"label": df.label, "value": f"… and {len(value) - 20} more rows"})
		else:
			details.append({"label": df.label or key, "value": clip(clean_value(value), 300)})
	ctx.add_part(
		{
			"type": "action",
			"action": action.name,
			"action_type": ACTION_TYPES[kind],
			"status": "Pending",
			"title": title,
			"subtitle": summary,
			"details": details,
			"danger": kind in ("delete", "cancel"),
		}
	)
	return {
		"status": "awaiting_user",
		"message": (
			"A confirmation card is now shown to the user. Nothing has been changed yet; the action runs only if the "
			"user presses Confirm. Briefly say what will happen and ask them to confirm on the card. "
			"Do not claim it is done."
		),
	}


# -- execution (only reached from the Confirm button) ------------------------------------------


def doc_route(doctype: str, name: str) -> str:
	from urllib.parse import quote

	base = "/desk" if frappe_major() >= 16 else "/app"
	return f"{base}/{frappe.scrub(doctype).replace('_', '-')}/{quote(str(name), safe='')}"


def execute_action(action_name: str, user: str) -> dict:
	"""Run a pending action as `user`. Raises on failure; the caller records the outcome."""
	action = frappe.get_doc("AI Assistant Action", action_name)
	if action.user != user:
		frappe.throw("This action belongs to another user.", frappe.PermissionError)
	if action.status != "Pending":
		frappe.throw(f"This action is already {action.status.lower()}.")
	payload = loads(action.payload, {})

	if action.action_type == "Save Report":
		from omni_ai.tools.reports import save_report

		result = save_report(payload, user)
		message = f"Report '{result['name']}' saved."
		if result.get("workspace"):
			message += f" It is also listed in the '{result['workspace']}' workspace."
	elif action.action_type == "Save Memory":
		from omni_ai.tools.memory import save_memory

		result = save_memory(payload, user, action.conversation)
		message = result.pop("message")
	elif action.action_type == "Create Schedule":
		from omni_ai.tools.schedule import create_schedule

		result = create_schedule(payload, user)
		result["route"] = doc_route(result["doctype"], result["name"])
		message = result.pop("message")
	elif action.action_type == "Run Automation":
		from omni_ai.tools.automation import run_automation

		result = run_automation(payload, user)
		message = result.pop("message")
	else:
		result = _execute_document_action(payload)
		message = result.pop("message")

	action.db_set(
		{
			"status": "Completed",
			"executed_at": now_datetime(),
			"reference_doctype": None if result.get("no_route") else result.get("doctype"),
			"reference_name": None if result.get("no_route") or not result.get("exists", True) else result.get("name"),
			"result": clip(message, 500),
		}
	)
	return {"status": "Completed", "message": message, "route": result.get("route"), "name": result.get("name")}


def _execute_document_action(payload: dict) -> dict:
	kind, doctype, name = payload.get("action"), payload.get("doctype"), payload.get("name")
	values = payload.get("values") or {}
	meta = get_meta_or_error(doctype)
	_check_writable(meta)

	if kind == "create":
		doc = frappe.get_doc({"doctype": doctype, **values})
		doc.insert()
		return {"doctype": doctype, "name": doc.name, "route": doc_route(doctype, doc.name), "message": f"{doctype} {doc.name} created."}

	doc = frappe.get_doc(doctype, name)
	if kind == "update":
		doc.check_permission("write")
		doc.update(values)
		doc.save()
		message = f"{doctype} {doc.name} updated."
	elif kind == "submit":
		doc.submit()
		message = f"{doctype} {doc.name} submitted."
	elif kind == "cancel":
		doc.cancel()
		message = f"{doctype} {doc.name} cancelled."
	elif kind == "delete":
		frappe.delete_doc(doctype, name)
		return {"doctype": doctype, "name": name, "exists": False, "message": f"{doctype} {name} deleted."}
	else:
		frappe.throw("Unknown action.")
	return {"doctype": doctype, "name": doc.name, "route": doc_route(doctype, doc.name), "message": message}


TOOLS = [
	Tool(
		"propose_document_action",
		"""Propose creating, updating, submitting, cancelling or deleting a business document. This shows the
		user a confirmation card; the change happens only if they press Confirm, and it runs with their own
		permissions and all normal validations. Use get_doctype_info first so field names and required fields
		are right, and find_documents to resolve Link values to real IDs. For child tables pass a list of row
		objects (on update this replaces the existing rows). System and configuration DocTypes cannot be
		changed. After calling, ask the user to confirm on the card; never say the change is done.""",
		{
			"type": "object",
			"properties": {
				"action": {"type": "string", "enum": list(ACTION_TYPES)},
				"doctype": {"type": "string"},
				"name": {"type": "string", "description": "Document ID (not needed for create)."},
				"values": {"type": "object", "description": "Field values for create or update."},
				"summary": {"type": "string", "description": "One sentence telling the user what will change."},
			},
			"required": ["action", "doctype"],
		},
		propose_document_action,
		label="Preparing change",
		enabled=lambda ctx: bool(ctx.settings.enable_write_actions) and not ctx.unattended,
	)
]
