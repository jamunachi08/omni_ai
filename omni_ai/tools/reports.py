"""Reports: find and run existing ones, and draft new ones for the user to save.

Nothing is saved by the assistant alone. `propose_report` validates the
definition, runs a preview and creates a pending action; the Report document
is created only when the user presses Save on the card in the chat.
"""

import json
import re

import frappe
from frappe.utils import add_days, add_months, cint, getdate, nowdate

from omni_ai.tools.base import (
	UI_TABLE_ROWS,
	Tool,
	ToolError,
	clean_rows,
	columns_from_names,
	tabular_reply,
)
from omni_ai.tools.data import (
	_normalise_filters,
	can_read,
	check_field,
	get_meta_or_error,
	permitted_fields,
	require_read,
)
from omni_ai.tools.sql import escape_percents, run_select, unescape_percents, validate_select
from omni_ai.utils import can_save_query_reports, clip, dumps, frappe_major, loads, to_int

FILTER_TYPES = ("Date", "Link", "Select", "Data", "Int", "Float", "Check")
COLUMN_TYPES = ("Data", "Link", "Currency", "Float", "Int", "Date", "Datetime", "Percent", "Check", "Time", "Duration")
REPORT_NAME_RE = re.compile(r"^[^<>\"'`/\\%?#&]{3,120}$")

# default tokens the assistant may use for report filters -> (python resolver, javascript expression)
DEFAULT_TOKENS = {
	"today": (lambda: nowdate(), "frappe.datetime.get_today()"),
	"yesterday": (lambda: str(add_days(nowdate(), -1)), "frappe.datetime.add_days(frappe.datetime.get_today(), -1)"),
	"month_start": (lambda: str(getdate(nowdate()).replace(day=1)), "frappe.datetime.month_start()"),
	"month_end": (lambda: str(frappe.utils.get_last_day(nowdate())), "frappe.datetime.month_end()"),
	"quarter_start": (lambda: str(frappe.utils.get_quarter_start(nowdate())), "frappe.datetime.quarter_start()"),
	"quarter_end": (lambda: str(frappe.utils.get_quarter_ending(nowdate())), "frappe.datetime.quarter_end()"),
	"year_start": (lambda: f"{getdate(nowdate()).year}-01-01", "frappe.datetime.year_start()"),
	"year_end": (lambda: f"{getdate(nowdate()).year}-12-31", "frappe.datetime.year_end()"),
	"default_company": (
		lambda: frappe.defaults.get_user_default("Company") or "",
		'frappe.defaults.get_user_default("Company")',
	),
}
RELATIVE_RE = re.compile(r"^(days|months)_ago:(\d{1,4})$")


# -- existing reports -------------------------------------------------------------


def _report_allowed(report, roles=None) -> bool:
	"""Same rule the desk uses: role on the report (if any are set) plus report permission on its DocType."""
	if roles is None:
		roles = frappe.get_all("Has Role", filters={"parent": report.name, "parenttype": "Report"}, pluck="role")
	if roles and not (set(roles) & set(frappe.get_roles())) and frappe.session.user != "Administrator":
		return False
	if not report.ref_doctype or not frappe.db.exists("DocType", report.ref_doctype):
		return False
	return bool(frappe.has_permission(report.ref_doctype, "report"))


def list_reports(args, ctx):
	query = (args.get("query") or "").strip()
	filters = {"disabled": 0}
	if args.get("doctype"):
		filters["ref_doctype"] = args["doctype"]
	if args.get("module"):
		filters["module"] = args["module"]
	or_filters = None
	if query:
		or_filters = {
			"name": ("like", f"%{query}%"),
			"ref_doctype": ("like", f"%{query}%"),
			"module": ("like", f"%{query}%"),
		}
	rows = frappe.get_all(
		"Report",
		filters=filters,
		or_filters=or_filters,
		fields=["name", "report_type", "ref_doctype", "module", "is_standard"],
		order_by="name asc",
		limit=300,
	)
	role_map = {}
	if rows:
		for link in frappe.get_all(
			"Has Role",
			filters={"parenttype": "Report", "parent": ("in", [r.name for r in rows])},
			fields=["parent", "role"],
			limit=0,
		):
			role_map.setdefault(link.parent, []).append(link.role)
	out = []
	for row in rows:
		if not _report_allowed(row, role_map.get(row.name, [])):
			continue
		out.append(
			{
				"report": row.name,
				"type": row.report_type,
				"doctype": row.ref_doctype,
				"module": row.module,
				"standard": row.is_standard == "Yes",
			}
		)
		if len(out) >= 60:
			break
	if not out:
		return {"reports": [], "note": "No accessible report matched. Try another keyword, or build the answer with query_data."}
	return {"reports": out}


