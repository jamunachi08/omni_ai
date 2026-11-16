"""Scheduled tasks: run a saved instruction as its owner and email the result."""

import datetime

import frappe
from frappe.utils import get_datetime, get_url, now_datetime

from omni_ai.utils import clip, frappe_major, get_settings, has_access, is_configured, to_int

DOCTYPE = "AI Assistant Schedule"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MAX_EMAIL_TABLE_ROWS = 60
NOTHING = "NOTHING_TO_REPORT"  # what an alert task answers when there is nothing worth sending


def next_run_after(doc, after=None) -> datetime.datetime:
	"""The first scheduled time strictly after `after` (site time)."""
	after = get_datetime(after or now_datetime())
	hour = min(max(to_int(doc.hour, 8), 0), 23)
	candidate = after.replace(hour=hour, minute=0, second=0, microsecond=0)
	for _ in range(0, 400):
		if candidate > after:
			if doc.frequency == "Daily":
				return candidate
			if doc.frequency == "Weekly" and WEEKDAYS[candidate.weekday()] == (doc.weekday or "Monday"):
				return candidate
			if doc.frequency == "Monthly" and candidate.day == min(max(to_int(doc.day_of_month, 1) or 1, 1), 28):
				return candidate
		candidate += datetime.timedelta(days=1)
	return candidate


def run_due():
	"""Hourly scheduler entry point."""
	settings = get_settings()
	if not settings.enabled or not settings.enable_schedules:
		return
	due = frappe.get_all(
		DOCTYPE, filters={"enabled": 1, "next_run": ("<=", now_datetime())}, pluck="name", limit=50, order_by="next_run asc"
	)
	for name in due:
		# move the next run first, so a slow or failing task can never be picked up twice
		doc = frappe.get_doc(DOCTYPE, name)
		frappe.db.set_value(DOCTYPE, name, "next_run", next_run_after(doc), update_modified=False)
		frappe.db.commit()
		frappe.enqueue("omni_ai.engine.schedule.run_schedule", queue="long", timeout=1800, name=name)


def run_schedule(name: str, manual: bool = False):
	"""Run one scheduled task. Never raises; the outcome is written on the task."""
	doc = frappe.get_doc(DOCTYPE, name)
	user = doc.user
	status, conversation = "Failed", None
	try:
		if not frappe.db.get_value("User", user, "enabled"):
			raise frappe.ValidationError("The user this task runs as is disabled.")
		frappe.set_user(user)
		if not has_access(user):
			raise frappe.ValidationError("The user this task runs as no longer has access to the assistant.")
		if not is_configured():
			raise frappe.ValidationError("The assistant is not connected to an AI provider.")
		from omni_ai.engine import guard

		guard.check_budget(user)
		conversation, reply = _run_prompt(doc, user)
		if reply.status == "Error":
			raise frappe.ValidationError(reply.error or "The assistant could not complete the task.")
		if doc.get("only_when_needed") and NOTHING in reply.text():
			status = "Nothing to report"
		else:
			_send_email(doc, user, conversation, reply)
			status = "Sent"
			if doc.get("chat_channel") and (doc.get("chat_webhook") or "").strip():
				try:
					from omni_ai.engine import delivery

					base = "/desk" if frappe_major() >= 16 else "/app"
					delivery.post(doc.chat_channel, doc.chat_webhook, doc.title, reply.text(), get_url(f"{base}/ai-assistant/{conversation}"))
					status = f"Sent, and posted to {doc.chat_channel}"
				except Exception as exc:
					status = clip(f"Sent by email; {doc.chat_channel} post failed: {exc}", 130)
	except Exception as exc:
		frappe.db.rollback()
		status = clip(f"Failed: {exc}", 130)
		if not isinstance(exc, frappe.ValidationError):
			frappe.log_error(title="AI Assistant scheduled task failed", message=frappe.get_traceback())
	finally:
		frappe.set_user("Administrator")
		frappe.db.set_value(
			DOCTYPE,
			name,
			{"last_run": now_datetime(), "last_status": status, "last_conversation": conversation},
			update_modified=False,
		)
		frappe.db.commit()


