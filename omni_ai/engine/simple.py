"""One-call access to the configured AI provider for other apps and server-side code.

    import omni_ai
    text = omni_ai.ask("Summarise this complaint in one sentence: ...")

The call uses the provider, key, personal-data masking, monthly budget and outage pause set up in
AI Assistant Settings. It has no tools: it cannot read or change anything in the system.
"""

import frappe

from omni_ai.engine import guard
from omni_ai.utils import get_settings, is_configured


def ask(prompt: str, system: str | None = None, max_tokens: int = 2000, model: str | None = None, user: str | None = None, timeout: int = 120) -> str:
	"""Send one prompt and return the reply text. Raises frappe.ValidationError when the assistant is
	not set up or the user's budget is used up, and ProviderError when the provider fails."""
	from omni_ai.engine.agent import make_client
	from omni_ai.engine.client import ProviderError

	settings = get_settings()
	if not settings.enabled or not is_configured():
		frappe.throw("The AI assistant is not set up on this site.")
	if not str(prompt or "").strip():
		frappe.throw("prompt is required.")
	guard.check_budget(user or frappe.session.user)
	guard.check_open("provider")
	payload = {
		"model": model or settings.model,
		"max_tokens": min(max(int(max_tokens or 2000), 16), 16000),
		"messages": [{"role": "user", "content": str(prompt)}],
	}
	if system:
		payload["system"] = str(system)
	try:
		reply = make_client(timeout).stream(payload)
	except ProviderError as exc:
		if guard.is_outage(exc):
			guard.record_failure("provider")
		raise
	guard.record_success("provider")
	return "".join(b.get("text", "") for b in reply.get("content") or [] if b.get("type") == "text").strip()