def _get_report(name):
	name = (name or "").strip()
	if not name or not frappe.db.exists("Report", name):
		close = frappe.get_all("Report", filters={"name": ("like", f"%{name}%")}, pluck="name", limit=8)
		hint = f" Similar reports: {', '.join(close)}." if close else " Use list_reports to find it."
		raise ToolError(f"Report '{name}' does not exist.{hint}")
	report = frappe.get_doc("Report", name)
	if report.disabled:
		raise ToolError(f"Report '{name}' is disabled.")
	if not _report_allowed(report):
		raise ToolError(f"The current user is not allowed to view the report '{name}'.", denied=True)
	return report


def get_report_info(args, ctx):
	report = _get_report(args.get("report_name"))
	info = {
		"report": report.name,
		"type": report.report_type,
		"doctype": report.ref_doctype,
		"module": report.module,
		"standard": report.is_standard == "Yes",
	}
	if report.report_type == "Report Builder":
		info["definition"] = loads(report.json, {})
		info["note"] = "Report Builder reports are saved list views. Reproduce them with query_data using these fields and filters."
		return info
	if report.filters:
		info["filters"] = [
			{
				"fieldname": f.fieldname,
				"label": f.label,
				"type": f.fieldtype,
				"options": f.options,
				"mandatory": cint(f.mandatory),
				"default": f.default,
			}
			for f in report.filters
		]
	try:
		from frappe.desk.query_report import get_script

		script = (get_script(report.name) or {}).get("script") or ""
		if script and "filters" in script:
			info["filter_script"] = clip(script, 7000)
			info["note"] = (
				"filter_script is the report's JavaScript. Read the filter fieldnames, types and which are "
				"mandatory (reqd) from it, then pass values for them to run_report."
			)
	except Exception:
		pass
	return info


def run_report(args, ctx):
	report = _get_report(args.get("report_name"))
	if report.report_type == "Report Builder":
		raise ToolError("This is a Report Builder report. Use get_report_info to read its definition and query_data to run it.")
	filters = args.get("filters") or {}
	if not isinstance(filters, dict):
		raise ToolError("filters must be an object of fieldname: value.")
	from frappe.desk.query_report import run

	try:
		result = run(report.name, filters=frappe._dict(filters), ignore_prepared_report=True, are_default_filters=False)
	except frappe.PermissionError as exc:
		raise ToolError(f"Permission denied: {exc}", denied=True) from exc
	except Exception as exc:
		frappe.db.rollback()
		messages = _pop_messages()
		detail = "; ".join(messages) or clip(exc, 400)
		raise ToolError(
			f"The report could not run: {detail}. Check the mandatory filters with get_report_info and try again."
		) from exc

	columns, rows = normalise_report_result(result.get("columns") or [], result.get("result") or [])
	extra = {}
	if result.get("message"):
		extra["report_message"] = clip(frappe.utils.strip_html_tags(str(result["message"])), 800)
	if result.get("report_summary"):
		extra["summary"] = [
			{"label": s.get("label"), "value": s.get("value")} for s in result["report_summary"] if isinstance(s, dict)
		][:12]
	return tabular_reply(ctx, columns, rows, title=report.name, preview=args.get("preview_rows"), extra=extra or None)


def _pop_messages() -> list:
	out = []
	for raw in frappe.local.message_log or []:
		try:
			item = json.loads(raw) if isinstance(raw, str) else raw
			text = item.get("message") if isinstance(item, dict) else str(item)
		except Exception:
			text = str(raw)
		if text:
			out.append(frappe.utils.strip_html_tags(str(text)))
	frappe.local.message_log = []
	return out


