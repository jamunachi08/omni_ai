"""Memory across conversations.

Notes are small, human-readable statements ("The fiscal year starts in April",
"I prefer amounts in thousands"). Nothing is remembered silently:

* the assistant proposes a note and the user sees the exact text on a card;
* a personal note is saved when that user accepts it;
* a company-wide note additionally needs a System Manager's approval, because
  it shapes every user's answers.

Approved notes are recalled by relevance and handed to the model with the
user's message as context, never as instructions that override its rules.
"""

import re

import frappe
from frappe.utils import now_datetime

from omni_ai.tools.base import Tool, ToolError
from omni_ai.utils import ADMIN_ROLES, clip, get_settings, has_any_role, to_int

DOCTYPE = "AI Assistant Memory"
MAX_NOTE_CHARS = 600
RECALL_CHAR_BUDGET = 3500
WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
STOPWORDS = set(
	"the and for with from that this what which when where how are was were has have had not you your our "
	"show give list make please can could would should about into over per all any each than then them they "
	"report reports data".split()
)
SECRET_HINTS = re.compile(
	r"(password|passwd|passcode|api[ _-]?key|secret|private key|bearer |token\b|sk-[A-Za-z0-9]{8,}|"
	r"-----BEGIN|[A-Za-z0-9+/]{40,}={0,2})",
	re.I,
)


def looks_like_secret(text: str) -> bool:
	return bool(SECRET_HINTS.search(text or ""))


def enabled() -> bool:
	return bool(get_settings().enable_memory)


def _words(text: str) -> set:
	return {w.lower() for w in WORD_RE.findall(text or "")} - STOPWORDS


def _visible_filters(user: str):
	"""Approved notes this user may be given: their own personal notes plus company notes."""
	return {"status": "Approved"}, [["scope", "=", "Company"], ["user", "=", user]]


def embeddings_on() -> bool:
	settings = get_settings()
	return bool(settings.enable_memory and settings.memory_embeddings and settings.embeddings_base_url and settings.embeddings_model)


def embed_texts(texts: list) -> list:
	"""Vectors from the configured embeddings service. Raises when it cannot be reached."""
	from omni_ai.engine.openai_client import embed

	settings = get_settings()
	key = settings.get_password("embeddings_api_key", raise_exception=False) or ""
	return embed(settings.embeddings_base_url, key, settings.embeddings_model, [clip(t, 2000) for t in texts])


def _cosine(a: list, b: list) -> float:
	if not a or not b or len(a) != len(b):
		return -1.0
	dot = sum(x * y for x, y in zip(a, b))
	na = sum(x * x for x in a) ** 0.5
	nb = sum(y * y for y in b) ** 0.5
	return dot / (na * nb) if na and nb else -1.0


def store_embedding(doc):
	"""Compute and keep a note's vector (best effort: recall falls back to shared words without it)."""
	if not embeddings_on() or doc.status != "Approved":
		return
	try:
		vector = embed_texts([f"{doc.title}. {doc.content}"])[0]
		frappe.db.set_value(
			DOCTYPE,
			doc.name,
			{"embedding": frappe.as_json([round(v, 5) for v in vector], indent=None), "embedding_model": get_settings().embeddings_model},
			update_modified=False,
		)
	except Exception:
		frappe.log_error(title="AI Assistant: could not embed a memory note")


def _recall_by_meaning(notes: list, query: str, limit: int) -> list | None:
	"""Notes ranked by similarity to the question, or None when the embeddings service is unavailable."""
	model = get_settings().embeddings_model
	usable = [n for n in notes if n.embedding and n.embedding_model == model]
	if not usable or not (query or "").strip():
		return None
	try:
		vector = embed_texts([query])[0]
	except Exception:
		return None
	scored = []
	for note in usable:
		try:
			scored.append((_cosine(vector, frappe.parse_json(note.embedding)), note))
		except Exception:
			continue
	scored.sort(key=lambda item: item[0], reverse=True)
	if not scored:
		return None
	# keep the notes clearly related to the question: close to the best match and not unrelated
	best = scored[0][0]
	chosen = [note for score, note in scored if score >= max(0.2, best - 0.25)][:limit]
	# notes without a vector yet (just approved, or the model changed) still get a chance by shared words
	rest = [n for n in notes if n not in usable]
	wanted = _words(query)
	chosen += [n for n in rest if wanted & _words(f"{n.title} {n.content}")][: max(0, limit - len(chosen))]
	return chosen


def recall(user: str, query: str, limit: int | None = None) -> list:
	"""Approved notes most relevant to `query`, within a small budget. Returns [{name, title, content, scope}]."""
	if not enabled():
		return []
	limit = max(1, min(to_int(limit or get_settings().memory_notes_per_reply, 8) or 8, 20))
	filters, or_filters = _visible_filters(user)
	notes = frappe.get_all(
		DOCTYPE,
		filters=filters,
		or_filters=or_filters,
		fields=["name", "title", "content", "scope", "user", "modified", "embedding", "embedding_model"],
		order_by="modified desc",
		limit=400,
	)
	notes = [n for n in notes if n.scope == "Company" or n.user == user]
	if not notes:
		return []
	if embeddings_on() and len(notes) > limit:
		ranked = _recall_by_meaning(notes, query, limit)
		if ranked is not None:
			out, used = [], 0
			for note in ranked:
				size = len(note.content) + len(note.title)
				if used + size > RECALL_CHAR_BUDGET:
					break
				used += size
				out.append({"name": note.name, "title": note.title, "content": note.content, "scope": note.scope})
			return out
	wanted = _words(query)
	scored = []
	for index, note in enumerate(notes):
		words = _words(f"{note.title} {note.content}")
		overlap = len(wanted & words)
		# with few notes everything is worth including; with many, a note must share words with the question
		if overlap == 0 and len(notes) > limit:
			continue
		title_bonus = 2 * len(wanted & _words(note.title))
		scored.append((overlap + title_bonus, -index, note))
	scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
	out, used = [], 0
	for _score, _order, note in scored[:limit]:
		size = len(note.content) + len(note.title)
		if used + size > RECALL_CHAR_BUDGET:
			break
		used += size
		out.append({"name": note.name, "title": note.title, "content": note.content, "scope": note.scope})
	return out


