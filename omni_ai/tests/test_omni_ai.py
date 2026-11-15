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
	review = None

	def __init__(self, *args, **kwargs):
		pass

	def stream(self, payload, on_text=None, on_block=None, should_stop=None):
		FakeClient.requests.append(payload)
		if "Write a title" in str(payload.get("system", "")):
			return {"content": [{"type": "text", "text": "Test title"}], "stop_reason": "end_turn", "usage": {}}
		if "You review report definitions" in str(payload.get("system", "")):
			text = FakeClient.review or '{"verdict": "ok", "issues": []}'
			return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "usage": {}}
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
			"select a.name from `tabToDo` t, `__Auth` a",
			"select a.name from `tabToDo` t, __Auth a",
			"select * from (select name from __auth) x",
			"select 1 from `tabToDo`#hidden",
			"select '\\' from `tabToDo` where 1=1 or '1",
			"select 'abc from `tabToDo`",
			'select a.name from `tabToDo` t, "__Auth" a',
			"select * from `tabToDo` t, `tabSingles` s",
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


class TestFigureCheck(TestCase):
	def test_untraced_figures(self):
		from omni_ai.engine.grounding import collect_sources, untraced

		sources = collect_sources(
			['{"rows":[["2026-01",3,12000.0],["2026-02",3,14000.0]]}', "the budget is 250,000 for 2026"],
			[{"rows": [["a", 12000.0, 3], ["b", 14000.0, 3], ["c", 103000.0, 21]]}],
		)
		self.assertEqual(untraced("Net sales are **SAR 129,000** across 27 invoices in 2026.", sources), [])
		self.assertEqual(untraced("The total is 131,500 and January was 12,000.", sources), ["131,500"])
		self.assertEqual(untraced("About 1.29M; the budget was 250,000; up 2,000 on January.", sources), ["1.29M"])
		self.assertEqual(untraced("See ACC-SINV-2026-00012 dated 2026-01-15, up 15% on 14,000.00.", sources), [])
		self.assertEqual(untraced("المبيعات ١٢٩٬٠٠٠ ريال", sources), [])
		self.assertEqual(untraced("`select 5000` call 0555123456", sources), [])

	def test_schedule_times(self):
		from omni_ai.engine.schedule import next_run_after

		weekly = frappe._dict(frequency="Weekly", weekday="Monday", hour=8)
		self.assertEqual(str(next_run_after(weekly, "2026-11-15 13:00:00")), "2026-11-16 08:00:00")
		self.assertEqual(str(next_run_after(weekly, "2026-11-16 08:00:00")), "2026-11-23 08:00:00")
		monthly = frappe._dict(frequency="Monthly", day_of_month=31, hour=7)
		self.assertEqual(str(next_run_after(monthly, "2026-11-15 13:00:00")), "2026-11-28 07:00:00")

	def test_memory_refuses_secrets(self):
		from omni_ai.tools.memory import looks_like_secret

		self.assertTrue(looks_like_secret("the admin password is hunter2"))
		self.assertTrue(looks_like_secret("key sk-abcdefghijklmnop"))
		self.assertFalse(looks_like_secret("The fiscal year starts on 1 April."))


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
		settings.enable_memory = 1
		settings.verify_figures = 1
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

	def test_memory_needs_acceptance_and_is_recalled(self):
		from omni_ai.api import chat

		frappe.db.delete("AI Assistant Memory", {"title": "Unit test note"})
		FakeClient.script = [
			[{"type": "tool_use", "name": "remember", "input": {"title": "Unit test note", "note": "Report amounts in thousands.", "scope": "personal"}}],
			[{"type": "text", "text": "Noted on a card."}],
		]
		reply = chat.send("remember that I like amounts in thousands")
		card = next(p for p in chat.get_conversation(reply["conversation"])["messages"][-1]["parts"] if p["type"] == "action")
		self.assertFalse(frappe.db.exists("AI Assistant Memory", {"title": "Unit test note"}))
		chat.confirm_action(card["action"])
		self.assertEqual(frappe.db.get_value("AI Assistant Memory", {"title": "Unit test note"}, "status"), "Approved")
		FakeClient.script, FakeClient.requests = [[{"type": "text", "text": "ok"}]], []
		chat.send("what were the amounts in thousands last month?")
		self.assertIn("<memory>", str(FakeClient.requests[0]["messages"][-1]))

	def test_untraced_figure_is_sent_back_then_flagged(self):
		from omni_ai.api import chat

		query = {"doctype": "ToDo", "aggregates": [{"function": "count", "field": "name", "as": "n"}]}
		FakeClient.script = [
			[{"type": "tool_use", "name": "query_data", "input": query}],
			[{"type": "text", "text": "The total value is 987,654."}],
			[{"type": "text", "text": "The total value is 987,654."}],
		]
		reply = chat.send("what is the total value?")
		message = chat.get_conversation(reply["conversation"])["messages"][-1]
		main = [r for r in FakeClient.requests if "Write a title" not in str(r.get("system"))]
		self.assertEqual(len(main), 3)
		self.assertIn("Automatic check", str(main[2]["messages"][-1]))
		self.assertTrue(any(p["type"] == "notice" and "987,654" in p["text"] for p in message["parts"]))

	def test_user_without_role_has_no_access(self):
		from omni_ai.api import chat

		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			chat.list_conversations()


