frappe.ui.form.on("AI Assistant Memory", {
	refresh(frm) {
		if (frm.is_new() || !frappe.user.has_role("System Manager")) return;
		if (frm.doc.status !== "Approved") {
			frm.add_custom_button(__("Approve"), () => frm.set_value("status", "Approved").then(() => frm.save())).addClass("btn-primary");
		}
		if (frm.doc.status === "Proposed") {
			frm.add_custom_button(__("Reject"), () => frm.set_value("status", "Rejected").then(() => frm.save()));
		}
	},
});
