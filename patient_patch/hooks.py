app_name = "patient_patch"
app_title = "Patient Patch"
app_publisher = "Dagaar"
app_description = "Patient Patch"
app_email = "info.dagaar@gmail.com"
app_license = "mit"

app_include_js = ["/assets/patient_patch/js/patient_quickentry_patch.js"]

doctype_js = {
    "Patient Appointment": ["public/js/patient_appointment_ui.js", "public/js/visit_detail.js"],
    "Patient Encounter": "public/js/patient_encounter_ui.js",
}

doc_events = {
    "Patient Encounter": {
        "on_submit": "patient_patch.patient_patch.prescription_invoice.on_submit_patient_encounter",
        "on_update_after_submit": "patient_patch.patient_patch.prescription_invoice.on_update_after_submit_patient_encounter",
        "on_cancel": "patient_patch.patient_patch.prescription_invoice.on_cancel_patient_encounter",
    },
    "Sales Invoice": {
        "on_submit": "patient_patch.patient_patch.prescription_invoice.on_submit_sales_invoice",
    },
}

# Ensure missing custom fields are created on fresh install and also re-applied
# safely during migrate/update without touching existing data values.
after_install = "patient_patch.patient_patch.patches.create_custom_fields.execute"
after_migrate = ["patient_patch.patient_patch.patches.create_custom_fields.execute"]

fixtures = [
    {
        "doctype": "Print Format",
        "filters": [["name", "=", "Medical Report Print"]],
    }
]
