"""Starter rules, created once at install for the DocTypes that exist on the site.

They all start as warnings: a warning never stops anyone from working, so installing the app cannot
break an existing process. An administrator turns the ones that matter into Block.
"""

import frappe

PRICE = dict(rule_type="Price Deviation", table_field="items")
STARTER_RULES = [
	# purchasing
	dict(title="Supplier invoice number entered twice", document_type="Purchase Invoice", rule_type="Duplicate", fields="supplier, bill_no"),
	dict(title="Purchase price well above the last purchase", document_type="Purchase Order", reference="Last Purchase Rate", direction="Above only", tolerance_percent=25, **PRICE),
	dict(title="Purchase price well above the last purchase", document_type="Purchase Invoice", reference="Last Purchase Rate", direction="Above only", tolerance_percent=25, **PRICE),
	dict(title="Unusually large purchase invoice", document_type="Purchase Invoice", rule_type="Unusual Amount", field="base_grand_total", group_field="supplier", factor=5),
	dict(title="Purchase invoice date out of range", document_type="Purchase Invoice", rule_type="Date Window", field="posting_date", days_back=60),
	# sales
	dict(title="Selling below cost", document_type="Sales Order", reference="Valuation Rate", direction="Below only", tolerance_percent=0, **PRICE),
	dict(title="Selling below cost", document_type="Sales Invoice", reference="Valuation Rate", direction="Below only", tolerance_percent=0, **PRICE),
	dict(title="Selling price far from the price list", document_type="Quotation", reference="Price List Rate", direction="Below only", tolerance_percent=30, **PRICE),
	dict(title="Unusually large sales invoice", document_type="Sales Invoice", rule_type="Unusual Amount", field="base_grand_total", group_field="customer", factor=5),
	dict(title="Sales invoice date out of range", document_type="Sales Invoice", rule_type="Date Window", field="posting_date", days_back=45),
	dict(title="Due date before the invoice date", document_type="Sales Invoice", rule_type="Condition", field="due_date", operator=">=", value="field:posting_date"),
	# accounts
	dict(title="Payment reference used twice for the same party", document_type="Payment Entry", rule_type="Duplicate", fields="party, reference_no"),
	dict(title="Unusually large payment", document_type="Payment Entry", rule_type="Unusual Amount", field="base_paid_amount", group_field="party", factor=5),
	dict(title="Payment date out of range", document_type="Payment Entry", rule_type="Date Window", field="posting_date", days_back=45),
	dict(title="Journal entry date out of range", document_type="Journal Entry", rule_type="Date Window", field="posting_date", days_back=45),
	dict(title="Unusually large journal entry", document_type="Journal Entry", rule_type="Unusual Amount", field="total_debit", factor=8),
	# stock
	dict(title="Stock entry date out of range", document_type="Stock Entry", rule_type="Date Window", field="posting_date", days_back=30),
	# master data
	dict(title="Customer already exists", document_type="Customer", rule_type="Duplicate", fields="customer_name", ignore_case_and_spacing=1),
	dict(title="Customer tax ID already used", document_type="Customer", rule_type="Duplicate", fields="tax_id"),
	dict(title="Supplier already exists", document_type="Supplier", rule_type="Duplicate", fields="supplier_name", ignore_case_and_spacing=1),
	dict(title="Supplier tax ID already used", document_type="Supplier", rule_type="Duplicate", fields="tax_id"),
	dict(title="Item with the same name exists", document_type="Item", rule_type="Duplicate", fields="item_name", ignore_case_and_spacing=1),
	# projects
	dict(title="Task ends before it starts", document_type="Task", rule_type="Condition", field="exp_end_date", operator=">=", value="field:exp_start_date"),
	dict(title="Project ends before it starts", document_type="Project", rule_type="Condition", field="expected_end_date", operator=">=", value="field:expected_start_date"),
	# HR (only when the HR app is installed)
	dict(title="Employee entered twice", document_type="Employee", rule_type="Duplicate", fields="employee_name, date_of_birth", ignore_case_and_spacing=1),
	dict(title="Expense claim date out of range", document_type="Expense Claim", rule_type="Date Window", field="posting_date", days_back=90),
	dict(title="Unusually large expense claim", document_type="Expense Claim", rule_type="Unusual Amount", field="total_claimed_amount", group_field="employee", factor=5),
	dict(title="Leave ends before it starts", document_type="Leave Application", rule_type="Condition", field="to_date", operator=">=", value="field:from_date"),
]


def create_starter_rules() -> int:
	"""Insert the starter rules whose DocType and fields exist here. Returns how many were created."""
	created = 0
	for values in STARTER_RULES:
		if not frappe.db.exists("DocType", values["document_type"]):
			continue
		if frappe.db.exists("AI Assistant Check Rule", {"title": values["title"], "document_type": values["document_type"]}):
			continue
		doc = frappe.get_doc({"doctype": "AI Assistant Check Rule", "enabled": 1, "action": "Warn", "run_on": "Save", **values})
		doc.flags.ignore_permissions = True
		try:
			doc.insert()
			created += 1
		except Exception:
			frappe.local.message_log = []  # a field this site does not have: skip that rule
	return created
