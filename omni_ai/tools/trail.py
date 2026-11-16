"""Document trail: where a document came from and what was created from it
(quotation -> order -> delivery -> invoice -> payment), and the Control Room's findings as a tool."""

import frappe

from omni_ai.tools.base import Tool, ToolError, clean_value
from omni_ai.tools.data import can_read, get_meta_or_error

SKIP = {
	"Comment", "Version", "ToDo", "File", "Communication", "Activity Log", "View Log", "Error Log", "GL Entry",
	"Stock Ledger Entry", "Payment Ledger Entry", "Email Queue", "Notification Log", "Access Log", "Tag Link", "DocShare",
	"Event", "Energy Point Log", "Serial and Batch Bundle", "Repost Item Valuation", "Bin", "Deleted Document",
}  # fmt: skip
ALSO_FOLLOW = {"Opportunity", "Lead", "Project", "Task", "Issue", "Timesheet", "Batch"}
BRIEF_FIELDS = (
	"status", "posting_date", "transaction_date", "grand_total", "outstanding_amount", "per_delivered", "per_billed",
	"per_received", "paid_amount", "customer", "supplier", "party",
)  # fmt: skip
PER_TYPE = 15


def _followed(doctype: str) -> bool:
	if doctype in SKIP or doctype.startswith("AI Assistant"):
		return False
	try:
		meta = frappe.get_meta(doctype)
	except Exception:
		return False
	return not meta.istable and not meta.issingle and (meta.is_submittable or doctype in ALSO_FOLLOW)


def _brief_fields(doctype: str) -> list:
	meta = frappe.get_meta(doctype)
	return ["name", "docstatus"] + [f for f in BRIEF_FIELDS if meta.get_field(f)]


def _brief(row) -> dict:
	out = {k: clean_value(v) for k, v in dict(row).items() if v not in (None, "")}
	out["state"] = {0: "Draft", 1: "Submitted", 2: "Cancelled"}.get(out.pop("docstatus", None), "")
	return out


def trace_document(args, ctx):
	doctype, name = args.get("doctype"), args.get("name")
	meta = get_meta_or_error(doctype)
	if not name or not frappe.db.exists(doctype, name):
		raise ToolError(f"{doctype} '{name}' was not found.")
	if not frappe.has_permission(doctype, "read", name):
		raise ToolError(f"The current user is not allowed to read {doctype} {name}.", denied=True)
	doc = frappe.get_doc(doctype, name)

	# where it came from: documents this one points to
	sources = {}
	holders = [(doc, meta)] + [(row, frappe.get_meta(row.doctype)) for df in meta.get_table_fields() for row in (doc.get(df.fieldname) or [])]
	for holder, holder_meta in holders:
		for df in holder_meta.fields:
			target = df.options if df.fieldtype == "Link" else holder.get(df.options) if df.fieldtype == "Dynamic Link" else None
			value = holder.get(df.fieldname)
			if not target or not value or (target == doctype and value == name) or not _followed(target):
				continue
			sources.setdefault(target, set()).add(value)
	comes_from = {}
	for target, names in sources.items():
		if not can_read(target, frappe.get_meta(target)):
			continue
		rows = frappe.get_list(target, filters={"name": ("in", list(names)[:PER_TYPE])}, fields=_brief_fields(target), limit=PER_TYPE)
		if rows:
			comes_from[target] = [_brief(r) for r in rows]

	# what was made from it: documents that point to this one
	from frappe.desk.form.linked_with import get_linked_doctypes

	leads_to = {}
	for target, link in (get_linked_doctypes(doctype) or {}).items():
		if not _followed(target) or not can_read(target, frappe.get_meta(target)):
			continue
		holder = link.get("child_doctype") or target
		names = set()
		for fieldname in link.get("fieldname") or []:
			filters = [[holder, fieldname, "=", name]]
			if link.get("doctype_fieldname"):
				filters.append([holder, link["doctype_fieldname"], "=", doctype])
			try:
				names.update(frappe.get_list(target, filters=filters, pluck="name", limit=PER_TYPE, distinct=True))
			except Exception:
				continue
		names.discard(name) if target == doctype else None
		if names:
			rows = frappe.get_list(target, filters={"name": ("in", list(names)[:PER_TYPE])}, fields=_brief_fields(target), limit=PER_TYPE)
			leads_to[target] = [_brief(r) for r in rows]

	head = _brief(frappe.get_list(doctype, filters={"name": name}, fields=_brief_fields(doctype), limit=1)[0])
	return {
		"document": {"doctype": doctype, **head},
		"comes_from": comes_from,
		"leads_to": leads_to,
		"note": (
			"Only documents the user may read are listed. A step that should exist but is absent from leads_to (for "
			"example a submitted, delivered order with no Sales Invoice) is where the process is stuck; drafts and "
			"cancelled documents do not count as done. Say plainly what is missing and who would normally do it."
		),
	}


def list_exceptions(args, ctx):
	from omni_ai.control.monitors import evaluate

	found = evaluate(ctx.user, area=args.get("area") or None)
	if not found:
		return {"exceptions": [], "note": "Nothing needs attention in the areas this user can see."}
	return {
		"exceptions": [
			{k: m[k] for k in ("area", "severity", "title", "why", "count", "amount", "currency", "doctype", "filters") if m.get(k) not in (None, "")}
			for m in found
		],
		"note": "Counts follow the user's permissions. To list the records behind one, call query_data with its doctype and filters.",
	}


TOOLS = [
	Tool(
		"trace_document",
		"""Follow one document through its process: the documents it was created from (for example the
		quotation and sales order behind an invoice) and the documents created from it (deliveries, invoices,
		payments, returns), each with its state. Use it for "where is this order stuck?", "has this been
		invoiced / paid / delivered?", "what happened to this request?".""",
		{
			"type": "object",
			"properties": {"doctype": {"type": "string"}, "name": {"type": "string"}},
			"required": ["doctype", "name"],
		},
		trace_document,
		label="Following the document trail",
	),
	Tool(
		"list_exceptions",
		"""The Control Room's findings: what currently needs attention in Sales, Purchasing, Accounts, Stock,
		Projects, HR and Master data (late orders, deliveries not invoiced, overdue invoices, negative stock,
		items below reorder level, old drafts and so on), with counts and amounts. Use it for "what needs my
		attention?", "how is the business doing operationally?", daily or weekly briefings, and before
		preparing follow-up actions.""",
		{
			"type": "object",
			"properties": {"area": {"type": "string", "enum": ["Sales", "Purchasing", "Accounts", "Stock", "Projects", "HR", "Master data"]}},
		},
		list_exceptions,
		label="Checking what needs attention",
		enabled=lambda ctx: bool(ctx.settings.get("enable_control_room")),
	),
]
