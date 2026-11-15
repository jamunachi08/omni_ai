"""Automations: workflows outside the system (for example n8n webhooks) that the assistant may start.

An administrator registers each automation with its URL, its parameters and the roles allowed to run
it. The assistant can only propose a run; the request is sent when the user confirms the card. The
URL and the secret header never reach the model or the browser.
"""

import json

import frappe
import requests

from omni_ai.tools.base import Tool, ToolError, clean_value
from omni_ai.utils import ADMIN_ROLES, clip, get_settings, has_any_role

DOCTYPE = "AI Assistant Automation"
MAX_PARAM_CHARS = 4000


def available(user: str) -> list:
	"""Enabled automations this user's roles may run."""
	if not get_settings().enable_automations:
		return []
	out = []
	for name in frappe.get_all(DOCTYPE, filters={"enabled": 1}, pluck="name", limit=100):
		doc = frappe.get_cached_doc(DOCTYPE, name)
		roles = {row.role for row in doc.roles or [] if row.role} or set(ADMIN_ROLES)
		if has_any_role(roles, user):
			out.append(doc)
	return out


def _describe(doc) -> dict:
	return {
		"automation": doc.name,
		"what_it_does": doc.description,
		"parameters": [
			{"name": p.parameter, "description": p.description or "", "required": bool(p.required)}
			for p in doc.parameters or []
		],
	}


def list_automations(args, ctx):
	docs = available(ctx.user)
	if not docs:
		return {"automations": [], "note": "No automations are available to this user."}
	return {"automations": [_describe(d) for d in docs]}


def _clean_parameters(doc, raw) -> dict:
	if not isinstance(raw, dict):
		raw = {}
	known = {p.parameter: p for p in doc.parameters or []}
	unknown = sorted(set(raw) - set(known))
	if unknown:
		raise ToolError(f"Unknown parameter(s): {', '.join(unknown)}. Allowed: {', '.join(known) or 'none'}.")
	missing = [name for name, p in known.items() if p.required and raw.get(name) in (None, "")]
	if missing:
		raise ToolError(f"Missing required parameter(s): {', '.join(missing)}. Ask the user if you do not know them.")
	out = {}
	for name, value in raw.items():
		if isinstance(value, (dict, list)):
			value = json.dumps(value, default=str, ensure_ascii=False)
		value = clean_value(value)
		if isinstance(value, str) and len(value) > MAX_PARAM_CHARS:
			raise ToolError(f"Parameter '{name}' is too long.")
		out[name] = value
	return out


def trigger_automation(args, ctx):
	docs = {d.name: d for d in available(ctx.user)}
	doc = docs.get(str(args.get("automation") or "").strip())
	if not doc:
		raise ToolError(
			"That automation does not exist or this user may not run it. Use list_automations to see what is available.",
			denied=True,
		)
	parameters = _clean_parameters(doc, args.get("parameters"))
	summary = clip(args.get("summary") or doc.description, 300)

	from omni_ai.tools.actions import create_action

	action = create_action(ctx, "Run Automation", f"Run: {doc.name}", {"automation": doc.name, "parameters": parameters})
	ctx.add_part(
		{
			"type": "action",
			"action": action.name,
			"action_type": "Run Automation",
			"status": "Pending",
			"title": doc.name,
			"subtitle": summary,
			"details": [{"label": frappe.unscrub(k), "value": clip(v, 400)} for k, v in parameters.items()],
		}
	)
	return {
		"status": "awaiting_user",
		"message": (
			"A confirmation card is now shown to the user. Nothing has been sent; the automation runs only if they "
			"press Run. Say what it will do and ask them to confirm. Do not claim it has run."
		),
	}


def call_webhook(doc, parameters: dict, user: str, test: bool = False) -> dict:
	headers = {"Content-Type": "application/json", "User-Agent": "frappe-ai-assistant"}
	if doc.auth_header:
		secret = doc.get_password("auth_value", raise_exception=False)
		if secret:
			headers[doc.auth_header] = secret
	body = {
		"automation": doc.name,
		"parameters": parameters,
		"requested_by": user,
		"site": frappe.local.site,
		"test": bool(test),
	}
	from omni_ai.engine import guard

	target = f"automation:{doc.name}"
	try:
		guard.check_open(target, label=f"The automation '{doc.name}'")
	except guard.ServicePaused as exc:
		return {"ok": False, "message": str(exc)}
	try:
		response = requests.post(
			doc.webhook_url, data=json.dumps(body, default=str), headers=headers, timeout=doc.timeout or 30, allow_redirects=False
		)
	except requests.RequestException as exc:
		guard.record_failure(target)
		return {"ok": False, "message": f"The automation could not be reached ({type(exc).__name__})."}
	text = clip((response.text or "").strip(), 500)
	if response.status_code >= 500:
		guard.record_failure(target)
	else:
		guard.record_success(target)
	if 200 <= response.status_code < 300:
		return {"ok": True, "message": f"Accepted (HTTP {response.status_code})." + (f" Reply: {text}" if text else "")}
	return {"ok": False, "message": f"The automation answered HTTP {response.status_code}." + (f" {text}" if text else "")}


def run_automation(payload: dict, user: str) -> dict:
	"""Runs when the user confirms the card. Permissions are checked again at that moment."""
	docs = {d.name: d for d in available(user)}
	doc = docs.get(payload.get("automation"))
	if not doc:
		frappe.throw("This automation is no longer available to you.", frappe.PermissionError)
	parameters = _clean_parameters(doc, payload.get("parameters"))
	result = call_webhook(doc, parameters, user)
	if not result["ok"]:
		frappe.throw(result["message"])
	return {"doctype": None, "name": doc.name, "message": f"{doc.name}: {result['message']}", "no_route": True}


def _any(ctx) -> bool:
	return not ctx.unattended and bool(available(ctx.user))


TOOLS = [
	Tool(
		"list_automations",
		"""List the automations (workflows outside this system, such as sending a WhatsApp message or
		starting an approval flow) that the current user is allowed to start, with their parameters.""",
		{"type": "object", "properties": {}},
		list_automations,
		label="Looking up automations",
		enabled=_any,
	),
	Tool(
		"trigger_automation",
		"""Propose starting one of the registered automations. The user sees a card with the parameters
		and the automation runs only if they confirm. Call list_automations first to get the exact name
		and parameters. Fill parameters only with values the user gave you or that you looked up; ask for
		anything missing rather than guessing.""",
		{
			"type": "object",
			"properties": {
				"automation": {"type": "string"},
				"parameters": {"type": "object"},
				"summary": {"type": "string", "description": "One sentence telling the user what will happen."},
			},
			"required": ["automation"],
		},
		trigger_automation,
		label="Preparing automation",
		enabled=_any,
	),
]