def normalise_report_result(columns, result) -> tuple:
	"""Query/Script reports return columns as dicts or 'Label:Type/Options:Width' strings, and rows as
	dicts or lists. Convert both to our plain columns + list rows."""
	cols = []
	for index, col in enumerate(columns):
		if isinstance(col, str):
			bits = col.split(":")
			label = bits[0]
			fieldtype, options = "Data", None
			if len(bits) > 1 and bits[1]:
				fieldtype, _, options = bits[1].partition("/")
			cols.append({"fieldname": frappe.scrub(label) or f"col_{index}", "label": label, "fieldtype": fieldtype, "options": options or None})
		elif isinstance(col, dict):
			if col.get("hidden"):
				cols.append(None)
				continue
			cols.append(
				{
					"fieldname": col.get("fieldname") or frappe.scrub(col.get("label") or f"col_{index}"),
					"label": col.get("label") or col.get("fieldname") or f"Column {index + 1}",
					"fieldtype": col.get("fieldtype") or "Data",
					"options": col.get("options") if col.get("fieldtype") in ("Link", "Currency") else None,
				}
			)
		else:
			cols.append({"fieldname": f"col_{index}", "label": str(col), "fieldtype": "Data"})
	keep = [i for i, c in enumerate(cols) if c]
	rows = []
	for row in result:
		if isinstance(row, dict):
			rows.append([row.get(cols[i]["fieldname"]) for i in keep])
		elif isinstance(row, (list, tuple)):
			rows.append([row[i] if i < len(row) else None for i in keep])
	return [cols[i] for i in keep], rows


# -- drafting new reports --------------------------------------------------------------


def resolve_default(value):
	"""Python value of a default token (used for previews)."""
	if isinstance(value, str):
		token = value.strip().lower()
		if token in DEFAULT_TOKENS:
			return DEFAULT_TOKENS[token][0]()
		match = RELATIVE_RE.match(token)
		if match:
			count = int(match.group(2))
			return str(add_days(nowdate(), -count) if match.group(1) == "days" else add_months(nowdate(), -count))
	return value


def default_js(value) -> str:
	"""JavaScript expression of a default token (used in the saved report's filter script)."""
	if isinstance(value, str):
		token = value.strip().lower()
		if token in DEFAULT_TOKENS:
			return DEFAULT_TOKENS[token][1]
		match = RELATIVE_RE.match(token)
		if match:
			fn = "add_days" if match.group(1) == "days" else "add_months"
			return f"frappe.datetime.{fn}(frappe.datetime.get_today(), -{int(match.group(2))})"
	return json.dumps(value)


def build_filter_script(report_name: str, filters: list) -> str:
	"""Deterministic client script for a Query Report's filters. Built by us from validated data,
	never written by the model."""
	lines = []
	for f in filters:
		parts = [
			f"fieldname: {json.dumps(f['fieldname'])}",
			f"label: __({json.dumps(f['label'])})",
			f"fieldtype: {json.dumps(f['fieldtype'])}",
		]
		if f.get("options"):
			parts.append(f"options: {json.dumps(f['options'])}")
		if f.get("default") not in (None, ""):
			parts.append(f"default: {default_js(f['default'])}")
		parts.append("reqd: 1")
		lines.append("\t\t{ " + ", ".join(parts) + " },")
	body = "\n".join(lines)
	return f"frappe.query_reports[{json.dumps(report_name)}] = {{\n\tfilters: [\n{body}\n\t],\n}};\n"


def _clean_filters(raw) -> list:
	out, seen = [], set()
	for f in raw or []:
		if not isinstance(f, dict):
			raise ToolError("Each report filter must be an object.")
		fieldname = str(f.get("fieldname") or "").strip()
		if not re.match(r"^[a-z][a-z0-9_]{0,60}$", fieldname):
			raise ToolError(f"Filter fieldname '{fieldname}' must be lowercase letters, numbers and underscores.")
		if fieldname in seen:
			raise ToolError(f"Duplicate filter '{fieldname}'.")
		seen.add(fieldname)
		fieldtype = f.get("fieldtype") or "Data"
		if fieldtype not in FILTER_TYPES:
			raise ToolError(f"Filter type '{fieldtype}' is not supported. Use one of: {', '.join(FILTER_TYPES)}.")
		options = f.get("options")
		if fieldtype == "Link":
			if not options or not frappe.db.exists("DocType", options):
				raise ToolError(f"Link filter '{fieldname}' needs options set to an existing DocType.")
		elif fieldtype == "Select":
			if isinstance(options, list):
				options = "\n".join(str(o) for o in options)
			if not options:
				raise ToolError(f"Select filter '{fieldname}' needs options.")
		else:
			options = None
		out.append(
			{
				"fieldname": fieldname,
				"label": str(f.get("label") or frappe.unscrub(fieldname))[:80],
				"fieldtype": fieldtype,
				"options": options,
				"default": f.get("default"),
			}
		)
	return out


