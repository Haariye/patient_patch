from __future__ import annotations

import json
import os
import re
import time
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


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"</tr>", "\n", text, flags=re.I)
    text = re.sub(r"</td>", " | ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


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


def _get_openai_api_key() -> str:
    # 1) Healthcare Settings custom password field
    try:
        hs = frappe.get_cached_doc("Healthcare Settings")
        if hs and hs.meta.has_field("custom_openai_api_key"):
            key = hs.get_password("custom_openai_api_key")
            if key:
                return key
    except Exception:
        pass

    # 2) site_config.json
    key = frappe.conf.get("openai_api_key")
    if key:
        return key

    # 3) environment variable
    return os.environ.get("OPENAI_API_KEY", "")


def _format_drug_prescriptions(encounter_doc) -> str:
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


def _get_template_type(template_name: str) -> str:
    if not template_name:
        return ""
    try:
        return _clean_text(
            frappe.db.get_value("Lab Test Template", template_name, "lab_test_template_type")
        )
    except Exception:
        return ""


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


def _get_recent_lab_tests_for_encounter(encounter_doc) -> List[Dict[str, Any]]:
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


def _collect_lab_and_imaging_results(encounter_doc):
    tests = _get_recent_lab_tests_for_encounter(encounter_doc)

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

        header = _clean_text(test_doc.lab_test_name or test_doc.template or test_doc.name)
        block = f"{header}\n" + "\n".join(lines)

        if template_type == "Imaging":
            imaging_blocks.append(block)
        else:
            laboratory_blocks.append(block)

    return laboratory_blocks, imaging_blocks


def _get_recent_patient_medical_records(encounter_doc, patient_doc) -> str:
    patient_id = encounter_doc.patient
    patient_name = _clean_text(_get_value(patient_doc, ["patient_name"], ""))

    try:
        records = frappe.get_all(
            "Patient Medical Record",
            or_filters=[
                {"patient": patient_id},
                {"patient": patient_name},
                {"reference_name": encounter_doc.name},
            ],
            fields=["name", "communication_date", "subject", "reference_name", "modified"],
            order_by="communication_date desc, modified desc",
            limit=5,
        )
    except Exception:
        records = []

    blocks = []
    for row in records:
        subject = _strip_html(_clean_text(row.get("subject")))
        if not subject:
            continue

        label_parts = []
        if row.get("name"):
            label_parts.append(_clean_text(row["name"]))
        if row.get("communication_date"):
            label_parts.append(_clean_text(row["communication_date"]))

        header = " | ".join(label_parts) if label_parts else "Patient Medical Record"
        blocks.append(f"{header}\n{subject}")

    return "\n\n".join(blocks).strip()


def _build_ai_context(encounter_name: str) -> Dict[str, str]:
    encounter = frappe.get_doc("Patient Encounter", encounter_name)
    patient = frappe.get_doc("Patient", encounter.patient)

    diagnosis = _clean_text(_get_value(encounter, ["diagnosis"], ""))
    symptoms = _clean_text(_get_value(encounter, ["custom_chief_complaint", "symptoms"], ""))
    doctor = _clean_text(_get_value(encounter, ["practitioner_name"], ""))

    treatment_text = _format_drug_prescriptions(encounter)
    lab_blocks, imaging_blocks = _collect_lab_and_imaging_results(encounter)
    medical_history = _get_recent_patient_medical_records(encounter, patient)

    context_parts = [
        f"Patient ID: {encounter.patient}",
        f"Patient Name: {_clean_text(_get_value(patient, ['patient_name'], encounter.patient))}",
        f"Sex: {_clean_text(_get_value(patient, ['sex'], ''))}",
        f"Age: {_get_patient_age(patient)}",
        f"Doctor: {doctor}",
        f"Consultation Reference: {encounter.name}",
    ]

    if symptoms:
        context_parts.append(f"Current Chief Complaint / Symptoms:\n{symptoms}")

    if diagnosis:
        context_parts.append(f"Current Consultation Diagnosis:\n{diagnosis}")

    if treatment_text:
        context_parts.append(f"Current Consultation Drug Prescription:\n{treatment_text}")

    if lab_blocks:
        context_parts.append("Recent Laboratory Results:\n" + "\n\n".join(lab_blocks))

    if imaging_blocks:
        context_parts.append("Recent Imaging Results:\n" + "\n\n".join(imaging_blocks))

    if medical_history:
        context_parts.append("Latest Patient Medical History:\n" + medical_history)

    return {
        "patient": encounter.patient,
        "patient_name": _clean_text(_get_value(patient, ["patient_name"], encounter.patient)),
        "patient_id": encounter.patient,
        "age": _get_patient_age(patient),
        "sex": _clean_text(_get_value(patient, ["sex"], "")),
        "report_date": nowdate(),
        "consultation_reference": encounter.name,
        "doctor": doctor,
        "context": "\n\n".join([x for x in context_parts if x]).strip(),
    }


def _extract_output_text(data: Dict[str, Any]) -> str:
    output_text = data.get("output_text")
    if output_text:
        return output_text.strip()

    output = data.get("output", []) or []
    for item in output:
        if item.get("type") != "message":
            continue
        for content in item.get("content", []) or []:
            if content.get("type") == "output_text" and content.get("text"):
                return content["text"].strip()

    return ""


def _extract_json_object(text: str) -> Dict[str, Any]:
    if not text:
        return {}

    text = text.strip()

    # remove code fences if any
    text = re.sub(r"^```json\s*", "", text, flags=re.I)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    # try full parse first
    try:
        return json.loads(text)
    except Exception:
        pass

    # fallback: first {...}
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            return {}

    return {}


@frappe.whitelist()
def get_medical_report_defaults(encounter_name: str) -> Dict[str, Any]:
    if not encounter_name:
        frappe.throw(_("Consultation reference is required"))

    base = _build_ai_context(encounter_name)
    return {
        "naming_series": "MR-.YYYY.-.#####",
        "patient": base["patient"],
        "patient_name": base["patient_name"],
        "patient_id": base["patient_id"],
        "age": base["age"],
        "sex": base["sex"],
        "report_date": base["report_date"],
        "consultation_reference": base["consultation_reference"],
        "doctor": base["doctor"],
        "diagnosis": "",
        "treatment": "",
        "recommendation": "",
    }


@frappe.whitelist()
def generate_medical_report_ai_fields(encounter_name: str) -> Dict[str, Any]:
    if not encounter_name:
        return {
            "diagnosis": "",
            "treatment": "",
            "recommendation": "",
            "message": "Missing consultation reference."
        }

    api_key = _get_openai_api_key()
    if not api_key:
        return {
            "diagnosis": "",
            "treatment": "",
            "recommendation": "",
            "message": "OpenAI API key is missing."
        }

    if not requests:
        return {
            "diagnosis": "",
            "treatment": "",
            "recommendation": "",
            "message": "Python requests package is not available on the server."
        }

    base = _build_ai_context(encounter_name)

    prompt = f"""
You are assisting a physician in drafting a medical report.

Using ONLY the patient information below, generate:
1. diagnosis
2. treatment
3. recommendation

Rules:
- Be medically professional and concise.
- Do not mention AI.
- Do not invent facts not supported by the source text.
- If information is limited, stay conservative.
- For diagnosis: summarize the clinically relevant diagnosis/examination findings.
- For treatment: summarize the actual treatment/medication plan already evidenced in the history.
- For recommendation: write a physician-style recommendation and follow-up advice.

Return STRICT JSON only in this format:
{{
  "diagnosis": "...",
  "treatment": "...",
  "recommendation": "..."
}}

Patient Clinical Context:
{base["context"]}
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
            text = _extract_output_text(data)

            if not text:
                if data.get("status") == "incomplete":
                    last_error = f"Incomplete response: {data.get('incomplete_details')}"
                else:
                    last_error = f"No output text found in response: {data}"
                continue

            parsed = _extract_json_object(text)
            if parsed:
                return {
                    "diagnosis": _clean_text(parsed.get("diagnosis")),
                    "treatment": _clean_text(parsed.get("treatment")),
                    "recommendation": _clean_text(parsed.get("recommendation")),
                    "message": "",
                }

            last_error = f"Could not parse AI JSON: {text}"

        except Exception:
            last_error = frappe.get_traceback()
            time.sleep(2 * (attempt + 1))

    frappe.log_error(last_error or "Unknown OpenAI error", "Medical Report AI Fields Error")
    return {
        "diagnosis": "",
        "treatment": "",
        "recommendation": "",
        "message": "Could not generate AI draft now. Please try again."
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