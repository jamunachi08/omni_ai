"""Entry checks: fixed rules that run when a document is saved or submitted.

The rules are records of AI Assistant Check Rule. They are evaluated by plain code, never by the AI
model, so the same entry always gets the same verdict, instantly and at no cost. A failed rule
either warns (the user sees a message and may continue) or blocks (the save is stopped).

Hooked on every DocType through doc_events; a DocType without rules costs one cached lookup.
"""

import json
import re
import statistics

import frappe
from frappe import _
from frappe.utils import add_days, flt, fmt_money, getdate, now, today

RULE = "AI Assistant Check Rule"
LOG = "AI Assistant Check Log"
CACHE_KEY = "omni_ai:check_rules"
LOG_KEY = "omni_ai:check_log"
MAX_BUFFERED = 5000
NUMERIC = ("Int", "Float", "Currency", "Percent", "Check")
RULE_FIELDS = [
	"name", "title", "document_type", "rule_type", "action", "run_on", "fields", "ignore_case_and_spacing", "field",
	"operator", "value", "days_back", "allow_future", "table_field", "reference", "direction", "tolerance_percent",
	"group_field", "factor", "message",
]  # fmt: skip


# -- loading -------------------------------------------------------------------------


def clear_cache():
	frappe.cache.delete_value(CACHE_KEY)


def load_rules() -> dict:
	"""{doctype: [rule, ...]} for every enabled rule, cached."""
	rules = frappe.cache.get_value(CACHE_KEY)
	if rules is not None:
		return rules
	rules = {}
	rows = frappe.get_all(RULE, filters={"enabled": 1}, fields=RULE_FIELDS, limit=0)
	overrides = {}
	if rows:
		for row in frappe.get_all(
			"AI Assistant Role", filters={"parenttype": RULE, "parent": ("in", [r.name for r in rows])}, fields=["parent", "role"], limit=0
		):
			overrides.setdefault(row.parent, []).append(row.role)
	for row in rows:
		row = dict(row)
		row["override_roles"] = overrides.get(row["name"], [])
		rules.setdefault(row["document_type"], []).append(row)
	frappe.cache.set_value(CACHE_KEY, rules)
	return rules


def _active(doc) -> list:
	if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch or frappe.flags.in_setup_wizard:
		return []
	if doc.doctype.startswith("AI Assistant") or doc.flags.get("skip_entry_checks"):
		return []
	try:
		rules = load_rules().get(doc.doctype)
		if not rules:
			return []
		if not frappe.get_cached_doc("AI Assistant Settings").get("enable_entry_checks"):
			return []
		return rules
	except Exception:
		return []  # the rule tables are not there yet (first migrate)


# -- helpers -------------------------------------------------------------------------


def norm(value) -> str:
	"""Text reduced to letters and digits in lower case, for 'same apart from spelling details' comparisons."""
	return re.sub(r"[\W_]+", "", str(value or "").casefold())


def split_names(text) -> list:
	return [part.strip() for part in re.split(r"[,\n]", text or "") if part.strip()]


def label_of(meta, fieldname: str) -> str:
	df = meta.get_field(fieldname)
	return _(df.label) if df and df.label else fieldname


def is_empty(value, df=None) -> bool:
	if value is None or value == "":
		return True
	if df and df.fieldtype in ("Float", "Currency", "Int", "Percent"):
		return flt(value) == 0
	return False


def targets(doc, path: str):
	"""Yield (row_label, holder, fieldname, meta) for 'field' or 'table.field'."""
	if "." in path:
		table, fieldname = path.split(".", 1)
		rows = doc.get(table) or []
		for row in rows:
			yield _("Row {0}: ").format(row.idx), row, fieldname, frappe.get_meta(row.doctype)
	else:
		yield "", doc, path, doc.meta


# -- the checks ----------------------------------------------------------------------


def check_duplicate(doc, rule) -> list:
	fields = [f for f in split_names(rule.get("fields")) if "." not in f]
	values = {f: doc.get(f) for f in fields}
	if not fields or any(is_empty(v) for v in values.values()):
		return []
	filters = [["docstatus", "<", 2]]
	if not doc.is_new():
		filters.append(["name", "!=", doc.name])
	if rule.get("ignore_case_and_spacing"):
		first = fields[0]
		words = sorted(re.findall(r"[^\W_]{3,}", str(values[first])), key=len, reverse=True)
		if words:
			filters.append([first, "like", f"%{words[0]}%"])
		else:
			filters.append([first, "=", values[first]])
		found = None
		wanted = {f: norm(v) for f, v in values.items()}
		for row in frappe.get_all(doc.doctype, filters=filters, fields=["name", *fields], limit=300):
			if all(norm(row.get(f)) == wanted[f] for f in fields):
				found = row.name
				break
	else:
		filters += [[f, "=", v] for f, v in values.items()]
		found = (frappe.get_all(doc.doctype, filters=filters, pluck="name", limit=1) or [None])[0]
	if not found:
		return []
	shown = ", ".join(f"{label_of(doc.meta, f)} {frappe.bold(str(values[f]))}" for f in fields)
	return [_("{0} already exists on {1} {2}.").format(shown, _(doc.doctype), frappe.bold(found))]