def _clean_columns(raw, names: list) -> list:
	if not raw:
		return [{"fieldname": frappe.scrub(n) or f"col_{i}", "label": n, "fieldtype": "Data", "width": 150} for i, n in enumerate(names)]
	if len(raw) != len(names):
		raise ToolError(
			f"The query returns {len(names)} columns ({', '.join(names)}) but {len(raw)} columns were described. "
			"Describe exactly one column per selected expression, in the same order."
		)
	out, seen = [], set()
	for index, col in enumerate(raw):
		if not isinstance(col, dict):
			raise ToolError("Each column must be an object.")
		fieldtype = col.get("fieldtype") or "Data"
		if fieldtype not in COLUMN_TYPES:
			raise ToolError(f"Column type '{fieldtype}' is not supported. Use one of: {', '.join(COLUMN_TYPES)}.")
		label = str(col.get("label") or names[index])[:80]
		fieldname = frappe.scrub(str(col.get("fieldname") or label)) or f"col_{index}"
		fieldname = re.sub(r"[^a-z0-9_]", "_", fieldname)[:60] or f"col_{index}"
		if fieldname in seen:
			fieldname = f"{fieldname}_{index}"
		seen.add(fieldname)
		options = col.get("options") if fieldtype in ("Link", "Currency") else None
		if fieldtype == "Link" and (not options or not frappe.db.exists("DocType", options)):
			raise ToolError(f"Link column '{label}' needs options set to an existing DocType.")
		out.append(
			{
				"fieldname": fieldname,
				"label": label,
				"fieldtype": fieldtype,
				"options": options,
				"width": min(max(to_int(col.get("width"), 150) or 150, 60), 500),
			}
		)
	return out


def _unique_report_name(name: str) -> str:
	if not frappe.db.exists("Report", name):
		return name
	for i in range(2, 100):
		candidate = f"{name} {i}"
		if not frappe.db.exists("Report", candidate):
			return candidate
	return f"{name} {frappe.generate_hash(length=4)}"


def prepare_query_report(args, user=None) -> dict:
	"""Validate a Query Report definition and return (payload, preview). Used for both proposing and saving."""
	if not can_save_query_reports(user):
		raise ToolError(
			"This user's role is not allowed to save SQL-based (Query) reports. "
			"Offer a Report Builder report instead (report_type 'Report Builder'), or show the result with show_table "
			"and export_data. An administrator can allow Query Reports for their role in the assistant settings.",
			denied=True,
		)
	checked = validate_select(args.get("query"))
	filters = _clean_filters(args.get("filters"))
	names = {f["fieldname"] for f in filters}
	missing = [p for p in checked["placeholders"] if p not in names]
	if missing:
		raise ToolError(f"The query uses %({missing[0]})s but there is no filter with that fieldname. Add it to filters.")
	unused = sorted(names - set(checked["placeholders"]))
	if unused:
		raise ToolError(f"Filter '{unused[0]}' is not used in the query. Reference it as %({unused[0]})s or remove it.")
	values = dict(args.get("preview_values") or {})
	for f in filters:
		if values.get(f["fieldname"]) in (None, ""):
			values[f["fieldname"]] = resolve_default(f.get("default"))
		if values.get(f["fieldname"]) in (None, ""):
			if f["fieldtype"] in ("Link", "Data", "Select"):
				raise ToolError(
					f"Filter '{f['fieldname']}' has no default. Every filter is mandatory in a Query Report, so give it a "
					"default, or pass a value in preview_values so the preview can run."
				)
			values[f["fieldname"]] = 0 if f["fieldtype"] in ("Int", "Float", "Check") else nowdate()

	# Frappe always %-formats a Query Report's SQL with the filter values (even when there are none),
	# so the stored text needs every literal percent sign doubled.
	stored_query = escape_percents(checked["query"])
	if filters:
		column_names, rows = run_select(stored_query, values, limit=UI_TABLE_ROWS)
	else:
		column_names, rows = run_select(unescape_percents(stored_query), None, limit=UI_TABLE_ROWS)
	columns = _clean_columns(args.get("columns"), column_names)
	payload = {
		"report_type": "Query Report",
		"query": stored_query,
		"columns": columns,
		"filters": filters,
		"doctypes": checked["doctypes"],
		"add_total_row": cint(args.get("add_total_row")),
	}
	return payload, columns, rows[:UI_TABLE_ROWS], values


