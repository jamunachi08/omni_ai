"""Control Room monitors: fixed, permission-aware counts of what needs attention in each area.

Every monitor is a DocType plus list filters, so a count can be opened as the same filtered list in
the desk. Counts are made with the viewing user's permissions: people see their own exceptions, and
a monitor on a DocType they may not read is left out. Nothing here calls the AI provider.
"""

import frappe
from frappe.utils import add_days, flt, today

from omni_ai.utils import agg

SEVERITY = ("high", "medium", "low")


def _monitors() -> list:
	t = today()
	open_so = ["status", "not in", ["Completed", "Closed", "On Hold", "Cancelled"]]
	return [
		# ---- sales
		dict(key="so_overdue", area="Sales", severity="high", title="Sales orders past their delivery date",
			why="Customers are waiting. Deliver, or agree a new date.", doctype="Sales Order",
			filters=[["docstatus", "=", 1], open_so, ["per_delivered", "<", 100], ["delivery_date", "<", t]], amount="base_grand_total"),
		dict(key="dn_to_bill", area="Sales", severity="high", title="Delivered but not invoiced",
			why="Goods have left and no invoice was raised: revenue and cash are delayed.", doctype="Delivery Note",
			filters=[["docstatus", "=", 1], ["status", "=", "To Bill"]], amount="base_grand_total"),
		dict(key="so_to_bill", area="Sales", severity="medium", title="Sales orders fully delivered, waiting for an invoice",
			why="Everything was delivered; only the invoice is missing.", doctype="Sales Order",
			filters=[["docstatus", "=", 1], ["status", "=", "To Bill"]], amount="base_grand_total"),
		dict(key="quotes_expired", area="Sales", severity="low", title="Open quotations past their validity date",
			why="Follow up or close them so the pipeline stays true.", doctype="Quotation",
			filters=[["docstatus", "=", 1], ["status", "=", "Open"], ["valid_till", "<", t]], amount="base_grand_total"),
		dict(key="si_drafts", area="Sales", severity="medium", title="Sales invoices left in draft for more than 3 days",
			why="A draft invoice is not revenue and the customer has not been billed.", doctype="Sales Invoice",
			filters=[["docstatus", "=", 0], ["creation", "<", add_days(t, -3)]], amount="base_grand_total"),
		# ---- purchasing
		dict(key="po_overdue", area="Purchasing", severity="high", title="Purchase orders past their required date",
			why="Suppliers are late. Chase them before stock or projects are affected.", doctype="Purchase Order",
			filters=[["docstatus", "=", 1], ["status", "in", ["To Receive and Bill", "To Receive"]], ["schedule_date", "<", t]], amount="base_grand_total"),
		dict(key="pr_to_bill", area="Purchasing", severity="medium", title="Received but supplier invoice not booked",
			why="Goods are in stock but the liability is not in the accounts yet.", doctype="Purchase Receipt",
			filters=[["docstatus", "=", 1], ["status", "=", "To Bill"]], amount="base_grand_total"),
		dict(key="mr_pending", area="Purchasing", severity="medium", title="Material requests pending for more than 7 days",
			why="Someone asked for material and nothing has been ordered.", doctype="Material Request",
			filters=[["docstatus", "=", 1], ["status", "=", "Pending"], ["transaction_date", "<", add_days(t, -7)]]),
		dict(key="pi_drafts", area="Purchasing", severity="medium", title="Purchase invoices left in draft for more than 3 days",
			why="Unbooked supplier invoices understate what the company owes.", doctype="Purchase Invoice",
			filters=[["docstatus", "=", 0], ["creation", "<", add_days(t, -3)]], amount="base_grand_total"),
		# ---- accounts
		dict(key="ar_overdue", area="Accounts", severity="high", title="Customer invoices overdue",
			why="Money that should already have been collected.", doctype="Sales Invoice",
			filters=[["docstatus", "=", 1], ["outstanding_amount", ">", 0], ["due_date", "<", t]], amount="outstanding_amount"),
		dict(key="ar_overdue_60", area="Accounts", severity="high", title="Customer invoices overdue by more than 60 days",
			why="The longer an invoice is unpaid, the less likely it is to be paid.", doctype="Sales Invoice",
			filters=[["docstatus", "=", 1], ["outstanding_amount", ">", 0], ["due_date", "<", add_days(t, -60)]], amount="outstanding_amount"),
		dict(key="ap_overdue", area="Accounts", severity="medium", title="Supplier invoices overdue",
			why="Late payments risk supply and late fees.", doctype="Purchase Invoice",
			filters=[["docstatus", "=", 1], ["outstanding_amount", ">", 0], ["due_date", "<", t]], amount="outstanding_amount"),
		dict(key="pe_unallocated", area="Accounts", severity="medium", title="Payments not matched to an invoice",
			why="Unallocated payments leave invoices looking unpaid.", doctype="Payment Entry",
			filters=[["docstatus", "=", 1], ["unallocated_amount", ">", 0]], amount="unallocated_amount"),
		dict(key="pe_drafts", area="Accounts", severity="medium", title="Payments left in draft for more than 3 days",
			why="Draft payments are not in the bank ledger.", doctype="Payment Entry",
			filters=[["docstatus", "=", 0], ["creation", "<", add_days(t, -3)]], amount="base_paid_amount"),
		dict(key="je_drafts", area="Accounts", severity="low", title="Journal entries left in draft for more than 3 days",
			why="Unposted journals mean the books are incomplete.", doctype="Journal Entry",
			filters=[["docstatus", "=", 0], ["creation", "<", add_days(t, -3)]], amount="total_debit"),
		# ---- stock
		dict(key="stock_negative", area="Stock", severity="high", title="Items with negative stock",
			why="Negative stock means a receipt or transfer was not entered, and costs are wrong until it is.", doctype="Bin",
			filters=[["actual_qty", "<", 0]]),
		dict(key="stock_reorder", area="Stock", severity="high", title="Items below their reorder level", custom="below_reorder",
			why="Order now to avoid running out.", doctype="Bin", route="query-report/Itemwise Recommended Reorder Level"),
		dict(key="batch_expiring", area="Stock", severity="medium", title="Batches expiring within 30 days",
			why="Sell, use or return them before they expire.", doctype="Batch",
			filters=[["disabled", "=", 0], ["batch_qty", ">", 0], ["expiry_date", "between", [t, add_days(t, 30)]]]),
		dict(key="batch_expired", area="Stock", severity="high", title="Expired batches still in stock",
			why="Expired stock must not be sold and should be written off.", doctype="Batch",
			filters=[["disabled", "=", 0], ["batch_qty", ">", 0], ["expiry_date", "<", t]]),
		dict(key="se_drafts", area="Stock", severity="medium", title="Stock entries left in draft for more than 3 days",
			why="The movement happened but stock levels do not show it.", doctype="Stock Entry",
			filters=[["docstatus", "=", 0], ["creation", "<", add_days(t, -3)]]),
		# ---- projects
		dict(key="tasks_overdue", area="Projects", severity="medium", title="Tasks past their end date",
			why="Late tasks push the project's delivery.", doctype="Task",
			filters=[["status", "not in", ["Completed", "Cancelled", "Template"]], ["exp_end_date", "<", t]]),
		dict(key="projects_overdue", area="Projects", severity="high", title="Open projects past their end date",
			why="The project is late or was never closed.", doctype="Project",
			filters=[["status", "=", "Open"], ["expected_end_date", "<", t]]),
		dict(key="timesheets_draft", area="Projects", severity="low", title="Timesheets left in draft for more than 7 days",
			why="Unsubmitted time cannot be billed or costed.", doctype="Timesheet",
			filters=[["docstatus", "=", 0], ["creation", "<", add_days(t, -7)]]),
		# ---- HR (present only when the HR app is installed)
		dict(key="leave_open", area="HR", severity="medium", title="Leave applications waiting for a decision",
			why="People are waiting for an answer.", doctype="Leave Application", filters=[["docstatus", "=", 0], ["status", "=", "Open"]]),
		dict(key="expense_open", area="HR", severity="medium", title="Expense claims waiting for approval",
			why="Employees are waiting to be reimbursed.", doctype="Expense Claim",
			filters=[["docstatus", "=", 0], ["approval_status", "=", "Draft"]], amount="total_claimed_amount"),
		dict(key="contracts_ending", area="HR", severity="medium", title="Employee contracts ending within 30 days",
			why="Renew or plan the handover.", doctype="Employee",
			filters=[["status", "=", "Active"], ["contract_end_date", "between", [t, add_days(t, 30)]]]),
		dict(key="passports_expiring", area="HR", severity="low", title="Passports expiring within 60 days",
			why="Renewals take time; residence and travel depend on them.", doctype="Employee",
			filters=[["status", "=", "Active"], ["valid_upto", "between", [t, add_days(t, 60)]]]),
		# ---- master data
		dict(key="customers_no_tax", area="Master data", severity="low", title="Customers without a tax ID",
			why="Tax invoices to registered customers need it.", doctype="Customer", filters=[["disabled", "=", 0], ["tax_id", "is", "not set"]]),
		dict(key="suppliers_no_tax", area="Master data", severity="low", title="Suppliers without a tax ID",
			why="Needed to reclaim input tax and for audits.", doctype="Supplier", filters=[["disabled", "=", 0], ["tax_id", "is", "not set"]]),
		dict(key="items_no_cost", area="Master data", severity="low", title="Stock items without a cost",
			why="Without a valuation rate, margins and stock value are wrong.", doctype="Item",
			filters=[["disabled", "=", 0], ["is_stock_item", "=", 1], ["valuation_rate", "=", 0], ["has_variants", "=", 0]]),
	]


