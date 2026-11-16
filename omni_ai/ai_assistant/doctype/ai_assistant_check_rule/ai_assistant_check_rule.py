import frappe
from frappe import _
from frappe.model.document import Document


class AIAssistantCheckRule(Document):
	def validate(self):
		from omni_ai.checks.engine import split_names

		self.title = " ".join((self.title or "").split())[:140]
		if not frappe.db.exists("DocType", self.document_type):
			frappe.throw(_("DocType {0} does not exist.").format(self.document_type))
		meta = frappe.get_meta(self.document_type)
		if meta.istable:
			frappe.throw(_("{0} is a child table. Create the rule on its parent DocType and write table.field.").format(self.document_type))
		if meta.issingle or self.document_type.startswith("AI Assistant"):
			frappe.throw(_("Rules cannot be created for {0}.").format(self.document_type))
		if self.run_on == "Submit" and not meta.is_submittable:
			frappe.throw(_("{0} cannot be submitted. Choose Run On: Save.").format(self.document_type))

		def need(fieldname, label):
			if not (self.get(fieldname) or "").strip():
				frappe.throw(_("{0} is required for this kind of check.").format(label))

		def known(path, allow_child=True):
			if "." in path:
				table, fieldname = path.split(".", 1)
				df = meta.get_field(table)
				if not allow_child or not df or df.fieldtype not in ("Table", "Table MultiSelect"):
					frappe.throw(_("{0} is not a table of {1}.").format(table, self.document_type))
				if not frappe.get_meta(df.options).get_field(fieldname):
					frappe.throw(_("Field {0} does not exist in {1}.").format(fieldname, df.options))
			elif not meta.get_field(path):
				frappe.throw(_("Field {0} does not exist in {1}.").format(path, self.document_type))

		kind = self.rule_type
		if kind in ("Duplicate", "Required"):
			need("fields", _("Fields"))
			for path in split_names(self.fields):
				known(path, allow_child=kind == "Required")
		elif kind == "Condition":
			need("field", _("Field"))
			need("operator", _("Must Be"))
			known(self.field.strip())
			if (self.value or "").startswith("field:"):
				other = self.value[6:].strip()
				child = frappe.get_meta(meta.get_field(self.field.split(".")[0]).options) if "." in self.field else None
				if not meta.get_field(other) and not (child and child.get_field(other)):
					frappe.throw(_("Field {0} does not exist in {1}.").format(other, self.document_type))
		elif kind == "Date Window":
			need("field", _("Field"))
			known(self.field.strip(), allow_child=False)
			if meta.get_field(self.field.strip()).fieldtype not in ("Date", "Datetime"):
				frappe.throw(_("{0} is not a date field.").format(self.field))
			if not self.days_back and self.allow_future:
				frappe.throw(_("This rule accepts every date. Set a number of days or do not allow future dates."))
		elif kind == "Price Deviation":
			table = (self.table_field or "items").strip()
			df = meta.get_field(table)
			if not df or df.fieldtype != "Table":
				frappe.throw(_("{0} is not a table of {1}.").format(table, self.document_type))
			child = frappe.get_meta(df.options)
			if not child.get_field("item_code") or not child.get_field("rate"):
				frappe.throw(_("The table {0} has no item and rate columns.").format(table))
			if not frappe.db.exists("DocType", "Item"):
				frappe.throw(_("Price checks need ERPNext."))
			self.table_field = table
		elif kind == "Unusual Amount":
			need("field", _("Field"))
			known(self.field.strip(), allow_child=False)
			if meta.get_field(self.field.strip()).fieldtype not in ("Currency", "Float", "Int"):
				frappe.throw(_("{0} is not a number field.").format(self.field))
			if (self.group_field or "").strip():
				known(self.group_field.strip(), allow_child=False)
			if frappe.utils.flt(self.factor) < 1.5:
				frappe.throw(_("Use a factor of at least 1.5, otherwise ordinary amounts would fail."))

	def on_update(self):
		from omni_ai.checks.engine import clear_cache

		clear_cache()

	def on_trash(self):
		from omni_ai.checks.engine import clear_cache

		clear_cache()
