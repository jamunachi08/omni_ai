import re

import frappe
from frappe import _
from frappe.model.document import Document

from omni_ai.utils import to_int


class AIAssistantSettings(Document):
	def validate(self):
		self.brand_name = (self.brand_name or "Assistant").strip()[:40]
		self.model = (self.model or "claude-opus-5-5").strip()
		self.base_url = (self.base_url or "https://api.anthropic.com").strip().rstrip("/")
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
	"""Check the saved API key and model. Returns the models the key can use."""
	frappe.only_for("System Manager")
	from omni_ai.engine.client import ClaudeClient, ProviderError
	from omni_ai.utils import get_api_key, get_settings

	settings = get_settings()
	key = get_api_key()
	if not key:
		return {"ok": False, "message": _("No API key saved yet. Enter the key, save, then test again.")}
	client = ClaudeClient(key, settings.base_url, 30)
	models = []
	try:
		models = [m.get("id") for m in client.list_models() if m.get("id")]
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
	return {
		"ok": True,
		"message": _("Connected. Model {0} replied: {1}").format(reply.get("model") or settings.model, text[:60] or "(empty)"),
		"models": models,
	}
