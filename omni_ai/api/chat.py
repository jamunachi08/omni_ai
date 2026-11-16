"""HTTP endpoints used by the chat interface. Every method checks access and conversation ownership."""

import re
import time

import frappe
from frappe import _
from frappe.utils import cint, now_datetime, nowdate

from omni_ai.engine import stream as bus
from omni_ai.engine.files import validate_attachments
from omni_ai.engine import guard
from omni_ai.utils import clip, dumps, get_settings, is_configured, loads, require_access, to_int

MAX_MESSAGE_CHARS = 60000
STALE_RUN_SECONDS = 20 * 60


# -- helpers ------------------------------------------------------------------------


def _conversation(name: str):
	"""Fetch a conversation and make sure it belongs to the caller."""
	require_access()
	row = frappe.db.get_value(
		"AI Assistant Conversation",
		name,
		["name", "title", "user", "pinned", "active_run", "last_message_at", "pending_notes", "read_only"],
		as_dict=True,
	)
	if not row or row.user != frappe.session.user:
		frappe.throw(_("Conversation not found."), frappe.DoesNotExistError)
	return row


def _active_run(conv) -> str | None:
	"""The run in progress for this conversation, or None. Clears runs that died without finishing."""
	run_id = conv.active_run
	if not run_id:
		return None
	state = bus.get_state(run_id)
	if state.get("status") == "done":
		frappe.db.set_value("AI Assistant Conversation", conv.name, "active_run", "", update_modified=False)
		return None
	started = state.get("started") or state.get("queued") or 0
	if not state or (time.time() - float(started)) > STALE_RUN_SECONDS:
		frappe.db.set_value("AI Assistant Conversation", conv.name, "active_run", "", update_modified=False)
		return None
	return run_id


def _check_daily_limit(user: str):
	limit = to_int(get_settings().daily_message_limit)
	if limit <= 0:
		return
	used = frappe.db.count(
		"AI Assistant Message", {"owner": user, "role": "user", "creation": (">=", f"{nowdate()} 00:00:00")}
	)
	if used >= limit:
		frappe.throw(_("You have reached today's limit of {0} messages. It resets at midnight.").format(limit))


def _clean_context(context) -> dict | None:
	"""Keep only a small, known set of keys from what the browser says the user is looking at."""
	context = loads(context, None)
	if not isinstance(context, dict):
		return None
	out = {}
	for key in ("view", "doctype", "docname", "title", "report_name", "route"):
		value = context.get(key)
		if isinstance(value, str) and value:
			out[key] = value[:200]
	filters = context.get("filters")
	if isinstance(filters, (dict, list)) and len(dumps(filters)) < 2000:
		out["filters"] = filters
	return out or None


def _message_row(row) -> dict:
	parts = loads(row.ui, None)
	if row.role == "assistant" and not parts and row.content:
		parts = [{"type": "text", "text": row.content}]
	return {
		"name": row.name,
		"role": row.role,
		"content": row.content or "",
		"parts": parts or [],
		"attachments": loads(row.attachments, []),
		"status": row.status,
		"error": row.error,
		"creation": str(row.creation),
	}


def _refresh_actions(messages: list, conversation: str):
	"""Action cards are stored with the reply; their live status comes from the AI Assistant Action records."""
	actions = {
		a.name: a
		for a in frappe.get_all(
			"AI Assistant Action",
			filters={"conversation": conversation},
			fields=["name", "status", "result", "reference_doctype", "reference_name"],
			limit=0,
		)
	}
	from omni_ai.tools.actions import doc_route
	from omni_ai.tools.reports import report_route

	for message in messages:
		for part in message["parts"]:
			if part.get("type") != "action":
				continue
			action = actions.get(part.get("action"))
			if not action:
				part["status"] = "Cancelled"
				continue
			part["status"] = action.status
			part["result"] = action.result
			if action.status == "Completed" and action.reference_doctype and action.reference_name:
				try:
					if action.reference_doctype == "Report":
						part["route"] = report_route(frappe.get_cached_doc("Report", action.reference_name))
					else:
						part["route"] = doc_route(action.reference_doctype, action.reference_name)
				except Exception:
					pass


# -- conversations --------------------------------------------------------------------


