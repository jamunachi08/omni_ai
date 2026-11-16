"""Let the user set up a scheduled task from the chat (confirmed on a card, like every other change)."""

import frappe

from omni_ai.tools.base import Tool, ToolError
from omni_ai.utils import clip, get_settings, to_int

DOCTYPE = "AI Assistant Schedule"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _clean(args) -> dict:
	frequency = str(args.get("frequency") or "Weekly").capitalize()
	if frequency not in ("Daily", "Weekly", "Monthly"):
		raise ToolError("frequency must be Daily, Weekly or Monthly.")
	weekday = str(args.get("weekday") or "Monday").capitalize()
	if weekday not in WEEKDAYS:
		raise ToolError(f"weekday must be one of: {', '.join(WEEKDAYS)}.")
	prompt = (args.get("prompt") or "").strip()
	if len(prompt) < 10:
		raise ToolError("prompt must be the full instruction to run each time, written so it makes sense by itself.")
	return {
		"title": " ".join(str(args.get("title") or "").split())[:100] or clip(prompt, 60),
		"prompt": prompt[:4000],
		"frequency": frequency,
		"weekday": weekday,
		"day_of_month": min(max(to_int(args.get("day_of_month"), 1) or 1, 1), 28),
		"hour": min(max(to_int(args.get("hour"), 8), 0), 23),
		"only_when_needed": 1 if args.get("only_when_needed") else 0,
	}


def when_text(values: dict) -> str:
	hour = f"{int(values['hour']):02d}:00"
	if values["frequency"] == "Daily":
		return f"Every day at {hour}"
	if values["frequency"] == "Weekly":
		return f"Every {values['weekday']} at {hour}"
	return f"On day {values['day_of_month']} of each month at {hour}"


def propose_schedule(args, ctx):
	if not get_settings().enable_schedules:
		raise ToolError("Scheduled tasks are turned off in the assistant settings.", denied=True)
	values = _clean(args)
	email = frappe.db.get_value("User", ctx.user, "email") or ctx.user

	from omni_ai.tools.actions import create_action

	action = create_action(ctx, "Create Schedule", f"Schedule: {values['title']}", values)
	ctx.add_part(
		{
			"type": "action",
			"action": action.name,
			"action_type": "Create Schedule",
			"status": "Pending",
			"title": values["title"],
			"subtitle": f"{when_text(values)} (site time), emailed to {email}"
			+ (" only when there is something to report" if values["only_when_needed"] else ""),
			"details": [{"label": "Each time I will", "value": values["prompt"]}],
		}
	)
	return {
		"status": "awaiting_user",
		"message": (
			"A card is now shown to the user. The schedule is NOT created yet; it is created only if they confirm. "
			"Tell them when it would run and that the result is emailed to them. Do not claim it is set up."
		),
	}


def create_schedule(payload: dict, user: str) -> dict:
	"""Runs when the user confirms the card, as that user (the controller enforces the limits)."""
	doc = frappe.get_doc({"doctype": DOCTYPE, **{k: payload.get(k) for k in ("title", "prompt", "frequency", "weekday", "day_of_month", "hour", "only_when_needed")}})
	doc.user = user
	doc.enabled = 1
	doc.insert()
	return {
		"doctype": DOCTYPE,
		"name": doc.name,
		"message": f"Scheduled. {when_text(payload)}; first run {frappe.utils.format_datetime(doc.next_run)}.",
	}


TOOLS = [
	Tool(
		"propose_schedule",
		"""Set up a task that runs on a schedule and emails the result to the user, for example a weekly
		sales summary. Use it when the user asks for something "every Monday", "daily", "each month" or
		"automatically". The prompt is what you will be asked each time, with no memory of this
		conversation, so write it as a complete instruction including the period in relative terms
		("last week", "month to date"), the company or filters, and the format wanted (table, chart).
		For an alert ("tell me when stock of X falls below 50", "warn me about invoices overdue more than 60
		days") set only_when_needed: the check then runs on the schedule but the user is only notified when
		there is something to report; state the condition clearly in the prompt.
		The user confirms on a card. Times are in the site's time zone.""",
		{
			"type": "object",
			"properties": {
				"title": {"type": "string"},
				"prompt": {"type": "string", "description": "Complete, self-contained instruction to run each time."},
				"frequency": {"type": "string", "enum": ["Daily", "Weekly", "Monthly"]},
				"weekday": {"type": "string", "enum": WEEKDAYS, "description": "For Weekly."},
				"day_of_month": {"type": "integer", "description": "For Monthly, 1 to 28."},
				"hour": {"type": "integer", "description": "0 to 23, site time. Default 8."},
				"only_when_needed": {"type": "boolean", "description": "Alert mode: notify only when there is something to report."},
			},
			"required": ["prompt", "frequency"],
		},
		propose_schedule,
		label="Preparing schedule",
		enabled=lambda ctx: bool(ctx.settings.enable_schedules) and not ctx.unattended,
	)
]
