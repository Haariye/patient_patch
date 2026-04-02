console.log("✅ patient_quickentry_patch.js loaded");

$(document).ready(function () {
	const parse_age_to_dob = (val) => {
		val = val?.trim().toLowerCase() || "";
		let years = 0, months = 0, days = 0;

		const yearMatch = val.match(/(\d+)\s*y/);
		const monthMatch = val.match(/(\d+)\s*m(?![a-z])/); // avoid matching 'male'
		const dayMatch = val.match(/(\d+)\s*d/);

		if (yearMatch) years = parseInt(yearMatch[1]);
		if (monthMatch) months = parseInt(monthMatch[1]);
		if (dayMatch) days = parseInt(dayMatch[1]);

		if (!yearMatch && !monthMatch && !dayMatch && /^\d+$/.test(val)) {
			years = parseInt(val);
		}

		const today = new Date();
		today.setDate(today.getDate() - days);
		today.setMonth(today.getMonth() - months);
		today.setFullYear(today.getFullYear() - years);

		return frappe.datetime.obj_to_str(today);
	};

    function inject_button(frm, input_wrapper) {
        if (!input_wrapper || input_wrapper.find('#quick_entry_dob_btn').length) return;

        input_wrapper.append(`<button id="quick_entry_dob_btn" class="btn btn-xs btn-primary ml-2">Set DOB</button>`);
        input_wrapper.find('#quick_entry_dob_btn').on('click', () => {
            const val = input_wrapper.find('input').val();
            if (val) {
                const dob = parse_age_to_dob(val);
                frm.set_value('dob', dob);
                frm.__dob_button_clicked = true;
                frappe.show_alert({ message: `DOB set to ${dob}`, indicator: 'green' });
            } else {
                frappe.show_alert({ message: 'Enter age first', indicator: 'red' });
            }
        });
    }

    const original = frappe.ui.form.QuickEntryForm.prototype.render_dialog;
    frappe.ui.form.QuickEntryForm.prototype.render_dialog = function () {
        original.call(this);

        if (this.doctype === 'Patient') {
            let tries = 0;
            const maxTries = 10;

            const interval = setInterval(() => {
                const input_wrapper = this.dialog?.fields_dict?.custom_patient_age?.$wrapper;
                const dob_field = this.dialog?.fields_dict?.dob;

                if (input_wrapper && dob_field && input_wrapper.find('input').length) {
                    inject_button(this.dialog, input_wrapper);

                    const save_btn = this.dialog.$wrapper.find('.modal-footer .btn-primary');
                    save_btn.off('click.setDobCheck').on('click.setDobCheck', (e) => {
                        const dob = this.dialog.get_value('dob');
                        if (!dob) {
                            e.preventDefault();
                            frappe.show_alert({ message: 'Click "Set DOB" first.', indicator: 'red' });
                        }
                    });

                    clearInterval(interval);
                } else if (++tries >= maxTries) {
                    console.warn("❌ Could not bind in Patient Quick Entry.");
                    clearInterval(interval);
                }
            }, 300);
        }
    };

    frappe.ui.form.on('Patient', {
        onload_post_render: function (frm) {
            if (!frm.fields_dict.custom_patient_age) return;

            const input_wrapper = frm.fields_dict.custom_patient_age.$wrapper;
            inject_button(frm, input_wrapper);

            frm.save = (function (original_save) {
                return function () {
                    if (!frm.doc.dob) {
                        frappe.msgprint('Click "Set DOB" before saving.');
                        return;
                    }
                    original_save.call(frm);
                };
            })(frm.save);
        }
    });
});
