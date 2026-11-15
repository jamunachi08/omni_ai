import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import validate_email_address

from omni_ai.utils import ADMIN_ROLES, get_settings, has_any_role, to_int


class AIAssistantSchedule(Document):
	def validate(self):
		from omni_ai.engine.schedule import next_run_after

		settings = get_settings()
		if not settings.enable_schedules:
			frappe.throw(_("Scheduled tasks are turned off in the assistant settings."))
		if self.is_new() or not self.user:
			self.user = frappe.session.user
		elif self.has_value_changed("user") and not has_any_role(ADMIN_ROLES):
			frappe.throw(_("Only a System Manager can change who a task runs as."))
		self.hour = min(max(to_int(self.hour, 8), 0), 23)
		self.day_of_month = min(max(to_int(self.day_of_month, 1) or 1, 1), 28)
		self.prompt = (self.prompt or "").strip()
		if len(self.prompt) > 4000:
			frappe.throw(_("The instruction is too long."))

		own_email = frappe.db.get_value("User", self.user, "email") or self.user
		wanted = [r.strip() for r in (self.recipients or "").replace(",", "\n").splitlines() if r.strip()]
		if not wanted:
			wanted = [own_email]
		for address in wanted:
			validate_email_address(address, throw=True)
		if not has_any_role(ADMIN_ROLES) and {a.lower() for a in wanted} != {own_email.lower()}:
			frappe.throw(_("You can only send scheduled results to your own address ({0}).").format(own_email))
		self.recipients = "\n".join(dict.fromkeys(wanted[:20]))

		limit = to_int(settings.max_schedules_per_user, 5) or 5
		others = frappe.db.count("AI Assistant Schedule", {"user": self.user, "name": ("!=", self.name or "")})
		if self.is_new() and others >= limit and not has_any_role(ADMIN_ROLES):
			frappe.throw(_("You already have {0} scheduled tasks, which is the limit.").format(others))

		if self.enabled:
			if self.is_new() or not self.next_run or any(
				self.has_value_changed(f) for f in ("frequency", "weekday", "day_of_month", "hour", "enabled")
			):
				self.next_run = next_run_after(self)
		else:
			self.next_run = None


@frappe.whitelist()
def run_now(name: str):
	"""Run a scheduled task immediately (its owner or a System Manager)."""
	doc = frappe.get_doc("AI Assistant Schedule", name)
	if doc.user != frappe.session.user:
		frappe.only_for("System Manager")
	frappe.enqueue("omni_ai.engine.schedule.run_schedule", queue="long", timeout=1800, name=name, manual=True)
	return True
