from __future__ import annotations

import json
from typing import Any, Dict, List

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


def _format_treatment(encounter_doc) -> str:
    rows = encounter_doc.get("drug_prescription") or []
    lines = []

    for row in rows:
        drug_name = _clean_text(row.get("drug_name") or row.get("drug_code"))
        dosage = _clean_text(row.get("dosage"))
        period = _clean_text(row.get("period"))
        comment = _clean_text(row.get("comment"))
        interval = _clean_text(row.get("interval"))
        interval_uom = _clean_text(row.get("interval_uom"))

        parts = []
        if drug_name:
            parts.append(f"Medication: {drug_name}")
        if dosage:
            parts.append(f"Dosage: {dosage}")
        if period:
            parts.append(f"Period: {period}")
        if interval:
            parts.append(f"Interval: {interval}")
        if interval_uom:
            parts.append(f"Interval UOM: {interval_uom}")
        if comment:
            parts.append(f"Comment: {comment}")

        if parts:
            lines.append("- " + " | ".join(parts))

    return "\n".join(lines).strip()


def _get_prescribed_lab_templates(encounter_doc) -> List[str]:
    rows = encounter_doc.get("lab_test_prescription") or []
    templates = []

    for row in rows:
        code = _clean_text(row.get("lab_test_code"))
        if code:
            templates.append(code)

    return list(dict.fromkeys(templates))


def _get_latest_lab_tests_for_encounter(encounter_doc) -> List[Dict[str, Any]]:
    patient = encounter_doc.patient
    prescribed_templates = _get_prescribed_lab_templates(encounter_doc)

    filters = {
        "patient": patient,
        "status": ["in", ["Completed", "Approved"]],
    }

    if prescribed_templates:
        filters["template"] = ["in", prescribed_templates]

    tests = frappe.get_all(
        "Lab Test",
        filters=filters,
        fields=["name", "template", "lab_test_name", "status", "date", "submitted_date"],
        order_by="date desc, modified desc",
        limit=50,
    )

    latest_by_template = {}
    for test in tests:
        key = test.get("template") or test.get("lab_test_name") or test.get("name")
        if key not in latest_by_template:
            latest_by_template[key] = test

    return list(latest_by_template.values())


def _get_template_type(template_name: str) -> str:
    if not template_name:
        return ""
    return _clean_text(frappe.db.get_value("Lab Test Template", template_name, "lab_test_template_type"))


def _format_normal_test_items(doc) -> List[str]:
    lines = []
    for row in doc.get("normal_test_items") or []:
        test_name = _clean_text(row.get("lab_test_name"))
        event = _clean_text(row.get("lab_test_event"))
        value = _clean_text(row.get("result_value"))
        uom = _clean_text(row.get("lab_test_uom"))
        sec = _clean_text(row.get("secondary_uom_result"))

        parts = []
        if test_name:
            parts.append(test_name)
        if event:
            parts.append(f"Event: {event}")
        if value:
            result = value if not uom else f"{value} {uom}"
            parts.append(f"Result: {result}")
        if sec:
            parts.append(f"Secondary: {sec}")

        if parts:
            lines.append("- " + " | ".join(parts))
    return lines


def _format_descriptive_test_items(doc) -> List[str]:
    lines = []
    for row in doc.get("descriptive_test_items") or []:
        particulars = _clean_text(row.get("lab_test_particulars"))
        value = _clean_text(row.get("result_value"))

        parts = []
        if particulars:
            parts.append(particulars)
        if value:
            parts.append(f"Result: {value}")

        if parts:
            lines.append("- " + " | ".join(parts))
    return lines


def _format_organism_test_items(doc) -> List[str]:
    lines = []
    for row in doc.get("organism_test_items") or []:
        organism = _clean_text(row.get("organism"))
        population = _clean_text(row.get("colony_population"))
        colony_uom = _clean_text(row.get("colony_uom"))

        parts = []
        if organism:
            parts.append(f"Organism: {organism}")
        if population:
            result = population if not colony_uom else f"{population} {colony_uom}"
            parts.append(f"Colony: {result}")

        if parts:
            lines.append("- " + " | ".join(parts))
    return lines


def _format_sensitivity_test_items(doc) -> List[str]:
    lines = []
    for row in doc.get("sensitivity_test_items") or []:
        antibiotic = _clean_text(row.get("antibiotic"))
        sensitivity = _clean_text(row.get("antibiotic_sensitivity"))

        parts = []
        if antibiotic:
            parts.append(f"Antibiotic: {antibiotic}")
        if sensitivity:
            parts.append(f"Sensitivity: {sensitivity}")

        if parts:
            lines.append("- " + " | ".join(parts))
    return lines


