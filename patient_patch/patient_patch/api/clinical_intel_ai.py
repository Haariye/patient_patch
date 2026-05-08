"""
Clinical Intel AI — Server-side AI report generation
Place this file at: patient_patch/patient_patch/api/clinical_intel_ai.py
Then call from JS: frappe.call({ method: 'patient_patch.patient_patch.api.clinical_intel_ai.generate_clinical_intel' })
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict

import frappe
from frappe import _

try:
    import requests
except Exception:
    requests = None


def _clean_text(val) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _strip_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _get_openai_api_key() -> str:
    """Get API key from Healthcare Settings password field, site_config, or env."""
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


def _extract_output_text(data: Dict[str, Any]) -> str:
    """Extract text from OpenAI Responses API format."""
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


@frappe.whitelist()
def generate_clinical_intel(patient: str) -> Dict[str, Any]:
    """
    Generate AI clinical intelligence report for a patient.
    Called from Clinical Intel client script.
    """
    if not patient:
        return {"success": False, "message": "Patient ID is required.", "report": ""}

    api_key = _get_openai_api_key()
    if not api_key:
        return {"success": False, "message": "OpenAI API key not configured. Check Healthcare Settings.", "report": ""}

    if not requests:
        return {"success": False, "message": "Python requests package not available on server.", "report": ""}

    # ── Collect patient data ──
    context_parts = []

    # Patient info
    try:
        pat = frappe.get_doc("Patient", patient)
        context_parts.append(f"Patient: {pat.patient_name or patient}")
        if pat.get("sex"):
            context_parts.append(f"Sex: {pat.sex}")
        dob = pat.get("dob")
        if dob:
            from frappe.utils import getdate, today
            d = getdate(dob)
            t = getdate(today())
            age = t.year - d.year - ((t.month, t.day) < (d.month, d.day))
            context_parts.append(f"Age: {age}")
    except Exception:
        context_parts.append(f"Patient: {patient}")

    # Medical Records (last 30)
    med_records = frappe.get_all(
        "Patient Medical Record",
        filters={"patient": patient},
        fields=["subject", "creation", "reference_doctype", "reference_name", "communication_date"],
        order_by="creation desc",
        limit=30,
    )

    if med_records:
        context_parts.append("=== MEDICAL HISTORY (Recent Records) ===")
        for r in med_records:
            date_str = str(r.communication_date or r.creation)[:10]
            subject = _strip_html(r.subject or "")[:250]
            ref_type = r.reference_doctype or "Record"
            context_parts.append(f"[{ref_type}] {date_str}: {subject}")

    # Encounters with child tables (last 10)
    encounter_names = list(set(
        r.reference_name for r in med_records
        if r.reference_doctype == "Patient Encounter" and r.reference_name
    ))[:10]

    for enc_name in encounter_names:
        try:
            enc = frappe.get_doc("Patient Encounter", enc_name)

            # Chief complaint
            cc = _clean_text(enc.get("custom_chief_complaint") or "")
            if cc:
                context_parts.append(f"\n[Encounter {enc_name}] Chief Complaint: {_strip_html(cc)}")

            # Physical exam
            pe = _clean_text(enc.get("custom_physical_examination") or "")
            if pe:
                context_parts.append(f"[Encounter {enc_name}] Physical Exam: {_strip_html(pe)}")

            # Notes
            notes = _clean_text(enc.get("encounter_comment") or "")
            if notes:
                context_parts.append(f"[Encounter {enc_name}] Notes: {_strip_html(notes)}")

            # Diagnosis
            for dx in (enc.get("diagnosis") or []):
                d = _clean_text(dx.get("diagnosis") or "")
                if d:
                    context_parts.append(f"[Encounter {enc_name}] Diagnosis: {d}")

            # Drugs
            for drug in (enc.get("drug_prescription") or []):
                name = _clean_text(drug.get("drug_name") or drug.get("drug_code") or "")
                dosage = _clean_text(drug.get("dosage") or "")
                period = _clean_text(drug.get("period") or "")
                if name:
                    context_parts.append(f"[Encounter {enc_name}] Drug: {name} {dosage} {period}")

            # Symptoms
            for sym in (enc.get("symptoms") or []):
                complaint = _clean_text(sym.get("complaint") or sym.get("symptom") or "")
                if complaint:
                    context_parts.append(f"[Encounter {enc_name}] Symptom: {complaint}")

        except Exception:
            pass

    # Lab Tests (last 10)
    lab_names = list(set(
        r.reference_name for r in med_records
        if r.reference_doctype == "Lab Test" and r.reference_name
    ))[:10]

    for lab_name in lab_names:
        try:
            lab = frappe.get_doc("Lab Test", lab_name)
            for item in (lab.get("normal_test_items") or []):
                test = _clean_text(item.get("lab_test_name") or "")
                val = _clean_text(item.get("result_value") or "")
                uom = _clean_text(item.get("lab_test_uom") or "")
                if test and val:
                    context_parts.append(f"[Lab {lab_name}] {test}: {val} {uom}")
        except Exception:
            pass

    full_context = "\n".join(context_parts)

    prompt = f"""You are a clinical intelligence assistant helping a doctor evaluate a patient.
Analyze the following patient history and provide a comprehensive clinical brief.

Rules:
- Be medically professional and concise.
- Do not mention AI or that you are an AI.
- Do not invent facts not in the data.
- Use markdown formatting with **bold** headers.

Provide these sections:
1. **Clinical Overview** — patient journey summary
2. **Key Findings** — important vitals, labs, diagnoses
3. **Abnormal Trends** — any concerning patterns
4. **Risk Assessment** — red flags or deterioration signs
5. **Recommendations** — suggested next steps

Patient Data:
{full_context}
"""

    # ── Call OpenAI Responses API (same as medical_report.py) ──
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
                    "max_output_tokens": 2000,
                },
                timeout=60,
            )

            if response.status_code == 429:
                last_error = f"Rate limited (429): {response.text}"
                time.sleep(2 * (attempt + 1))
                continue

            if response.status_code >= 400:
                last_error = f"API error ({response.status_code}): {response.text[:300]}"
                break

            data = response.json()
            text = _extract_output_text(data)

            if text:
                return {"success": True, "message": "", "report": text}

            if data.get("status") == "incomplete":
                last_error = f"Incomplete: {data.get('incomplete_details')}"
            else:
                last_error = f"No output text in response"
            continue

        except Exception as e:
            last_error = str(e)
            time.sleep(2 * (attempt + 1))

    frappe.log_error(last_error or "Unknown error", "Clinical Intel AI Error")
    return {"success": False, "message": f"AI generation failed: {last_error}", "report": ""}
