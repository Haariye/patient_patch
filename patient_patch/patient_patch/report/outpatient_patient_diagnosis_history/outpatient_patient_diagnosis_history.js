// Copyright (c) 2026, Dagaar and contributors
// For license information, please see license.txt
//
// Outpatient Patient Diagnosis History — client side
// --------------------------------------------------
// Filters and toolbar buttons for the Outpatient Patient Diagnosis History
// script report.
//
// The dashboard charts above the table are streamed in by the Python side
// via the `message` HTML payload — this file does NOT render those charts;
// it only configures filters, the formatter, and the page-level toolbar
// buttons (Download Report, Get AI Summary).

const PYTHON_PATH =
    "patient_patch.patient_patch.report.outpatient_patient_diagnosis_history" +
    ".outpatient_patient_diagnosis_history";

frappe.query_reports["Outpatient Patient Diagnosis History"] = {
    filters: [
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
            reqd: 1,
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
            reqd: 1,
        },
        {
            fieldname: "practitioner",
            label: __("Healthcare Practitioner"),
            fieldtype: "Link",
            options: "Healthcare Practitioner",
        },
        // Patient filter — replaces the old District filter.
        // Frappe Link to "Patient" automatically renders the patient_name
        // alongside the ID in the dropdown (it's the title field of Patient
        // in standard ERPNext Healthcare).
        {
            fieldname: "patient",
            label: __("Patient"),
            fieldtype: "Link",
            options: "Patient",
            on_change: function () {
                // Refresh button visibility (AI Summary requires exactly one patient).
                _ppd_refresh_buttons();
                frappe.query_report.refresh();
            },
        },
        // Diagnosis — Link to the Diagnosis doctype.
        // The Diagnosis doctype's `name` IS the diagnosis text (autoname=field:diagnosis),
        // so the selected value plugs straight into the SQL filter.
        {
            fieldname: "diagnosis",
            label: __("Diagnosis"),
            fieldtype: "Link",
            options: "Diagnosis",
        },
        // City — Link to Address. The selected Address's `city` is resolved
        // server-side and used as the actual filter value.
        {
            fieldname: "city",
            label: __("City"),
            fieldtype: "Link",
            options: "Address",
        },
        // Age — multi-select age groups (includes 0–12 mo for babies).
        {
            fieldname: "age_groups",
            label: __("Age Groups"),
            fieldtype: "MultiSelectList",
            get_data: function () {
                return [
                    { value: "0–12 mo",  description: __("Babies under 1 year") },
                    { value: "1–5 yr",   description: "" },
                    { value: "6–17 yr",  description: "" },
                    { value: "18–30 yr", description: "" },
                    { value: "31–45 yr", description: "" },
                    { value: "46–60 yr", description: "" },
                    { value: "60+",      description: "" },
                ];
            },
        },
        {
            fieldname: "sex",
            label: __("Sex"),
            fieldtype: "Select",
            options: ["", "Male", "Female", "Other", "Prefer not to say"].join("\n"),
        },
        {
            fieldname: "group_by",
            label: __("Group By"),
            fieldtype: "Select",
            options: ["Day", "Month", "Year"].join("\n"),
            default: "Month",
        },
        {
            fieldname: "include_no_diagnosis",
            label: __("Include encounters with no diagnosis"),
            fieldtype: "Check",
            default: 0,
        },
    ],

    formatter: function (value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);

        if (column.fieldname === "diagnosis" && value && data) {
            value = `<span title="${frappe.utils.escape_html(
                String(data.diagnosis || "")
            )}">${value}</span>`;
        }
        if (column.fieldname === "age_display" && data && data.age_display) {
            // Highlight infants for clarity.
            const v = String(data.age_display);
            if (v.endsWith("mo")) {
                value = `<span style="background:#fff8e1;border-radius:3px;padding:0 4px">${v}</span>`;
            }
        }
        return value;
    },

    onload: function (report) {
        // Quick-action: Clear Diagnosis
        report.page.add_inner_button(__("Clear Diagnosis"), function () {
            frappe.query_report.set_filter_value("diagnosis", "");
            frappe.query_report.refresh();
        });

        // Download Report (A4 landscape printable)
        report.page.add_inner_button(__("Download Report"), function () {
            _ppd_download_report();
        });

        // AI Summary — added/removed dynamically based on filters
        report.page._ppd_ai_button_added = false;
        _ppd_refresh_buttons();
    },

    after_datatable_render: function () {
        // Filters may have changed via the URL or via the report header;
        // re-evaluate button visibility after each refresh.
        _ppd_refresh_buttons();
    },
};


// ---------------------------------------------------------------------------
// Toolbar button helpers
// ---------------------------------------------------------------------------

function _ppd_get_filters_safe() {
    if (!frappe.query_report) return {};
    return frappe.query_report.get_filter_values() || {};
}