@frappe.whitelist()
def list_conversations(search: str | None = None, start: int = 0, limit: int = 40):
	require_access()
	filters = {"user": frappe.session.user}
	if search:
		filters["title"] = ("like", f"%{search.strip()[:80]}%")
	rows = frappe.get_all(
		"AI Assistant Conversation",
		filters=filters,
		fields=["name", "title", "pinned", "read_only", "last_message_at", "modified"],
		order_by="pinned desc, modified desc",
		limit_start=cint(start),
		limit_page_length=min(cint(limit) or 40, 100),
	)
	return rows


@frappe.whitelist()
def get_conversation(name: str):
	conv = _conversation(name)
	rows = frappe.get_all(
		"AI Assistant Message",
		filters={"conversation": conv.name},
		fields=["name", "role", "content", "ui", "attachments", "status", "error", "creation"],
		order_by="creation asc, name asc",
		limit=0,
	)
	messages = [_message_row(r) for r in rows]
	_refresh_actions(messages, conv.name)
	return {
		"name": conv.name,
		"title": conv.title,
		"pinned": conv.pinned,
		"read_only": cint(conv.read_only),
		"messages": messages,
		"active_run": _active_run(conv),
	}


@frappe.whitelist(methods=["POST"])
def rename_conversation(name: str, title: str):
	conv = _conversation(name)
	title = " ".join((title or "").split())[:120] or "Conversation"
	frappe.db.set_value("AI Assistant Conversation", conv.name, "title", title)
	return title


@frappe.whitelist(methods=["POST"])
def pin_conversation(name: str, pinned: int = 1):
	conv = _conversation(name)
	frappe.db.set_value("AI Assistant Conversation", conv.name, "pinned", 1 if cint(pinned) else 0, update_modified=False)
	return cint(pinned)


@frappe.whitelist(methods=["POST"])
def set_read_only(name: str, read_only: int = 1):
	"""Switch a conversation between normal and read-only (no change can be proposed in it)."""
	conv = _conversation(name)
	frappe.db.set_value("AI Assistant Conversation", conv.name, "read_only", 1 if cint(read_only) else 0, update_modified=False)
	return cint(read_only)


@frappe.whitelist(methods=["POST"])
def delete_conversation(name: str):
	conv = _conversation(name)
	if _active_run(conv):
		bus.request_stop(conv.active_run)
	from omni_ai.tasks import delete_conversation_data

	delete_conversation_data(conv.name)
	return True


# -- sending --------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def send(
	message: str,
	conversation: str | None = None,
	attachments=None,
	page_context=None,
	run_id: str | None = None,
	read_only: int = 0,
):
	"""Store the user's message and start the assistant's reply.

	The browser may pass its own random `run_id` so it can start listening for the streamed reply before
	this call returns (needed for Inline execution, where the call only returns when the reply is done)."""
	require_access()
	user = frappe.session.user
	message = (message or "").strip()
	attachments = loads(attachments, []) or []
	if not message and not attachments:
		frappe.throw(_("Type a message first."))
	if len(message) > MAX_MESSAGE_CHARS:
		frappe.throw(_("That message is too long. Attach it as a file instead."))
	settings = get_settings()
	if not is_configured():
		frappe.throw(_("The assistant is not connected yet. Ask your administrator to add an API key in {0} Settings.").format(settings.brand_name or "Assistant"))
	_check_daily_limit(user)
	guard.check_rate(user)
	guard.check_budget(user)

	created = False
	if conversation:
		conv = _conversation(conversation)
		if _active_run(conv):
			frappe.throw(_("Please wait for the current reply to finish, or stop it first."))
	else:
		doc = frappe.get_doc(
			{"doctype": "AI Assistant Conversation", "title": "New conversation", "user": user, "read_only": 1 if cint(read_only) else 0}
		)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		conv = frappe._dict(name=doc.name, title=doc.title, pending_notes=None)
		created = True

	refs = []
	if attachments:
		if not settings.enable_file_uploads:
			frappe.throw(_("File attachments are turned off."))
		refs = validate_attachments(attachments, user)
		for ref in refs:
			frappe.db.set_value(
				"File",
				ref["file"],
				{"attached_to_doctype": "AI Assistant Conversation", "attached_to_name": conv.name},
				update_modified=False,
			)

	context = _clean_context(page_context)
	notes = loads(conv.pending_notes, []) or []
	recalled = []
	try:
		from omni_ai.tools import memory

		recalled = memory.recall(user, message)
		memory.mark_used([n["name"] for n in recalled])
	except Exception:
		frappe.log_error(title="AI Assistant memory recall failed")
	msg = frappe.get_doc(
		{
			"doctype": "AI Assistant Message",
			"conversation": conv.name,
			"role": "user",
			"status": "Complete",
			"content": message,
			"attachments": dumps(refs) if refs else None,
			"page_context": dumps(context) if context else None,
			"notes": dumps(notes) if notes else None,
			"memory": dumps([{"title": n["title"], "content": n["content"]} for n in recalled]) if recalled else None,
		}
	)
	msg.flags.ignore_permissions = True
	msg.insert(ignore_permissions=True)

	run_id = start_run(conv.name, user, clear_notes=bool(notes), run_id=run_id)
	return {
		"conversation": conv.name,
		"created": created,
		"run_id": run_id,
		"message": {"name": msg.name, "role": "user", "content": message, "attachments": refs, "parts": []},
	}