def check_required(doc, rule) -> list:
	out = []
	for path in split_names(rule.get("fields")):
		for prefix, holder, fieldname, meta in targets(doc, path):
			if is_empty(holder.get(fieldname), meta.get_field(fieldname)):
				out.append(prefix + _("{0} is missing.").format(frappe.bold(label_of(meta, fieldname))))
	return out[:8]


def _compare(left, operator: str, right, numeric: bool, is_date: bool) -> bool:
	if operator in ("in", "not in"):
		options = [norm(v) for v in (right if isinstance(right, list) else split_names(str(right)))]
		inside = norm(left) in options
		return inside if operator == "in" else not inside
	if is_date:
		left, right = getdate(left) if left else None, getdate(right) if right else None
		if left is None or right is None:
			return True  # nothing to compare yet
	elif numeric:
		left, right = flt(left), flt(right)
	else:
		left, right = norm(left), norm(right)
	return {
		"=": left == right,
		"!=": left != right,
		">": left > right,
		">=": left >= right,
		"<": left < right,
		"<=": left <= right,
	}[operator]


def check_condition(doc, rule) -> list:
	operator, raw = rule.get("operator"), rule.get("value") or ""
	if not operator or not rule.get("field"):
		return []
	out = []
	for prefix, holder, fieldname, meta in targets(doc, rule["field"]):
		df = meta.get_field(fieldname)
		if not df:
			continue
		left = holder.get(fieldname)
		if raw.startswith("field:"):
			other = raw[6:].strip()
			right = holder.get(other) if holder.meta.get_field(other) else doc.get(other)
			shown = label_of(holder.meta if holder.meta.get_field(other) else doc.meta, other)
		else:
			right, shown = raw, raw
		numeric = df.fieldtype in NUMERIC
		is_date = df.fieldtype in ("Date", "Datetime")
		try:
			ok = _compare(left, operator, right, numeric, is_date)
		except Exception:
			continue
		if not ok:
			actual = left if left not in (None, "") else _("empty")
			out.append(prefix + _("{0} must be {1} {2} (it is {3}).").format(frappe.bold(label_of(meta, fieldname)), operator, shown, actual))
	return out[:8]


def check_date_window(doc, rule) -> list:
	fieldname = rule.get("field")
	value = doc.get(fieldname) if fieldname else None
	if not value:
		return []
	date, label = getdate(value), frappe.bold(label_of(doc.meta, fieldname))
	if not rule.get("allow_future") and date > getdate(today()):
		return [_("{0} {1} is in the future.").format(label, frappe.format(date, {"fieldtype": "Date"}))]
	days = int(rule.get("days_back") or 0)
	if days > 0 and date < getdate(add_days(today(), -days)):
		return [_("{0} {1} is more than {2} days in the past.").format(label, frappe.format(date, {"fieldtype": "Date"}), days)]
	return []


def check_price(doc, rule) -> list:
	rows = [r for r in (doc.get(rule.get("table_field") or "items") or []) if r.get("item_code") and flt(r.get("rate"))]
	if not rows:
		return []
	reference = rule.get("reference") or "Last Purchase Rate"
	direction = rule.get("direction") or "Above or below"
	tolerance = abs(flt(rule.get("tolerance_percent")))
	items = {}
	if reference != "Price List Rate":
		for item in frappe.get_all(
			"Item", filters={"name": ("in", list({r.item_code for r in rows}))}, fields=["name", "last_purchase_rate", "valuation_rate"], limit=0
		):
			items[item.name] = item
	currency = frappe.get_cached_value("Company", doc.get("company"), "default_currency") if doc.get("company") else None
	out = []
	for row in rows:
		if reference == "Price List Rate":
			actual, expected, name, money = flt(row.get("rate")), flt(row.get("price_list_rate")), _("the price list rate"), doc.get("currency")
		else:
			# company currency, per stock unit, so it can be compared with what is stored on the item
			base = flt(row.get("base_rate")) or flt(row.get("rate")) * (flt(doc.get("conversion_rate")) or 1)
			actual = base / (flt(row.get("conversion_factor")) or 1)
			item = items.get(row.item_code) or {}
			money = currency
			if reference == "Valuation Rate":
				expected = 0
				if row.get("warehouse"):
					expected = flt(frappe.db.get_value("Bin", {"item_code": row.item_code, "warehouse": row.warehouse}, "valuation_rate"))
				expected = expected or flt(item.get("valuation_rate"))
				name = _("the cost (valuation rate)")
			else:
				expected, name = flt(item.get("last_purchase_rate")), _("the last purchase rate")
		if expected <= 0:
			continue  # nothing to compare with yet
		change = (actual - expected) / expected * 100
		if (change > tolerance and direction != "Below only") or (change < -tolerance and direction != "Above only"):
			out.append(
				_("Row {0}, {1}: the rate {2} is {3}% {4} {5} ({6}).").format(
					row.idx,
					frappe.bold(row.item_code),
					fmt_money(actual, currency=money),
					round(abs(change), 1),
					_("above") if change > 0 else _("below"),
					name,
					fmt_money(expected, currency=money),
				)
			)
	return out[:8]


