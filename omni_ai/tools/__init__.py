"""Tool registry and the single place where tools are executed, logged and guarded."""

import frappe

from omni_ai.tools.base import MAX_RESULT_CHARS, Timer, ToolContext, ToolError
from omni_ai.utils import clip, dumps, to_int


def all_tools() -> list:
	from omni_ai.tools import actions, automation, data, display, memory, reports, schedule, sql

	return [
		*data.TOOLS,
		*reports.TOOLS,
		*sql.TOOLS,
		*display.TOOLS,
		*actions.TOOLS,
		*memory.TOOLS,
		*schedule.TOOLS,
		*automation.TOOLS,
	]


DATA_TOOLS = {"query_data", "run_sql", "run_report", "get_document", "find_documents"}


def available_tools(ctx: ToolContext) -> dict:
	"""Tools this user may use right now, by name. Disabled features are simply not offered to the model."""
	tools = {}
	for tool in all_tools():
		try:
			if tool.enabled is None or tool.enabled(ctx):
				tools[tool.name] = tool
		except Exception:
			continue
	return tools


def describe_call(tool, args: dict) -> str:
	"""Short human label for the activity line in the chat, e.g. 'Querying Sales Invoice'."""
	subject = args.get("doctype") or args.get("report_name") or args.get("title") or ""
	if tool.name == "search_doctypes":
		subject = args.get("query") or args.get("module") or ""
	if tool.name == "find_documents":
		subject = f"{args.get('doctype', '')} for “{clip(args.get('text', ''), 40)}”"
	if tool.name == "list_reports":
		subject = args.get("query") or args.get("doctype") or ""
	if tool.name in ("show_chart", "show_table", "export_data", "run_sql"):
		subject = args.get("title") or args.get("filename") or ""
	if tool.name == "remember":
		subject = ""
	if tool.name == "trigger_automation":
		subject = args.get("automation") or ""
	if tool.name == "propose_document_action":
		return f"Preparing to {args.get('action', 'change')} {args.get('doctype', '')}".strip()
	return f"{tool.label} {subject}".strip()


def execute_tool(ctx: ToolContext, tools: dict, name: str, args: dict) -> tuple:
	"""Run one tool call. Returns (content_for_model, is_error, summary_for_ui). Never raises."""
	tool = tools.get(name)
	if not tool:
		return f"Tool '{name}' is not available.", True, "Not available"
	if not isinstance(args, dict):
		args = {}
	status, error, rows, result = "Success", None, 0, None
	with Timer() as timer:
		try:
			result = tool.handler(args, ctx)
			# Each tool call is its own transaction: what it created (an action card, a file) must
			# survive a later tool failing and rolling back in the same reply.
			frappe.db.commit()
		except ToolError as exc:
			status, error = ("Denied" if exc.denied else "Error"), str(exc)
			_rollback()
		except frappe.PermissionError as exc:
			status, error = "Denied", f"Permission denied: {clip(exc, 300) or 'not allowed'}"
			_rollback()
		except Exception as exc:
			status, error = "Error", f"{type(exc).__name__}: {clip(exc, 400)}"
			_rollback()
			frappe.log_error(title=f"AI Assistant tool failed: {name}")
		finally:
			# frappe.throw / msgprint queue desk popups; they belong in the tool result, not the UI
			try:
				frappe.local.message_log = []
			except Exception:
				pass

	if isinstance(result, dict):
		rows = to_int(result.get("total_rows"), 0)
	if not error and name in DATA_TOOLS:
		ctx.data_tools_used += 1
	_log(ctx, name, args, status, error, timer.ms, rows)

	if error:
		summary = "No permission" if status == "Denied" else "Could not complete"
		return error, True, summary
	if isinstance(result, dict) and "total_rows" in result:
		summary = f"{rows:,} row{'s' if rows != 1 else ''}"
	elif isinstance(result, dict) and result.get("status") == "awaiting_user":
		summary = "Waiting for you"
	else:
		summary = "Done"
	text = result if isinstance(result, str) else dumps(result)
	return clip(text, MAX_RESULT_CHARS * 2), False, summary


def _rollback():
	try:
		frappe.db.rollback()
	except Exception:
		pass


def _log(ctx, name, args, status, error, duration_ms, rows):
	try:
		if not ctx.settings.log_tool_calls:
			return
		doc = frappe.get_doc(
			{
				"doctype": "AI Assistant Tool Log",
				"tool": name,
				"user": ctx.user,
				"conversation": ctx.conversation,
				"status": status,
				"duration_ms": duration_ms,
				"row_count": rows,
				"arguments": clip(dumps(args), 8000),
				"error": clip(error or "", 1000),
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		_rollback()
