import frappe
from frappe.model.document import Document


class AIAssistantConversation(Document):
	def before_insert(self):
		# a conversation always belongs to whoever it was created for by the server
		if not self.user:
			self.user = frappe.session.user

	def validate(self):
		if not self.is_new() and self.has_value_changed("user"):
			frappe.throw("The owner of a conversation cannot be changed.")