def prepare_builder_report(args, meta) -> tuple:
	"""Validate a Report Builder definition (a saved, permission-aware list view) and preview it."""
	allowed = permitted_fields(meta)
	fields = [check_field(meta, f, allowed) for f in (args.get("fields") or [])]
	if not fields:
		raise ToolError("A Report Builder report needs fields: the columns to show.")
	if "name" not in fields:
		fields.insert(0, "name")
	filters = _normalise_filters(args.get("builder_filters"), meta, allowed) or []
	sort_by = args.get("sort_by") or ("creation" if frappe_major() >= 16 else "modified")
	check_field(meta, sort_by, allowed)
	sort_order = "asc" if str(args.get("sort_order") or "desc").lower() == "asc" else "desc"
	rows = frappe.get_list(
		meta.name,
		fields=fields,
		filters=filters,
		order_by=f"{sort_by} {sort_order}",
		limit_page_length=50,
		as_list=True,
	)
	columns = []
	for name in fields:
		df = meta.get_field(name)
		columns.append({"fieldname": name, "label": (df.label if df else None) or frappe.unscrub(name), "fieldtype": df.fieldtype if df else "Data"})
	definition = {
		"filters": [[meta.name, f[0], f[1], f[2]] for f in filters],
		"fields": [[name, meta.name] for name in fields],
		"order_by": f"`tab{meta.name}`.`{sort_by}` {sort_order}",
		"add_totals_row": cint(args.get("add_total_row")),
		"page_length": 100,
		"column_widths": {},
	}
	payload = {"report_type": "Report Builder", "json": definition}
	return payload, columns, rows


def propose_report(args, ctx):
	if not ctx.settings.enable_report_creation:
		raise ToolError("Saving reports is turned off in the assistant settings. Show the result with show_table instead.", denied=True)
	name = " ".join(str(args.get("report_name") or "").split())
	if not REPORT_NAME_RE.match(name):
		raise ToolError("report_name must be 3 to 120 characters without quotes, slashes or special symbols.")
	meta = get_meta_or_error(args.get("ref_doctype"))
	if meta.istable or meta.issingle:
		raise ToolError("ref_doctype must be a normal DocType (not a child table or Single), usually the main DocType of the report.")
	require_read(meta.name, meta)
	if not frappe.has_permission(meta.name, "report"):
		raise ToolError(f"The current user does not have report permission on {meta.name}.", denied=True)

	report_type = args.get("report_type") or "Query Report"
	if report_type == "Query Report":
		payload, columns, rows, values = prepare_query_report(args, ctx.user)
	elif report_type == "Report Builder":
		payload, columns, rows = prepare_builder_report(args, meta)
		values = {}
	else:
		raise ToolError("report_type must be 'Query Report' or 'Report Builder'.")

	final_name = _unique_report_name(name)
	payload.update(
		{
			"report_name": final_name,
			"ref_doctype": meta.name,
			"module": meta.module,
			"description": clip(args.get("description") or "", 500),
		}
	)
	from omni_ai.tools.actions import create_action

	rows = clean_rows(rows)
	action = create_action(ctx, "Save Report", f"Save report: {final_name}", payload)
	ctx.add_part(
		{
			"type": "action",
			"action": action.name,
			"action_type": "Save Report",
			"status": "Pending",
			"title": final_name,
			"subtitle": f"{payload['report_type']} · {meta.name}",
			"description": payload["description"],
			"columns": columns,
			"rows": rows[:50],
			"total_rows": len(rows),
			"details": _report_details(payload, values),
		}
	)
	reply = {
		"status": "awaiting_user",
		"report_name": final_name,
		"preview_rows": len(rows),
		"preview": rows[:8],
		"columns": [c["label"] for c in columns],
		"message": (
			"A preview card with a Save button is now shown to the user. The report is NOT saved yet; it is saved only "
			"if the user presses Save. Briefly say what the report shows and that they can save it or ask for changes. "
			"Do not claim it has been saved."
		),
	}
	if final_name != name:
		reply["renamed"] = f"A report named '{name}' already exists, so this draft is named '{final_name}'."
	if not rows:
		reply["warning"] = "The preview returned no rows. Check the filters and tell the user if that is expected."
	return reply


