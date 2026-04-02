frappe.ui.form.on('Patient Encounter', {
    onload(frm) {
        style_sections();
    },
    refresh(frm) {
        style_sections();
        add_floating_save_submit_button(frm);
        add_create_medical_report_button(frm);
    }
});

function style_sections() {
    setTimeout(() => {
        $('.section-head').each(function () {
            const sectionText = $(this).text().trim().toLowerCase();
            let bgColor = "#f5f5f5";

            if (sectionText.includes("encounter")) bgColor = "#e6f7ff";
            else if (sectionText.includes("medical")) bgColor = "#f0f0f0";
            else if (sectionText.includes("heading")) bgColor = "#d1f7c4";
            else if (sectionText.includes("history")) bgColor = "#B2D8D8";
            else if (sectionText.includes("medication")) bgColor = "#fde2e2";
            else if (sectionText.includes("investigations")) bgColor = "#e2f0cb";
            else if (sectionText.includes("procedure")) bgColor = "#e8daef";
            else if (sectionText.includes("rehabilitation")) bgColor = "#d1f2eb";
            else if (sectionText.includes("ref")) bgColor = "#f8c471";
            else if (sectionText.includes("note")) bgColor = "#f6ddcc";
            else if (sectionText.includes("lab")) bgColor = "#DCEEF2";
            else if (sectionText.includes("imaging")) bgColor = "#E9EEF5";

            $(this).css({
                "background-color": bgColor,
                "padding": "8px 12px",
                "border-radius": "4px",
                "font-weight": "bold",
                "margin-top": "12px"
            });
        });
    }, 300);
}

function add_floating_save_submit_button(frm) {
    setTimeout(() => {
        if (frm.doc.docstatus !== 0 || $('#floating-submit-btn').length) return;

        const btn = $(`
            <button id="floating-submit-btn" class="btn btn-success btn-md">
                Save and Submit
            </button>
        `).css({
            position: "fixed",
            bottom: "20px",
            right: "30px",
            "z-index": 9999,
            "box-shadow": "0 4px 8px rgba(0,0,0,0.2)"
        });

        btn.on("click", () => {
            frm.save().then(() => {
                if (frm.doc.docstatus === 0) frm.savesubmit();
            });
        });

        $('body').append(btn);
    }, 500);
}

function add_create_medical_report_button(frm) {
    if (frm.is_new() || frm.doc.docstatus === 2) return;

    frm.remove_custom_button(__('Medical Report'), __('Create'));

    frm.add_custom_button(__('Medical Report'), function () {
        open_medical_report_dialog(frm);
    }, __('Create'));
}

function open_medical_report_dialog(frm) {
    frappe.call({
        method: 'patient_patch.patient_patch.api.medical_report.get_medical_report_defaults',
        args: {
            encounter_name: frm.doc.name
        },
        freeze: true,
        freeze_message: __('Loading Medical Report...'),
        callback: function (r) {
            if (!r.message) return;

            const d = new frappe.ui.Dialog({
                title: __('Create Medical Report'),
                size: 'large',
                fields: [
                    {
                        fieldname: 'naming_series',
                        label: 'Series',
                        fieldtype: 'Select',
                        options: 'MR-.YYYY.-.#####',
                        default: r.message.naming_series,
                        reqd: 1
                    },
                    {
                        fieldname: 'patient',
                        label: 'Patient',
                        fieldtype: 'Link',
                        options: 'Patient',
                        default: r.message.patient,
                        reqd: 1,
                        read_only: 1
                    },
                    {
                        fieldname: 'patient_name',
                        label: 'Patient Name',
                        fieldtype: 'Data',
                        default: r.message.patient_name,
                        read_only: 1
                    },
                    {
                        fieldname: 'patient_id',
                        label: 'Patient ID',
                        fieldtype: 'Data',
                        default: r.message.patient_id,
                        read_only: 1
                    },
                    {
                        fieldname: 'age',
                        label: 'Age',
                        fieldtype: 'Data',
                        default: r.message.age,
                        read_only: 1
                    },
                    {
                        fieldname: 'sex',
                        label: 'Sex',
                        fieldtype: 'Data',
                        default: r.message.sex,
                        read_only: 1
                    },
                    {
                        fieldname: 'report_date',
                        label: 'Report Date',
                        fieldtype: 'Date',
                        default: r.message.report_date,
                        reqd: 1
                    },
                    {
                        fieldname: 'consultation_reference',
                        label: 'Consultation Reference',
                        fieldtype: 'Link',
                        options: 'Patient Encounter',
                        default: r.message.consultation_reference,
                        read_only: 1
                    },
                    {
                        fieldname: 'doctor',
                        label: 'Doctor',
                        fieldtype: 'Link',
                        options: 'Healthcare Practitioner',
                        default: r.message.doctor
                    },
                    {
                        fieldname: 'diagnosis',
                        label: 'Diagnosis / Examination',
                        fieldtype: 'Long Text',
                        default: r.message.diagnosis
                    },
                    {
                        fieldname: 'treatment',
                        label: 'Treatment',
                        fieldtype: 'Long Text',
                        default: r.message.treatment
                    },
                    {
                        fieldname: 'recommendation',
                        label: 'Recommendations',
                        fieldtype: 'Long Text',
                        default: r.message.recommendation
                    }
                ],
                primary_action_label: __('Save and Print'),
                primary_action(values) {
                    frappe.call({
                        method: 'patient_patch.patient_patch.api.medical_report.create_medical_report',
                        args: {
                            data: values
                        },
                        freeze: true,
                        freeze_message: __('Saving Medical Report...'),
                        callback: function (res) {
                            if (!res.message) return;
                            d.hide();
                            frappe.set_route('print', 'Medical Report', res.message);
                        }
                    });
                }
            });

            d.show();
        },
        error: function (err) {
            frappe.msgprint({
                title: __('Medical Report Error'),
                indicator: 'red',
                message: __('Failed to load Medical Report data. Check server error log.')
            });
            console.error(err);
        }
    });
}

frappe.router.on('change', () => {
    $('#floating-submit-btn').remove();
});