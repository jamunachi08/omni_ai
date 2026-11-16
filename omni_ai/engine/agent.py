"""The assistant loop: build the conversation, call the model, run tools, stream and save the reply."""

import time

import frappe
from frappe.utils import now_datetime

from omni_ai.engine import stream as bus
from omni_ai.engine import guard
from omni_ai.engine.client import ClaudeClient, ProviderError
from omni_ai.engine.openai_client import OpenAICompatClient
from omni_ai.engine.files import attachment_blocks
from omni_ai.engine.prompt import build_system_prompt
from omni_ai.tools import ToolContext, available_tools, describe_call, execute_tool
from omni_ai.utils import clip, dumps, get_api_key, get_settings, loads, to_int

FULL_FIDELITY_TURNS = 6  # recent replies replayed with their tool calls; older ones as text only
WEB_SEARCH_TOOL = "web_search_20250305"
STEP_LIMIT_NOTE = (
	"Step limit reached for this reply. Do not call any more tools. Give the user your best answer now from what "
	"you already have, and say briefly what is still missing."
)


def make_client(timeout: int | None = None, vault=None):
	"""The client for the provider chosen in settings. Both clients take and return the same message format.
	With personal-data masking on, the client is wrapped so nothing is sent unmasked."""
	settings = get_settings()
	timeout = timeout or to_int(settings.request_timeout, 600) or 600
	if settings.provider == "OpenAI-compatible":
		client = OpenAICompatClient(get_api_key(), settings.base_url, timeout)
	else:
		client = ClaudeClient(get_api_key(), settings.base_url, timeout)
	if settings.get("mask_personal_data"):
		from omni_ai.engine.shield import ShieldedClient

		client = ShieldedClient(client, vault)
	return client


# -- history -------------------------------------------------------------------------


def _strip_blocks(content):
	"""Remove thinking blocks (not needed from earlier turns) and empty text blocks."""
	if isinstance(content, str):
		return content
	out = []
	for block in content or []:
		kind = block.get("type")
		if kind in ("thinking", "redacted_thinking"):
			continue
		if kind == "text" and not (block.get("text") or "").strip():
			continue
		block = {k: v for k, v in block.items() if not k.startswith("_")}
		out.append(block)
	return out


def sanitize_turn(api_messages: list) -> list:
	"""Make a stored turn safe to replay: every tool_use answered, no empty messages, ends with the assistant."""
	out = []
	messages = list(api_messages or [])
	for index, message in enumerate(messages):
		content = message.get("content")
		if isinstance(content, str):
			content = [{"type": "text", "text": content}]
		content = [dict(b) for b in (content or [])]
		if message.get("role") == "assistant":
			tool_ids = [b["id"] for b in content if b.get("type") == "tool_use"]
			if tool_ids:
				nxt = messages[index + 1] if index + 1 < len(messages) else None
				answered = set()
				if nxt and nxt.get("role") == "user" and isinstance(nxt.get("content"), list):
					answered = {b.get("tool_use_id") for b in nxt["content"] if b.get("type") == "tool_result"}
				content = [b for b in content if b.get("type") != "tool_use" or b["id"] in answered]
		content = [b for b in content if not (b.get("type") == "text" and not (b.get("text") or "").strip())]
		if not content:
			continue
		out.append({"role": message.get("role"), "content": content})
	# drop orphan tool results (their tool_use was removed above)
	cleaned = []
	for message in out:
		if message["role"] == "user":
			prev_ids = set()
			if cleaned and cleaned[-1]["role"] == "assistant":
				prev_ids = {b.get("id") for b in cleaned[-1]["content"] if b.get("type") == "tool_use"}
			content = [b for b in message["content"] if b.get("type") != "tool_result" or b.get("tool_use_id") in prev_ids]
			if not content:
				continue
			message = {"role": "user", "content": content}
		cleaned.append(message)
	while cleaned and cleaned[-1]["role"] != "assistant":
		cleaned.pop()
	return cleaned


