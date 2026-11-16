"""Guarded read-only SQL.

SQL is the one path that does not go through Frappe's row-level permission
engine, so it is locked down in layers:

1. Only roles chosen in settings may use it at all.
2. One statement, SELECT (or WITH ... SELECT) only; comments, write keywords,
   file access, sleep/benchmark and system schemas are rejected.
3. Every table must be a backtick-quoted DocType table and the user needs
   read permission on that DocType (or on a parent, for child tables). Any
   other name that matches a real table in the database is rejected wherever
   it appears, so internal tables cannot be reached through comma joins,
   sub-queries or quoted names.
4. Secrets tables and credential columns are always blocked, and credential
   columns are also removed from results (so `SELECT *` cannot return them).
5. A row cap and a statement timeout are enforced.

The query is scanned with a small scanner of our own rather than a general SQL
parser, so that what we treat as a string literal is exactly what the database
treats as one. Backslashes are refused for the same reason.
"""

import re

import frappe
from omni_ai.tools.base import Tool, ToolError, columns_from_names, tabular_reply
from omni_ai.tools.data import HIDDEN_DOCTYPES, SECRET_FIELD_RE, WITHHELD_MESSAGE, can_read, withheld
from omni_ai.utils import can_use_sql, clip, to_int

STATEMENT_TIMEOUT = 45  # seconds

FORBIDDEN_WORDS = (
	"insert", "update", "delete", "drop", "alter", "create", "truncate", "rename", "grant", "revoke",
	"call", "handler", "load_file", "load", "outfile", "dumpfile", "benchmark", "sleep", "get_lock",
	"release_lock", "kill", "shutdown", "prepare", "execute", "deallocate", "lock", "unlock",
	"information_schema", "performance_schema", "mysql", "pg_catalog", "pg_sleep", "sys",
	"into", "procedure", "analyse", "set",
)  # fmt: skip
FORBIDDEN_RE = re.compile(r"\b(" + "|".join(FORBIDDEN_WORDS) + r")\b", re.I)
SECRET_COLUMNS_RE = SECRET_FIELD_RE
TABLE_RE = re.compile(r"`tab([^`]+)`")
QUOTED_NAME_RE = re.compile(r"`([^`]*)`")
WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
BARE_TABLE_RE = re.compile(r"\b(?:from|join)\s+([A-Za-z_][\w$.]*)", re.I)
CTE_RE = re.compile(r"(?:\bwith\b|,)\s*([A-Za-z_]\w*)\s+as\s*\(", re.I)
TRAILING_LIMIT_RE = re.compile(r"\blimit\s+\d+(\s*,\s*\d+|\s+offset\s+\d+)?\s*$", re.I)
PLACEHOLDER_RE = re.compile(r"%\((\w+)\)s")


PERCENT_RE = re.compile(r"%\(\w+\)s|%%|%")


class SQLGuardError(ToolError):
	pass


def escape_percents(query: str) -> str:
	"""Double every literal % (keeping %(name)s placeholders) so the driver can format the query."""
	return PERCENT_RE.sub(lambda m: "%%" if m.group(0) == "%" else m.group(0), query)


def unescape_percents(query: str) -> str:
	"""Plain form for queries that run without parameter formatting."""
	return query.replace("%%", "%")


def _skeleton(query: str) -> str:
	"""The query with string literals and backtick-quoted names blanked out, for keyword checks.

	Follows MariaDB's own rules: a literal starts at a quote and ends at the next matching quote,
	a doubled quote stands for the quote itself. (Backslash escapes are refused before this runs.)
	"""
	out, i, n = [], 0, len(query)
	while i < n:
		ch = query[i]
		if ch in ("'", '"', "`"):
			j = i + 1
			while True:
				j = query.find(ch, j)
				if j == -1:
					raise SQLGuardError("The query has an unterminated quote.")
				if j + 1 < n and query[j + 1] == ch:
					j += 2
					continue
				break
			out.append(" `` " if ch == "`" else " '' ")
			i = j + 1
		else:
			out.append(ch)
			i += 1
	return "".join(out)


def _database_tables() -> set:
	"""Lower-cased names of every table in the site database (cached briefly)."""
	tables = frappe.cache.get_value("omni_ai:db_tables")
	if not tables:
		tables = [t.lower() for t in frappe.db.get_tables(cached=False)]
		try:
			# every schema this database user can see is off limits too
			tables += [str(r[0]).lower() for r in frappe.db.sql("show databases")]
		except Exception:
			pass
		frappe.cache.set_value("omni_ai:db_tables", tables, expires_in_sec=300)
	return set(tables)


