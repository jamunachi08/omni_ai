frappe.ui.form.on("AI Assistant Schedule", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Run Now"), () => {
			frappe.call({
				method: "omni_ai.ai_assistant.doctype.ai_assistant_schedule.ai_assistant_schedule.run_now",
				args: { name: frm.doc.name },
				callback() {
					frappe.show_alert({ message: __("Started. The result will be emailed when it is ready."), indicator: "green" });
				},
			});
		});
		if (frm.doc.last_conversation) {
			frm.add_custom_button(__("Open Last Result"), () => frappe.set_route("ai-assistant", frm.doc.last_conversation));
		}
	},
});
