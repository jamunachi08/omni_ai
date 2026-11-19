"""The 1.5.1 guards, against a small stand-in for Frappe: credential fields, `*` in SQL, report columns,
report roles, spreadsheet formulas and approved chat webhooks.

They run anywhere (no bench needed) and are skipped when real Frappe is importable, so `bench run-tests`
is not affected. They prove the guards' own logic; the bench tests in test_omni_ai.py cover the rest.
"""

import importlib
import sys
import types
import unittest
from types import SimpleNamespace

try:
	import frappe  # noqa: F401

	REAL_FRAPPE = True
except ImportError:
	REAL_FRAPPE = False


class _Loose(types.ModuleType):
	"""A module that hands out a harmless function for any name a module imports from it."""

	def __getattr__(self, name):
		if name.startswith("__"):
			raise AttributeError(name)
		return lambda *a, **k: None


# DocType -> table columns, and DocType -> roles that may read it
COLUMNS = {
	"User": ["name", "email", "full_name", "api_key", "enabled"],
	"Sales Invoice": ["name", "customer", "grand_total", "docstatus"],
	"Company": ["name", "company_name", "custom_zatca_csid"],
}
READ_ROLES = {
	"Sales Invoice": {"Accounts User", "Accounts Manager", "Sales User"},
	"Customer": {"Sales User", "Accounts User"},
	"ToDo": {"All"},
}


def install_fakes(state):
	fm = _Loose("frappe")
	fm.PermissionError = type("PermissionError", (Exception,), {})
	fm.ValidationError = type("ValidationError", (Exception,), {})
	fm._ = lambda text: text
	fm.local = SimpleNamespace(omni_ai_withheld=set(), message_log=[])
	fm.flags = SimpleNamespace(in_test=True)
	fm.cache = SimpleNamespace(get_value=lambda key: None, set_value=lambda *a, **k: None)
	fm.has_permission = lambda *a, **k: True
	fm.get_roles = lambda user=None: state["roles"]
	fm.get_meta = lambda doctype: SimpleNamespace(issingle=0, istable=0, is_virtual=0, fields=[], permissions=[])
	fm.get_cached_doc = lambda name: state["settings"]
	fm.db = SimpleNamespace(
		db_type="mariadb",
		exists=lambda doctype, name=None: doctype == "DocType" and name in COLUMNS,
		get_tables=lambda cached=False: [f"tab{d}" for d in COLUMNS] + ["__Auth", "tabSingles"],
		get_table_columns=lambda doctype: COLUMNS[doctype],
		sql=lambda *a, **k: [],
	)
	model = types.ModuleType("frappe.model")
	model.default_fields, model.optional_fields, model.no_value_fields = ("name", "owner"), (), ("Section Break",)
	permissions = types.ModuleType("frappe.permissions")
	permissions.get_doctype_roles = lambda doctype, access_type="read": READ_ROLES.get(doctype, set())
	utils = _Loose("frappe.utils")
	fm.utils, fm.model, fm.permissions = utils, model, permissions
	mods = {"frappe": fm, "frappe.model": model, "frappe.permissions": permissions, "frappe.utils": utils}
	for name in ("frappe.utils.xlsxutils", "frappe.desk", "frappe.desk.query_report"):
		mods[name] = _Loose(name)
	ours = [k for k in sys.modules if k == "omni_ai" or k.startswith("omni_ai.")]
	saved = {k: sys.modules.get(k) for k in [*mods, *ours]}
	for key in ours:
		sys.modules.pop(key)
	sys.modules.update(mods)

	def restore():
		for key in [k for k in sys.modules if k.startswith("omni_ai")]:
			sys.modules.pop(key)
		for key, value in saved.items():
			if value is None:
				sys.modules.pop(key, None)
			else:
				sys.modules[key] = value

	return restore