def _run_prompt(doc, user: str):
	from omni_ai.engine import stream as bus
	from omni_ai.engine.agent import Run

	may_propose = bool(doc.get("allow_proposals")) and bool(get_settings().get("enable_proposals"))
	conv = frappe.get_doc({"doctype": "AI Assistant Conversation", "title": doc.title, "user": user, "schedule": doc.name})
	conv.flags.ignore_permissions = True
	conv.insert(ignore_permissions=True)
	frappe.db.set_value("AI Assistant Conversation", conv.name, "owner", user, update_modified=False)
	message = frappe.get_doc(
		{
			"doctype": "AI Assistant Message",
			"conversation": conv.name,
			"role": "user",
			"status": "Complete",
			"content": doc.prompt,
			"notes": frappe.as_json(
				[
					"This is a scheduled task running unattended; nobody is present to answer questions or confirm cards. "
					"Do the work now and give a complete, self-contained result suitable for an email. Tables shown with "
					"show_table and files made with export_data are included in the email; charts are not, so describe trends in words."
				]
				+ (
					[
						"You may prepare documents (propose_document_action) and emails (draft_email) in this run. Nothing "
						"is created or sent now: each one waits in the Control Room until the user approves it. Prepare only "
						"what the instruction asks for, at most 20 items, and end your result with a short list of what is "
						"waiting for approval and why."
					]
					if may_propose
					else []
				)
				+ (
					[
						"This task is an alert. Check the data first. If nothing needs the reader's attention (the condition "
						f"in the instruction is not met, or there are no matching records), reply with exactly {NOTHING} and "
						"nothing else; nobody is notified then. Otherwise report what needs attention, most important first."
					]
					if doc.get("only_when_needed")
					else []
				)
			),
		}
	)
	message.flags.ignore_permissions = True
	message.insert(ignore_permissions=True)
	frappe.db.set_value("AI Assistant Message", message.name, "owner", user, update_modified=False)
	frappe.db.commit()

	run_id = frappe.generate_hash(length=16)
	bus.set_state(run_id, status="running", conversation=conv.name)
	job = Run(run_id, conv.name, user, unattended=True, proposals=may_propose)
	try:
		job.execute()
	except Exception as exc:
		from omni_ai.engine.client import ProviderError

		job.status = "Error"
		job.error = str(exc) if isinstance(exc, ProviderError) else "Something went wrong while running the task."
		if not isinstance(exc, ProviderError):
			frappe.log_error(title="AI Assistant scheduled run failed", message=frappe.get_traceback())
		frappe.db.rollback()
	job._close_running_parts("Stopped")
	job.save()
	frappe.db.commit()
	bus.set_state(run_id, status="done")
	return conv.name, job


def _table_html(part: dict) -> str:
	esc = frappe.utils.escape_html
	head = "".join(
		f'<th style="text-align:left;padding:6px 10px;border-bottom:2px solid #d1d5db;white-space:nowrap">{esc(str(c.get("label") or c.get("fieldname") or ""))}</th>'
		for c in part.get("columns") or []
	)
	body = []
	for row in (part.get("rows") or [])[:MAX_EMAIL_TABLE_ROWS]:
		cells = []
		for value in row:
			number = isinstance(value, (int, float)) and not isinstance(value, bool)
			text = f"{value:,.2f}" if isinstance(value, float) else ("" if value is None else str(value))
			align = "right" if number else "left"
			cells.append(f'<td style="padding:5px 10px;border-bottom:1px solid #e5e7eb;text-align:{align}">{esc(text)}</td>')
		body.append("<tr>" + "".join(cells) + "</tr>")
	total = part.get("total_rows") or len(part.get("rows") or [])
	more = ""
	if total > MAX_EMAIL_TABLE_ROWS:
		more = f'<p style="color:#6b7280;font-size:12px">Showing the first {MAX_EMAIL_TABLE_ROWS} of {total} rows.</p>'
	title = f'<p style="font-weight:600;margin:16px 0 6px">{esc(part.get("title") or "")}</p>' if part.get("title") else ""
	return f'{title}<table style="border-collapse:collapse;font-size:13px">{"<tr>" + head + "</tr>"}{"".join(body)}</table>{more}'


