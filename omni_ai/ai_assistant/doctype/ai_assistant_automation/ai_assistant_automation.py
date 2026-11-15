import re

import frappe
from frappe import _
from frappe.model.document import Document

from omni_ai.utils import to_int


class AIAssistantAutomation(Document):
	def validate(self):
		self.webhook_url = (self.webhook_url or "").strip()
		if not re.match(r"^https?://[^\s]+$", self.webhook_url):
			frappe.throw(_("Webhook URL must start with http:// or https://"))
		self.timeout = min(max(to_int(self.timeout, 30) or 30, 5), 120)
		if self.auth_header and not re.match(r"^[A-Za-z0-9-]+$", self.auth_header):
			frappe.throw(_("Secret Header Name may contain only letters, numbers and hyphens."))
		seen = set()
		for row in self.parameters or []:
			row.parameter = frappe.scrub((row.parameter or "").strip())
			if not re.match(r"^[a-z][a-z0-9_]{0,40}$", row.parameter):
				frappe.throw(_("Parameter names must be lowercase letters, numbers and underscores."))
			if row.parameter in seen:
				frappe.throw(_("Duplicate parameter {0}.").format(row.parameter))
			seen.add(row.parameter)


@frappe.whitelist()
def send_test(name: str):
	"""Send a test request so an administrator can check the webhook and its secret."""
	frappe.only_for("System Manager")
	from omni_ai.tools.automation import call_webhook

	doc = frappe.get_doc("AI Assistant Automation", name)
	sample = {row.parameter: f"test {row.parameter}" for row in doc.parameters or []}
	return call_webhook(doc, sample, frappe.session.user, test=True)
