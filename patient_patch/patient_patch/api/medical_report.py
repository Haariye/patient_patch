from __future__ import annotations

import json
import os
import time
from typing import Any, Dict

import frappe
from frappe import _
from frappe.utils import getdate, nowdate, today

try:
    import requests
except Exception:
    requests = None


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
def generate_medical_report_recommendation(diagnosis: str = "", treatment: str = "") -> str:
    diagnosis = _clean_text(diagnosis)
    treatment = _clean_text(treatment)

    if not diagnosis and not treatment:
        return "Please enter Diagnosis / Examination or Treatment first."

    api_key = frappe.conf.get("openai_api_key") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        frappe.log_error("Missing OpenAI key in site config and env", "Medical Report Recommendation Error")
        return ""

    if not requests:
        frappe.log_error("requests package missing", "Medical Report Recommendation Error")
        return ""

    prompt = f"""
You are assisting a physician in writing the Recommendations section of a medical report.

Write a short, professional physician recommendation based only on the information below.
Do not mention AI.
Do not add unsupported facts.
Keep it practical and clinically useful.

Diagnosis / Examination:
{diagnosis or "N/A"}

Treatment:
{treatment or "N/A"}

Return only the recommendation text.
""".strip()

    last_error = None

    for attempt in range(3):
        try:
            response = requests.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-5-mini",
                    "input": prompt,
                    "max_output_tokens": 500,
                    "reasoning": {"effort": "minimal"},
                    "text": {"verbosity": "low"},
                },
                timeout=60,
            )

            if response.status_code == 429:
                last_error = f"429: {response.text}"
                time.sleep(2 * (attempt + 1))
                continue

            if response.status_code >= 400:
                last_error = f"{response.status_code}: {response.text}"
                break

            data = response.json()

            output = data.get("output", []) or []
            for item in output:
                if item.get("type") != "message":
                    continue
                for content in item.get("content", []) or []:
                    if content.get("type") == "output_text" and content.get("text"):
                        return content["text"].strip()

            if data.get("status") == "incomplete":
                last_error = f"Incomplete response: {data.get('incomplete_details')}"
            else:
                last_error = f"No output_text found in response: {data}"

        except Exception:
            last_error = frappe.get_traceback()
            time.sleep(2 * (attempt + 1))

    frappe.log_error(last_error or "Unknown OpenAI error", "Medical Report Recommendation Error")
    return ""


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