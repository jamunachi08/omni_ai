"""Read tools: discover DocTypes, inspect their fields and query records.

Everything here runs through Frappe's permission-aware APIs as the logged-in
user, so role permissions, User Permissions and field-level permissions all
apply exactly as they do in the desk.
"""

import re

import frappe
from frappe.model import default_fields, no_value_fields, optional_fields

from omni_ai.tools.base import Tool, ToolError, clean_value, tabular_reply
from omni_ai.utils import clip, frappe_major, to_int

STANDARD_FIELDS = set(default_fields) | set(optional_fields) | {"name", "docstatus", "idx"}
AGG_FUNCTIONS = {"sum", "count", "avg", "min", "max"}
FIELDNAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SKIP_FIELDTYPES = set(no_value_fields) - {"Table", "Table MultiSelect"}
# Field names that hold credentials or tokens. They are never read, whatever their field type.
SECRET_FIELD_RE = re.compile(
	r"\b\w*(?:password|passwd|api_key|api_secret|otp_secret|client_secret|access_token|refresh_token|"
	r"private_key|secret_key|secret_access_key|webhook_secret|encryption_key|auth_token|bearer_token)\w*\b",
	re.I,
)
# Never expose these through the assistant, whatever the user's role.
HIDDEN_DOCTYPES = {
	"OAuth Bearer Token",
	"OAuth Authorization Code",
	"OAuth Client",
	"Token Cache",
	"User Social Login",
	"Social Login Key",
	"Connected App",
	"Sessions",
	"AI Assistant Settings",
	"Integration Request",
	"Webhook Request Log",
	"Access Log",
	"Activity Log",
	"User Email",
	"Email Account",
	"Email Domain",
	"LDAP Settings",
	"Google Settings",
	"S3 Backup Settings",
	"Dropbox Settings",
	"Webhook",
	"Push Notification Settings",
	"AI Assistant Automation",
	"AI Assistant Knowledge",
}


# -- shared checks --------------------------------------------------------------


def withheld(doctype: str, meta=None) -> bool:
	"""Is this DocType marked sensitive while the AI provider is an outside service?"""
	blocked = getattr(frappe.local, "omni_ai_withheld", None)
	if not blocked:
		return False
	if doctype in blocked:
		return True
	meta = meta or frappe.get_meta(doctype)
	return bool(meta.istable and any(parent in blocked for parent in parents_of(doctype)))


WITHHELD_MESSAGE = (
	"'{0}' is marked as sensitive, and its records are not sent to an outside AI provider. Tell the user this data "
	"is not available in the assistant; they can open it in the desk."
)


def get_meta_or_error(doctype: str):
	doctype = (doctype or "").strip()
	if not doctype or not frappe.db.exists("DocType", doctype):
		close = frappe.get_all(
			"DocType", filters={"name": ("like", f"%{doctype}%")}, pluck="name", limit=8
		)
		hint = f" Did you mean one of: {', '.join(close)}?" if close else " Use search_doctypes to find the right name."
		raise ToolError(f"DocType '{doctype}' does not exist.{hint}")
	if doctype in HIDDEN_DOCTYPES:
		raise ToolError(f"'{doctype}' is not available to the assistant.", denied=True)
	meta = frappe.get_meta(doctype)
	if withheld(doctype, meta):
		raise ToolError(WITHHELD_MESSAGE.format(doctype), denied=True)
	return meta


def parents_of(child_doctype: str) -> list:
	parents = set(
		frappe.get_all(
			"DocField",
			filters={"options": child_doctype, "fieldtype": ("in", ("Table", "Table MultiSelect"))},
			pluck="parent",
		)
	)
	parents.update(
		frappe.get_all(
			"Custom Field",
			filters={"options": child_doctype, "fieldtype": ("in", ("Table", "Table MultiSelect"))},
			pluck="dt",
		)
	)
	return sorted(parents)


