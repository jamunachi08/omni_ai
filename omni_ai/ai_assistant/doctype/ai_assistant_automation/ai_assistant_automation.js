frappe.ui.form.on("AI Assistant Automation", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Send Test Request"), () => {
			frappe.call({
				method: "omni_ai.ai_assistant.doctype.ai_assistant_automation.ai_assistant_automation.send_test",
				args: { name: frm.doc.name },
				freeze: true,
				callback(r) {
					const res = r.message || {};
					frappe.msgprint({
						title: res.ok ? __("The webhook answered") : __("The webhook did not accept the request"),
						indicator: res.ok ? "green" : "red",
						message: `<p>${frappe.utils.escape_html(res.message || "")}</p>`,
					});
				},
			});
		});
	},
});