def start_run(conversation: str, user: str, clear_notes: bool = False, run_id: str | None = None) -> str:
	settings = get_settings()
	if not (run_id and re.fullmatch(r"[a-f0-9]{16,32}", run_id) and not bus.get_state(run_id)):
		run_id = frappe.generate_hash(length=16)
	values = {"active_run": run_id, "last_message_at": now_datetime()}
	if clear_notes:
		values["pending_notes"] = None
	frappe.db.set_value("AI Assistant Conversation", conversation, values)
	count = frappe.db.count("AI Assistant Message", {"conversation": conversation})
	frappe.db.set_value("AI Assistant Conversation", conversation, "message_count", count, update_modified=False)
	bus.set_state(run_id, status="queued", conversation=conversation, queued=time.time())
	frappe.db.commit()

	if settings.execution_mode == "Inline" or frappe.flags.in_test:
		from omni_ai.engine.agent import run

		run(run_id, conversation, user)
	else:
		frappe.enqueue(
			"omni_ai.engine.agent.run",
			queue="long",
			timeout=max(900, to_int(settings.request_timeout, 600) * 3),
			job_id=f"omni_ai_run_{run_id}",
			run_id=run_id,
			conversation=conversation,
			user=user,
		)
	return run_id


@frappe.whitelist(methods=["POST"])
def regenerate(conversation: str, run_id: str | None = None):
	"""Discard the last reply and answer the last user message again."""
	conv = _conversation(conversation)
	if _active_run(conv):
		frappe.throw(_("Please wait for the current reply to finish, or stop it first."))
	_check_daily_limit(frappe.session.user)
	guard.check_rate(frappe.session.user)
	guard.check_budget(frappe.session.user)
	last = frappe.get_all(
		"AI Assistant Message",
		filters={"conversation": conv.name},
		fields=["name", "role"],
		order_by="creation desc, name desc",
		limit=1,
	)
	if last and last[0].role == "assistant":
		frappe.delete_doc("AI Assistant Message", last[0].name, ignore_permissions=True, force=True)
	elif not last:
		frappe.throw(_("There is nothing to regenerate."))
	return {"run_id": start_run(conv.name, frappe.session.user, run_id=run_id)}


@frappe.whitelist()
def poll(run_id: str, after: int = 0):
	"""Events of a run after sequence number `after`. The fallback when realtime is unavailable."""
	require_access()
	state = bus.get_state(run_id)
	if not state:
		return {"events": [], "done": True, "missing": True}
	conversation = state.get("conversation")
	if not conversation or frappe.db.get_value("AI Assistant Conversation", conversation, "user") != frappe.session.user:
		frappe.throw(_("Not found."), frappe.DoesNotExistError)
	events = bus.read_events(run_id, cint(after))
	status = state.get("status")
	waiting = None
	if status == "queued":
		waiting = int(time.time() - float(state.get("queued") or time.time()))
	return {"events": events, "done": status == "done", "status": status, "waiting": waiting}


