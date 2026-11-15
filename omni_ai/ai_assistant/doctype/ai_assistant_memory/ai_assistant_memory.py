import frappe
from frappe import _
from frappe.model.document import Document

MAX_NOTE_CHARS = 600


class AIAssistantMemory(Document):
	def validate(self):
		from omni_ai.tools.memory import looks_like_secret

		self.title = " ".join((self.title or "").split())[:120]
		self.content = (self.content or "").strip()
		if len(self.content) > MAX_NOTE_CHARS:
			frappe.throw(_("A note can be at most {0} characters. Split it into smaller notes.").format(MAX_NOTE_CHARS))
		if looks_like_secret(self.content) or looks_like_secret(self.title):
			frappe.throw(_("This looks like a password or key. The assistant's memory must not hold secrets."))
		if self.scope == "Personal":
			self.user = self.user or self.proposed_by or frappe.session.user
		else:
			self.user = None
		if not self.proposed_by:
			self.proposed_by = frappe.session.user
		if self.status == "Approved" and not self.approved_by:
			self.approved_by = frappe.session.user
		if self.scope == "Company" and self.status == "Approved" and not self.flags.approved_by_admin:
			# company-wide notes shape every user's answers: only a System Manager may approve them
			frappe.only_for("System Manager")

	def on_update(self):
		if self.status == "Approved" and (
			not self.embedding or self.has_value_changed("content") or self.has_value_changed("title")
		):
			from omni_ai.tools.memory import store_embedding

			store_embedding(self)