def check_amount(doc, rule) -> list:
	fieldname = rule.get("field")
	value = flt(doc.get(fieldname)) if fieldname else 0
	if value <= 0:
		return []
	filters = [["docstatus", "=", 1] if doc.meta.is_submittable else ["docstatus", "<", 2]]
	if not doc.is_new():
		filters.append(["name", "!=", doc.name])
	group = rule.get("group_field")
	if group:
		if not doc.get(group):
			return []
		filters.append([group, "=", doc.get(group)])
	history = [flt(v) for v in frappe.get_all(doc.doctype, filters=filters, pluck=fieldname, order_by="creation desc", limit=50) if flt(v) > 0]
	if len(history) < 5:
		return []  # too little history to call anything unusual
	usual = statistics.median(history)
	factor = flt(rule.get("factor")) or 5
	if usual <= 0 or value <= usual * factor:
		return []
	whom = f" {_('for')} {frappe.bold(str(doc.get(group)))}" if group else ""
	return [
		_("{0} {1} is {2} times the usual amount{3} (typically {4}, from the last {5} documents).").format(
			frappe.bold(label_of(doc.meta, fieldname)), fmt_money(value), round(value / usual, 1), whom, fmt_money(usual), len(history)
		)
	]


CHECKS = {
	"Duplicate": check_duplicate,
	"Required": check_required,
	"Condition": check_condition,
	"Date Window": check_date_window,
	"Price Deviation": check_price,
	"Unusual Amount": check_amount,
}


def evaluate(doc, rule) -> list:
	"""Failure messages of one rule for a document (empty when it passes)."""
	failures = CHECKS[rule["rule_type"]](doc, rule)
	if failures and (rule.get("message") or "").strip():
		return [frappe.utils.escape_html(rule["message"].strip())]
	return failures


# -- running -------------------------------------------------------------------------


def run(doc, event: str):
	rules = [r for r in _active(doc) if r.get("run_on") == event]
	if not rules:
		return
	roles = set(frappe.get_roles())
	warnings, blocks = [], []
	for rule in rules:
		try:
			failures = evaluate(doc, rule)
		except Exception:
			frappe.log_error(title=f"Entry check failed to run: {rule.get('title')}")
			continue  # a broken rule must never stop people from working
		if not failures:
			continue
		blocking = rule.get("action") == "Block" and not (roles & set(rule.get("override_roles") or []))
		(blocks if blocking else warnings).extend(failures)
		buffer_log(rule, doc, "Blocked" if blocking else "Warned", failures)
	if blocks:
		frappe.throw(_as_list(blocks + warnings), title=_("This entry was stopped"))
	if warnings:
		frappe.msgprint(_as_list(warnings), title=_("Please check before you continue"), indicator="orange")


def _as_list(messages: list) -> str:
	if len(messages) == 1:
		return messages[0]
	return "<ul>" + "".join(f"<li>{m}</li>" for m in messages) + "</ul>"


def on_validate(doc, method=None):
	run(doc, "Save")


def on_before_submit(doc, method=None):
	run(doc, "Submit")


# -- log -----------------------------------------------------------------------------
# A blocked save is rolled back, which would take a database log row with it. Hits are therefore
# buffered in Redis and written to the log table later (hourly, and when the Control Room opens).


def buffer_log(rule, doc, outcome: str, failures: list):
	try:
		entry = {
			"rule": rule.get("name"),
			"rule_title": rule.get("title"),
			"document_type": doc.doctype,
			"document_name": None if doc.is_new() else doc.name,
			"user": frappe.session.user,
			"outcome": outcome,
			"message": frappe.utils.strip_html(" ".join(failures))[:900],
			"logged_on": now(),
		}
		frappe.cache.rpush(LOG_KEY, json.dumps(entry))
		if frappe.cache.llen(LOG_KEY) > MAX_BUFFERED:
			frappe.cache.ltrim(LOG_KEY, -MAX_BUFFERED, -1)
	except Exception:
		pass


def flush_log() -> int:
	"""Move buffered hits into the log table. Returns how many rows were written."""
	written, seen = 0, set()
	try:
		for _i in range(MAX_BUFFERED):
			raw = frappe.cache.lpop(LOG_KEY)
			if not raw:
				break
			entry = json.loads(raw)
			# the same warning on every save of a draft is one finding, not twenty
			key = (entry.get("rule"), entry.get("document_type"), entry.get("document_name"), entry.get("outcome"), entry.get("message"), (entry.get("logged_on") or "")[:13])
			if key in seen:
				continue
			seen.add(key)
			if entry.get("document_name") and not frappe.db.exists(entry["document_type"], entry["document_name"]):
				entry["document_name"] = None
			doc = frappe.get_doc({"doctype": LOG, **entry})
			doc.flags.ignore_permissions = True
			doc.flags.ignore_links = True
			doc.insert()
			written += 1
	except Exception:
		frappe.log_error(title="Entry check log could not be written")
	return written
