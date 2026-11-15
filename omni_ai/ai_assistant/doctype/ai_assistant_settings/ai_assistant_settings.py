import re

import frappe
from frappe import _
from frappe.model.document import Document

from omni_ai.utils import to_int


class AIAssistantSettings(Document):
	def validate(self):
		self.brand_name = (self.brand_name or "Assistant").strip()[:40]
		self.provider = self.provider or "Claude"
		self.base_url = (self.base_url or "").strip().rstrip("/")
		if self.provider == "OpenAI-compatible":
			if not self.base_url or "anthropic.com" in self.base_url:
				frappe.throw(_("Enter the API Base URL of your provider, ending in /v1 (for example http://localhost:11434/v1)."))
			self.model = (self.model or "").strip()
			if not self.model or self.model.startswith("claude-"):
				frappe.throw(_("Enter the model name as your provider calls it."))
			if self.enable_web_search:
				frappe.msgprint(_("Web search is only available with the Claude provider and has been switched off."))
				self.enable_web_search = 0
		else:
			self.model = (self.model or "claude-opus-5-5").strip()
			self.base_url = self.base_url or "https://api.anthropic.com"
		if self.memory_embeddings:
			self.embeddings_base_url = (self.embeddings_base_url or "").strip().rstrip("/")
			if not re.match(r"^https?://", self.embeddings_base_url) or not (self.embeddings_model or "").strip():
				frappe.throw(_("To find notes by meaning, enter the embeddings API base URL and model."))
		if not re.match(r"^https?://", self.base_url):
			frappe.throw(_("API Base URL must start with http:// or https://"))
		if self.accent_color and not re.match(r"^#[0-9a-fA-F]{6}$", self.accent_color):
			frappe.throw(_("Accent Color must be a hex colour such as #4F46E5"))
		self.max_tokens = min(max(to_int(self.max_tokens, 16000) or 16000, 1024), 128000)
		self.max_rows = min(max(to_int(self.max_rows, 5000) or 5000, 50), 50000)
		self.max_tool_rounds = min(max(to_int(self.max_tool_rounds, 25) or 25, 3), 60)
		self.history_turns = min(max(to_int(self.history_turns, 20) or 20, 4), 100)
		self.request_timeout = min(max(to_int(self.request_timeout, 600) or 600, 60), 1800)
		self.web_search_max_uses = min(max(to_int(self.web_search_max_uses, 5) or 5, 1), 20)

	def on_update(self):
		frappe.clear_document_cache(self.doctype, self.name)


@frappe.whitelist()
def test_connection():
	"""Check the saved provider settings: reach the model, and for other providers check tool calling."""
	frappe.only_for("System Manager")
	from omni_ai.engine.agent import make_client
	from omni_ai.engine.client import ProviderError
	from omni_ai.utils import get_settings, is_configured

	settings = get_settings()
	if not is_configured():
		if settings.provider == "OpenAI-compatible":
			return {"ok": False, "message": _("Enter the API Base URL and the model, save, then test again.")}
		return {"ok": False, "message": _("No API key saved yet. Enter the key, save, then test again.")}
	client = make_client(60)
	models = []
	try:
		models = [m.get("id") for m in client.list_models() if m.get("id")][:200]
	except ProviderError as exc:
		if exc.status in (401, 403):
			return {"ok": False, "message": str(exc)}
	except Exception:
		pass  # gateways often do not implement the models endpoint; the message test below is what counts
	try:
		reply = client.stream(
			{"model": settings.model, "max_tokens": 300, "messages": [{"role": "user", "content": "Reply with the single word: ready"}]}
		)
	except ProviderError as exc:
		return {"ok": False, "message": str(exc), "models": models}
	except Exception as exc:
		return {"ok": False, "message": f"{type(exc).__name__}: {exc}", "models": models}
	text = " ".join(b.get("text", "") for b in reply.get("content") or [] if b.get("type") == "text").strip()
	notes = []
	if settings.provider == "OpenAI-compatible":
		# the assistant's data features depend on the model calling tools, which not every model can do
		try:
			probe = client.stream(
				{
					"model": settings.model,
					"max_tokens": 300,
					"messages": [{"role": "user", "content": "Use the get_time tool to find the current time."}],
					"tools": [{"name": "get_time", "description": "Returns the current time.", "input_schema": {"type": "object", "properties": {}}}],
				}
			)
			called = any(b.get("type") == "tool_use" and b.get("name") == "get_time" for b in probe.get("content") or [])
			notes.append(
				_("Tool calling works.")
				if called
				else _("Warning: this model did not call a tool when asked. Data questions, reports and actions need tool calling; choose a model that supports it.")
			)
		except Exception as exc:
			notes.append(_("Warning: the tool-calling check failed ({0}).").format(str(exc)[:200]))
	if settings.enable_memory and settings.memory_embeddings:
		try:
			from omni_ai.tools.memory import embed_texts

			vector = embed_texts(["connection test"])[0]
			notes.append(_("Embeddings work ({0} dimensions).").format(len(vector)))
		except Exception as exc:
			notes.append(_("Warning: the embeddings service did not answer ({0}). Notes will be found by shared words.").format(str(exc)[:200]))
	return {
		"ok": True,
		"message": _("Connected. Model {0} replied: {1}").format(reply.get("model") or settings.model, text[:60] or "(empty)"),
		"notes": notes,
		"models": models,
	}