def _collect_lab_and_imaging_results(encounter_doc):
    tests = _get_latest_lab_tests_for_encounter(encounter_doc)

    laboratory_blocks = []
    imaging_blocks = []

    for test_row in tests:
        test_doc = frappe.get_doc("Lab Test", test_row.name)
        template_type = _get_template_type(test_doc.template)

        lines = []
        lines.extend(_format_normal_test_items(test_doc))
        lines.extend(_format_descriptive_test_items(test_doc))
        lines.extend(_format_organism_test_items(test_doc))
        lines.extend(_format_sensitivity_test_items(test_doc))

        if not lines:
            continue

        block_header = f"{test_doc.lab_test_name or test_doc.template}"
        block_body = "\n".join(lines)
        block = f"{block_header}\n{block_body}"

        if template_type == "Imaging":
            imaging_blocks.append(block)
        else:
            laboratory_blocks.append(block)

    return laboratory_blocks, imaging_blocks


def _build_diagnosis(encounter_doc) -> str:
    blocks = []

    diagnosis = _clean_text(_get_value(encounter_doc, ["diagnosis"], ""))
    if diagnosis:
        blocks.append(f"Diagnosis:\n{diagnosis}")

    symptoms = _clean_text(_get_value(encounter_doc, ["custom_chief_complaint", "symptoms"], ""))
    if symptoms:
        blocks.append(f"Chief Complaint / Symptoms:\n{symptoms}")

    laboratory_blocks, imaging_blocks = _collect_lab_and_imaging_results(encounter_doc)

    if laboratory_blocks:
        blocks.append("Laboratory Results:\n" + "\n\n".join(laboratory_blocks))

    if imaging_blocks:
        blocks.append("Imaging Results:\n" + "\n\n".join(imaging_blocks))

    return "\n\n".join(blocks).strip()


def _fallback_recommendation(diagnosis_text: str, treatment_text: str) -> str:
    lines = []

    if diagnosis_text:
        lines.append("Review the documented diagnosis, symptoms, and investigation results together before the next follow-up.")

    if treatment_text:
        lines.append("Continue the prescribed treatment as documented and monitor clinical response closely.")

    lines.append("Advise the patient to return for reassessment if symptoms persist, worsen, or if new concerning symptoms appear.")
    lines.append("Arrange follow-up with the treating doctor to review progress and laboratory or imaging findings.")

    return "\n".join(f"- {x}" for x in lines)


def _generate_ai_recommendation(encounter_name: str, diagnosis_text: str, treatment_text: str) -> str:
    cache_key = f"medical_report_ai::{encounter_name}"
    cached = frappe.cache().get_value(cache_key)
    if cached:
        return cached

    api_key = frappe.conf.get("openai_api_key")
    if not api_key or not requests:
        result = _fallback_recommendation(diagnosis_text, treatment_text)
        frappe.cache().set_value(cache_key, result, expires_in_sec=600)
        return result

    prompt = f"""
You are helping a doctor write the Recommendations section of a medical report.

Use only the information below.
Write a short, professional, human clinical recommendation.
Do not mention AI.
Do not add unsupported facts.

Diagnosis / Examination:
{diagnosis_text or "N/A"}

Treatment:
{treatment_text or "N/A"}

Return only the recommendation text.
""".strip()

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
                "max_output_tokens": 220,
            },
            timeout=60,
        )

        if response.status_code == 429:
            result = _fallback_recommendation(diagnosis_text, treatment_text)
            frappe.cache().set_value(cache_key, result, expires_in_sec=600)
            return result

        response.raise_for_status()
        data = response.json()

        output_text = data.get("output_text")
        if output_text:
            result = output_text.strip()
            frappe.cache().set_value(cache_key, result, expires_in_sec=600)
            return result

        output = data.get("output", []) or []
        for item in output:
            for content in item.get("content", []) or []:
                text = content.get("text")
                if text:
                    result = text.strip()
                    frappe.cache().set_value(cache_key, result, expires_in_sec=600)
                    return result

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Medical Report AI Recommendation Error")

    result = _fallback_recommendation(diagnosis_text, treatment_text)
    frappe.cache().set_value(cache_key, result, expires_in_sec=600)
    return result


@frappe.whitelist()
def get_medical_report_defaults(encounter_name: str) -> Dict[str, Any]:
    if not encounter_name:
        frappe.throw(_("Consultation reference is required"))

    encounter = frappe.get_doc("Patient Encounter", encounter_name)
    patient = frappe.get_doc("Patient", encounter.patient)

    diagnosis_text = _build_diagnosis(encounter)
    treatment_text = _format_treatment(encounter)
    recommendation_text = _generate_ai_recommendation(encounter.name, diagnosis_text, treatment_text)

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
        "diagnosis": diagnosis_text,
        "treatment": treatment_text,
        "recommendation": recommendation_text,
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
    return doc.name