def _report_details(payload: dict, values: dict) -> list:
	details = []
	if payload["report_type"] == "Query Report":
		for f in payload["filters"]:
			details.append({"label": f"Filter: {f['label']}", "value": f"{f['fieldtype']}, default {values.get(f['fieldname'])}"})
		details.append({"label": "SQL", "value": payload["query"], "code": True})
	else:
		d = payload["json"]
		if d["filters"]:
			details.append({"label": "Filters", "value": "; ".join(f"{f[1]} {f[2]} {f[3]}" for f in d["filters"])})
		details.append({"label": "Sorted by", "value": d["order_by"].replace("`", "")})
	return details


# -- saving (called from the action executor, i.e. only after the user confirms) -----------------


def save_report(payload: dict, user: str) -> dict:
	meta = get_meta_or_error(payload.get("ref_doctype"))
	if not can_read(meta.name, meta) or not frappe.has_permission(meta.name, "report"):
		frappe.throw(f"You do not have report permission on {meta.name}.", frappe.PermissionError)

	name = _unique_report_name(payload["report_name"])
	doc = frappe.new_doc("Report")
	doc.report_name = name
	doc.ref_doctype = meta.name
	doc.module = payload.get("module") or meta.module
	doc.is_standard = "No"
	doc.report_type = payload["report_type"]

	if payload["report_type"] == "Query Report":
		if not can_save_query_reports(user):
			frappe.throw("Your role is not allowed to save Query Reports.", frappe.PermissionError)
		# validate again at save time: permissions or settings may have changed since the draft
		validate_select(payload["query"])
		doc.query = payload["query"]
		doc.add_total_row = cint(payload.get("add_total_row"))
		for col in payload.get("columns") or []:
			doc.append(
				"columns",
				{
					"fieldname": col["fieldname"],
					"label": col["label"],
					"fieldtype": col["fieldtype"],
					"options": col.get("options"),
					"width": col.get("width") or 150,
				},
			)
		if payload.get("filters"):
			doc.javascript = build_filter_script(name, payload["filters"])
	elif payload["report_type"] == "Report Builder":
		doc.json = dumps(payload["json"])
	else:
		frappe.throw("Unsupported report type.")

	# Frappe restricts creating reports to Report/Script Managers. Here the site administrator has
	# delegated that through the assistant settings, the SQL has passed the guard, and the user has
	# explicitly confirmed, so the insert runs with elevated rights and is attributed to the user.
	original_user = frappe.session.user
	try:
		frappe.set_user("Administrator")
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
	finally:
		frappe.set_user(original_user)
	frappe.db.set_value("Report", doc.name, "owner", user, update_modified=False)

	route = report_route(doc)
	workspace = None
	try:
		from omni_ai.tools.workspace import add_report_shortcut

		workspace = add_report_shortcut(doc)
	except Exception:
		frappe.log_error(title="AI Assistant: could not add report to workspace")
	return {"doctype": "Report", "name": doc.name, "route": route, "workspace": workspace}


def report_route(doc) -> str:
	base = "/desk" if frappe_major() >= 16 else "/app"
	from urllib.parse import quote

	if doc.report_type == "Report Builder":
		return f"{base}/{frappe.scrub(doc.ref_doctype).replace('_', '-')}/view/report/{quote(doc.name)}"
	return f"{base}/query-report/{quote(doc.name)}"


FILTER_ITEM = {
	"type": "object",
	"properties": {
		"fieldname": {"type": "string", "description": "lowercase_name used in the SQL as %(fieldname)s"},
		"label": {"type": "string"},
		"fieldtype": {"type": "string", "enum": list(FILTER_TYPES)},
		"options": {"type": "string", "description": "DocType for Link; newline-separated choices for Select."},
		"default": {
			"description": (
				"Literal value or a token: today, yesterday, month_start, month_end, quarter_start, quarter_end, "
				"year_start, year_end, days_ago:N, months_ago:N, default_company."
			)
		},
	},
	"required": ["fieldname", "label", "fieldtype"],
}