def can_read(doctype: str, meta=None) -> bool:
	meta = meta or frappe.get_meta(doctype)
	if doctype in HIDDEN_DOCTYPES or withheld(doctype, meta):
		return False
	if meta.istable:
		return any(frappe.has_permission(p, "read") for p in parents_of(doctype))
	return bool(frappe.has_permission(doctype, "read"))


def require_read(doctype: str, meta=None):
	if not can_read(doctype, meta):
		raise ToolError(
			f"The current user does not have permission to read '{doctype}'. "
			"Tell the user and do not try to work around it.",
			denied=True,
		)


def permitted_fields(meta, parent_doctype=None) -> set:
	try:
		names = set(meta.get_permitted_fieldnames(parenttype=parent_doctype, with_virtual_fields=False))
	except TypeError:
		names = set(meta.get_permitted_fieldnames())
	return names | STANDARD_FIELDS | ({"parent", "parenttype", "parentfield"} if meta.istable else set())


def check_field(meta, fieldname: str, allowed: set):
	fieldname = (fieldname or "").strip()
	if not FIELDNAME_RE.match(fieldname):
		raise ToolError(f"'{fieldname}' is not a valid field name. Use plain field names from get_doctype_info.")
	if fieldname in STANDARD_FIELDS or fieldname in ("parent", "parenttype", "parentfield"):
		return fieldname
	df = meta.get_field(fieldname)
	if not df:
		raise ToolError(
			f"Field '{fieldname}' does not exist on {meta.name}. Call get_doctype_info('{meta.name}') to see its fields."
		)
	if df.fieldtype in SKIP_FIELDTYPES or df.fieldtype in ("Table", "Table MultiSelect", "Password"):
		raise ToolError(f"Field '{fieldname}' ({df.fieldtype}) cannot be selected in a query.")
	if SECRET_FIELD_RE.search(fieldname):
		raise ToolError(f"Field '{fieldname}' holds credentials and is not available to the assistant.", denied=True)
	if fieldname not in allowed:
		raise ToolError(f"The current user is not allowed to read field '{fieldname}' on {meta.name}.", denied=True)
	return fieldname


# -- tools -----------------------------------------------------------------------


def search_doctypes(args, ctx):
	query = (args.get("query") or "").strip()
	module = (args.get("module") or "").strip()
	filters = {"istable": 0}
	or_filters = None
	if module:
		filters["module"] = module
	if query:
		or_filters = {"name": ("like", f"%{query}%"), "module": ("like", f"%{query}%")}
	rows = frappe.get_all(
		"DocType",
		filters=filters,
		or_filters=or_filters,
		fields=["name", "module", "issingle", "is_submittable"],
		order_by="name asc",
		limit=400,
	)
	if query and not rows:
		# fall back to word-by-word matching ("purchase invoices" -> Purchase Invoice)
		words = [w.rstrip("s") for w in re.split(r"\W+", query) if len(w) > 2]
		seen = {}
		for word in words:
			for row in frappe.get_all(
				"DocType",
				filters={"istable": 0, "name": ("like", f"%{word}%")},
				fields=["name", "module", "issingle", "is_submittable"],
				limit=100,
			):
				seen[row.name] = row
		rows = list(seen.values())
	out = []
	for row in rows:
		if row.name in HIDDEN_DOCTYPES or withheld(row.name) or not frappe.has_permission(row.name, "read"):
			continue
		item = {"doctype": row.name, "module": row.module}
		if row.issingle:
			item["single"] = True
		if row.is_submittable:
			item["submittable"] = True
		out.append(item)
		if len(out) >= 60:
			break
	if not out:
		return {"doctypes": [], "note": "No readable DocTypes matched. Try a broader word, or the user may lack permission."}
	return {"doctypes": out}


