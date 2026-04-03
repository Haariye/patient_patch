import frappe


FIELD_CONFIGS = [
    # Sales Invoice
    {
        "dt": "Sales Invoice",
        "fieldname": "custom_patient_encounter",
        "fieldtype": "Link",
        "label": "Patient Encounter",
        "options": "Patient Encounter",
        "read_only": 1,
        "hidden": 0,
        "allow_on_submit": 1,
    },
    {
        "dt": "Sales Invoice",
        "fieldname": "custom_is_prescription_invoice",
        "fieldtype": "Check",
        "label": "Prescription Invoice",
        "read_only": 1,
        "hidden": 0,
        "allow_on_submit": 1,
    },
    {
        "dt": "Sales Invoice",
        "fieldname": "custom_prescription_invoice_type",
        "fieldtype": "Select",
        "label": "Prescription Invoice Type",
        "options": "Original\nReplacement",
        "read_only": 1,
        "hidden": 0,
        "allow_on_submit": 1,
    },
    {
        "dt": "Sales Invoice",
        "fieldname": "custom_previous_prescription_invoice",
        "fieldtype": "Link",
        "label": "Previous Prescription Invoice",
        "options": "Sales Invoice",
        "read_only": 1,
        "hidden": 0,
        "allow_on_submit": 1,
    },
    {
        "dt": "Sales Invoice",
        "fieldname": "custom_prescription_sync_hash",
        "fieldtype": "Data",
        "label": "Prescription Sync Hash",
        "read_only": 1,
        "hidden": 0,
        "allow_on_submit": 1,
    },

    # Sales Invoice Item
    {
        "dt": "Sales Invoice Item",
        "fieldname": "custom_drug_prescription_row_id",
        "fieldtype": "Data",
        "label": "Drug Prescription Row ID",
        "read_only": 1,
        "hidden": 0,
        "allow_on_submit": 1,
    },
    {
        "dt": "Sales Invoice Item",
        "fieldname": "custom_medication_request",
        "fieldtype": "Link",
        "label": "Medication Request",
        "options": "Medication Request",
        "read_only": 1,
        "hidden": 0,
        "allow_on_submit": 1,
    },
    {
        "dt": "Sales Invoice Item",
        "fieldname": "custom_patient_encounter",
        "fieldtype": "Link",
        "label": "Patient Encounter",
        "options": "Patient Encounter",
        "read_only": 1,
        "hidden": 0,
        "allow_on_submit": 1,
    },

    # Patient Encounter
    {
        "dt": "Patient Encounter",
        "fieldname": "custom_latest_pharmacy_invoice",
        "fieldtype": "Link",
        "label": "Latest Pharmacy Invoice",
        "options": "Sales Invoice",
        "read_only": 1,
        "hidden": 0,
        "allow_on_submit": 1,
    },
    {
        "dt": "Patient Encounter",
        "fieldname": "custom_last_prescription_sync_hash",
        "fieldtype": "Data",
        "label": "Last Prescription Sync Hash",
        "read_only": 1,
        "hidden": 0,
        "allow_on_submit": 1,
    },
    {
        "dt": "Patient Encounter",
        "fieldname": "custom_prescription_invoice_status",
        "fieldtype": "Data",
        "label": "Prescription Invoice Status",
        "read_only": 1,
        "hidden": 0,
        "allow_on_submit": 1,
    },

    # Drug Prescription
    {
        "dt": "Drug Prescription",
        "fieldname": "custom_is_billed",
        "fieldtype": "Check",
        "label": "Is Billed",
        "read_only": 1,
        "hidden": 0,
        "allow_on_submit": 1,
    },
    {
        "dt": "Drug Prescription",
        "fieldname": "custom_billed_sales_invoice",
        "fieldtype": "Link",
        "label": "Billed Sales Invoice",
        "options": "Sales Invoice",
        "read_only": 1,
        "hidden": 0,
        "allow_on_submit": 1,
    },
    {
        "dt": "Drug Prescription",
        "fieldname": "custom_billed_sales_invoice_item",
        "fieldtype": "Data",
        "label": "Billed Sales Invoice Item",
        "read_only": 1,
        "hidden": 0,
        "allow_on_submit": 1,
    },

    # Patient
    {
        "dt": "Patient",
        "fieldname": "custom_patient_age",
        "fieldtype": "Data",
        "label": "Patient Age",
        "read_only": 0,
        "hidden": 0,
        "allow_on_submit": 1,
    },

    # Patient Appointment
    {
        "dt": "Patient Appointment",
        "fieldname": "custom_jawaab_queue",
        "fieldtype": "Data",
        "label": "Jawaab Queue",
        "read_only": 1,
        "hidden": 0,
        "allow_on_submit": 1,
    },
    {
        "dt": "Patient Appointment",
        "fieldname": "custom_position_in_queue",
        "fieldtype": "Data",
        "label": "Position In Queue",
        "read_only": 1,
        "hidden": 0,
        "allow_on_submit": 1,
    },

    # Healthcare Settings
    {
        "dt": "Healthcare Settings",
        "fieldname": "custom_openai_api_key",
        "fieldtype": "Password",
        "label": "OpenAI API Key",
        "read_only": 0,
        "hidden": 0,
        "allow_on_submit": 0,
    },
]


def _upsert_custom_field(cfg):
    cf_name = f"{cfg['dt']}-{cfg['fieldname']}"
    existing = frappe.db.exists("Custom Field", cf_name)

    if existing:
        doc = frappe.get_doc("Custom Field", cf_name)

        old_fieldtype = doc.fieldtype
        new_fieldtype = cfg.get("fieldtype")

        update_cfg = cfg.copy()
        if old_fieldtype != new_fieldtype:
            update_cfg["fieldtype"] = old_fieldtype

        for key, value in update_cfg.items():
            setattr(doc, key, value)

        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc({
            "doctype": "Custom Field",
            **cfg
        })
        doc.insert(ignore_permissions=True)


def execute():
    for cfg in FIELD_CONFIGS:
        _upsert_custom_field(cfg)

    frappe.db.commit()