function _ppd_refresh_buttons() {
    const report = frappe.query_report;
    if (!report || !report.page) return;

    const f = _ppd_get_filters_safe();
    const has_one_patient = !!(f.patient && String(f.patient).trim());

    // Add the AI Summary button when exactly one patient is selected.
    // Remove it otherwise. Frappe doesn't expose a clean "remove inner button"
    // API, so we re-add it idempotently with a stable group + label and rely
    // on hide()/show() of the wrapper if the button object exists.
    if (has_one_patient) {
        if (!report.page._ppd_ai_button_added) {
            report.page.add_inner_button(__("Get AI Summary"), function () {
                _ppd_show_ai_summary_dialog();
            }, __("AI"));
            report.page._ppd_ai_button_added = true;
        }
        // Show it (in case it was previously hidden).
        const $btn = report.page.inner_toolbar.find('button:contains("Get AI Summary")');
        if ($btn && $btn.length) $btn.parent().show();
    } else {
        // Hide the AI button (it would be misleading without a single patient).
        if (report.page._ppd_ai_button_added) {
            const $btn = report.page.inner_toolbar.find('button:contains("Get AI Summary")');
            if ($btn && $btn.length) $btn.parent().hide();
        }
    }
}


// ---------------------------------------------------------------------------
// Download Report — opens the printable A4 landscape HTML in a new window
// ---------------------------------------------------------------------------

function _ppd_download_report() {
    const f = _ppd_get_filters_safe();
    if (!f.from_date || !f.to_date) {
        frappe.msgprint(__("Please select From Date and To Date first."));
        return;
    }

    frappe.dom.freeze(__("Building printable report…"));

    frappe.call({
        method: PYTHON_PATH + ".get_print_html",
        args: { filters: JSON.stringify(f) },
        callback: function (r) {
            frappe.dom.unfreeze();
            const html = r && r.message;
            if (!html) {
                frappe.msgprint(__("Could not build the printable report."));
                return;
            }

            // Open in a new tab and stream the HTML in. The HTML itself
            // auto-triggers window.print() once the layout settles.
            const w = window.open("", "_blank", "width=1200,height=800");
            if (!w) {
                frappe.msgprint(
                    __("Pop-up blocked. Please allow pop-ups for this site to download the report.")
                );
                return;
            }
            w.document.open();
            w.document.write(html);
            w.document.close();
        },
        error: function () {
            frappe.dom.unfreeze();
        },
    });
}


// ---------------------------------------------------------------------------
// AI Summary — single-patient bullet summary for the selected period
// ---------------------------------------------------------------------------

function _ppd_show_ai_summary_dialog() {
    const f = _ppd_get_filters_safe();
    if (!f.patient) {
        frappe.msgprint(__("Select a single Patient to generate an AI summary."));
        return;
    }
    if (!f.from_date || !f.to_date) {
        frappe.msgprint(__("Please select From Date and To Date first."));
        return;
    }

    const dialog = new frappe.ui.Dialog({
        title: __("AI Summary — {0}", [f.patient]),
        size: "large",
        fields: [
            {
                fieldname: "info_html",
                fieldtype: "HTML",
                options: `
                    <div style="padding:8px 0;color:#607080;font-size:12px">
                        <b>${__('Patient')}:</b> ${frappe.utils.escape_html(f.patient)} &nbsp;·&nbsp;
                        <b>${__('Period')}:</b> ${frappe.utils.escape_html(f.from_date)}
                            → ${frappe.utils.escape_html(f.to_date)}
                    </div>
                `,
            },
            {
                fieldname: "summary_html",
                fieldtype: "HTML",
                options: `
                    <div id="ppd-ai-summary-body" style="
                        padding: 18px; min-height: 160px;
                        background: #f8fafc; border: 1px solid #e2e8f0;
                        border-radius: 8px; font-size: 13px; line-height: 1.6;
                        white-space: pre-wrap;">
                        <div style="color:#94a3b8">${__('Generating summary…')}</div>
                    </div>
                `,
            },
        ],
        primary_action_label: __("Copy to Clipboard"),
        primary_action: function () {
            const text = dialog.$wrapper.find("#ppd-ai-summary-body").text() || "";
            navigator.clipboard.writeText(text).then(
                () => frappe.show_alert({ message: __("Copied"), indicator: "green" }),
                () => frappe.msgprint(__("Could not copy."))
            );
        },
    });

    dialog.show();
    dialog.disable_primary_action();

    frappe.call({
        method: PYTHON_PATH + ".generate_patient_period_summary",
        args: {
            patient: f.patient,
            from_date: f.from_date,
            to_date: f.to_date,
        },
        callback: function (r) {
            const $body = dialog.$wrapper.find("#ppd-ai-summary-body");
            const msg = r && r.message;
            if (!msg || !msg.success) {
                $body.html(
                    `<div style="color:#c0392b">${frappe.utils.escape_html(
                        (msg && msg.message) || __("Failed to generate summary.")
                    )}</div>`
                );
                return;
            }
            // Render bullets with a touch of styling — keep it simple and safe.
            const safe = frappe.utils.escape_html(msg.summary || "");
            $body.html(safe);
            dialog.enable_primary_action();
        },
        error: function () {
            const $body = dialog.$wrapper.find("#ppd-ai-summary-body");
            $body.html(
                `<div style="color:#c0392b">${__("Network or server error.")}</div>`
            );
        },
    });
}