def get_doctype_info(args, ctx):
	meta = get_meta_or_error(args.get("doctype"))
	require_read(meta.name, meta)
	allowed = permitted_fields(meta)
	# table fields carry no value of their own, so Frappe leaves them out of the permitted field
	# names; check their permission level directly
	try:
		levels = set(meta.get_permlevel_access(permission_type="read")) if meta.get_permissions() else None
	except Exception:
		levels = None
	fields, child_tables = [], []
	for df in meta.fields:
		if df.fieldtype in SKIP_FIELDTYPES or df.fieldtype == "Password" or SECRET_FIELD_RE.search(df.fieldname):
			continue
		if df.fieldtype in ("Table", "Table MultiSelect"):
			if levels is None or (df.permlevel or 0) in levels or meta.istable:
				child_tables.append({"fieldname": df.fieldname, "label": df.label, "child_doctype": df.options})
			continue
		if df.fieldname not in allowed:
			continue
		item = {"fieldname": df.fieldname, "label": df.label, "type": df.fieldtype}
		if df.fieldtype in ("Link", "Dynamic Link") and df.options:
			item["links_to"] = df.options
		elif df.fieldtype == "Select" and df.options:
			item["options"] = clip(" | ".join(o for o in str(df.options).split("\n") if o), 300)
		if df.reqd:
			item["required"] = True
		fields.append(item)
	info = {
		"doctype": meta.name,
		"module": meta.module,
		"fields": fields[:300],
		"standard_fields": ["name", "owner", "creation", "modified", "modified_by", "docstatus"],
	}
	if meta.istable:
		info["is_child_table"] = True
		info["parent_doctypes"] = parents_of(meta.name)
		info["note"] = "Child table rows link to their parent through the 'parent' field. Pass parent_doctype when querying."
	if meta.issingle:
		info["is_single"] = True
		info["note"] = "Single DocType (one record, e.g. settings). Read it with get_document(doctype, doctype)."
	if meta.is_submittable:
		info["submittable"] = "docstatus: 0 = Draft, 1 = Submitted, 2 = Cancelled. Most business reports should filter docstatus = 1."
	if child_tables:
		info["child_tables"] = child_tables
	if meta.title_field:
		info["title_field"] = meta.title_field
	if meta.get("search_fields"):
		info["search_fields"] = meta.search_fields
	try:
		info["record_count"] = frappe.db.estimate_count(meta.name) if not meta.issingle else 1
	except Exception:
		pass
	return info


def _aggregate_field(spec: dict, meta, allowed: set):
	fn = str(spec.get("function") or "").lower()
	if fn not in AGG_FUNCTIONS:
		raise ToolError(f"Unsupported aggregate '{fn}'. Use one of: {', '.join(sorted(AGG_FUNCTIONS))}.")
	field = spec.get("field") or "name"
	check_field(meta, field, allowed)
	alias = spec.get("as") or f"{fn}_{field}"
	if not FIELDNAME_RE.match(alias):
		raise ToolError(f"'{alias}' is not a valid alias. Use letters, numbers and underscores.")
	if frappe_major() >= 16:
		return {fn.upper(): field, "as": alias}, alias
	return f"{fn}(`tab{meta.name}`.`{field}`) as {alias}", alias


def _order_by(value, valid_names: set, meta) -> str:
	if not value:
		return ""
	parts = []
	for chunk in str(value).split(","):
		bits = chunk.strip().split()
		if not bits:
			continue
		name = bits[0].strip("`")
		direction = bits[1].lower() if len(bits) > 1 else "asc"
		if direction not in ("asc", "desc") or len(bits) > 2:
			raise ToolError("order_by must look like 'fieldname desc' or 'field1 asc, field2 desc'.")
		if name not in valid_names:
			raise ToolError(f"Cannot order by '{name}': it is not a selected field, alias or field of {meta.name}.")
		parts.append(f"{name} {direction}")
	return ", ".join(parts)


def _normalise_filters(filters, meta, allowed: set):
	"""Accept {'field': value}, {'field': ['>', 5]} or [[field, op, value], ...]."""
	if not filters:
		return None
	out = []
	if isinstance(filters, dict):
		for key, value in filters.items():
			check_field(meta, key, allowed)
			if isinstance(value, (list, tuple)) and len(value) == 2 and isinstance(value[0], str):
				out.append([key, value[0], value[1]])
			else:
				out.append([key, "=", value])
		return out
	if isinstance(filters, list):
		for item in filters:
			if not isinstance(item, (list, tuple)):
				raise ToolError("Each filter must be [field, operator, value].")
			item = list(item)
			if len(item) == 4:  # [doctype, field, op, value]
				item = item[1:]
			if len(item) == 2:
				item = [item[0], "=", item[1]]
			if len(item) != 3:
				raise ToolError("Each filter must be [field, operator, value].")
			check_field(meta, item[0], allowed)
			out.append(item)
		return out
	raise ToolError("filters must be an object or a list of [field, operator, value].")