def _below_reorder(user: str) -> int:
	"""Item/warehouse pairs whose projected quantity is under the reorder level set on the item."""
	if not frappe.db.exists("DocType", "Item Reorder"):
		return 0
	Bin, Reorder, Item = frappe.qb.DocType("Bin"), frappe.qb.DocType("Item Reorder"), frappe.qb.DocType("Item")
	from frappe.query_builder.functions import Count

	rows = (
		frappe.qb.from_(Reorder)
		.join(Item).on(Item.name == Reorder.parent)
		.left_join(Bin).on((Bin.item_code == Reorder.parent) & (Bin.warehouse == Reorder.warehouse))
		.select(Count(Reorder.name))
		.where((Reorder.parenttype == "Item") & (Item.disabled == 0) & (Reorder.warehouse_reorder_level > 0))
		.where((Bin.projected_qty < Reorder.warehouse_reorder_level) | Bin.name.isnull())
	).run()
	return int(rows[0][0]) if rows else 0


CUSTOM = {"below_reorder": _below_reorder}


def _usable(monitor: dict) -> bool:
	"""Does this site have the DocType and every field the monitor filters on?"""
	if not frappe.db.exists("DocType", monitor["doctype"]):
		return False
	meta = frappe.get_meta(monitor["doctype"])
	standard = {"name", "creation", "modified", "docstatus", "owner"}
	for flt_ in monitor.get("filters") or []:
		if flt_[0] not in standard and not meta.get_field(flt_[0]):
			return False
	return True


