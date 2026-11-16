"""Email drafted by the assistant, sent only after the user confirms it on a card."""

import re

import frappe
from frappe.utils import validate_email_address

from omni_ai.tools.base import Tool, ToolError
from omni_ai.utils import clip

MAX_RECIPIENTS = 10
MAX_BODY_CHARS = 20000


def _addresses(value, label: str) -> list:
	if isinstance(value, str):
		value = re.split(r"[,;\n]", value)
	out = []
	for item in value or []:
		address = str(item or "").strip()
		if not address:
			continue
		if not validate_email_address(address):
			raise ToolError(f"'{address}' in {label} is not a valid email address. Ask the user or look it up; never guess one.")
		if address.lower() not in [a.lower() for a in out]:
			out.append(address)
	return out


def body_html(text: str) -> str:
	"""Plain text (with blank lines between paragraphs) as simple, escaped HTML."""
	paragraphs = [p.strip() for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]
	return "".join("<p>" + frappe.utils.escape_html(p).replace("\n", "<br>") + "</p>" for p in paragraphs)


def draft_email(args, ctx):
	if not ctx.settings.get("enable_email"):
		raise ToolError("Drafting emails is turned off in the assistant settings. Give the user the text to send themselves.", denied=True)
	to = _addresses(args.get("to"), "to")
	cc = _addresses(args.get("cc"), "cc")
	if not to:
		raise ToolError("At least one recipient is required in 'to'.")
	if len(to) + len(cc) > MAX_RECIPIENTS:
		raise ToolError(f"An email from the assistant can go to at most {MAX_RECIPIENTS} addresses.")
	subject = " ".join(str(args.get("subject") or "").split())[:200]
	body = str(args.get("body") or "").strip()
	if not subject or len(body) < 5:
		raise ToolError("subject and body are required.")
	if len(body) > MAX_BODY_CHARS:
		raise ToolError("The email text is too long.")
	doctype, name = args.get("reference_doctype"), args.get("reference_name")
	if doctype or name:
		from omni_ai.tools.data import get_meta_or_error

		get_meta_or_error(doctype)
		if not name or not frappe.db.exists(doctype, name):
			raise ToolError(f"{doctype} '{name}' was not found.")
		if not frappe.has_permission(doctype, "read", name):
			raise ToolError(f"The current user is not allowed to read {doctype} {name}.", denied=True)

	from omni_ai.tools.actions import create_action

	payload = {"to": to, "cc": cc, "subject": subject, "body": body, "reference_doctype": doctype, "reference_name": name}
	action = create_action(ctx, "Send Email", f"Email: {subject}", payload)
	details = [{"label": "To", "value": ", ".join(to)}]
	if cc:
		details.append({"label": "Cc", "value": ", ".join(cc)})
	details.append({"label": "Subject", "value": subject})
	if doctype:
		details.append({"label": "Filed under", "value": f"{doctype} {name}"})
	ctx.add_part(
		{
			"type": "action",
			"action": action.name,
			"action_type": "Send Email",
			"status": "Pending",
			"title": clip(subject, 120),
			"subtitle": "Email to " + ", ".join(to),
			"description": body,
			"details": details,
		}
	)
	return {
		"status": "awaiting_user",
		"message": (
			"A card with the recipients, subject and full text is now shown to the user. Nothing has been sent; the "
			"email goes out only if they press Confirm. Ask them to check the recipients and the text. Do not say it was sent."
		),
	}


def send_email(payload: dict, user: str) -> dict:
	"""Runs when the user confirms the card, as that user."""
	from frappe.core.doctype.communication.email import make

	doctype, name = payload.get("reference_doctype"), payload.get("reference_name")
	if doctype and not frappe.has_permission(doctype, "read", name):
		frappe.throw("You are not allowed to read the document this email is filed under.", frappe.PermissionError)
	make(
		doctype=doctype or None,
		name=name or None,
		recipients=", ".join(payload.get("to") or []),
		cc=", ".join(payload.get("cc") or []) or None,
		subject=payload.get("subject"),
		content=body_html(payload.get("body")),
		send_email=True,
		communication_medium="Email",
	)
	out = {"doctype": doctype, "name": name, "message": "Email sent to " + ", ".join(payload.get("to") or []) + "."}
	if not doctype:
		out["no_route"] = True
	return out


TOOLS = [
	Tool(
		"draft_email",
		"""Prepare an email for the user to send: a follow-up to a customer, a payment reminder, a reply to
		a supplier. The user sees the recipients, subject and full text on a card and it is sent only if
		they confirm. Take addresses from the user's message or from records (find_documents, get_document);
		never invent one. Write the body as plain text with blank lines between paragraphs, in the
		recipient's language, signed with the user's name. Pass reference_doctype and reference_name to file
		the email under a document (it then appears in that document's timeline).""",
		{
			"type": "object",
			"properties": {
				"to": {"type": "array", "items": {"type": "string"}},
				"cc": {"type": "array", "items": {"type": "string"}},
				"subject": {"type": "string"},
				"body": {"type": "string"},
				"reference_doctype": {"type": "string"},
				"reference_name": {"type": "string"},
			},
			"required": ["to", "subject", "body"],
		},
		draft_email,
		label="Drafting email",
		enabled=lambda ctx: bool(ctx.settings.get("enable_email")) and (not ctx.unattended or ctx.proposals),
	)
]