TOOLS = [
	Tool(
		"list_reports",
		"""List existing reports the user can open (standard ERPNext reports such as General Ledger, Accounts
		Receivable, Stock Balance, Sales Analytics, plus custom ones). Prefer an existing standard report over
		writing your own query for accounting, stock valuation and other figures with business rules built in.""",
		{
			"type": "object",
			"properties": {
				"query": {"type": "string", "description": "Keyword, e.g. 'receivable', 'stock', 'payroll'."},
				"doctype": {"type": "string"},
				"module": {"type": "string"},
			},
		},
		list_reports,
		label="Looking for reports",
		enabled=lambda ctx: bool(ctx.settings.enable_reports),
	),
	Tool(
		"get_report_info",
		"""Get a report's type and its filters (names, types, which are mandatory). Call this before
		run_report so you pass the right filter fieldnames.""",
		{"type": "object", "properties": {"report_name": {"type": "string"}}, "required": ["report_name"]},
		get_report_info,
		label="Checking report",
		enabled=lambda ctx: bool(ctx.settings.enable_reports),
	),
	Tool(
		"run_report",
		"""Run an existing Query or Script report with filters and get its rows, with the user's permissions.
		Typical mandatory filters are company, from_date, to_date or fiscal year; read them with
		get_report_info first. The full result is stored as result_id; you see a sample.""",
		{
			"type": "object",
			"properties": {
				"report_name": {"type": "string"},
				"filters": {"type": "object", "description": "Filter values keyed by filter fieldname."},
				"preview_rows": {"type": "integer"},
			},
			"required": ["report_name"],
		},
		run_report,
		label="Running report",
		enabled=lambda ctx: bool(ctx.settings.enable_reports),
	),
	Tool(
		"propose_report",
		"""Draft a new report for the user to save into the ERP's report list. This validates the definition,
		runs a preview and shows the user a card with the preview and a Save button. It does not save anything
		itself. Use it when the user asks to create, build, design or save a report, or agrees to keep an
		analysis as a reusable report.

		Two types:
		- 'Report Builder': a saved list view of one DocType (fields, filters, sort). Honours row-level
		  permissions and any assistant user may save it. Use for simple lists.
		- 'Query Report': SQL-based, for joins, grouping and calculations. Only permitted roles can save it.
		  SQL rules are the same as run_sql (single SELECT, backtick `tab<DocType>` tables, no comments).
		  Runtime filters are referenced in the SQL as %(fieldname)s and described in `filters`; every filter
		  is mandatory at runtime, so give each a sensible default. For an optional text match use a Data
		  filter with default '%' and `column LIKE %(fieldname)s`. Literal percent signs in the SQL are
		  escaped for you. Describe one entry in `columns` per selected expression, in order.

		Test the logic with run_sql or query_data first when it is not trivial. After calling, tell the user
		the draft is ready to save; never say it has been saved.""",
		{
			"type": "object",
			"properties": {
				"report_name": {"type": "string", "description": "Clear business name, e.g. 'Monthly Sales by Customer'."},
				"report_type": {"type": "string", "enum": ["Query Report", "Report Builder"]},
				"ref_doctype": {"type": "string", "description": "Main DocType; decides the module and who can open the report."},
				"description": {"type": "string", "description": "One or two sentences on what the report shows."},
				"query": {"type": "string", "description": "Query Report only: the SELECT statement."},
				"columns": {
					"type": "array",
					"description": "Query Report only: one per selected expression, in order.",
					"items": {
						"type": "object",
						"properties": {
							"label": {"type": "string"},
							"fieldtype": {"type": "string", "enum": list(COLUMN_TYPES)},
							"options": {"type": "string", "description": "DocType for Link columns."},
							"width": {"type": "integer"},
						},
						"required": ["label", "fieldtype"],
					},
				},
				"filters": {"type": "array", "description": "Query Report only: runtime filters.", "items": FILTER_ITEM},
				"preview_values": {"type": "object", "description": "Filter values for the preview when a default is not enough."},
				"fields": {"type": "array", "items": {"type": "string"}, "description": "Report Builder only: fields of ref_doctype to show."},
				"builder_filters": {"type": "array", "items": {"type": "array"}, "description": "Report Builder only: [[field, operator, value], ...]."},
				"sort_by": {"type": "string", "description": "Report Builder only."},
				"sort_order": {"type": "string", "enum": ["asc", "desc"]},
				"add_total_row": {"type": "boolean"},
			},
			"required": ["report_name", "report_type", "ref_doctype"],
		},
		propose_report,
		label="Drafting report",
		enabled=lambda ctx: bool(ctx.settings.enable_report_creation),
	),
]