def evaluate(user: str | None = None, area: str | None = None, include_clear: bool = False) -> list:
	"""Monitor results for `user`: [{key, area, severity, title, why, count, amount, currency, doctype, filters, route}]."""
	user = user or frappe.session.user
	currency = frappe.db.get_default("currency")
	out = []
	for monitor in _monitors():
		if area and monitor["area"].lower() != area.lower():
			continue
		try:
			if not _usable(monitor) or not frappe.has_permission(monitor["doctype"], "read", user=user):
				continue
			amount = None
			if monitor.get("custom"):
				count = CUSTOM[monitor["custom"]](user)
			else:
				fields = [agg("count", "name", "n")]
				sum_field = monitor.get("amount")
				if sum_field and frappe.get_meta(monitor["doctype"]).get_field(sum_field):
					fields.append(agg("sum", sum_field, "total"))
				row = frappe.get_list(monitor["doctype"], filters=monitor["filters"], fields=fields, user=user, limit=1)[0]
				count = int(row.get("n") or 0)
				amount = flt(row.get("total")) if "total" in row else None
		except Exception:
			frappe.log_error(title=f"Control Room monitor failed: {monitor['key']}")
			continue
		if not count and not include_clear:
			continue
		out.append(
			{
				"key": monitor["key"],
				"area": monitor["area"],
				"severity": monitor["severity"],
				"title": monitor["title"],
				"why": monitor["why"],
				"count": count,
				"amount": amount,
				"currency": currency if amount is not None else None,
				"doctype": monitor["doctype"],
				"filters": monitor.get("filters"),
				"route": monitor.get("route"),
			}
		)
	out.sort(key=lambda m: (SEVERITY.index(m["severity"]), -m["count"]))
	return out