def _user_content(row, user: str, with_files: bool) -> list:
	blocks = []
	refs = loads(row.attachments, [])
	if refs:
		if with_files:
			blocks.extend(attachment_blocks(refs, user))
		else:
			names = ", ".join(r.get("file_name") or "file" for r in refs)
			blocks.append({"type": "text", "text": f"[Earlier attachments, no longer loaded: {names}]"})
	text = row.content or ""
	notes = loads(row.notes, [])
	if notes:
		text = "".join(f"<system_note>{n}</system_note>\n" for n in notes) + text
	context = loads(row.page_context, None)
	if context and context.get("doctype"):
		from omni_ai.tools.data import withheld

		try:
			if withheld(context["doctype"]):
				context = None  # the user is looking at a sensitive record: do not even pass its name on
		except Exception:
			context = None
	if context:
		text = f"<page_context>{dumps(context)}</page_context>\n{text}"
	memory = loads(row.memory, [])
	if memory:
		lines = "\n".join(f"- {m.get('title')}: {m.get('content')}" for m in memory)
		text = f"<memory>\n{lines}\n</memory>\n{text}"
	blocks.append({"type": "text", "text": text or "(no text)"})
	return blocks


def build_messages(conversation: str, user: str, settings) -> list:
	rows = frappe.get_all(
		"AI Assistant Message",
		filters={"conversation": conversation},
		fields=["name", "role", "content", "api_messages", "attachments", "page_context", "notes", "memory", "status", "ui"],
		order_by="creation asc, name asc",
		limit=0,
	)
	keep_turns = max(2, to_int(settings.history_turns, 20) or 20)
	user_rows = [r for r in rows if r.role == "user"]
	omitted = False
	if len(user_rows) > keep_turns:
		first_kept = user_rows[-keep_turns].name
		index = next(i for i, r in enumerate(rows) if r.name == first_kept)
		rows, omitted = rows[index:], True

	assistant_rows = [r for r in rows if r.role == "assistant"]
	full_from = {r.name for r in assistant_rows[-FULL_FIDELITY_TURNS:]}
	recent_users = {r.name for r in [r for r in rows if r.role == "user"][-FULL_FIDELITY_TURNS:]}

	messages = []

	def push(role, content):
		if isinstance(content, str):
			content = [{"type": "text", "text": content}]
		if not content:
			return
		if messages and messages[-1]["role"] == role:
			messages[-1]["content"].extend(content)
		else:
			messages.append({"role": role, "content": list(content)})

	if omitted:
		push("user", "[The earlier part of this conversation is not included.]")
	for row in rows:
		if row.role == "user":
			push("user", _user_content(row, user, with_files=row.name in recent_users))
			continue
		turn = sanitize_turn(loads(row.api_messages, [])) if row.name in full_from else []
		if turn:
			for message in turn:
				push(message["role"], _strip_blocks(message["content"]))
		else:
			text = (row.content or "").strip()
			tools_used = sorted({p.get("name") for p in loads(row.ui, []) if p.get("type") == "tool" and p.get("name")})
			if tools_used:
				text += f"\n[Tools used in this reply: {', '.join(tools_used)}]"
			if row.status == "Error":
				text = (text + "\n[This reply ended with an error.]").strip()
			if row.status == "Stopped":
				text = (text + "\n[The user stopped this reply.]").strip()
			push("assistant", text or "(no reply)")
	# the API needs the conversation to end with the user's turn
	while messages and messages[-1]["role"] != "user":
		messages.pop()
	return messages


# -- running -------------------------------------------------------------------------


