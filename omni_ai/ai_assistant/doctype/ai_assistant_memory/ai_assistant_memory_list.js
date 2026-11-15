frappe.listview_settings["AI Assistant Memory"] = {
	get_indicator(doc) {
		const colors = { Proposed: "orange", Approved: "green", Rejected: "gray" };
		return [__(doc.status), colors[doc.status] || "gray", "status,=," + doc.status];
	},
};
