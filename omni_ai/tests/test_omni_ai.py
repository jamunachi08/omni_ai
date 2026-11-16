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


class TestShield(TestCase):
	def test_mask_and_unmask(self):
		from omni_ai.engine import shield

		valid_id = next(str(n) for n in range(1012345670, 1012345690) if shield._luhn(str(n)))
		text = f"Write to sara@example.com or 0501234567, IBAN SA0380000000608010167519, card 4111111111111111, ID {valid_id}. Total 1,250,000.00."
		vault = shield.Vault()
		masked = shield.mask(text, vault)
		for secret in ("sara@example.com", "0501234567", "SA0380000000608010167519", "4111111111111111", valid_id):
			self.assertNotIn(secret, masked)
		self.assertIn("1,250,000.00", masked)  # amounts are never touched
		self.assertEqual(shield.unmask(masked, vault), text)
		self.assertEqual(shield.mask(text, vault), masked)  # stable placeholders within one reply
		# numbers that only look like personal data stay as they are
		plain = f"Order {int(valid_id) + 1}, ref SA0380000000608010167510, code 1234567890123456"
		self.assertEqual(shield.mask(plain, shield.Vault()), plain)

	def test_streamed_placeholders(self):
		from omni_ai.engine import shield

		vault = shield.Vault()
		shield.mask("a@b.co", vault)
		for pieces in (["x [[EMAIL_1]] y"], ["x [", "[EMAIL_1]", "] y"], ["x [[EM", "AIL_", "1]] y"], list("x [[EMAIL_1]] y")):
			out = []
			unmasker = shield.StreamUnmasker(vault, out.append)
			for piece in pieces:
				unmasker.feed(piece)
			unmasker.flush()
			self.assertEqual("".join(out), "x a@b.co y", pieces)
			self.assertFalse(any("EMAIL_1" in o for o in out))
		out = []
		unmasker = shield.StreamUnmasker(vault, out.append)
		unmasker.feed("see [1] and [[UNKNOWN_9]] and [[")
		unmasker.flush()
		self.assertEqual("".join(out), "see [1] and [[UNKNOWN_9]] and [[")  # nothing is ever swallowed

	def test_client_wrapper(self):
		from omni_ai.engine import shield

		seen = {}

		class Inner:
			def stream(self, payload, on_text=None, on_block=None, should_stop=None):
				seen["payload"] = payload
				on_text("Mail [[EMA")
				on_text("IL_1]] today")
				return {"content": [{"type": "text", "text": "Mail [[EMAIL_1]] today"}, {"type": "tool_use", "id": "t", "name": "q", "input": {"to": "[[EMAIL_1]]"}}], "stop_reason": "tool_use"}

		heard = []
		client = shield.ShieldedClient(Inner())
		reply = client.stream({"system": "s", "messages": [{"role": "user", "content": "ask omar@example.com"}]}, on_text=heard.append)
		self.assertNotIn("omar@example.com", frappe.as_json(seen["payload"]))
		self.assertEqual("".join(heard), "Mail omar@example.com today")
		self.assertEqual(reply["content"][0]["text"], "Mail omar@example.com today")
		self.assertEqual(reply["content"][1]["input"], {"to": "omar@example.com"})


class TestKnowledgeAndDelivery(TestCase):
	def test_passages(self):
		from omni_ai.tools import knowledge

		text = "\n\n".join(f"Section {i}. " + "This sentence fills the section with ordinary words. " * 12 for i in range(1, 9))
		passages = knowledge.split_passages(text)
		self.assertGreater(len(passages), 4)
		self.assertTrue(all(len(p) <= knowledge.PASSAGE_CHARS for p in passages))
		self.assertTrue(all(f"Section {i}." in " ".join(passages) for i in range(1, 9)))  # nothing is lost
		self.assertEqual(knowledge.split_passages("  short text  "), ["short text"])
		self.assertEqual(knowledge.split_passages(""), [])
		self.assertLessEqual(len(knowledge.split_passages("word " * 200000)), knowledge.MAX_PASSAGES)

	def test_ranking(self):
		from omni_ai.tools import knowledge

		passages = [
			("a", "Employees receive 21 days of annual leave each year."),
			("b", "Travel expenses are reimbursed within 14 days."),
			("c", "The office is closed on public holidays."),
			("d", "تُمنح إجازة سنوية مدتها 21 يوماً لكل موظف."),
		]
		self.assertEqual(knowledge.rank_by_words("annual leave days", passages)[0][1], "a")
		self.assertEqual(knowledge.rank_by_words("travel reimbursed", passages)[0][1], "b")
		self.assertEqual(knowledge.rank_by_words("إجازة سنوية", passages)[0][1], "d")
		self.assertEqual(knowledge.rank_by_words("zebra", passages), [])
		self.assertEqual(knowledge.rank_by_words("the and for", passages), [])  # only filler words

	def test_webhook_addresses(self):
		from omni_ai.engine import delivery

		ok = [
			("Slack", "https://hooks.slack.com/services/T0/B0/abc"),
			("Google Chat", "https://chat.googleapis.com/v1/spaces/AAA/messages?key=k&token=t"),
			("Microsoft Teams", "https://contoso.webhook.office.com/webhookb2/abc"),
			("Microsoft Teams", "https://prod-12.westeurope.logic.azure.com:443/workflows/abc/triggers/manual/paths/invoke"),
		]
		for channel, url in ok:
			self.assertEqual(delivery.check_webhook(channel, url), url)
		bad = [
			("Slack", "http://hooks.slack.com/services/x"),
			("Slack", "https://hooks.slack.com.attacker.io/x"),
			("Slack", "https://attacker.io/hooks.slack.com"),
			("Slack", "https://user@hooks.slack.com@attacker.io/x"),
			("Slack", "https://169.254.169.254/latest/meta-data"),
			("Slack", "https://chat.googleapis.com/v1/spaces/x"),
			("Microsoft Teams", "https://webhook.office.com.attacker.io/x"),
			("Email", "https://hooks.slack.com/services/x"),
			("Slack", ""),
		]
		for channel, url in bad:
			with self.assertRaises(ValueError, msg=url):
				delivery.check_webhook(channel, url)
		text = delivery.plain_text("## Title\n**Bold** point\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nEnd")
		self.assertEqual(text, "*Title*\n*Bold* point\n\nEnd")
		self.assertLessEqual(len(delivery.plain_text("x" * 10000)), delivery.MAX_TEXT)

	def test_writing_number_check(self):
		from omni_ai.api.writing import changed_numbers, strip_fences

		self.assertEqual(changed_numbers("Pay 1,500 by 30 June", "Please pay 1,500 by 30 June."), [])
		self.assertEqual(changed_numbers("Pay 1,500 by 30 June", "Please pay 1,800 by 30 June."), ["1500", "1800"])
		self.assertEqual(changed_numbers("Total 1500", "المجموع ١٥٠٠"), [])
		self.assertEqual(changed_numbers("<p>Item 5</p>", "<p>Item 5</p><p>and 6</p>"), ["6"])
		self.assertEqual(strip_fences("```\nhello\n```"), "hello")
		self.assertEqual(strip_fences("```html\n<p>x</p>\n```"), "<p>x</p>")
		self.assertEqual(strip_fences("plain"), "plain")