class Run:
	def __init__(self, run_id: str, conversation: str, user: str, unattended: bool = False, proposals: bool = False):
		self.unattended = unattended
		self.proposals = proposals
		self.figures_checked = False
		self.run_id = run_id
		self.conversation = conversation
		self.user = user
		self.settings = get_settings()
		self.stream = bus.RunStream(run_id, user, conversation)
		self.parts = []
		self.turn_messages = []
		self.usage = {"input": 0, "output": 0, "cache_read": 0, "web_searches": 0}
		self.model = self.settings.model or "claude-opus-5-5"
		self.status = "Complete"
		self.error = None
		self.sources = {}
		self.started = time.monotonic()

	# -- text + parts -------------------------------------------------------------

	def on_text(self, delta: str):
		if not self.parts or self.parts[-1].get("type") != "text" or self.parts[-1].get("draft"):
			self.parts.append({"type": "text", "text": ""})
		self.parts[-1]["text"] += delta
		self.stream.text(delta)

	def note(self, text: str):
		"""Text added by the app (not the model), e.g. a cut-off notice."""
		self.on_text(("\n\n" if self.parts and self.parts[-1].get("type") == "text" else "") + text)

	def update_part(self, part_id: str, **patch):
		for part in self.parts:
			if part.get("id") == part_id:
				part.update(patch)
				break
		self.stream.emit("part_update", id=part_id, patch=patch)

	def on_block(self, block: dict):
		kind = block.get("type")
		if kind == "tool_use":
			tool = self.tools.get(block.get("name"))
			self.ctx.add_part(
				{
					"type": "tool",
					"id": block.get("id"),
					"name": block.get("name"),
					"label": tool.label if tool else block.get("name"),
					"status": "running",
				}
			)
		elif kind == "server_tool_use":
			self.ctx.add_part(
				{"type": "tool", "id": block.get("id"), "name": "web_search", "label": "Searching the web", "status": "running"}
			)
		elif kind == "web_search_tool_result":
			content = block.get("content")
			if isinstance(content, list):
				for item in content:
					if item.get("url"):
						self.sources.setdefault(item["url"], item.get("title") or item["url"])
				self.update_part(block.get("tool_use_id"), status="done", summary=f"{len(content)} results")
			else:
				self.update_part(block.get("tool_use_id"), status="error", summary="Search unavailable")
		elif kind == "thinking":
			self.stream.emit("status", s="thinking")

	# -- request ------------------------------------------------------------------

	def payload(self, system: str, messages: list) -> dict:
		tools = [tool.definition() for tool in self.tools.values()]
		claude = self.settings.provider != "OpenAI-compatible"
		if self.settings.enable_web_search and claude:
			tools.append(
				{
					"type": WEB_SEARCH_TOOL,
					"name": "web_search",
					"max_uses": max(1, to_int(self.settings.web_search_max_uses, 5) or 5),
				}
			)
		payload = {
			"model": self.model,
			"max_tokens": min(max(1024, to_int(self.settings.max_tokens, 16000) or 16000), 128000),
			"system": system,
			"messages": messages,
			"tools": tools,
		}
		if self.settings.effort and claude:
			payload["output_config"] = {"effort": self.settings.effort}
		if self.settings.prompt_caching and claude:
			payload["cache_control"] = {"type": "ephemeral"}
		return payload

	def add_usage(self, usage: dict):
		usage = usage or {}
		cache_read = to_int(usage.get("cache_read_input_tokens"))
		self.usage["input"] += to_int(usage.get("input_tokens")) + to_int(usage.get("cache_creation_input_tokens")) + cache_read
		self.usage["cache_read"] += cache_read
		self.usage["output"] += to_int(usage.get("output_tokens"))
		self.usage["web_searches"] += to_int((usage.get("server_tool_use") or {}).get("web_search_requests"))

	# -- main loop ----------------------------------------------------------------

	def execute(self):
		self.ctx = ToolContext(
			self.user,
			self.conversation,
			stream=self.stream,
			parts=self.parts,
			unattended=self.unattended,
			proposals=self.proposals,
			read_only=bool(frappe.db.get_value("AI Assistant Conversation", self.conversation, "read_only")),
		)
		self.tools = available_tools(self.ctx)
		guard.check_open("provider")
		client = make_client()
		system = build_system_prompt(self.user, set(self.tools))
		if self.ctx.read_only:
			system += (
				"\n\nThis conversation is read-only. You can look things up, analyse and explain, but you cannot propose "
				"changes, reports to save, notes, schedules, emails or automations here. If the user asks for one, say that "
				"this conversation is read-only and that they can switch it off in the conversation menu."
			)
		history = build_messages(self.conversation, self.user, self.settings)
		if not history:
			raise ProviderError("There is no message to answer.")
		max_rounds = max(1, to_int(self.settings.max_tool_rounds, 25) or 25)

		rounds = 0
		while True:
			rounds += 1
			self.stream.emit("status", s="thinking")
			try:
				message = client.stream(
					self.payload(system, history + self.turn_messages),
					on_text=self.on_text,
					on_block=self.on_block,
					should_stop=self.stream.should_stop,
				)
			except ProviderError as exc:
				if guard.is_outage(exc):
					guard.record_failure("provider")
				raise
			guard.record_success("provider")
			self.stream.flush()
			self.add_usage(message.get("usage"))
			self.model = message.get("model") or self.model
			stop_reason = message.get("stop_reason")
			content = message.get("content") or []
			# a tool call whose JSON was cut off cannot be executed
			broken = [b for b in content if b.get("_invalid_json") is not None]
			content = [b for b in content if b.get("_invalid_json") is None]
			for block in broken:
				self.update_part(block.get("id"), status="error", summary="Cut off")
			for block in content:
				for citation in block.get("citations") or []:
					if citation and citation.get("url"):
						self.sources.setdefault(citation["url"], citation.get("title") or citation["url"])
			if content:
				self.turn_messages.append({"role": "assistant", "content": content})

			if stop_reason == "user_stopped":
				self.status = "Stopped"
				self._close_running_parts("Stopped")
				break
			if stop_reason == "pause_turn":
				continue
			if stop_reason == "max_tokens":
				self.note("_The reply reached the length limit. Ask me to continue if you need the rest._")
				self._close_running_parts("Cut off")
				break

			tool_calls = [b for b in content if b.get("type") == "tool_use"]
			if stop_reason != "tool_use" or not tool_calls:
				if stop_reason == "refusal":
					self.note("_I can't help with that request._")
					break
				recheck = self.check_figures(history)
				if recheck:
					self.turn_messages.append({"role": "user", "content": [{"type": "text", "text": recheck}]})
					continue
				break
			if rounds > max_rounds:
				self.note("_I stopped here because this request needed more steps than allowed._")
				self._close_running_parts("Skipped")
				break

			results = []
			for call in tool_calls:
				if self.stream.should_stop():
					self.status = "Stopped"
					break
				tool = self.tools.get(call.get("name"))
				args = call.get("input") or {}
				if tool:
					self.update_part(call["id"], label=describe_call(tool, args))
				text, is_error, summary = execute_tool(self.ctx, self.tools, call.get("name"), args)
				self.update_part(call["id"], status="error" if is_error else "done", summary=summary)
				result = {"type": "tool_result", "tool_use_id": call["id"], "content": text}
				if is_error:
					result["is_error"] = True
				results.append(result)
			frappe.db.commit()  # action cards, files and audit rows must exist before the user can click them
			if self.status == "Stopped":
				self._close_running_parts("Stopped")
				break
			if rounds >= max_rounds:
				results.append({"type": "text", "text": STEP_LIMIT_NOTE})
			self.turn_messages.append({"role": "user", "content": results})

		if self.sources:
			self.ctx.add_part(
				{"type": "sources", "items": [{"url": url, "title": clip(title, 120)} for url, title in list(self.sources.items())[:12]]}
			)

	def check_figures(self, history: list) -> str | None:
		"""Trace the large numbers in the answer to query results. Returns a note for the model when it
		should recompute (once per reply); adds a visible caveat when figures still cannot be traced."""
		if not self.settings.verify_figures or not self.ctx.data_tools_used:
			return None
		from omni_ai.engine.grounding import collect_sources, untraced

		texts = []
		for message in history:  # what the user wrote and what earlier replies established
			content = message.get("content")
			for block in content if isinstance(content, list) else [{"type": "text", "text": content}]:
				if block.get("type") == "text":
					texts.append(block.get("text") or "")
				elif block.get("type") == "tool_result":
					texts.append(block.get("content") if isinstance(block.get("content"), str) else dumps(block.get("content")))
		for message in self.turn_messages:  # from this reply only tool results count, never its own drafts
			for block in message.get("content") or []:
				if block.get("type") == "tool_result":
					texts.append(block.get("content") if isinstance(block.get("content"), str) else dumps(block.get("content")))
		for part in self.parts:
			if part.get("type") in ("table", "action") and part.get("rows"):
				texts.append(dumps(part["rows"]))
		last = self.turn_messages[-1]["content"] if self.turn_messages else []
		answer = "\n".join(b.get("text") or "" for b in last if b.get("type") == "text")
		missing = untraced(answer, collect_sources(texts, self.ctx.results))
		if not missing:
			return None
		listed = ", ".join(missing)
		if not self.figures_checked:
			self.figures_checked = True
			for part in self.parts:
				if part.get("type") == "text":
					part["draft"] = True
			self.stream.emit("retract")
			self.stream.emit("status", s="checking")
			return (
				"<system_note>Automatic check of your draft: these figures do not appear in any query result of this "
				f"conversation: {listed}. Do not rely on arithmetic done in your head. Recompute each one with a query "
				"(an aggregate in query_data or run_sql), or remove or correct it, then give the complete final answer "
				"again. The user will only read the new answer, so do not refer to the draft or to this check.</system_note>"
			)
		self.ctx.add_part(
			{
				"type": "notice",
				"level": "warn",
				"text": f"These figures could not be matched to a query result, so please verify them before relying on them: {listed}",
			}
		)
		return None

	def _close_running_parts(self, summary: str):
		for part in self.parts:
			if part.get("type") == "tool" and part.get("status") == "running":
				self.update_part(part["id"], status="error", summary=summary)

	# -- persistence --------------------------------------------------------------

	def text(self) -> str:
		return "\n\n".join(
			p["text"].strip()
			for p in self.parts
			if p.get("type") == "text" and p.get("text", "").strip() and not p.get("draft")
		)

	def save(self) -> str:
		doc = frappe.get_doc(
			{
				"doctype": "AI Assistant Message",
				"conversation": self.conversation,
				"role": "assistant",
				"status": self.status,
				"run_id": self.run_id,
				"model": self.model,
				"content": self.text(),
				"error": clip(self.error or "", 1000),
				"ui": dumps(self.parts),
				"api_messages": dumps(sanitize_turn(self.turn_messages)),
				"input_tokens": self.usage["input"],
				"output_tokens": self.usage["output"],
				"cache_read_tokens": self.usage["cache_read"],
				"web_searches": self.usage["web_searches"],
				"duration_ms": int((time.monotonic() - self.started) * 1000),
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		frappe.db.set_value("AI Assistant Message", doc.name, "owner", self.user, update_modified=False)
		conv = frappe.db.get_value(
			"AI Assistant Conversation", self.conversation, ["message_count", "input_tokens", "output_tokens"], as_dict=True
		)
		frappe.db.set_value(
			"AI Assistant Conversation",
			self.conversation,
			{
				"message_count": to_int(conv.message_count) + 1,
				"input_tokens": to_int(conv.input_tokens) + self.usage["input"],
				"output_tokens": to_int(conv.output_tokens) + self.usage["output"],
				"last_message_at": now_datetime(),
				"model": self.model,
				"active_run": "",
			},
		)
		return doc.name


def run(run_id: str, conversation: str, user: str):
	"""Entry point for one assistant reply (background job or inline). Never raises."""
	if frappe.session.user != user:
		frappe.set_user(user)
	bus.set_state(run_id, status="running", conversation=conversation, started=time.time())
	job = Run(run_id, conversation, user)
	try:
		job.execute()
	except ProviderError as exc:
		job.status, job.error = "Error", str(exc)
	except Exception as exc:
		job.status, job.error = "Error", "Something went wrong while preparing the reply. Please try again."
		frappe.log_error(title="AI Assistant run failed", message=frappe.get_traceback())
		del exc
		try:
			frappe.db.rollback()
		except Exception:
			pass
	message_name = None
	try:
		job._close_running_parts("Stopped")
		message_name = job.save()
		frappe.db.commit()
	except Exception:
		frappe.log_error(title="AI Assistant could not save reply", message=frappe.get_traceback())
		try:
			frappe.db.rollback()
			frappe.db.set_value("AI Assistant Conversation", conversation, "active_run", "")
			frappe.db.commit()
		except Exception:
			pass
	if job.error:
		job.stream.emit("error", message=job.error)
	job.stream.emit("done", message=message_name, status=job.status, usage=job.usage)
	bus.set_state(run_id, status="done")
	try:
		maybe_set_title(conversation, user, job)
	except Exception:
		pass


def maybe_set_title(conversation: str, user: str, job: Run):
	"""Name a new conversation after its first exchange, using a small fast model."""
	conv = frappe.db.get_value("AI Assistant Conversation", conversation, ["title", "message_count"], as_dict=True)
	if not conv or (conv.title and conv.title != "New conversation") or job.status == "Error":
		return
	first = frappe.get_all(
		"AI Assistant Message",
		filters={"conversation": conversation, "role": "user"},
		fields=["content"],
		order_by="creation asc",
		limit=1,
	)
	question = (first[0].content if first else "") or ""
	title = " ".join(question.split())[:60] or "Conversation"
	model = (job.settings.title_model or "").strip()
	if job.settings.provider == "OpenAI-compatible" and model.startswith("claude-"):
		model = job.settings.model  # the default title model only exists on Claude
	if model and question:
		try:
			client = make_client(30)
			reply = client.stream(
				{
					"model": model,
					"max_tokens": 400,
					"system": "Write a title of at most six words for a conversation that starts with the exchange below. "
					"Use the same language as the user. Reply with the title only: no quotes, no full stop.",
					"messages": [
						{
							"role": "user",
							"content": f"User: {clip(question, 600)}\n\nAssistant: {clip(job.text(), 600)}",
						}
					],
				}
			)
			text = " ".join(
				b.get("text", "") for b in reply.get("content") or [] if b.get("type") == "text"
			).strip().strip('"').strip()
			if 2 <= len(text) <= 80:
				title = text.splitlines()[0][:80]
		except Exception:
			pass
	frappe.db.set_value("AI Assistant Conversation", conversation, "title", title)
	frappe.db.commit()
	job.stream.emit("title", title=title)