class TestProviders(TestCase):
	def test_messages_translate_to_chat_completions(self):
		from omni_ai.engine.openai_client import to_chat_messages, to_chat_tools

		messages = [
			{"role": "user", "content": "How many invoices?"},
			{"role": "assistant", "content": [
				{"type": "thinking", "thinking": "private"},
				{"type": "text", "text": "Let me check."},
				{"type": "tool_use", "id": "t1", "name": "query_data", "input": {"doctype": "Sales Invoice"}},
			]},
			{"role": "user", "content": [
				{"type": "tool_result", "tool_use_id": "t1", "content": '{"total_rows": 3}'},
				{"type": "tool_result", "tool_use_id": "t2", "content": "no such field", "is_error": True},
			]},
		]  # fmt: skip
		out = to_chat_messages([{"type": "text", "text": "Rules"}, {"type": "text", "text": "More"}], messages)
		self.assertEqual([m["role"] for m in out], ["system", "user", "assistant", "tool", "tool"])
		self.assertEqual(out[0]["content"], "Rules\n\nMore")
		self.assertEqual(out[1]["content"], "How many invoices?")
		self.assertEqual(out[2]["content"], "Let me check.")  # thinking is never forwarded
		call = out[2]["tool_calls"][0]
		self.assertEqual((call["id"], call["function"]["name"]), ("t1", "query_data"))
		self.assertEqual(frappe.parse_json(call["function"]["arguments"]), {"doctype": "Sales Invoice"})
		self.assertEqual(out[3], {"role": "tool", "tool_call_id": "t1", "content": '{"total_rows": 3}'})
		self.assertTrue(out[4]["content"].startswith("ERROR: "))

		tools = to_chat_tools([
			{"type": "web_search_20250305", "name": "web_search"},
			{"name": "query_data", "description": "Read rows", "input_schema": {"type": "object", "properties": {}}},
		])  # fmt: skip
		self.assertEqual(len(tools), 1)  # provider-side tools exist only on Claude
		self.assertEqual(tools[0]["function"]["name"], "query_data")

	def test_images_become_data_urls(self):
		from omni_ai.engine.openai_client import to_chat_messages

		out = to_chat_messages("", [{"role": "user", "content": [
			{"type": "text", "text": "What is this?"},
			{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
		]}])  # fmt: skip
		self.assertEqual(out[0]["content"][1]["image_url"]["url"], "data:image/png;base64,AAAA")

	def test_reviewer_reply_must_match_the_expected_shape(self):
		from omni_ai.engine.review import parse_verdict

		self.assertEqual(parse_verdict('{"verdict": "ok", "issues": []}'), {"verdict": "ok", "issues": []})
		self.assertEqual(
			parse_verdict('Here you go:\n```json\n{"verdict": "issues", "issues": ["Cancelled invoices are counted."]}\n```'),
			{"verdict": "issues", "issues": ["Cancelled invoices are counted."]},
		)
		self.assertEqual(parse_verdict('{"verdict": "ok", "issues": ["ignored"]}')["issues"], [])
		for bad in ("", "looks fine", '{"verdict": "maybe"}', '{"verdict": "issues", "issues": []}', '{"verdict": "issues", "issues": [1]}', '{"verdict": "issues", "issues": "x"}'):
			self.assertIsNone(parse_verdict(bad), bad)
		self.assertEqual(len(parse_verdict(frappe.as_json({"verdict": "issues", "issues": ["a", "b", "c", "d", "e", "f"]}))["issues"]), 4)


class TestGuard(TestCase):
	def test_only_outages_trip_the_breaker(self):
		from omni_ai.engine.client import ProviderError
		from omni_ai.engine.guard import is_outage

		self.assertTrue(is_outage(ProviderError("down", status=503)))
		self.assertTrue(is_outage(ProviderError("overloaded", status=529)))
		self.assertTrue(is_outage(ProviderError("no route", kind="connection_error")))
		self.assertFalse(is_outage(ProviderError("bad key", status=401, kind="authentication_error")))
		self.assertFalse(is_outage(ProviderError("bad request", status=400)))
		self.assertFalse(is_outage(ProviderError("slow down", status=429)))
		self.assertFalse(is_outage(ValueError("bug")))

	def test_breaker_opens_after_repeated_failures_and_closes_again(self):
		from omni_ai.engine import guard

		target = "unit-" + frappe.generate_hash(length=6)
		guard.check_open(target)
		guard.record_failure(target)
		guard.record_failure(target)
		guard.check_open(target)  # two failures are not enough
		guard.record_success(target)  # a success forgets earlier failures
		guard.record_failure(target)
		guard.record_failure(target)
		guard.check_open(target)
		guard.record_failure(target)
		with self.assertRaises(guard.ServicePaused):
			guard.check_open(target, "Test service")
		guard.check_open(target + "-other")  # one failing service does not pause another
		frappe.cache.delete_value(guard._key(target, "open_until"))
		guard.check_open(target)

	def test_rate_limit_and_budget(self):
		from omni_ai.engine import guard

		settings = frappe.get_single("AI Assistant Settings")
		before = (settings.messages_per_minute, settings.monthly_token_budget)
		user = "rate-" + frappe.generate_hash(length=6) + "@example.com"
		try:
			settings.messages_per_minute, settings.monthly_token_budget = 2, 0
			settings.save()
			guard.check_rate(user)
			guard.check_rate(user)
			with self.assertRaises(frappe.TooManyRequestsError):
				guard.check_rate(user)
			guard.check_rate(user + "x")  # limits are per user
			settings.messages_per_minute = 0
			settings.save()
			guard.check_rate(user)  # 0 switches the limit off
			guard.check_budget(user)  # no budget set
			settings.monthly_token_budget = 1000
			settings.save()
			guard.check_budget(user)  # nothing used yet
			with patch("omni_ai.engine.guard.tokens_this_month", return_value=1000):
				with self.assertRaises(frappe.ValidationError):
					guard.check_budget(user)
		finally:
			settings.messages_per_minute, settings.monthly_token_budget = before
			settings.save()

	def test_recall_similarity(self):
		from omni_ai.tools.memory import _cosine

		self.assertAlmostEqual(_cosine([1, 0], [1, 0]), 1.0)
		self.assertAlmostEqual(_cosine([1, 0], [0, 1]), 0.0)
		self.assertEqual(_cosine([1, 0], [1, 0, 0]), -1.0)  # vectors from different models are never compared
		self.assertEqual(_cosine([], [1]), -1.0)