@frappe.whitelist(methods=["POST"])
def stop(run_id: str):
	require_access()
	state = bus.get_state(run_id)
	conversation = state.get("conversation")
	if not conversation or frappe.db.get_value("AI Assistant Conversation", conversation, "user") != frappe.session.user:
		return False
	bus.request_stop(run_id)
	if state.get("status") == "queued":
		# the job never started (for example no background worker): release the conversation
		frappe.db.set_value("AI Assistant Conversation", conversation, "active_run", "", update_modified=False)
		bus.set_state(run_id, status="done")
	return True


# -- action cards ---------------------------------------------------------------------


def _add_note(conversation: str, note: str):
	notes = loads(frappe.db.get_value("AI Assistant Conversation", conversation, "pending_notes"), []) or []
	notes.append(clip(note, 400))
	frappe.db.set_value("AI Assistant Conversation", conversation, "pending_notes", dumps(notes[-10:]), update_modified=False)


@frappe.whitelist(methods=["POST"])
def confirm_action(action: str):
	"""The user pressed Save / Confirm on a card. Runs the action as that user."""
	require_access()
	user = frappe.session.user
	row = frappe.db.get_value("AI Assistant Action", action, ["name", "user", "status", "conversation", "title"], as_dict=True)
	if not row or row.user != user:
		frappe.throw(_("Action not found."), frappe.DoesNotExistError)
	if row.status != "Pending":
		frappe.throw(_("This action is already {0}.").format(_(row.status).lower()))
	from omni_ai.tools.actions import execute_action

	try:
		result = execute_action(action, user)
	except Exception as exc:
		frappe.db.rollback()
		messages = []
		for raw in frappe.local.message_log or []:
			item = loads(raw, raw) if isinstance(raw, str) else raw
			text = item.get("message") if isinstance(item, dict) else str(item)
			if text:
				messages.append(frappe.utils.strip_html_tags(str(text)))
		frappe.local.message_log = []
		reason = clip("; ".join(messages) or str(exc) or type(exc).__name__, 500)
		if not isinstance(exc, (frappe.ValidationError, frappe.PermissionError)):
			frappe.log_error(title="AI Assistant action failed")
		frappe.db.set_value("AI Assistant Action", action, {"status": "Failed", "result": reason})
		_add_note(row.conversation, f"The user confirmed '{row.title}' but it failed: {reason}")
		frappe.db.commit()
		return {"status": "Failed", "message": reason}
	# warnings raised while saving (entry checks, stock notices) must reach the user, not vanish
	notices = []
	for raw in frappe.local.message_log or []:
		item = loads(raw, raw) if isinstance(raw, str) else raw
		text = item.get("message") if isinstance(item, dict) else str(item)
		if text:
			notices.append(clip(frappe.utils.strip_html_tags(str(text)), 400))
	frappe.local.message_log = []
	if notices:
		result["warnings"] = notices[:5]
		result["message"] = f"{result['message']} Please check: {' '.join(notices[:5])}"
	_add_note(row.conversation, f"The user confirmed '{row.title}'. Result: {result['message']}")
	return result


@frappe.whitelist(methods=["POST"])
def cancel_action(action: str):
	require_access()
	row = frappe.db.get_value("AI Assistant Action", action, ["name", "user", "status", "conversation", "title"], as_dict=True)
	if not row or row.user != frappe.session.user:
		frappe.throw(_("Action not found."), frappe.DoesNotExistError)
	if row.status == "Pending":
		frappe.db.set_value("AI Assistant Action", action, {"status": "Cancelled", "result": "Dismissed by the user."})
		_add_note(row.conversation, f"The user dismissed '{row.title}' without confirming it.")
	return {"status": "Cancelled"}


# -- reports saved through the assistant ----------------------------------------------


@frappe.whitelist()
def my_reports():
	require_access()
	rows = frappe.get_all(
		"AI Assistant Action",
		filters={"user": frappe.session.user, "action_type": "Save Report", "status": "Completed"},
		fields=["reference_name", "executed_at"],
		order_by="executed_at desc",
		limit=50,
	)
	from omni_ai.tools.reports import report_route

	out = []
	for row in rows:
		if row.reference_name and frappe.db.exists("Report", row.reference_name):
			doc = frappe.get_cached_doc("Report", row.reference_name)
			out.append({"name": doc.name, "type": doc.report_type, "route": report_route(doc), "saved": str(row.executed_at)})
	return out
