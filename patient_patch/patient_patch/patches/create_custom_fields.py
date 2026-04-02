"""Create or update custom fields required by Patient Patch.

Rules agreed with user:
- install: create missing fields
- update/reinstall: keep existing fields and data; only create missing/update schema props
- uninstall: do not remove custom fields, so their data remains intact

Field property rules:
- hidden = 0 for all app-created fields
- allow_on_submit = 1 for all app-created fields
- read_only = 1 for all app-created fields except Patient.custom_patient_age
"""

import frappe


FIELD_DEFS = [
    # Sales Invoice
    {"dt": "Sales Invoice", "fieldname": "custom_patient_encounter", "fieldtype": "Link", "label": "Patient Encounter", "options": "Patient Encounter"},
    {"dt": "Sales Invoice", "fieldname": "custom_is_prescription_invoice", "fieldtype": "Check", "label": "Prescription Invoice"},
    {"dt": "Sales Invoice", "fieldname": "custom_prescription_invoice_type", "fieldtype": "Select", "label": "Prescription Invoice Type", "options": "Original\nReplacement"},
    {"dt": "Sales Invoice", "fieldname": "custom_previous_prescription_invoice", "fieldtype": "Link", "label": "Previous Prescription Invoice", "options": "Sales Invoice"},
    {"dt": "Sales Invoice", "fieldname": "custom_prescription_sync_hash", "fieldtype": "Data", "label": "Prescription Sync Hash"},
    # Sales Invoice Item
    {"dt": "Sales Invoice Item", "fieldname": "custom_drug_prescription_row_id", "fieldtype": "Link", "label": "Drug Prescription Row", "options": "Drug Prescription"},
    {"dt": "Sales Invoice Item", "fieldname": "custom_medication_request", "fieldtype": "Link", "label": "Medication Request", "options": "Medication Request"},
    {"dt": "Sales Invoice Item", "fieldname": "custom_patient_encounter", "fieldtype": "Link", "label": "Patient Encounter", "options": "Patient Encounter"},
    # Drug Prescription
    {"dt": "Drug Prescription", "fieldname": "custom_is_billed", "fieldtype": "Check", "label": "Is Billed"},
    {"dt": "Drug Prescription", "fieldname": "custom_billed_sales_invoice", "fieldtype": "Link", "label": "Billed Sales Invoice", "options": "Sales Invoice"},
    {"dt": "Drug Prescription", "fieldname": "custom_billed_sales_invoice_item", "fieldtype": "Link", "label": "Billed Sales Invoice Item", "options": "Sales Invoice Item"},
    # Patient Encounter
    {"dt": "Patient Encounter", "fieldname": "custom_latest_pharmacy_invoice", "fieldtype": "Link", "label": "Latest Pharmacy Invoice", "options": "Sales Invoice"},
    {"dt": "Patient Encounter", "fieldname": "custom_last_prescription_sync_hash", "fieldtype": "Data", "label": "Last Prescription Sync Hash"},
    {"dt": "Patient Encounter", "fieldname": "custom_prescription_invoice_status", "fieldtype": "Data", "label": "Prescription Invoice Status"},
    # Patient
    {"dt": "Patient", "fieldname": "custom_patient_age", "fieldtype": "Int", "label": "Patient Age"},
    # Patient Appointment
    {"dt": "Patient Appointment", "fieldname": "custom_jawaab_queue", "fieldtype": "Int", "label": "Jawaab Queue"},
    {"dt": "Patient Appointment", "fieldname": "custom_position_in_queue", "fieldtype": "Int", "label": "Position In Queue"},
]


def _apply_common_props(doc):
    doc.hidden = 0
    doc.allow_on_submit = 1
    doc.read_only = 0 if (doc.dt == "Patient" and doc.fieldname == "custom_patient_age") else 1


def _upsert_custom_field(cfg):
    cf_name = f"{cfg['dt']}-{cfg['fieldname']}"
    doc = frappe.get_doc("Custom Field", cf_name) if frappe.db.exists("Custom Field", cf_name) else frappe.new_doc("Custom Field")
    if doc.is_new():
        doc.dt = cfg["dt"]
        doc.fieldname = cfg["fieldname"]
        doc.label = cfg.get("label") or cfg["fieldname"].replace("_", " ").title()
        doc.fieldtype = cfg.get("fieldtype", "Data")
        if cfg.get("options"):
            doc.options = cfg["options"]
    else:
        # Keep field identity stable, but update schema metadata/options.
        doc.label = cfg.get("label") or doc.label
        doc.fieldtype = cfg.get("fieldtype", doc.fieldtype)
        doc.options = cfg.get("options", doc.options)

    _apply_common_props(doc)
    doc.insert(ignore_permissions=True) if doc.is_new() else doc.save(ignore_permissions=True)


def execute():
    for cfg in FIELD_DEFS:
        _upsert_custom_field(cfg)
    frappe.db.commit()