def mark_used(names: list):
	if not names:
		return
	try:
		for name in names:
			frappe.db.set_value(
				DOCTYPE,
				name,
				{"use_count": to_int(frappe.db.get_value(DOCTYPE, name, "use_count")) + 1, "last_used": now_datetime()},
				update_modified=False,
			)
	except Exception:
		pass


# -- tools ---------------------------------------------------------------------------


def remember(args, ctx):
	if not enabled():
		raise ToolError("Memory is turned off in the assistant settings.", denied=True)
	note = " ".join(str(args.get("note") or "").split())
	title = " ".join(str(args.get("title") or "").split())[:120] or clip(note, 60)
	scope = "Company" if str(args.get("scope") or "").lower() == "company" else "Personal"
	if len(note) < 8:
		raise ToolError("The note is too short to be useful.")
	if len(note) > MAX_NOTE_CHARS:
		raise ToolError(f"Keep a note under {MAX_NOTE_CHARS} characters. Save separate facts as separate notes.")
	if looks_like_secret(note) or looks_like_secret(title):
		raise ToolError("This looks like a password, key or token. Secrets are never stored in memory.", denied=True)
	duplicate = frappe.db.exists(DOCTYPE, {"content": note, "status": ("in", ("Approved", "Proposed"))})
	if duplicate:
		raise ToolError("This exact note is already saved or waiting for approval.")

	from omni_ai.tools.actions import create_action

	needs_admin = scope == "Company" and not has_any_role(ADMIN_ROLES, ctx.user)
	action = create_action(ctx, "Save Memory", f"Remember: {title}", {"title": title, "note": note, "scope": scope})
	ctx.add_part(
		{
			"type": "action",
			"action": action.name,
			"action_type": "Save Memory",
			"status": "Pending",
			"title": title,
			"subtitle": "For everyone in the company" if scope == "Company" else "Only for you",
			"description": note,
			"details": (
				[{"label": "Approval", "value": "A System Manager must approve this before it is used."}] if needs_admin else []
			),
		}
	)
	return {
		"status": "awaiting_user",
		"message": (
			"A card with the note is now shown to the user. It is NOT remembered yet: it is saved only if they accept it"
			+ (", and then a System Manager must approve it because it is company-wide" if needs_admin else "")
			+ ". Mention this in one short sentence and carry on with their request."
		),
	}


def save_memory(payload: dict, user: str, conversation: str) -> dict:
	"""Runs when the user accepts a memory card."""
	if not enabled():
		frappe.throw("Memory is turned off in the assistant settings.")
	scope = payload.get("scope") or "Personal"
	approved = scope == "Personal" or has_any_role(ADMIN_ROLES, user)
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"title": payload.get("title"),
			"content": payload.get("note"),
			"scope": scope,
			"user": user if scope == "Personal" else None,
			"status": "Approved" if approved else "Proposed",
			"proposed_by": user,
			"approved_by": user if approved else None,
			"source_conversation": conversation,
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	frappe.db.set_value(DOCTYPE, doc.name, "owner", user, update_modified=False)
	if approved:
		message = "Saved. I'll keep this in mind in future conversations."
	else:
		message = "Sent for approval. It will be used once a System Manager approves it."
	return {"doctype": DOCTYPE, "name": doc.name, "message": message, "no_route": True}


def search_memory(args, ctx):
	if not enabled():
		raise ToolError("Memory is turned off in the assistant settings.", denied=True)
	notes = recall(ctx.user, str(args.get("query") or ""), limit=15)
	if not notes:
		return {"notes": [], "note": "Nothing relevant is saved in memory."}
	mark_used([n["name"] for n in notes])
	return {"notes": [{"title": n["title"], "note": n["content"], "applies_to": n["scope"]} for n in notes]}


TOOLS = [
	Tool(
		"remember",
		"""Offer to remember something for future conversations: a stable preference, a business rule, a
		definition or a correction the user gave you (for example "our fiscal year starts in April",
		"exclude customer X from sales figures", "I want amounts in thousands"). Use scope 'personal' for
		one person's preferences and 'company' for rules that apply to everyone. The user sees the exact
		text and decides; company notes also need an administrator's approval. Write the note as one
		short, self-contained statement. Never store secrets, personal data about individuals, or
		one-off facts such as today's figures. Do not call this for things already in a <memory> block.""",
		{
			"type": "object",
			"properties": {
				"title": {"type": "string", "description": "A few words naming the note."},
				"note": {"type": "string", "description": "The statement to remember (max 600 characters)."},
				"scope": {"type": "string", "enum": ["personal", "company"]},
			},
			"required": ["note", "scope"],
		},
		remember,
		label="Suggesting a note to remember",
		enabled=lambda ctx: enabled() and not ctx.unattended,
	),
	Tool(
		"search_memory",
		"""Search the notes saved from earlier conversations (the user's own and the company's). The most
		relevant notes are already given to you in a <memory> block with each message; use this only when
		you need to look for something that block does not cover.""",
		{"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
		search_memory,
		label="Checking memory",
		enabled=lambda ctx: enabled(),
	),
]