def validate_select(query: str, check_permissions: bool = True) -> dict:
	"""Raise SQLGuardError unless `query` is a single safe SELECT. Returns {query, doctypes, placeholders}."""
	query = (query or "").strip().rstrip(";").strip()
	if not query:
		raise SQLGuardError("The query is empty.")
	if len(query) > 20000:
		raise SQLGuardError("The query is too long.")
	if "\\" in query:
		raise SQLGuardError("Backslashes are not allowed in assistant queries.")
	if "\x00" in query:
		raise SQLGuardError("The query contains an invalid character.")
	skeleton = _skeleton(query)
	if "/*" in skeleton or "--" in skeleton or "#" in skeleton:
		raise SQLGuardError("SQL comments are not allowed.")
	if ";" in skeleton:
		raise SQLGuardError("Exactly one SQL statement is allowed.")

	first = skeleton.strip().lstrip("(").strip().split(None, 1)[0].lower() if skeleton.strip() else ""
	if first not in ("select", "with"):
		raise SQLGuardError("Only SELECT queries are allowed.")
	if first == "with" and frappe.db.db_type not in ("mariadb", "postgres"):
		raise SQLGuardError("WITH queries are not supported on this database.")

	bad = FORBIDDEN_RE.search(skeleton)
	if bad:
		raise SQLGuardError(f"'{bad.group(1)}' is not allowed in assistant queries. Only read-only SELECT is permitted.")
	if re.search(r"@@|@\w+\s*:=", skeleton):
		raise SQLGuardError("Server and user variables are not allowed.")
	if re.search(r"\bfor\s+(update|share)\b", skeleton, re.I):
		raise SQLGuardError("Locking reads are not allowed.")
	secret = SECRET_COLUMNS_RE.search(query)
	if secret:
		raise SQLGuardError(f"Column '{secret.group(0)}' holds credentials and cannot be queried.")

	cte_names = {m.group(1).lower() for m in CTE_RE.finditer(skeleton)}
	for match in BARE_TABLE_RE.finditer(skeleton):
		name = match.group(1).lower()
		if name in cte_names or name in ("dual",):
			continue
		raise SQLGuardError(
			f"Table '{match.group(1)}' must be written as a backtick-quoted DocType table, for example `tabSales Invoice`."
		)

	# Nothing but permitted DocType tables may be named, in any position and however it is quoted.
	tables = _database_tables()
	if "``" in query:
		raise SQLGuardError("Empty or doubled backticks are not allowed.")
	for name in QUOTED_NAME_RE.findall(query):
		if name.startswith("tab"):
			continue
		if name.startswith("__") or name.lower() in tables:
			raise SQLGuardError(f"`{name}` is not available to the assistant.")
		if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_ ]*", name):
			raise SQLGuardError(
				f"`{name}`: quoted column names and aliases may contain only letters, numbers, spaces and underscores."
			)
	if re.search(r"``\s*\.\s*``", skeleton) and re.search(r"`(?!tab)[^`]*`\s*\.\s*`", query):
		raise SQLGuardError("Schema-qualified names are not allowed. Use `tab<DocType>` tables only.")
	# double quotes are string literals on MariaDB but identifiers on Postgres (or with ANSI_QUOTES)
	for name in re.findall(r'"([^"]*)"', query):
		if name.startswith(("__", "tab")) or name.lower() in tables:
			raise SQLGuardError(f'"{name}" is not available to the assistant.')
	for word in set(WORD_RE.findall(skeleton)):
		if word.startswith("__") or word.lower() in tables:
			raise SQLGuardError(
				f"'{word}' is a database table. Only backtick-quoted DocType tables such as `tabSales Invoice` may be used."
			)

	doctypes = sorted(set(TABLE_RE.findall(query)))
	if not doctypes and "from" in skeleton.lower():
		raise SQLGuardError("Reference tables as `tab<DocType>`, for example `tabSales Invoice`.")
	for doctype in doctypes:
		if doctype in HIDDEN_DOCTYPES or doctype.startswith("__"):
			raise SQLGuardError(f"`tab{doctype}` is not available to the assistant.")
		if not frappe.db.exists("DocType", doctype):
			raise SQLGuardError(f"`tab{doctype}` is not a DocType table. Check the DocType name with search_doctypes.")
		meta = frappe.get_meta(doctype)
		if meta.issingle:
			raise SQLGuardError(f"{doctype} is a Single DocType and has no table. Use get_document instead.")
		if getattr(meta, "is_virtual", 0):
			raise SQLGuardError(f"{doctype} is a virtual DocType and has no table.")
		if check_permissions and withheld(doctype, meta):
			raise SQLGuardError(WITHHELD_MESSAGE.format(doctype), denied=True)
		if check_permissions and not can_read(doctype, meta):
			raise SQLGuardError(f"The current user does not have permission to read {doctype}.", denied=True)

	return {"query": query, "doctypes": doctypes, "placeholders": sorted(set(PLACEHOLDER_RE.findall(query)))}


