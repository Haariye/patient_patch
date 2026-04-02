from __future__ import annotations

import json
from typing import Any, Dict

import frappe
from frappe import _
from frappe.utils import getdate, nowdate, today


def _get_value(doc, fieldnames, default=""):
    for fieldname in fieldnames:
        try:
            val = doc.get(fieldname)
            if val not in (None, "", []):
                return val
        except Exception:
            pass
    return default


def _clean_text(val) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _get_patient_age(patient_doc) -> str:
    age_html = _get_value(patient_doc, ["age_html"], "")
    if age_html:
        return _clean_text(age_html)

    dob = _get_value(patient_doc, ["dob"], None)
    if not dob:
        return ""

    dob = getdate(dob)
    t = getdate(today())
    years = t.year - dob.year - ((t.month, t.day) < (dob.month, dob.day))
    return str(years)


@frappe.whitelist()
def get_medical_report_defaults(encounter_name: str) -> Dict[str, Any]:
    if not encounter_name:
        frappe.throw(_("Consultation reference is required"))

    encounter = frappe.get_doc("Patient Encounter", encounter_name)
    patient = frappe.get_doc("Patient", encounter.patient)

    return {
        "naming_series": "MR-.YYYY.-.#####",
        "patient": encounter.patient,
        "patient_name": _clean_text(_get_value(patient, ["patient_name"], encounter.patient)),
        "patient_id": encounter.patient,
        "age": _get_patient_age(patient),
        "sex": _clean_text(_get_value(patient, ["sex"], "")),
        "report_date": nowdate(),
        "consultation_reference": encounter.name,
        "doctor": _clean_text(_get_value(encounter, ["practitioner_name"], "")),
        "diagnosis": "",
        "treatment": "",
        "recommendation": "",
    }


@frappe.whitelist()
def create_medical_report(data):
    if isinstance(data, str):
        data = json.loads(data)

    if not isinstance(data, dict):
        frappe.throw(_("Invalid Medical Report data"))

    doc = frappe.new_doc("Medical Report")
    doc.update(data)
    doc.insert(ignore_permissions=True)

    if doc.docstatus == 0:
        doc.submit()

    return doc.name