def query_data(args, ctx):
	meta = get_meta_or_error(args.get("doctype"))
	if meta.issingle:
		raise ToolError(f"{meta.name} is a Single DocType. Use get_document('{meta.name}', '{meta.name}').")
	parent_doctype = args.get("parent_doctype")
	if meta.istable:
		parents = parents_of(meta.name)
		if not parent_doctype:
			readable = [p for p in parents if frappe.has_permission(p, "read")]
			if not readable:
				require_read(meta.name, meta)
			parent_doctype = readable[0]
		elif parent_doctype not in parents:
			raise ToolError(f"{meta.name} is not a child table of {parent_doctype}. Its parents are: {', '.join(parents)}.")
	require_read(meta.name, meta)
	allowed = permitted_fields(meta, parent_doctype)

	group_by = args.get("group_by") or []
	if isinstance(group_by, str):
		group_by = [g.strip() for g in group_by.split(",") if g.strip()]
	group_by = [check_field(meta, g, allowed) for g in group_by]

	fields, names = [], []
	for field in args.get("fields") or []:
		if field == "*":
			raise ToolError("Select specific fields instead of '*'.")
		fields.append(check_field(meta, field, allowed))
		names.append(field)
	for spec in args.get("aggregates") or []:
		expr, alias = _aggregate_field(spec, meta, allowed)
		fields.append(expr)
		names.append(alias)
	for g in group_by:
		if g not in names:
			fields.insert(0, g)
			names.insert(0, g)
	if not fields:
		title = meta.title_field if meta.title_field and meta.title_field in allowed else None
		fields = ["name"] + ([title] if title and title != "name" else [])
		names = list(fields)

	limit = min(max(1, to_int(args.get("limit"), 100) or 100), ctx.max_rows)
	offset = max(0, to_int(args.get("offset"), 0))
	order_by = _order_by(args.get("order_by"), set(names) | allowed, meta)
	kwargs = dict(
		fields=fields,
		filters=_normalise_filters(args.get("filters"), meta, allowed),
		or_filters=_normalise_filters(args.get("or_filters"), meta, allowed),
		limit_start=offset,
		limit_page_length=limit,
		as_list=True,
	)
	if group_by:
		kwargs["group_by"] = ", ".join(f"`tab{meta.name}`.`{g}`" if frappe_major() < 16 else g for g in group_by)
	if order_by:
		kwargs["order_by"] = order_by
	elif group_by:
		kwargs["order_by"] = f"{group_by[0]} asc"
	if parent_doctype:
		kwargs["parent_doctype"] = parent_doctype
	if args.get("distinct"):
		kwargs["distinct"] = True

	try:
		rows = frappe.get_list(meta.name, **kwargs)
	except frappe.PermissionError as exc:
		raise ToolError(f"Permission denied: {exc}", denied=True) from exc
	except ToolError:
		raise
	except Exception as exc:
		frappe.db.rollback()
		raise ToolError(f"The query failed: {clip(exc, 400)}. Check field names and filter values.") from exc

	columns = []
	for name in names:
		df = meta.get_field(name)
		columns.append(
			{
				"fieldname": name,
				"label": (df.label if df else None) or frappe.unscrub(name),
				"fieldtype": df.fieldtype if df else None,
				"options": df.options if df and df.fieldtype in ("Link", "Currency") else None,
			}
		)
	reply = tabular_reply(ctx, columns, rows, title=meta.name, preview=args.get("preview_rows"))
	if len(rows) == limit and not group_by:
		reply["more"] = f"Returned exactly the limit ({limit}); there may be more rows. Use offset, a higher limit, or aggregates."
	return reply