def run_select(query: str, values: dict = None, limit: int = 1000) -> tuple:
	"""Execute a validated SELECT with a row cap and a time limit. Returns (column_names, rows)."""
	limited = query
	if not TRAILING_LIMIT_RE.search(query):
		limited = f"{query}\nLIMIT {int(limit) + 1}"
	prefix = ""
	if frappe.db.db_type == "mariadb":
		prefix = f"SET STATEMENT max_statement_time={STATEMENT_TIMEOUT} FOR "
	try:
		# Passing values (even an empty dict) makes the driver %-format the query, so only pass real ones.
		args = (values,) if values else ()
		try:
			rows = frappe.db.sql(prefix + limited, *args, as_list=True)
		except Exception as exc:
			# MySQL (not MariaDB) does not know SET STATEMENT: retry plain.
			if prefix and "syntax" in str(exc).lower():
				frappe.db.rollback()
				rows = frappe.db.sql(limited, *args, as_list=True)
			else:
				raise
		description = frappe.db.get_description() if hasattr(frappe.db, "get_description") else None
		if description is None:
			description = frappe.db._cursor.description
		names = [d[0] for d in (description or [])]
		# credential columns never leave the database, whatever the query looked like (e.g. SELECT *)
		hidden = [i for i, name in enumerate(names) if SECRET_COLUMNS_RE.search(str(name))]
		if hidden:
			keep = [i for i in range(len(names)) if i not in set(hidden)]
			names = [names[i] for i in keep]
			rows = [[row[i] for i in keep] for row in rows]
	except SQLGuardError:
		raise
	except KeyError as exc:
		frappe.db.rollback()
		raise ToolError(f"The query uses the parameter {exc} but no value was given for it.") from exc
	except Exception as exc:
		frappe.db.rollback()
		message = str(exc)
		if "max_statement_time" in message or "1969" in message:
			raise ToolError(
				f"The query took longer than {STATEMENT_TIMEOUT} seconds and was stopped. Narrow the date range or add filters."
			) from exc
		raise ToolError(f"SQL error: {clip(message, 500)}") from exc
	return names, [list(r) for r in rows]


def run_sql(args, ctx):
	if not can_use_sql(ctx.user):
		raise ToolError(
			"SQL access is not enabled for this user. Use query_data and run_report instead, "
			"or tell the user that an administrator can enable SQL for their role.",
			denied=True,
		)
	checked = validate_select(args.get("query"))
	values = args.get("values") or {}
	missing = [p for p in checked["placeholders"] if p not in values]
	if missing:
		raise ToolError(f"Provide values for: {', '.join(missing)}.")
	limit = min(max(1, to_int(args.get("limit"), 1000) or 1000), ctx.max_rows)
	query = escape_percents(checked["query"]) if values else unescape_percents(checked["query"])
	names, rows = run_select(query, values, limit)
	extra = None
	if len(rows) > limit:
		rows = rows[:limit]
		extra = {"more": f"More than {limit} rows matched; only the first {limit} were kept. Aggregate or filter further."}
	return tabular_reply(
		ctx, columns_from_names(names), rows, title=args.get("title") or "Query result",
		preview=args.get("preview_rows"), extra=extra,
	)  # fmt: skip


TOOLS = [
	Tool(
		"run_sql",
		"""Run one read-only SELECT against the site database, for analysis that needs joins, sub-queries or
		calculations that query_data cannot express. Rules: a single SELECT (WITH is allowed), no comments,
		every table written as a backtick-quoted DocType table such as `tabSales Invoice` and
		`tabSales Invoice Item` (child rows join on child.parent = parent.name). The user needs read
		permission on every DocType used, but row-level User Permissions are NOT applied, so mention that
		when it matters. Filter docstatus = 1 for submitted documents. Use %(name)s placeholders with
		`values` for user-supplied text; literal percent signs are escaped for you. The full result is stored as result_id; you see a sample.""",
		{
			"type": "object",
			"properties": {
				"query": {"type": "string", "description": "The SELECT statement."},
				"values": {"type": "object", "description": "Values for %(name)s placeholders."},
				"title": {"type": "string", "description": "Short name for the result."},
				"limit": {"type": "integer", "description": "Max rows to keep (default 1000)."},
				"preview_rows": {"type": "integer"},
			},
			"required": ["query"],
		},
		run_sql,
		label="Running analysis",
		enabled=lambda ctx: can_use_sql(ctx.user),
	)
]
