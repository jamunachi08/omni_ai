"""Tests that run without network access: the provider client is replaced by a scripted fake.

Run with:  bench --site <site> run-tests --app omni_ai
"""

from unittest.mock import patch

import frappe

try:  # Frappe v16
	from frappe.tests import IntegrationTestCase as TestCase
except ImportError:  # Frappe v15
	from frappe.tests.utils import FrappeTestCase as TestCase

from omni_ai.engine.agent import sanitize_turn
from omni_ai.tools.reports import build_filter_script, default_js, normalise_report_result, resolve_default
from omni_ai.tools.sql import SQLGuardError, escape_percents, unescape_percents, validate_select


class FakeClient:
	"""Stands in for ClaudeClient. `script` is a list of replies; each reply is a list of content blocks."""

	script = []
	requests = []

	def __init__(self, *args, **kwargs):
		pass

	def stream(self, payload, on_text=None, on_block=None, should_stop=None):
		FakeClient.requests.append(payload)
		if "Write a title" in str(payload.get("system", "")):
			return {"content": [{"type": "text", "text": "Test title"}], "stop_reason": "end_turn", "usage": {}}
		blocks = FakeClient.script.pop(0) if FakeClient.script else [{"type": "text", "text": "ok"}]
		for index, block in enumerate(blocks):
			if block["type"] == "tool_use":
				block.setdefault("id", f"toolu_{len(FakeClient.requests)}_{index}")
			if on_block:
				on_block(block)
			if block["type"] == "text" and on_text:
				on_text(block["text"])
		stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
		return {"content": blocks, "stop_reason": stop, "usage": {"input_tokens": 10, "output_tokens": 5}, "model": "fake"}


class TestSQLGuard(TestCase):
	def test_allows_plain_selects(self):
		for query in (
			"select name from `tabToDo` where status = 'Open'",
			"with x as (select name from `tabToDo`) select count(*) from x",
			"select date_format(creation, '%Y-%m') m, count(*) from `tabToDo` group by m",
			"select name from `tabToDo` where description like '%delete; drop%'",
		):
			validate_select(query)

	def test_blocks_everything_else(self):
		for query in (
			"delete from `tabToDo`",
			"update `tabToDo` set status = 'Closed'",
			"select 1; drop table `tabToDo`",
			"select * from `tabToDo` -- comment",
			"select * from `tabToDo` /* comment */",
			"select * from tabToDo",
			"select * from `__Auth`",
			"select * from `tabSingles`",
			"select * from `tabOAuth Bearer Token`",
			"select password from `tabUser`",
			"select * from information_schema.tables",
			"select * from mysql.user",
			"select sleep(5)",
			"select load_file('/etc/passwd')",
			"select * from `tabToDo` into outfile '/tmp/x'",
			"select * from `tabToDo` for update",
			"select @@version",
			"",
		):
			with self.assertRaises(SQLGuardError, msg=query):
				validate_select(query)

	def test_percent_handling(self):
		query = "select date_format(creation, '%Y') y from `tabToDo` where name like %(n)s and x like '%%a'"
		escaped = escape_percents(query)
		self.assertIn("'%%Y'", escaped)
		self.assertIn("%(n)s", escaped)
		self.assertIn("'%%a'", escaped)
		self.assertEqual(unescape_percents("like '%%a'"), "like '%a'")


class TestHelpers(TestCase):
	def test_sanitize_turn_drops_unanswered_tool_calls(self):
		turn = [
			{"role": "assistant", "content": [{"type": "text", "text": "a"}, {"type": "tool_use", "id": "t1", "name": "x", "input": {}}]},
			{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "r"}]},
			{"role": "assistant", "content": [{"type": "tool_use", "id": "t2", "name": "x", "input": {}}, {"type": "text", "text": "b"}]},
		]
		clean = sanitize_turn(turn)
		self.assertEqual([m["role"] for m in clean], ["assistant", "user", "assistant"])
		self.assertEqual([b["type"] for b in clean[-1]["content"]], ["text"])
		self.assertEqual(sanitize_turn([{"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "x", "input": {}}]}]), [])

	def test_report_result_normalising(self):
		columns, rows = normalise_report_result(
			["Customer:Link/Customer:200", {"fieldname": "total", "label": "Total", "fieldtype": "Currency"}, {"fieldname": "h", "hidden": 1}],
			[{"customer": "A", "total": 5, "h": 1}, ["B", 7, 2]],
		)
		self.assertEqual([c["fieldname"] for c in columns], ["customer", "total"])
		self.assertEqual(columns[0]["options"], "Customer")
		self.assertEqual(rows, [["A", 5], ["B", 7]])

	def test_filter_script_is_built_from_data_only(self):
		script = build_filter_script(
			'My "Report"',
			[{"fieldname": "from_date", "label": "From </script>", "fieldtype": "Date", "default": "month_start"}],
		)
		self.assertIn('frappe.query_reports["My \\"Report\\""]', script)
		self.assertIn("frappe.datetime.month_start()", script)
		self.assertIn("reqd: 1", script)
		self.assertEqual(default_js("days_ago:30"), "frappe.datetime.add_days(frappe.datetime.get_today(), -30)")
		self.assertEqual(default_js("alert(1)"), '"alert(1)"')
		self.assertEqual(resolve_default("plain"), "plain")


class TestChat(TestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		settings = frappe.get_single("AI Assistant Settings")
		settings.api_key = "test"
		settings.enabled = 1
		settings.execution_mode = "Inline"
		settings.save()
		FakeClient.script, FakeClient.requests = [], []
		self.patcher = patch("omni_ai.engine.agent.ClaudeClient", FakeClient)
		self.patcher.start()

	def tearDown(self):
		self.patcher.stop()
		frappe.set_user("Administrator")

	def test_tool_loop_and_report_save(self):
		from omni_ai.api import chat

		name = "Omni Test Open ToDos"
		if frappe.db.exists("Report", name):
			frappe.delete_doc("Report", name, force=True)
		frappe.get_doc({"doctype": "ToDo", "description": "omni unit test"}).insert()
		FakeClient.script = [
			[{"type": "tool_use", "name": "query_data", "input": {"doctype": "ToDo", "aggregates": [{"function": "count", "field": "name", "as": "n"}]}}],
			[{"type": "tool_use", "name": "propose_report", "input": {
				"report_name": name, "report_type": "Query Report", "ref_doctype": "ToDo",
				"query": "select status, count(*) as todos from `tabToDo` group by status",
				"columns": [{"label": "Status", "fieldtype": "Data"}, {"label": "ToDos", "fieldtype": "Int"}],
			}}],
			[{"type": "text", "text": "Draft ready."}],
		]  # fmt: skip
		reply = chat.send("count todos and draft a report")
		data = chat.get_conversation(reply["conversation"])
		assistant = data["messages"][-1]
		self.assertEqual(assistant["status"], "Complete")
		self.assertEqual(assistant["content"], "Draft ready.")
		card = next(p for p in assistant["parts"] if p["type"] == "action")
		self.assertEqual(card["status"], "Pending")
		self.assertFalse(frappe.db.exists("Report", name))

		result = chat.confirm_action(card["action"])
		self.assertEqual(result["status"], "Completed")
		report = frappe.get_doc("Report", name)
		self.assertEqual(report.report_type, "Query Report")
		self.assertEqual(len(report.columns), 2)
		with self.assertRaises(frappe.ValidationError):
			chat.confirm_action(card["action"])

	def test_user_without_role_has_no_access(self):
		from omni_ai.api import chat

		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			chat.list_conversations()