class TestChecksAndAnalysis(TestCase):
	def test_text_comparison(self):
		from omni_ai.checks.engine import _compare, norm, split_names

		self.assertEqual(norm("Al-Noor  Trading Co."), norm("alnoor trading co"))
		self.assertEqual(norm("شركة  الواحة"), norm("شركة الواحة"))
		self.assertNotEqual(norm("Al Noor Trading"), norm("Al Noor Trading 2"))
		self.assertEqual(split_names("supplier, bill_no\nitems.rate ,"), ["supplier", "bill_no", "items.rate"])
		self.assertTrue(_compare(5, ">=", "5", True, False))
		self.assertFalse(_compare(4.99, ">=", "5", True, False))
		self.assertTrue(_compare("High", "in", "low, HIGH", False, False))
		self.assertTrue(_compare("Urgent", "not in", "low, high", False, False))
		self.assertTrue(_compare("2026-03-10", ">", "2026-03-09", False, True))
		self.assertTrue(_compare(None, ">", "2026-03-09", False, True))  # an empty date is not a wrong date
		self.assertTrue(_compare("Open", "=", "open", False, False))

	def test_outliers_and_trend(self):
		from omni_ai.tools import analysis

		normal = [100, 102, 98, 101, 99, 103, 97, 100]
		self.assertEqual(analysis.outliers([(v, i) for i, v in enumerate(normal)]), [])
		found = analysis.outliers([(v, i) for i, v in enumerate(normal + [400])])
		self.assertEqual([(index, value) for _s, index, value, _t in found], [(8, 400)])
		self.assertEqual(analysis.outliers([(v, i) for i, v in enumerate([1, 2, 500])]), [])  # too few to judge
		line = analysis.trend([10, 20, 30, 40, 50, 60])
		self.assertAlmostEqual(line["slope"], 10)
		self.assertAlmostEqual(line["fit"], 1.0)
		self.assertAlmostEqual(line["error"], 0)
		flat = analysis.trend([5, 5, 5, 5, 5, 5])
		self.assertEqual((flat["slope"], flat["fit"]), (0, 1.0))
		self.assertEqual(analysis.summary([1, 2, 3, 10])["median"], 2.5)

	def test_starter_rules_are_well_formed(self):
		from omni_ai.checks.defaults import STARTER_RULES
		from omni_ai.checks.engine import CHECKS

		self.assertGreater(len(STARTER_RULES), 20)
		for rule in STARTER_RULES:
			self.assertIn(rule["rule_type"], CHECKS, rule["title"])
			self.assertTrue(rule["title"] and rule["document_type"])
			self.assertNotIn("action", rule)  # every starter rule begins as a warning
		pairs = [(r["title"], r["document_type"]) for r in STARTER_RULES]
		self.assertEqual(len(pairs), len(set(pairs)))

	def test_monitors_are_well_formed(self):
		from omni_ai.control import monitors

		found = monitors._monitors()
		keys = [m["key"] for m in found]
		self.assertEqual(len(keys), len(set(keys)))
		for monitor in found:
			self.assertIn(monitor["severity"], monitors.SEVERITY)
			self.assertTrue(monitor["title"] and monitor["why"] and monitor["doctype"] and monitor["area"])
			self.assertTrue(monitor.get("filters") or monitor.get("custom") in monitors.CUSTOM)