def get_document(args, ctx):
	meta = get_meta_or_error(args.get("doctype"))
	name = args.get("name") or (meta.name if meta.issingle else None)
	if not name:
		raise ToolError("name is required.")
	if meta.istable:
		raise ToolError(f"{meta.name} is a child table. Open its parent document instead.")
	if not meta.issingle and not frappe.db.exists(meta.name, name):
		raise ToolError(f"{meta.name} '{name}' was not found. Use find_documents to look it up by text.")
	try:
		doc = frappe.get_doc(meta.name, name)
		doc.check_permission("read")
		doc.apply_fieldlevel_read_permissions()
	except frappe.PermissionError as exc:
		raise ToolError(f"The current user cannot read {meta.name} {name}.", denied=True) from exc

	wanted = set(args.get("fields") or [])
	max_child_rows = 100

	def row_dict(d, d_meta):
		out = {}
		for df in d_meta.fields:
			if df.fieldtype in SKIP_FIELDTYPES or df.fieldtype in ("Password", "Table", "Table MultiSelect"):
				continue
			if SECRET_FIELD_RE.search(df.fieldname):
				continue
			value = d.get(df.fieldname)
			if value in (None, "", 0, 0.0) and df.fieldtype != "Check":
				continue
			if wanted and d_meta is meta and df.fieldname not in wanted:
				continue
			value = clean_value(value)
			if isinstance(value, str):
				if df.fieldtype in ("Text Editor", "HTML Editor", "Markdown Editor"):
					value = frappe.utils.strip_html_tags(value)
				value = clip(value, 3000)
			out[df.fieldname] = value
		return out

	data = {"doctype": meta.name, "name": doc.name}
	if not meta.issingle:
		data.update(
			{
				"owner": doc.owner,
				"creation": str(doc.creation),
				"modified": str(doc.modified),
				"docstatus": doc.docstatus,
			}
		)
	data.update(row_dict(doc, meta))
	for df in meta.get_table_fields():
		if wanted and df.fieldname not in wanted:
			continue
		rows = doc.get(df.fieldname) or []
		if not rows:
			continue
		child_meta = frappe.get_meta(df.options)
		data[df.fieldname] = [row_dict(r, child_meta) for r in rows[:max_child_rows]]
		if len(rows) > max_child_rows:
			data[f"{df.fieldname}_note"] = f"{len(rows)} rows in total; first {max_child_rows} shown."
	return data


def find_documents(args, ctx):
	"""Resolve a name the user typed ("Al Noor Trading") to actual document IDs."""
	meta = get_meta_or_error(args.get("doctype"))
	require_read(meta.name, meta)
	text = (args.get("text") or "").strip()
	if not text:
		raise ToolError("text is required.")
	allowed = permitted_fields(meta)
	search_fields = ["name"]
	if meta.title_field:
		search_fields.append(meta.title_field)
	if meta.get("search_fields"):
		search_fields += [s.strip() for s in meta.search_fields.split(",")]
	search_fields = [
		s for s in dict.fromkeys(search_fields) if s == "name" or (meta.get_field(s) and s in allowed)
	][:6]
	or_filters = [[f, "like", f"%{text}%"] for f in search_fields]
	limit = min(max(1, to_int(args.get("limit"), 10) or 10), 50)
	rows = frappe.get_list(
		meta.name, fields=search_fields, or_filters=or_filters, limit_page_length=limit, as_list=True
	)
	columns = [{"fieldname": f, "label": frappe.unscrub(f)} for f in search_fields]
	return tabular_reply(ctx, columns, rows, title=f"{meta.name} matching '{text}'")


FILTER_SCHEMA = {
	"description": (
		"Filters as a list of [field, operator, value]. Operators: =, !=, >, <, >=, <=, like, not like, in, "
		"not in, between, is (value 'set' or 'not set'). Dates are 'YYYY-MM-DD'. "
		"Example: [[\"docstatus\",\"=\",1],[\"posting_date\",\"between\",[\"2026-01-01\",\"2026-03-31\"]]]"
	),
	"type": "array",
	"items": {"type": "array"},
}