@unittest.skipIf(REAL_FRAPPE, "stand-in based; on a bench the real Frappe is used by test_omni_ai.py")
class TestGuards(unittest.TestCase):
	def setUp(self):
		self.state = {"roles": ["Accounts User", "Employee", "All"], "settings": SimpleNamespace(get=lambda key: None)}
		self.addCleanup(install_fakes(self.state))
		self.data = importlib.import_module("omni_ai.tools.data")
		self.sql = importlib.import_module("omni_ai.tools.sql")

	# -- credential field names
	def test_credential_names_custom_fields_included(self):
		for name in (
			"password", "api_secret", "consumer_secret", "app_secret", "client_secret", "access_token", "webhook_token",
			"custom_token", "custom_zatca_csid", "production_csid", "custom_basic_auth", "private_key", "api_key",
		):  # fmt: skip
			self.assertTrue(self.data.SECRET_FIELD_RE.search(name), name)

	def test_ordinary_names_are_not_mistaken_for_credentials(self):
		for name in ("input_tokens", "token_count", "secretary_name", "grand_total", "customer", "keyword"):
			self.assertFalse(self.data.SECRET_FIELD_RE.search(name), name)

	def test_mcp_tokens_are_hidden_from_the_assistant(self):
		self.assertIn("ERP MCP Token", self.data.HIDDEN_DOCTYPES)
		self.assertIn("ERP MCP Settings", self.data.HIDDEN_DOCTYPES)

	# -- SQL
	def assertRefused(self, query, fragment=""):
		with self.assertRaises(self.sql.SQLGuardError) as caught:
			self.sql.validate_select(query)
		self.assertIn(fragment, str(caught.exception))

	def test_union_cannot_rename_credential_columns(self):
		self.assertRefused("SELECT 1 AS a, 2, 3, 4, 5 UNION ALL SELECT * FROM `tabUser`", "credential columns")
		self.assertRefused("SELECT u.* FROM `tabUser` u", "credential columns")
		self.assertRefused("SELECT DISTINCT * FROM `tabCompany`", "credential columns")  # a ZATCA CSID column
		self.assertRefused("SELECT name, api_key FROM `tabUser`", "holds credentials")

	def test_star_still_works_where_there_is_nothing_to_hide(self):
		self.sql.validate_select("SELECT * FROM `tabSales Invoice` WHERE docstatus = 1")
		self.sql.validate_select("SELECT count(*) AS n FROM `tabUser`")
		self.sql.validate_select("SELECT name, 2 * 3 AS six FROM `tabUser`")

	# -- reports
	def test_report_columns_with_credential_names_are_dropped(self):
		reports = importlib.import_module("omni_ai.tools.reports")
		columns = [{"fieldname": "name"}, {"fieldname": "api_key"}, {"fieldname": "total"}]
		cols, rows = reports.drop_secret_columns(columns, [["a", "k1", 1], ["b", "k2", 2]])
		self.assertEqual([c["fieldname"] for c in cols], ["name", "total"])
		self.assertEqual(rows, [["a", 1], ["b", 2]])

	def test_saved_query_report_is_limited_to_roles_that_can_read_everything_in_it(self):
		reports = importlib.import_module("omni_ai.tools.reports")
		self.assertEqual(reports.report_roles(["Sales Invoice"], "u@x.com"), ["Accounts User"])  # not Employee/All
		self.state["roles"] = ["Sales User", "Accounts User"]
		self.assertEqual(reports.report_roles(["Sales Invoice", "Customer"], "u@x.com"), ["Accounts User", "Sales User"])
		self.assertEqual(reports.report_roles(["Sales Invoice", "ToDo"], "u@x.com"), ["Accounts User", "Sales User"])
		self.state["roles"] = ["Employee"]
		self.assertEqual(reports.report_roles(["Sales Invoice"], "u@x.com"), ["System Manager"])

	# -- exports
	def test_spreadsheet_formulas_in_data_are_neutralised(self):
		display = importlib.import_module("omni_ai.tools.display")
		self.assertEqual(display.safe_cell('=HYPERLINK("http://x")'), '\'=HYPERLINK("http://x")')
		self.assertEqual(display.safe_cell("@SUM(A1)"), "'@SUM(A1)")
		self.assertEqual(display.safe_cell("Al Noor Trading"), "Al Noor Trading")
		self.assertEqual(display.safe_cell(-5), -5)  # numbers stay numbers

	# -- schedules
	def test_only_listed_chat_webhooks_are_approved(self):
		delivery = importlib.import_module("omni_ai.engine.delivery")
		listed = "https://hooks.slack.com/services/T1/B1/abc\n\nhttps://chat.googleapis.com/v1/spaces/x/messages?key=k\n"
		self.state["settings"] = SimpleNamespace(get=lambda key: listed if key == "approved_chat_webhooks" else None)
		self.assertTrue(delivery.webhook_approved("https://hooks.slack.com/services/T1/B1/abc/"))
		self.assertFalse(delivery.webhook_approved("https://hooks.slack.com/services/T9/B9/attacker"))
		self.assertFalse(delivery.webhook_approved(""))


if __name__ == "__main__":
	unittest.main()