def _send_email(doc, user: str, conversation: str, reply):
	settings = get_settings()
	brand = settings.brand_name or "Assistant"
	sections, attachments, waiting = [], [], []
	for part in reply.parts:
		kind = part.get("type")
		if kind == "text" and (part.get("text") or "").strip():
			sections.append(frappe.utils.md_to_html(part["text"]))
		elif kind == "table":
			sections.append(_table_html(part))
		elif kind == "kpis":
			cells = "".join(
				'<td style="padding:10px 18px 10px 0;vertical-align:top"><div style="color:#6b7280;font-size:12px">'
				f'{frappe.utils.escape_html(t.get("label") or "")}</div><div style="font-size:20px;font-weight:600">'
				f'{frappe.utils.escape_html(t.get("value") or "")}</div><div style="color:#6b7280;font-size:12px">'
				f'{frappe.utils.escape_html(t.get("note") or "")}</div></td>'
				for t in part.get("items") or []
			)
			sections.append(f'<table style="border-collapse:collapse;margin:6px 0 12px"><tr>{cells}</tr></table>')
		elif kind == "action" and part.get("status") == "Pending":
			waiting.append(frappe.utils.escape_html(part.get("title") or ""))
		elif kind == "chart":
			sections.append(
				f'<p style="color:#6b7280;font-size:12px">Chart: {frappe.utils.escape_html(part.get("title") or "")} (open the conversation to view it)</p>'
			)
		elif kind == "file" and part.get("file_url"):
			try:
				file_doc = frappe.get_doc("File", {"file_url": part["file_url"], "attached_to_name": conversation})
				attachments.append({"fname": file_doc.file_name, "fcontent": file_doc.get_content()})
			except Exception:
				pass
	base = "/desk" if frappe_major() >= 16 else "/app"
	link = get_url(f"{base}/ai-assistant/{conversation}")
	if waiting:
		room = get_url(f"{base}/control-room")
		sections.append(
			f'<p style="margin-top:16px;padding:10px 14px;border:1px solid #fcd34d;border-radius:8px;background:#fffbeb">'
			f"<b>{len(waiting)} prepared for your approval</b> (nothing has been created or sent):<br>"
			+ "<br>".join(waiting[:20])
			+ f'<br><a href="{room}">Review and approve in the Control Room</a></p>'
		)
	html = (
		'<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;font-size:14px;line-height:1.55;color:#1f2937">'
		+ "".join(sections)
		+ f'<p style="margin-top:22px"><a href="{link}">Open this result in {frappe.utils.escape_html(brand)}</a></p>'
		+ f'<p style="color:#6b7280;font-size:12px">Sent automatically by {frappe.utils.escape_html(brand)} for the scheduled task '
		f"“{frappe.utils.escape_html(doc.title)}”. AI-generated: check important figures. "
		"To stop these emails, disable the task under AI Assistant Schedule.</p></div>"
	)
	recipients = [r.strip() for r in (doc.recipients or "").splitlines() if r.strip()]
	if not recipients:
		recipients = [frappe.db.get_value("User", user, "email") or user]
	frappe.sendmail(
		recipients=recipients,
		subject=f"{doc.title} · {frappe.utils.formatdate(frappe.utils.nowdate())}",
		message=html,
		attachments=attachments or None,
		reference_doctype=DOCTYPE,
		reference_name=doc.name,
		now=False,
	)