TOOLS = [
	Tool(
		"search_doctypes",
		"""Find DocTypes (the tables of this ERP) by keyword or module, limited to the ones the current user
		may read. Use this first whenever you are not certain of the exact DocType name, including for custom
		DocTypes that exist only on this site.""",
		{
			"type": "object",
			"properties": {
				"query": {"type": "string", "description": "Keyword, e.g. 'invoice', 'stock', 'leave'."},
				"module": {"type": "string", "description": "Optional exact module name, e.g. 'Accounts'."},
			},
		},
		search_doctypes,
		label="Looking up document types",
	),
	Tool(
		"get_doctype_info",
		"""Get the fields of a DocType: field names, labels, types, link targets, select options and child
		tables. Always call this before querying a DocType you have not inspected in this conversation;
		field names differ between sites because of customisation, so never guess them.""",
		{
			"type": "object",
			"properties": {"doctype": {"type": "string"}},
			"required": ["doctype"],
		},
		get_doctype_info,
		label="Reading the structure of",
	),
	Tool(
		"query_data",
		"""Query records of one DocType with the current user's permissions (role, user and field level
		permissions all apply). Supports filters, grouping and aggregates, so prefer it for totals, counts,
		top-N lists and time series on a single DocType. For child tables (for example Sales Invoice Item)
		pass parent_doctype. For submittable DocTypes filter docstatus = 1 unless drafts or cancelled
		documents are wanted. The full result is stored and returned as result_id; you only see a sample.""",
		{
			"type": "object",
			"properties": {
				"doctype": {"type": "string"},
				"fields": {"type": "array", "items": {"type": "string"}, "description": "Plain field names to return."},
				"filters": FILTER_SCHEMA,
				"or_filters": {**FILTER_SCHEMA, "description": "Same format as filters; any one may match."},
				"aggregates": {
					"type": "array",
					"description": "Aggregate columns, e.g. [{\"function\":\"sum\",\"field\":\"grand_total\",\"as\":\"total\"}].",
					"items": {
						"type": "object",
						"properties": {
							"function": {"type": "string", "enum": sorted(AGG_FUNCTIONS)},
							"field": {"type": "string"},
							"as": {"type": "string"},
						},
						"required": ["function", "field"],
					},
				},
				"group_by": {"type": "array", "items": {"type": "string"}, "description": "Fields to group by."},
				"order_by": {"type": "string", "description": "e.g. 'total desc' or 'posting_date asc'."},
				"limit": {"type": "integer", "description": "Max rows (default 100)."},
				"offset": {"type": "integer"},
				"distinct": {"type": "boolean"},
				"parent_doctype": {"type": "string", "description": "Required context when doctype is a child table."},
				"preview_rows": {"type": "integer", "description": "How many rows to show you (default 30, max 200)."},
			},
			"required": ["doctype"],
		},
		query_data,
		label="Querying",
	),
	Tool(
		"get_document",
		"""Read one document in full, including its child table rows, with the current user's permissions.
		Use it to answer questions about a specific record or the document the user is looking at.
		For Single DocTypes (settings) pass the DocType name as name.""",
		{
			"type": "object",
			"properties": {
				"doctype": {"type": "string"},
				"name": {"type": "string", "description": "Document ID, e.g. 'ACC-SINV-2026-00012'."},
				"fields": {"type": "array", "items": {"type": "string"}, "description": "Optional: only these fields."},
			},
			"required": ["doctype"],
		},
		get_document,
		label="Opening",
	),
	Tool(
		"find_documents",
		"""Find documents by a name or text the user mentioned (for example a customer or item name) and get
		their exact IDs. Use this before filtering on a Link field when you only know an approximate name.""",
		{
			"type": "object",
			"properties": {
				"doctype": {"type": "string"},
				"text": {"type": "string"},
				"limit": {"type": "integer"},
			},
			"required": ["doctype", "text"],
		},
		find_documents,
		label="Searching",
	),
]
