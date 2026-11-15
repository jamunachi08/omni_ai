frappe.ui.form.on("AI Assistant Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test Connection"), () => {
			if (frm.is_dirty()) {
				frappe.msgprint(__("Save the settings first, then test the connection."));
				return;
			}
			frappe.call({
				method: "omni_ai.ai_assistant.doctype.ai_assistant_settings.ai_assistant_settings.test_connection",
				freeze: true,
				freeze_message: __("Contacting the AI provider…"),
				callback(r) {
					const res = r.message || {};
					let html = `<p>${frappe.utils.escape_html(res.message || "")}</p>`;
					(res.notes || []).forEach((n) => (html += `<p>${frappe.utils.escape_html(n)}</p>`));
					if (res.models && res.models.length) {
						html += `<p class="text-muted small">${__("Models available to this key")}:</p><p class="small">${res.models
							.map((m) => `<code>${frappe.utils.escape_html(m)}</code>`)
							.join(" ")}</p>`;
					}
					frappe.msgprint({
						title: res.ok ? __("Connection OK") : __("Connection failed"),
						indicator: res.ok ? "green" : "red",
						message: html,
					});
				},
			});
		});
		frm.add_custom_button(__("Open Assistant"), () => {
			if (window.omni_ai && omni_ai.open) omni_ai.open();
			else frappe.set_route("ai-assistant");
		});
		if (!frm.doc.api_key && !frm.is_new()) {
			frm.dashboard.set_headline_alert(
				__("Add your API key and press Test Connection to switch the assistant on."),
				"orange"
			);
		}
	},
	after_save() {
		frappe.show_alert({ message: __("Reload the page to see branding changes."), indicator: "blue" });
	},
});
