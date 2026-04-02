import re
import math
import json
import hashlib
from typing import Dict, List, Optional, Tuple

import frappe
from frappe import _
from frappe.utils import flt, cint, nowdate


def on_submit_patient_encounter(doc, method=None):
    sync_prescription_invoice(doc)


def on_update_after_submit_patient_encounter(doc, method=None):
    sync_prescription_invoice(doc)


def on_cancel_patient_encounter(doc, method=None):
    invoice_name = frappe.db.get_value(
        "Sales Invoice",
        {
            "custom_patient_encounter": doc.name,
            "custom_is_prescription_invoice": 1,
            "docstatus": 0,
        },
        "name",
        order_by="creation desc",
    )
    if invoice_name:
        try:
            frappe.get_doc("Sales Invoice", invoice_name).add_comment(
                "Comment",
                _("Patient Encounter {0} was cancelled. Review this draft invoice manually.").format(doc.name),
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Prescription Invoice Cancel Notice")


def on_submit_sales_invoice(doc, method=None):
    """
    Mark source Drug Prescription rows as billed after invoice submit.
    Required custom fields on Drug Prescription:
    - custom_is_billed (Check)
    - custom_billed_sales_invoice (Link -> Sales Invoice)
    - custom_billed_sales_invoice_item (Data)
    """
    if not cint(getattr(doc, "custom_is_prescription_invoice", 0)):
        return

    has_row_id = frappe.get_meta("Sales Invoice Item").has_field("custom_drug_prescription_row_id")

    if not has_row_id:
        return

    for item in doc.get("items") or []:
        row_id = item.get("custom_drug_prescription_row_id")
        if not row_id:
            continue

        if frappe.db.exists("Drug Prescription", row_id):
            frappe.db.set_value(
                "Drug Prescription",
                row_id,
                {
                    "custom_is_billed": 1,
                    "custom_billed_sales_invoice": doc.name,
                    "custom_billed_sales_invoice_item": item.name,
                },
                update_modified=False,
            )


def sync_prescription_invoice(encounter_doc):
    if encounter_doc.doctype != "Patient Encounter":
        return

    prescription_rows = build_prescription_rows(encounter_doc)
    if not prescription_rows:
        return

    customer = get_customer_from_patient(encounter_doc.patient)
    if not customer:
        frappe.throw(_("Patient {0} has no linked Customer.").format(encounter_doc.patient))

    current_hash = make_prescription_hash(encounter_doc, prescription_rows)
    last_hash = getattr(encounter_doc, "custom_last_prescription_sync_hash", None)
    if last_hash and last_hash == current_hash:
        return

    latest_invoice = get_latest_linked_prescription_invoice(encounter_doc.name)

    if latest_invoice and latest_invoice.docstatus == 0:
        invoice = update_existing_draft_invoice(latest_invoice, encounter_doc, customer, prescription_rows)
    else:
        invoice_type = "Replacement" if latest_invoice and latest_invoice.docstatus == 1 else "Original"
        previous_invoice = latest_invoice.name if latest_invoice and latest_invoice.docstatus == 1 else None

        invoice = create_draft_sales_invoice_from_encounter(
            encounter_doc,
            customer,
            prescription_rows,
            invoice_type=invoice_type,
            previous_invoice=previous_invoice,
        )

    update_encounter_sync_state(encounter_doc.name, invoice.name, current_hash)


def build_prescription_rows(encounter_doc) -> List[Dict]:
    rows = []
    errors = []

    for row in encounter_doc.get("drug_prescription") or []:
        if cint(getattr(row, "custom_is_billed", 0)) == 1:
            continue

        item_code = (row.drug_code or row.drug_name or "").strip()
        if not item_code:
            errors.append(f"Row #{row.idx}: Missing drug_code/drug_name")
            continue

        item_code = resolve_item_code(item_code, row.drug_name)
        if not item_code:
            errors.append(
                f"Row #{row.idx}: Item not found for drug_code='{row.drug_code}' drug_name='{row.drug_name}'"
            )
            continue

        qty, calc_note = calculate_prescription_qty(row)
        if qty <= 0:
            errors.append(f"Row #{row.idx}: Computed quantity <= 0 for item {item_code}")
            continue

        rows.append(
            {
                "row_id": row.name,
                "idx": row.idx,
                "item_code": item_code,
                "item_name": frappe.db.get_value("Item", item_code, "item_name") or item_code,
                "description": build_item_description(row, calc_note),
                "qty": qty,
                "dosage": row.dosage,
                "period": row.period,
                "medication_request": row.medication_request,
                "reference_dt": "Medication Request" if row.medication_request else None,
                "reference_dn": row.medication_request if row.medication_request else None,
            }
        )

    if errors:
        frappe.throw(_("Prescription invoice validation failed:<br>{0}").format("<br>".join(errors)))

    return rows


def make_prescription_hash(encounter_doc, rows: List[Dict]) -> str:
    payload = {
        "encounter": encounter_doc.name,
        "patient": encounter_doc.patient,
        "company": encounter_doc.company,
        "rows": [
            {
                "row_id": r["row_id"],
                "item_code": r["item_code"],
                "qty": flt(r["qty"]),
                "dosage": r["dosage"],
                "period": r["period"],
                "medication_request": r["medication_request"],
            }
            for r in rows
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def update_encounter_sync_state(encounter_name: str, invoice_name: str, sync_hash: str):
    values = {
        "custom_latest_pharmacy_invoice": invoice_name,
        "custom_last_prescription_sync_hash": sync_hash,
        "custom_prescription_invoice_status": "Draft Linked",
    }
    frappe.db.set_value("Patient Encounter", encounter_name, values, update_modified=False)


def get_customer_from_patient(patient_name: str) -> Optional[str]:
    customer = frappe.db.get_value("Patient", patient_name, "customer")
    if customer:
        return customer

    link = frappe.get_all(
        "Dynamic Link",
        filters={
            "link_doctype": "Patient",
            "link_name": patient_name,
            "parenttype": "Customer",
        },
        fields=["parent"],
        limit=1,
    )
    return link[0].parent if link else None


def get_latest_linked_prescription_invoice(encounter_name: str):
    invoice_name = frappe.db.get_value(
        "Sales Invoice",
        {
            "custom_patient_encounter": encounter_name,
            "custom_is_prescription_invoice": 1,
        },
        "name",
        order_by="creation desc",
    )
    return frappe.get_doc("Sales Invoice", invoice_name) if invoice_name else None


def create_draft_sales_invoice_from_encounter(
    encounter_doc,
    customer: str,
    prescription_rows: List[Dict],
    invoice_type: str = "Original",
    previous_invoice: Optional[str] = None,
):
    invoice = frappe.new_doc("Sales Invoice")
    invoice.customer = customer
    invoice.patient = encounter_doc.patient
    invoice.company = encounter_doc.company
    invoice.due_date = encounter_doc.encounter_date or nowdate()
    invoice.posting_date = encounter_doc.encounter_date or nowdate()
    invoice.custom_patient_encounter = encounter_doc.name
    invoice.custom_prescription_invoice_type = invoice_type
    invoice.custom_previous_prescription_invoice = previous_invoice
    invoice.custom_is_prescription_invoice = 1
    invoice.custom_prescription_sync_hash = make_prescription_hash(encounter_doc, prescription_rows)
    invoice.remarks = _("Auto-generated from Patient Encounter {0}").format(encounter_doc.name)

    append_invoice_items(invoice, encounter_doc, prescription_rows)

    invoice.set_missing_values()
    invoice.calculate_taxes_and_totals()
    invoice.insert(ignore_permissions=True)
    return invoice


def update_existing_draft_invoice(invoice_doc, encounter_doc, customer: str, prescription_rows: List[Dict]):
    invoice_doc.customer = customer
    invoice_doc.patient = encounter_doc.patient
    invoice_doc.company = encounter_doc.company
    invoice_doc.due_date = encounter_doc.encounter_date or nowdate()
    invoice_doc.posting_date = encounter_doc.encounter_date or nowdate()
    invoice_doc.custom_patient_encounter = encounter_doc.name
    invoice_doc.custom_is_prescription_invoice = 1
    invoice_doc.custom_prescription_sync_hash = make_prescription_hash(encounter_doc, prescription_rows)
    invoice_doc.remarks = _("Auto-synced from Patient Encounter {0}").format(encounter_doc.name)

    invoice_doc.set("items", [])
    append_invoice_items(invoice_doc, encounter_doc, prescription_rows)

    invoice_doc.set_missing_values()
    invoice_doc.calculate_taxes_and_totals()
    invoice_doc.save(ignore_permissions=True)
    return invoice_doc


def append_invoice_items(invoice_doc, encounter_doc, prescription_rows: List[Dict]):
    has_row_id = frappe.get_meta("Sales Invoice Item").has_field("custom_drug_prescription_row_id")
    has_med_req = frappe.get_meta("Sales Invoice Item").has_field("custom_medication_request")
    has_enc = frappe.get_meta("Sales Invoice Item").has_field("custom_patient_encounter")

    for row in prescription_rows:
        child = invoice_doc.append("items", {})
        child.item_code = row["item_code"]
        child.item_name = row["item_name"]
        child.description = row["description"]
        child.qty = row["qty"]

        if row.get("reference_dt"):
            child.reference_dt = row["reference_dt"]
        if row.get("reference_dn"):
            child.reference_dn = row["reference_dn"]

        if has_row_id:
            child.custom_drug_prescription_row_id = row["row_id"]
        if has_med_req:
            child.custom_medication_request = row["medication_request"]
        if has_enc:
            child.custom_patient_encounter = encounter_doc.name


def resolve_item_code(drug_code: Optional[str], drug_name: Optional[str]) -> Optional[str]:
    candidates = []
    if drug_code:
        candidates.append(drug_code.strip())
    if drug_name and drug_name.strip() not in candidates:
        candidates.append(drug_name.strip())

    for code in candidates:
        if frappe.db.exists("Item", code):
            return code

    for code in candidates:
        item = frappe.db.get_value("Item", {"item_name": code}, "name")
        if item:
            return item

    return None


def build_item_description(row, calc_note: str) -> str:
    parts = [
        row.drug_name or row.drug_code or "",
        f"Dosage: {row.dosage or '-'}",
        f"Period: {row.period or '-'}",
    ]
    if calc_note:
        parts.append(f"Qty Logic: {calc_note}")
    return "\n".join(parts)


def calculate_prescription_qty(rx_row) -> Tuple[float, str]:
    dosage = (rx_row.dosage or "").strip()
    period = (rx_row.period or "").strip()
    interval = flt(rx_row.interval) if rx_row.interval is not None else 0
    interval_uom = (rx_row.interval_uom or "").strip()

    admins_per_day, dosage_note = parse_dosage_frequency(dosage)
    if admins_per_day <= 0:
        admins_per_day = infer_frequency_from_interval(interval, interval_uom) or 1
        dosage_note = dosage_note or "Dosage ambiguous; interval fallback used."

    duration_days, duration_note = parse_period_to_days(period)
    if duration_days and duration_days > 0:
        qty = admins_per_day * duration_days
        return max(1, math.ceil(qty)), f"{dosage_note}; {duration_note}; qty={admins_per_day} * {duration_days}"

    if period and "hour" in period.lower():
        hours = extract_number(period)
        if hours and hours > 0:
            qty = admins_per_day * (hours / 24.0)
            return max(1, math.ceil(qty)), f"{dosage_note}; hour-window period {hours}h"

    interval_days = interval_uom_to_days(interval, interval_uom)
    if interval_days and interval_days > 0:
        qty = admins_per_day * interval_days
        return max(1, math.ceil(qty)), f"{dosage_note}; interval fallback {interval} {interval_uom}"

    return max(1, math.ceil(admins_per_day)), f"{dosage_note}; default daily administrations"


def parse_dosage_frequency(dosage: str) -> Tuple[float, str]:
    if not dosage:
        return 0, "No dosage provided"

    value = dosage.strip().upper()
    pattern = re.match(r"^\s*(\d*\.?\d+)\s*-\s*(\d*\.?\d+)\s*-\s*(\d*\.?\d+)\s*$", value)
    if pattern:
        total = flt(pattern.group(1)) + flt(pattern.group(2)) + flt(pattern.group(3))
        return total, f"Structured dosage {dosage} => {total}/day"

    abbr_map = {
        "OD": 1,
        "QD": 1,
        "BD": 2,
        "BID": 2,
        "TID": 3,
        "QID": 4,
        "HS": 1,
        "QHS": 1,
        "NOCTE": 1,
    }

    units = extract_leading_quantity(value) or 1
    for key, freq in abbr_map.items():
        if re.search(rf"\b{re.escape(key)}\b", value):
            return units * freq, f"Abbreviation {key} => {units} x {freq}/day"

    qh = re.search(r"\bQ\s*(\d+)\s*H\b", value)
    if qh:
        every_hours = cint(qh.group(1))
        if every_hours > 0:
            return units * (24 / every_hours), f"q{every_hours}h parsed"

    eh = re.search(r"\bEVERY\s*(\d+)\s*H", value)
    if eh:
        every_hours = cint(eh.group(1))
        if every_hours > 0:
            return units * (24 / every_hours), f"every {every_hours} hours parsed"

    return 0, f"Ambiguous dosage '{dosage}'"


def parse_period_to_days(period: str) -> Tuple[Optional[float], str]:
    if not period:
        return None, "No period provided"

    value = period.strip().lower()
    qty = extract_number(value)
    if not qty or qty <= 0:
        return None, f"Could not parse period '{period}'"

    if "day" in value:
        return qty, f"{qty} day(s)"
    if "week" in value:
        return qty * 7, f"{qty} week(s)"
    if "month" in value:
        return qty * 30, f"{qty} month(s)"
    if "hour" in value:
        return None, f"{qty} hour(s)"
    return None, f"Unknown unit in '{period}'"


def infer_frequency_from_interval(interval: float, interval_uom: str) -> Optional[float]:
    if not interval or interval <= 0 or not interval_uom:
        return None
    uom = interval_uom.strip().lower()
    if uom == "day":
        return 1 / interval
    if uom == "hour":
        return 24 / interval
    if uom == "week":
        return 1 / (interval * 7)
    return None


def interval_uom_to_days(interval: float, interval_uom: str) -> Optional[float]:
    if not interval or interval <= 0 or not interval_uom:
        return None
    uom = interval_uom.strip().lower()
    if uom == "day":
        return interval
    if uom == "week":
        return interval * 7
    if uom == "month":
        return interval * 30
    if uom == "hour":
        return interval / 24
    return None


def extract_number(text: str) -> Optional[float]:
    m = re.search(r"(\d+(?:\.\d+)?)", text or "")
    return flt(m.group(1)) if m else None


def extract_leading_quantity(text: str) -> Optional[float]:
    m = re.match(r"^\s*(\d+(?:\.\d+)?)", text or "")
    return flt(m.group(1)) if m else None