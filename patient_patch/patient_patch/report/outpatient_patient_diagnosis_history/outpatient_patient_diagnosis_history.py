# Copyright (c) 2026, Dagaar and contributors
# For license information, please see license.txt
#
# Outpatient Patient Diagnosis History
# ------------------------------------
# A Script Report under the Patient Patch app that lists outpatient encounters
# (one row per diagnosis line) for a configurable period and dimension set.
#
# It returns:
#   - columns:        the table schema for desk
#   - data:           the row list
#   - message:        an HTML dashboard rendered above the table
#   - chart:          a single Frappe chart (trend) for the native chart slot
#   - report_summary: the KPI cards strip rendered by Frappe
#
# It also exposes whitelisted endpoints for:
#   - Diagnosis filter autocomplete
#   - City filter autocomplete (sourced from `tabAddress.city`)
#   - AI summary (single-patient, OpenAI Responses API — same pattern as
#     patient_patch.api.clinical_intel_ai)
#   - A4-landscape printable HTML report
#
# Field-name compatibility
# ------------------------
# This file is intentionally defensive. ERPNext Healthcare has shipped slightly
# different field names across versions (especially for the diagnosis child
# table). We resolve fieldnames at runtime via frappe.get_meta(...).has_field(...)
# and degrade gracefully when something is missing. See `_resolve_schema()`.

from __future__ import annotations

import json
import os
import re
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

import frappe
from frappe import _
from frappe.utils import cint, cstr, escape_html, flt, getdate, today

try:
    import requests
except Exception:
    requests = None


# ---------------------------------------------------------------------------
# Schema resolution
# ---------------------------------------------------------------------------

PATIENT_CITY_CANDIDATES = ["custom_city", "city"]
ENCOUNTER_TYPE_CANDIDATES = [
    "encounter_type",
    "appointment_type",
    "custom_encounter_type",
]
ENCOUNTER_DEPARTMENT_CANDIDATES = ["medical_department", "department"]
ENCOUNTER_REMARKS_CANDIDATES = ["custom_chief_complaint", "symptoms", "physical_examination"]


def _resolve_schema() -> Dict[str, Optional[str]]:
    """Look up which optional fields actually exist on this site."""

    patient_meta = frappe.get_meta("Patient")
    encounter_meta = frappe.get_meta("Patient Encounter")

    def first_existing(meta, candidates):
        for fn in candidates:
            if meta.has_field(fn):
                return fn
        return None

    schema = {
        "patient_city": first_existing(patient_meta, PATIENT_CITY_CANDIDATES),
        "encounter_type": first_existing(encounter_meta, ENCOUNTER_TYPE_CANDIDATES),
        "encounter_department": first_existing(encounter_meta, ENCOUNTER_DEPARTMENT_CANDIDATES),
        "encounter_remarks": first_existing(encounter_meta, ENCOUNTER_REMARKS_CANDIDATES),
        "has_diagnosis_child_table": frappe.db.exists("DocType", "Patient Encounter Diagnosis") and True,
    }
    return schema


# ---------------------------------------------------------------------------
# Age groups
# ---------------------------------------------------------------------------
# 0–12 mo is included as its own bucket so the doctor can target infants.
# Each bucket is an inclusive [low_months, high_months] range so the SQL
# filter is exact regardless of unit.

AGE_GROUPS: "OrderedDict[str, Tuple[int, Optional[int]]]" = OrderedDict([
    ("0–12 mo",  (0, 11)),       # 0 to 11 completed months
    ("1–5 yr",   (12, 71)),      # 12..71 months  (1y .. <6y)
    ("6–17 yr",  (72, 215)),     # 6y .. <18y
    ("18–30 yr", (216, 371)),    # 18y .. <31y
    ("31–45 yr", (372, 551)),    # 31y .. <46y
    ("46–60 yr", (552, 731)),    # 46y .. <61y
    ("60+",      (732, None)),   # 61y+
])

AGE_GROUP_LABELS = list(AGE_GROUPS.keys())


def _bucket_for_months(months: Optional[int]) -> Optional[str]:
    if months is None or months < 0:
        return None
    for label, (lo, hi) in AGE_GROUPS.items():
        if hi is None:
            if months >= lo:
                return label
        elif lo <= months <= hi:
            return label
    return None


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def execute(filters: Optional[Dict[str, Any]] = None):
    filters = _normalise_filters(filters or {})
    schema = _resolve_schema()

    columns = get_columns(schema)
    data = get_data(filters, schema)

    chart = get_chart(filters, data)
    report_summary = get_report_summary(filters, data)
    message = get_message_html(filters, data, schema)

    return columns, data, message, chart, report_summary


# ---------------------------------------------------------------------------
# Filter validation
# ---------------------------------------------------------------------------

def _normalise_filters(filters: Dict[str, Any]) -> Dict[str, Any]:
    if not filters.get("from_date") or not filters.get("to_date"):
        frappe.throw(_("From Date and To Date are required."))

    from_date = getdate(filters["from_date"])
    to_date = getdate(filters["to_date"])
    if from_date > to_date:
        frappe.throw(_("From Date cannot be after To Date."))

    age_groups = filters.get("age_groups") or []
    if isinstance(age_groups, str):
        try:
            age_groups = json.loads(age_groups)
        except Exception:
            age_groups = [g.strip() for g in age_groups.split(",") if g.strip()]
    age_groups = [g for g in (age_groups or []) if g in AGE_GROUPS]

    out = {
        "from_date": from_date,
        "to_date": to_date,
        "practitioner": (filters.get("practitioner") or "").strip() or None,
        "patient": (filters.get("patient") or "").strip() or None,
        "diagnosis": (filters.get("diagnosis") or "").strip() or None,
        "city": (filters.get("city") or "").strip() or None,
        "sex": (filters.get("sex") or "").strip() or None,
        "age_groups": age_groups,
        "group_by": (filters.get("group_by") or "Month").strip(),
        "include_no_diagnosis": cint(filters.get("include_no_diagnosis") or 0),
    }

    # NOTE on the City filter:
    # `Patient.custom_city` is a Link field whose option is `Address`, so it
    # stores the Address ID (e.g. "PAT-Home-001") — NOT a city string.
    # The filter value coming from the JS Link is also an Address ID, so we
    # compare it directly. We additionally fetch the Address's city for use
    # in the printable filter chip.
    out["city_display"] = None
    if out["city"]:
        try:
            city_label = frappe.db.get_value("Address", out["city"], "city")
        except Exception:
            city_label = None
        out["city_display"] = city_label or out["city"]

    if out["group_by"] not in ("Day", "Month", "Year"):
        out["group_by"] = "Month"

    return out


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------

def get_columns(schema: Dict[str, Optional[str]]) -> List[Dict[str, Any]]:
    columns = [
        {"label": _("Encounter"), "fieldname": "encounter", "fieldtype": "Link",
         "options": "Patient Encounter", "width": 160},
        {"label": _("Date"), "fieldname": "encounter_date", "fieldtype": "Date", "width": 100},
        {"label": _("Patient"), "fieldname": "patient", "fieldtype": "Link",
         "options": "Patient", "width": 130},
        {"label": _("Patient Name"), "fieldname": "patient_name", "fieldtype": "Data", "width": 180},
        {"label": _("Age"), "fieldname": "age_display", "fieldtype": "Data", "width": 80},
        {"label": _("Sex"), "fieldname": "sex", "fieldtype": "Data", "width": 80},
        {"label": _("City"), "fieldname": "city", "fieldtype": "Data", "width": 120},
        {"label": _("Practitioner"), "fieldname": "practitioner", "fieldtype": "Link",
         "options": "Healthcare Practitioner", "width": 160},
        {"label": _("Practitioner Name"), "fieldname": "practitioner_name", "fieldtype": "Data", "width": 160},
        {"label": _("Diagnosis"), "fieldname": "diagnosis", "fieldtype": "Data", "width": 240},
        {"label": _("Diagnosis Code"), "fieldname": "diagnosis_code", "fieldtype": "Data", "width": 120},
        {"label": _("Department"), "fieldname": "department", "fieldtype": "Link",
         "options": "Medical Department", "width": 140},
        {"label": _("Encounter Type"), "fieldname": "encounter_type", "fieldtype": "Data", "width": 130},
        {"label": _("Notes"), "fieldname": "remarks", "fieldtype": "Small Text", "width": 220},
    ]
    return columns


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def get_data(filters: Dict[str, Any], schema: Dict[str, Optional[str]]) -> List[Dict[str, Any]]:
    """One row per (encounter, diagnosis line) pair."""

    city_select = (
        f"p.`{schema['patient_city']}`" if schema["patient_city"] else "''"
    )
    enc_type_select = (
        f"pe.`{schema['encounter_type']}`" if schema["encounter_type"] else "''"
    )
    department_select = (
        f"pe.`{schema['encounter_department']}`" if schema["encounter_department"] else "NULL"
    )
    remarks_select = (
        f"pe.`{schema['encounter_remarks']}`" if schema["encounter_remarks"] else "''"
    )

    conditions = [
        "pe.docstatus = 1",
        "IFNULL(pe.inpatient_record, '') = ''",
        "pe.encounter_date BETWEEN %(from_date)s AND %(to_date)s",
    ]
    params: Dict[str, Any] = {
        "from_date": filters["from_date"],
        "to_date": filters["to_date"],
        "today_for_age": today(),
    }

    if filters["practitioner"]:
        conditions.append("pe.practitioner = %(practitioner)s")
        params["practitioner"] = filters["practitioner"]

    if filters["patient"]:
        conditions.append("pe.patient = %(patient)s")
        params["patient"] = filters["patient"]

    if filters["sex"]:
        conditions.append("p.sex = %(sex)s")
        params["sex"] = filters["sex"]

    if filters["city"] and schema["patient_city"]:
        conditions.append(f"p.`{schema['patient_city']}` = %(city)s")
        params["city"] = filters["city"]

    # Age groups → SQL OR list on TIMESTAMPDIFF(MONTH, ...).
    if filters["age_groups"]:
        conditions.append("p.dob IS NOT NULL")
        clauses = []
        for i, label in enumerate(filters["age_groups"]):
            lo, hi = AGE_GROUPS[label]
            lo_key = f"agelo_{i}"
            params[lo_key] = lo
            if hi is None:
                clauses.append(
                    f"TIMESTAMPDIFF(MONTH, p.dob, %(today_for_age)s) >= %({lo_key})s"
                )
            else:
                hi_key = f"agehi_{i}"
                params[hi_key] = hi
                clauses.append(
                    f"TIMESTAMPDIFF(MONTH, p.dob, %(today_for_age)s) "
                    f"BETWEEN %({lo_key})s AND %({hi_key})s"
                )
        conditions.append("(" + " OR ".join(clauses) + ")")

    use_child = bool(schema["has_diagnosis_child_table"])
    if use_child:
        diagnosis_join = (
            "LEFT JOIN `tabPatient Encounter Diagnosis` ped "
            "  ON ped.parent = pe.name "
            "  AND ped.parenttype = 'Patient Encounter'"
        )
        diagnosis_label_expr = "ped.diagnosis"
        ped_meta = frappe.get_meta("Patient Encounter Diagnosis")
        if ped_meta.has_field("medical_code"):
            diagnosis_code_expr = "ped.medical_code"
        elif ped_meta.has_field("code"):
            diagnosis_code_expr = "ped.code"
        else:
            diagnosis_code_expr = "''"
    else:
        diagnosis_join = ""
        diagnosis_label_expr = "pe.diagnosis"
        diagnosis_code_expr = "''"

    if filters["diagnosis"]:
        conditions.append(f"{diagnosis_label_expr} = %(diagnosis)s")
        params["diagnosis"] = filters["diagnosis"]
    elif not filters["include_no_diagnosis"]:
        conditions.append(
            f"{diagnosis_label_expr} IS NOT NULL AND {diagnosis_label_expr} != ''"
        )

    where_clause = " AND ".join(conditions)

    query = f"""
        SELECT
            pe.name                               AS encounter,
            pe.encounter_date                     AS encounter_date,
            pe.patient                            AS patient,
            p.patient_name                        AS patient_name,
            p.dob                                 AS dob,
            CASE
                WHEN p.dob IS NULL THEN NULL
                ELSE TIMESTAMPDIFF(MONTH, p.dob, %(today_for_age)s)
            END                                   AS age_months,
            p.sex                                 AS sex,
            {city_select}                         AS city,
            pe.practitioner                       AS practitioner,
            pe.practitioner_name                  AS practitioner_name,
            COALESCE({diagnosis_label_expr}, '')  AS diagnosis,
            COALESCE({diagnosis_code_expr}, '')   AS diagnosis_code,
            {department_select}                   AS department,
            COALESCE({enc_type_select}, '')       AS encounter_type,
            COALESCE({remarks_select}, '')        AS remarks
        FROM `tabPatient Encounter` pe
        INNER JOIN `tabPatient` p ON p.name = pe.patient
        {diagnosis_join}
        WHERE {where_clause}
        ORDER BY pe.encounter_date DESC, pe.name DESC
    """
    rows = frappe.db.sql(query, params, as_dict=True)

    # Human-friendly age display ("9 mo", "3 yr", "47 yr").
    for r in rows:
        m = r.get("age_months")
        if m is None:
            r["age_display"] = ""
        elif m < 12:
            r["age_display"] = f"{cint(m)} mo"
        else:
            r["age_display"] = f"{cint(m // 12)} yr"

    return rows


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------

def _aggregate(rows: List[Dict[str, Any]], group_by: str) -> Dict[str, Any]:
    unique_patients = set()
    unique_encounters = set()
    diagnosis_count = 0

    by_diagnosis_encounters: Dict[str, set] = {}
    by_diagnosis_patients: Dict[str, set] = {}
    by_age_group: "OrderedDict[str, int]" = OrderedDict((k, 0) for k in AGE_GROUP_LABELS)
    age_group_seen_patients: Dict[str, set] = {k: set() for k in AGE_GROUP_LABELS}
    by_city: Dict[str, set] = {}
    by_period_encounters: Dict[str, set] = {}
    by_period_patients: Dict[str, set] = {}

    by_practitioner_patients: Dict[str, set] = {}
    by_practitioner_encounters: Dict[str, set] = {}
    practitioner_display_name: Dict[str, str] = {}

    for r in rows:
        patient = r.get("patient")
        enc = r.get("encounter")
        if patient:
            unique_patients.add(patient)
        if enc:
            unique_encounters.add(enc)
        if r.get("diagnosis"):
            diagnosis_count += 1
            by_diagnosis_encounters.setdefault(r["diagnosis"], set()).add(enc)
            by_diagnosis_patients.setdefault(r["diagnosis"], set()).add(patient)

        bucket = _bucket_for_months(r.get("age_months"))
        if bucket and patient and patient not in age_group_seen_patients[bucket]:
            age_group_seen_patients[bucket].add(patient)
            by_age_group[bucket] += 1

        if r.get("city"):
            by_city.setdefault(r["city"], set()).add(patient)

        period = _period_key(r.get("encounter_date"), group_by)
        if period:
            by_period_encounters.setdefault(period, set()).add(enc)
            by_period_patients.setdefault(period, set()).add(patient)

        prac = r.get("practitioner")
        if prac:
            by_practitioner_patients.setdefault(prac, set()).add(patient)
            by_practitioner_encounters.setdefault(prac, set()).add(enc)
            if prac not in practitioner_display_name:
                practitioner_display_name[prac] = (
                    r.get("practitioner_name") or prac
                )

    # Sex distribution: per unique patient.
    sex_by_patient: Dict[str, set] = {}
    for r in rows:
        sex_by_patient.setdefault(r.get("sex") or _("Unknown"), set()).add(r.get("patient"))
    by_sex_unique = {k: len(v) for k, v in sex_by_patient.items()}

    return {
        "unique_patients": len(unique_patients),
        "unique_encounters": len(unique_encounters),
        "diagnosis_count": diagnosis_count,
        "by_diagnosis_encounters": {k: len(v) for k, v in by_diagnosis_encounters.items()},
        "by_diagnosis_patients": {k: len(v) for k, v in by_diagnosis_patients.items()},
        "by_sex_unique": by_sex_unique,
        "by_age_group": dict(by_age_group),
        "by_city": {k: len(v) for k, v in by_city.items()},
        "by_period_encounters": {k: len(v) for k, v in by_period_encounters.items()},
        "by_period_patients": {k: len(v) for k, v in by_period_patients.items()},
        "by_practitioner_patients": {k: len(v) for k, v in by_practitioner_patients.items()},
        "by_practitioner_encounters": {k: len(v) for k, v in by_practitioner_encounters.items()},
        "practitioner_display_name": practitioner_display_name,
    }


def _period_key(d, group_by: str) -> str:
    if not d:
        return ""
    d = getdate(d)
    if group_by == "Day":
        return d.strftime("%Y-%m-%d")
    if group_by == "Year":
        return d.strftime("%Y")
    return d.strftime("%Y-%m")


def _sorted_period_keys(keys: List[str]) -> List[str]:
    return sorted(keys)


# ---------------------------------------------------------------------------
# Native chart slot
# ---------------------------------------------------------------------------

def get_chart(filters: Dict[str, Any], rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not rows:
        return None

    agg = _aggregate(rows, filters["group_by"])
    labels = _sorted_period_keys(list(agg["by_period_patients"].keys()))
    if not labels:
        return None

    patient_series = [agg["by_period_patients"].get(l, 0) for l in labels]
    encounter_series = [agg["by_period_encounters"].get(l, 0) for l in labels]

    return {
        "data": {
            "labels": labels,
            "datasets": [
                {"name": _("Unique Patients"), "values": patient_series},
                {"name": _("Encounters"), "values": encounter_series},
            ],
        },
        "type": "line",
        "fieldtype": "Int",
        "colors": ["#5e64ff", "#7cd6fd"],
        "lineOptions": {"regionFill": 1, "hideDots": 0},
        "axisOptions": {"xIsSeries": 1},
    }


# ---------------------------------------------------------------------------
# Summary cards
# ---------------------------------------------------------------------------

def get_report_summary(filters: Dict[str, Any], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    agg = _aggregate(rows, filters["group_by"])

    summary = [
        {"label": _("Outpatient Patients"), "value": agg["unique_patients"],
         "datatype": "Int", "indicator": "Blue"},
        {"label": _("Encounters"), "value": agg["unique_encounters"],
         "datatype": "Int", "indicator": "Green"},
        {"label": _("Diagnosis Entries"), "value": agg["diagnosis_count"],
         "datatype": "Int", "indicator": "Orange"},
    ]

    if filters["diagnosis"]:
        sel_patients = agg["by_diagnosis_patients"].get(filters["diagnosis"], 0)
        summary.append({
            "label": _("Patients with “{0}”").format(filters["diagnosis"]),
            "value": sel_patients,
            "datatype": "Int",
            "indicator": "Red",
        })

    return summary


# ---------------------------------------------------------------------------
# HTML message dashboard
# ---------------------------------------------------------------------------

def get_message_html(filters: Dict[str, Any], rows: List[Dict[str, Any]],
                     schema: Dict[str, Optional[str]]) -> str:
    if not rows:
        return ""

    agg = _aggregate(rows, filters["group_by"])

    # Top 10 diagnoses
    top_diag = sorted(
        agg["by_diagnosis_encounters"].items(),
        key=lambda kv: kv[1], reverse=True,
    )[:10]
    total_diag = max(agg["diagnosis_count"], 1)
    top_diag_rows_html = ""
    for name, enc_count in top_diag:
        pat_count = agg["by_diagnosis_patients"].get(name, 0)
        pct = (enc_count / total_diag) * 100
        top_diag_rows_html += (
            f"<tr>"
            f"<td>{escape_html(name)}</td>"
            f"<td class='text-right'>{pat_count}</td>"
            f"<td class='text-right'>{enc_count}</td>"
            f"<td class='text-right'>{pct:.1f}%</td>"
            f"</tr>"
        )
    if not top_diag_rows_html:
        top_diag_rows_html = "<tr><td colspan='4' class='text-muted text-center'>—</td></tr>"

    # Top cities
    top_cities = sorted(agg["by_city"].items(), key=lambda kv: kv[1], reverse=True)[:5]

    # Top doctors (replaces the old districts card)
    top_doctors = sorted(
        agg["by_practitioner_patients"].items(),
        key=lambda kv: (kv[1], agg["by_practitioner_encounters"].get(kv[0], 0)),
        reverse=True,
    )[:8]

    def list_html(pairs, empty_label):
        if not pairs:
            return f"<div class='text-muted'>{empty_label}</div>"
        items = "".join(
            f"<li><span>{escape_html(k)}</span>"
            f"<span class='text-muted'>{v}</span></li>"
            for k, v in pairs
        )
        return f"<ul class='ppd-list'>{items}</ul>"

    cities_html = list_html(top_cities, _("No city data."))

    if top_doctors:
        doctor_items = ""
        for prac_id, pat_count in top_doctors:
            display = agg["practitioner_display_name"].get(prac_id, prac_id)
            enc_count = agg["by_practitioner_encounters"].get(prac_id, 0)
            doctor_items += (
                f"<li>"
                f"<span class='ppd-doc-name'>{escape_html(display)}</span>"
                f"<span class='ppd-doc-stats'>"
                f"<b>{pat_count}</b> {_('patients')} · "
                f"<b>{enc_count}</b> {_('visits')}"
                f"</span>"
                f"</li>"
            )
        doctors_html = f"<ul class='ppd-list ppd-list-doctors'>{doctor_items}</ul>"
    else:
        doctors_html = f"<div class='text-muted'>{_('No doctor data.')}</div>"

    sex_pairs = sorted(agg["by_sex_unique"].items(), key=lambda kv: kv[1], reverse=True)
    age_pairs = list(agg["by_age_group"].items())  # preserve order

    # Doctors chart payload (top 10)
    doc_chart = sorted(
        agg["by_practitioner_patients"].items(),
        key=lambda kv: kv[1], reverse=True,
    )[:10]
    doc_chart_labels = [agg["practitioner_display_name"].get(k, k) for k, _v in doc_chart]
    doc_chart_values = [v for _k, v in doc_chart]

    payload = {
        "sex_labels": [k for k, _v in sex_pairs],
        "sex_values": [v for _k, v in sex_pairs],
        "age_labels": [k for k, _v in age_pairs],
        "age_values": [v for _k, v in age_pairs],
        "diag_trend_labels": _sorted_period_keys(list(agg["by_period_encounters"].keys())),
        "diag_trend_values_patients": [
            agg["by_period_patients"].get(k, 0)
            for k in _sorted_period_keys(list(agg["by_period_patients"].keys()))
        ],
        "top_diag_labels": [d[0] for d in top_diag],
        "top_diag_values": [d[1] for d in top_diag],
        "doc_chart_labels": doc_chart_labels,
        "doc_chart_values": doc_chart_values,
        "selected_diagnosis": filters["diagnosis"] or "",
        "group_by": filters["group_by"],
    }

    selected_block = ""
    if filters["diagnosis"]:
        sel_patients = agg["by_diagnosis_patients"].get(filters["diagnosis"], 0)
        sel_encounters = agg["by_diagnosis_encounters"].get(filters["diagnosis"], 0)
        selected_block = (
            f"<div class='ppd-selected'>"
            f"<div class='ppd-selected-title'>"
            f"  {_('Selected Diagnosis')}: <b>{escape_html(filters['diagnosis'])}</b>"
            f"</div>"
            f"<div class='ppd-selected-stats'>"
            f"  <span><b>{sel_patients}</b> {_('unique patients')}</span>"
            f"  <span><b>{sel_encounters}</b> {_('encounters')}</span>"
            f"</div>"
            f"</div>"
        )

    payload_json = json.dumps(payload)

    html = f"""
<style>
.ppd-dashboard {{ margin: 0 0 12px 0; padding: 0; }}
.ppd-grid {{
    display: grid; grid-template-columns: repeat(12, 1fr); grid-gap: 12px;
}}
.ppd-card {{
    background: var(--card-bg, #fff);
    border: 1px solid var(--border-color, #ebeef0);
    border-radius: 8px; padding: 14px 16px;
    box-shadow: 0 1px 2px rgba(0,0,0,.03);
}}
.ppd-card h6 {{
    margin: 0 0 10px; font-size: 12px; font-weight: 600;
    text-transform: uppercase; letter-spacing: .04em;
    color: var(--text-muted, #74808b);
}}
.ppd-col-4 {{ grid-column: span 4; }}
.ppd-col-6 {{ grid-column: span 6; }}
.ppd-col-8 {{ grid-column: span 8; }}
.ppd-col-12 {{ grid-column: span 12; }}
@media (max-width: 992px) {{
    .ppd-col-4, .ppd-col-6, .ppd-col-8 {{ grid-column: span 12; }}
}}
.ppd-list {{ list-style: none; margin: 0; padding: 0; }}
.ppd-list li {{
    display: flex; justify-content: space-between; align-items: center;
    padding: 6px 0; border-bottom: 1px dashed var(--border-color, #ebeef0);
    font-size: 13px;
}}
.ppd-list li:last-child {{ border-bottom: none; }}
.ppd-list-doctors li {{ flex-direction: column; align-items: flex-start; gap: 2px; }}
.ppd-doc-name {{ font-weight: 600; }}
.ppd-doc-stats {{ font-size: 12px; color: var(--text-muted, #74808b); }}
.ppd-table {{ width: 100%; font-size: 13px; border-collapse: collapse; }}
.ppd-table th, .ppd-table td {{
    padding: 6px 8px; border-bottom: 1px solid var(--border-color, #ebeef0);
    text-align: left;
}}
.ppd-table th {{
    font-size: 11px; text-transform: uppercase; letter-spacing: .04em;
    color: var(--text-muted, #74808b); font-weight: 600;
}}
.ppd-table .text-right {{ text-align: right; }}
.ppd-selected {{
    margin-bottom: 12px; padding: 10px 14px;
    background: var(--bg-yellow, #fff8e1);
    border: 1px solid #ffe082; border-radius: 8px;
}}
.ppd-selected-title {{ font-size: 13px; }}
.ppd-selected-stats {{ margin-top: 4px; font-size: 13px; }}
.ppd-selected-stats span {{ margin-right: 16px; }}
.ppd-chart {{ min-height: 220px; }}
</style>

<div class="ppd-dashboard">
    {selected_block}
    <div class="ppd-grid">
        <div class="ppd-card ppd-col-8">
            <h6>{_('Top Diagnoses')}</h6>
            <div id="ppd-chart-top-diag" class="ppd-chart"></div>
        </div>
        <div class="ppd-card ppd-col-4">
            <h6>{_('Sex Distribution (unique patients)')}</h6>
            <div id="ppd-chart-sex" class="ppd-chart"></div>
        </div>

        <div class="ppd-card ppd-col-6">
            <h6>{_('Age Group Distribution')}</h6>
            <div id="ppd-chart-age" class="ppd-chart"></div>
        </div>
        <div class="ppd-card ppd-col-6">
            <h6>{_('Diagnosis Trend (by {0})').format(filters['group_by'])}</h6>
            <div id="ppd-chart-diag-trend" class="ppd-chart"></div>
        </div>

        <div class="ppd-card ppd-col-4">
            <h6>{_('Top Diagnoses Ranking')}</h6>
            <table class="ppd-table">
                <thead>
                    <tr>
                        <th>{_('Diagnosis')}</th>
                        <th class="text-right">{_('Patients')}</th>
                        <th class="text-right">{_('Enc.')}</th>
                        <th class="text-right">%</th>
                    </tr>
                </thead>
                <tbody>{top_diag_rows_html}</tbody>
            </table>
        </div>
        <div class="ppd-card ppd-col-4">
            <h6>{_('Top Cities')}</h6>
            {cities_html}
        </div>
        <div class="ppd-card ppd-col-4">
            <h6>{_('Top Doctors by Patient Visits')}</h6>
            {doctors_html}
        </div>

        <div class="ppd-card ppd-col-12">
            <h6>{_('Doctors — Unique Patients (Top 10)')}</h6>
            <div id="ppd-chart-doctors" class="ppd-chart"></div>
        </div>
    </div>
</div>

<script>
(function () {{
    var payload = {payload_json};
    function render() {{
        if (!window.frappe || !frappe.Chart) {{
            return setTimeout(render, 200);
        }}
        try {{
            if (document.getElementById('ppd-chart-top-diag') && payload.top_diag_labels.length) {{
                new frappe.Chart('#ppd-chart-top-diag', {{
                    data: {{ labels: payload.top_diag_labels,
                            datasets: [{{ name: '{_('Encounters')}', values: payload.top_diag_values }}] }},
                    type: 'bar', height: 240, colors: ['#5e64ff'],
                    axisOptions: {{ xAxisMode: 'tick' }}
                }});
            }}
            if (document.getElementById('ppd-chart-sex') && payload.sex_labels.length) {{
                new frappe.Chart('#ppd-chart-sex', {{
                    data: {{ labels: payload.sex_labels,
                            datasets: [{{ values: payload.sex_values }}] }},
                    type: 'donut', height: 240,
                    colors: ['#7cd6fd', '#ff7a90', '#ffa00a', '#9aa6b3']
                }});
            }}
            if (document.getElementById('ppd-chart-age')) {{
                new frappe.Chart('#ppd-chart-age', {{
                    data: {{ labels: payload.age_labels,
                            datasets: [{{ name: '{_('Patients')}', values: payload.age_values }}] }},
                    type: 'bar', height: 240, colors: ['#28a745']
                }});
            }}
            if (document.getElementById('ppd-chart-diag-trend') && payload.diag_trend_labels.length) {{
                new frappe.Chart('#ppd-chart-diag-trend', {{
                    data: {{ labels: payload.diag_trend_labels,
                            datasets: [{{ name: '{_('Patients')}', values: payload.diag_trend_values_patients }}] }},
                    type: 'line', height: 240, colors: ['#ff5858'],
                    lineOptions: {{ regionFill: 1 }}
                }});
            }}
            if (document.getElementById('ppd-chart-doctors') && payload.doc_chart_labels.length) {{
                new frappe.Chart('#ppd-chart-doctors', {{
                    data: {{ labels: payload.doc_chart_labels,
                            datasets: [{{ name: '{_('Unique Patients')}', values: payload.doc_chart_values }}] }},
                    type: 'bar', height: 260, colors: ['#1f8de0'],
                    axisOptions: {{ xAxisMode: 'tick' }}
                }});
            }}
        }} catch (e) {{
            console && console.warn && console.warn('OutpatientDashboard render error:', e);
        }}
    }}
    render();
}})();
</script>
"""
    return html


# ---------------------------------------------------------------------------
# AI Summary (single patient, OpenAI Responses API)
# ---------------------------------------------------------------------------
# Reuses the same OpenAI key resolution and Responses API call shape as
# patient_patch.api.clinical_intel_ai. The prompt is constrained so the
# output is short and clinically useful.

def _get_openai_api_key() -> str:
    try:
        hs = frappe.get_cached_doc("Healthcare Settings")
        if hs and hs.meta.has_field("custom_openai_api_key"):
            key = hs.get_password("custom_openai_api_key")
            if key:
                return key
    except Exception:
        pass

    key = frappe.conf.get("openai_api_key")
    if key:
        return key

    return os.environ.get("OPENAI_API_KEY", "")


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


def _build_patient_period_context(patient: str, from_date, to_date) -> str:
    """Compact, clinically relevant snapshot of one patient's
    activity in [from_date, to_date]."""

    pat = frappe.get_doc("Patient", patient)
    parts: List[str] = []
    parts.append(f"Patient: {pat.patient_name or patient}")
    if pat.get("sex"):
        parts.append(f"Sex: {pat.sex}")
    if pat.get("dob"):
        d = getdate(pat.dob)
        t = getdate(today())
        years = t.year - d.year - ((t.month, t.day) < (d.month, d.day))
        parts.append(f"Age: {years}")

    parts.append(f"Period: {from_date} to {to_date}")

    encounters = frappe.get_all(
        "Patient Encounter",
        filters={
            "patient": patient,
            "docstatus": 1,
            "encounter_date": ["between", [from_date, to_date]],
            "inpatient_record": ["in", ["", None]],
        },
        fields=["name", "encounter_date", "practitioner", "practitioner_name"],
        order_by="encounter_date asc",
    )

    parts.append(f"Total Outpatient Encounters in period: {len(encounters)}")

    for enc_row in encounters:
        try:
            enc = frappe.get_doc("Patient Encounter", enc_row["name"])
        except Exception:
            continue

        header = (
            f"\n[Encounter {enc.name} | {enc_row['encounter_date']} | "
            f"Dr. {enc_row.get('practitioner_name') or enc_row.get('practitioner') or ''}]"
        )
        parts.append(header)

        cc = _strip_html(enc.get("custom_chief_complaint") or "")
        if cc:
            parts.append(f"Chief Complaint: {cc[:300]}")

        ex = _strip_html(enc.get("custom_physical_examination") or
                         enc.get("physical_examination") or "")
        if ex:
            parts.append(f"Examination: {ex[:300]}")

        # Diagnosis (child table preferred, fallback to free-text)
        diag_lines = []
        diag_field = enc.get("diagnosis")
        if isinstance(diag_field, list):
            for dx in diag_field:
                d = (dx.get("diagnosis") if hasattr(dx, "get") else None) or ""
                if d:
                    diag_lines.append(d)
        elif isinstance(diag_field, str) and diag_field.strip():
            diag_lines.append(diag_field.strip())
        if diag_lines:
            parts.append("Diagnosis: " + "; ".join(diag_lines[:8]))

        # Drugs
        drug_lines = []
        for drug in (enc.get("drug_prescription") or []):
            name = drug.get("drug_name") or drug.get("drug_code") or ""
            dosage = drug.get("dosage") or ""
            period = drug.get("period") or ""
            if name:
                drug_lines.append(
                    " ".join([str(x) for x in [name, dosage, period] if x]).strip()
                )
        if drug_lines:
            parts.append("Prescription: " + "; ".join(drug_lines[:8]))

        # Lab tests prescribed
        lab_lines = []
        for lab in (enc.get("lab_test_prescription") or []):
            code = lab.get("lab_test_code") or lab.get("lab_test_name") or ""
            if code:
                lab_lines.append(str(code))
        if lab_lines:
            parts.append("Labs ordered: " + "; ".join(lab_lines[:8]))

        # Imaging / radiology
        rad_lines = []
        for rad in (enc.get("radiology_procedure_prescription") or []):
            code = rad.get("radiology_examination_template") or rad.get("radiology_procedure_name") or ""
            if code:
                rad_lines.append(str(code))
        if rad_lines:
            parts.append("Imaging ordered: " + "; ".join(rad_lines[:8]))

    # Lab Test results in window
    try:
        lab_tests = frappe.get_all(
            "Lab Test",
            filters={
                "patient": patient,
                "docstatus": ["<", 2],
                "result_date": ["between", [from_date, to_date]],
            },
            fields=["name", "template", "result_date", "status"],
            order_by="result_date asc",
            limit=20,
        )
    except Exception:
        lab_tests = []
    if lab_tests:
        parts.append("\nLab Test Results in period:")
        for lab_row in lab_tests:
            try:
                lab = frappe.get_doc("Lab Test", lab_row["name"])
                items = []
                for it in (lab.get("normal_test_items") or []):
                    nm = it.get("lab_test_name") or ""
                    val = it.get("result_value") or ""
                    uom = it.get("lab_test_uom") or ""
                    if nm and val:
                        items.append(f"{nm}={val}{(' ' + uom) if uom else ''}")
                if items:
                    parts.append(f"- [{lab.name} {lab_row['result_date']}] " + ", ".join(items[:8]))
            except Exception:
                continue

    return "\n".join(parts)


@frappe.whitelist()
def generate_patient_period_summary(patient: str,
                                    from_date: str,
                                    to_date: str) -> Dict[str, Any]:
    """Short AI summary of one patient's outpatient activity in [from, to]."""
    if not patient or not from_date or not to_date:
        return {"success": False,
                "message": _("Patient, From Date and To Date are required."),
                "summary": ""}

    api_key = _get_openai_api_key()
    if not api_key:
        return {"success": False,
                "message": _("OpenAI API key is not configured. Add it under Healthcare Settings."),
                "summary": ""}

    if not requests:
        return {"success": False,
                "message": _("Python `requests` package not available on the server."),
                "summary": ""}

    try:
        from_d = getdate(from_date)
        to_d = getdate(to_date)
        context = _build_patient_period_context(patient, from_d, to_d)
    except Exception as exc:
        return {"success": False, "message": str(exc), "summary": ""}

    prompt = f"""You are a senior physician writing a brief clinical summary for a colleague.

Summarise the following patient's OUTPATIENT activity for the period.
Be very concise. Maximum 8 short bullet points covering, in this order, only what is supported by the data:
- visits & doctors seen
- main complaints / symptoms
- key examination findings
- diagnoses
- prescriptions given
- labs / investigations ordered and any notable results
- overall trajectory in the period

Rules:
- Strict: do not invent anything.
- Do not mention AI.
- Use plain text bullets starting with "• ".
- No headings, no preamble, no closing remarks.
- If a category has no data, skip it entirely.

Patient & period data:
{context}
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
                    "max_output_tokens": 600,
                    "reasoning": {"effort": "minimal"},
                    "text": {"verbosity": "low"},
                },
                timeout=60,
            )

            if response.status_code == 429:
                last_error = f"429: {response.text[:200]}"
                time.sleep(2 * (attempt + 1))
                continue

            if response.status_code >= 400:
                last_error = f"{response.status_code}: {response.text[:300]}"
                break

            data = response.json()
            text = _extract_output_text(data)
            if text:
                return {"success": True, "message": "", "summary": text}

            if data.get("status") == "incomplete":
                last_error = f"Incomplete: {data.get('incomplete_details')}"
            else:
                last_error = "No output text in response."
            continue
        except Exception as exc:
            last_error = str(exc)
            time.sleep(2 * (attempt + 1))

    frappe.log_error(last_error or "Unknown OpenAI error",
                     "Outpatient Diagnosis Report AI Summary Error")
    return {"success": False,
            "message": _("Could not generate summary now. Please try again."),
            "summary": ""}


# ---------------------------------------------------------------------------
# Printable A4 landscape report
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_print_html(filters: Optional[str] = None) -> str:
    """Return a complete standalone HTML document ready for printing.

    The JS opens this in a new window and triggers `window.print()`. CSS is
    set up for A4 landscape with print-friendly styling.
    """

    if isinstance(filters, str):
        try:
            filters = json.loads(filters)
        except Exception:
            filters = {}
    filters = filters or {}

    f = _normalise_filters(filters)
    schema = _resolve_schema()
    rows = get_data(f, schema)

    if not rows:
        return _build_print_html_empty(f)

    agg = _aggregate(rows, f["group_by"])
    return _build_print_html(f, rows, agg, schema)


def _site_brand() -> Dict[str, str]:
    company = ""
    try:
        company = frappe.defaults.get_global_default("company") or ""
    except Exception:
        pass
    return {"company": company or "Hospital"}


def _build_print_html_empty(f: Dict[str, Any]) -> str:
    brand = _site_brand()
    return f"""<!doctype html><html><head>
<meta charset="utf-8"><title>Outpatient Diagnosis History</title>
<style>body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;padding:40px;color:#333}}</style>
</head><body>
<h2>{escape_html(brand['company'])}</h2>
<h3>Outpatient Patient Diagnosis History</h3>
<p>From {f['from_date']} to {f['to_date']}</p>
<p style="color:#888">No data found for the selected filters.</p>
</body></html>"""


def _filters_summary(f: Dict[str, Any]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = [("Period", f"{f['from_date']} → {f['to_date']}")]
    if f.get("practitioner"):
        out.append(("Practitioner", f["practitioner"]))
    if f.get("patient"):
        out.append(("Patient", f["patient"]))
    if f.get("diagnosis"):
        out.append(("Diagnosis", f["diagnosis"]))
    if f.get("city"):
        # `city` IS the Address ID (matches Patient.custom_city). Show the
        # human-readable city alongside it for clarity in print.
        if f.get("city_display") and f["city_display"] != f["city"]:
            out.append(("City", f"{f['city_display']}  ({f['city']})"))
        else:
            out.append(("City", f["city"]))
    if f.get("sex"):
        out.append(("Sex", f["sex"]))
    if f.get("age_groups"):
        out.append(("Age Groups", ", ".join(f["age_groups"])))
    out.append(("Group By", f["group_by"]))
    return out


def _build_print_html(f: Dict[str, Any], rows: List[Dict[str, Any]],
                      agg: Dict[str, Any], schema: Dict[str, Optional[str]]) -> str:
    brand = _site_brand()

    # KPIs
    kpi_cards = [
        ("Outpatient Patients", agg["unique_patients"], "#1f8de0"),
        ("Encounters", agg["unique_encounters"], "#28a745"),
        ("Diagnosis Entries", agg["diagnosis_count"], "#e67e22"),
    ]
    if f.get("diagnosis"):
        kpi_cards.append((
            f"Patients with “{f['diagnosis']}”",
            agg["by_diagnosis_patients"].get(f["diagnosis"], 0),
            "#c0392b",
        ))

    kpis_html = "".join(
        f"<div class='kpi' style='border-top:3px solid {color}'>"
        f"<div class='kpi-label'>{escape_html(label)}</div>"
        f"<div class='kpi-value'>{value}</div></div>"
        for label, value, color in kpi_cards
    )

    # Top 10 diagnoses
    top_diag = sorted(agg["by_diagnosis_encounters"].items(),
                      key=lambda kv: kv[1], reverse=True)[:10]
    total_diag = max(agg["diagnosis_count"], 1)
    if top_diag:
        diag_rows = "".join(
            f"<tr><td>{i+1}</td>"
            f"<td>{escape_html(name)}</td>"
            f"<td class='r'>{agg['by_diagnosis_patients'].get(name, 0)}</td>"
            f"<td class='r'>{enc}</td>"
            f"<td class='r'>{(enc/total_diag)*100:.1f}%</td></tr>"
            for i, (name, enc) in enumerate(top_diag)
        )
        diag_table = f"""
        <table class='tbl'>
            <thead><tr><th>#</th><th>Diagnosis</th><th class='r'>Patients</th>
            <th class='r'>Enc.</th><th class='r'>%</th></tr></thead>
            <tbody>{diag_rows}</tbody>
        </table>"""
    else:
        diag_table = "<div class='muted'>No diagnoses in period.</div>"

    # Top doctors
    top_doctors = sorted(
        agg["by_practitioner_patients"].items(),
        key=lambda kv: (kv[1], agg["by_practitioner_encounters"].get(kv[0], 0)),
        reverse=True,
    )[:10]
    if top_doctors:
        doc_rows = "".join(
            f"<tr><td>{i+1}</td>"
            f"<td>{escape_html(agg['practitioner_display_name'].get(prac, prac))}</td>"
            f"<td class='r'>{patients}</td>"
            f"<td class='r'>{agg['by_practitioner_encounters'].get(prac, 0)}</td></tr>"
            for i, (prac, patients) in enumerate(top_doctors)
        )
        doctors_table = f"""
        <table class='tbl'>
            <thead><tr><th>#</th><th>Doctor</th>
            <th class='r'>Patients</th><th class='r'>Visits</th></tr></thead>
            <tbody>{doc_rows}</tbody>
        </table>"""
    else:
        doctors_table = "<div class='muted'>No doctor data.</div>"

    # Age groups
    age_rows = "".join(
        f"<tr><td>{escape_html(label)}</td><td class='r'>{count}</td></tr>"
        for label, count in agg["by_age_group"].items()
    )
    age_table = f"""
    <table class='tbl'>
        <thead><tr><th>Age Group</th><th class='r'>Patients</th></tr></thead>
        <tbody>{age_rows}</tbody>
    </table>"""

    # Sex
    sex_rows = "".join(
        f"<tr><td>{escape_html(k)}</td><td class='r'>{v}</td></tr>"
        for k, v in sorted(agg["by_sex_unique"].items(), key=lambda kv: kv[1], reverse=True)
    )
    sex_table = f"""
    <table class='tbl'>
        <thead><tr><th>Sex</th><th class='r'>Patients</th></tr></thead>
        <tbody>{sex_rows or "<tr><td colspan='2' class='muted'>—</td></tr>"}</tbody>
    </table>"""

    # Detail rows
    detail_rows_html = "".join(
        f"<tr>"
        f"<td>{escape_html(cstr(r.get('encounter_date') or ''))}</td>"
        f"<td>{escape_html(cstr(r.get('encounter') or ''))}</td>"
        f"<td>{escape_html(cstr(r.get('patient_name') or r.get('patient') or ''))}</td>"
        f"<td>{escape_html(cstr(r.get('age_display') or ''))}</td>"
        f"<td>{escape_html(cstr(r.get('sex') or ''))}</td>"
        f"<td>{escape_html(cstr(r.get('city') or ''))}</td>"
        f"<td>{escape_html(cstr(r.get('practitioner_name') or r.get('practitioner') or ''))}</td>"
        f"<td>{escape_html(cstr(r.get('diagnosis') or ''))}</td>"
        f"<td>{escape_html(cstr(r.get('diagnosis_code') or ''))}</td>"
        f"<td>{escape_html(cstr(r.get('encounter_type') or ''))}</td>"
        f"</tr>"
        for r in rows
    )

    filter_chips_html = "".join(
        f"<span class='chip'><b>{escape_html(label)}:</b> {escape_html(cstr(value))}</span>"
        for label, value in _filters_summary(f)
    )

    generated = frappe.utils.now_datetime().strftime("%Y-%m-%d %H:%M")
    user = frappe.session.user

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Outpatient Patient Diagnosis History — {f['from_date']} to {f['to_date']}</title>
<style>
  @page {{ size: A4 landscape; margin: 12mm 10mm 14mm 10mm; }}

  *, *::before, *::after {{ box-sizing: border-box; }}
  html, body {{
      margin: 0; padding: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                   "Helvetica Neue", Arial, sans-serif;
      color: #1f2933; background: #fff;
      font-size: 11px; line-height: 1.45;
  }}
  .page {{ padding: 0 6mm; }}

  /* HEADER */
  .doc-header {{
      display: flex; justify-content: space-between; align-items: flex-end;
      border-bottom: 3px solid #2c3e50; padding: 8px 0 10px; margin-bottom: 10px;
  }}
  .doc-header .brand {{ display: flex; align-items: center; gap: 12px; }}
  .doc-header .brand .logo {{
      width: 44px; height: 44px; border-radius: 50%;
      background: linear-gradient(135deg, #2c3e50, #1f8de0);
      color: #fff; display: flex; align-items: center; justify-content: center;
      font-size: 22px; font-weight: 700;
  }}
  .doc-header .brand h1 {{ margin: 0; font-size: 14px; color: #2c3e50; font-weight: 700; }}
  .doc-header .brand h2 {{ margin: 0; font-size: 11px; color: #607080; font-weight: 500; }}
  .doc-header .meta {{ text-align: right; font-size: 10px; color: #607080; }}
  .doc-header .meta .title {{
      font-size: 13px; color: #2c3e50; font-weight: 700; margin-bottom: 2px;
  }}

  /* FILTER CHIPS */
  .chips {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }}
  .chip {{
      background: #f1f5f9; border: 1px solid #e2e8f0;
      border-radius: 999px; padding: 3px 10px; font-size: 10px; color: #334155;
  }}
  .chip b {{ color: #1f2933; font-weight: 600; }}

  /* KPIs */
  .kpis {{
      display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px;
      margin-bottom: 12px;
  }}
  .kpi {{
      background: #fff; border: 1px solid #e2e8f0; border-radius: 8px;
      padding: 10px 12px; box-shadow: 0 1px 2px rgba(0,0,0,.03);
  }}
  .kpi-label {{
      font-size: 9px; text-transform: uppercase; letter-spacing: .06em;
      color: #607080; font-weight: 600;
  }}
  .kpi-value {{ font-size: 22px; font-weight: 700; color: #1f2933; margin-top: 2px; }}

  /* SECTIONS */
  .grid-3 {{ display: grid; grid-template-columns: 2fr 2fr 1fr 1fr; gap: 10px; }}
  .panel {{
      background: #fff; border: 1px solid #e2e8f0; border-radius: 8px;
      padding: 8px 10px;
  }}
  .panel h3 {{
      margin: 0 0 6px; font-size: 10px; text-transform: uppercase;
      letter-spacing: .06em; color: #1f8de0; font-weight: 700;
      border-bottom: 1px solid #e2e8f0; padding-bottom: 4px;
  }}

  .tbl {{ width: 100%; border-collapse: collapse; font-size: 10px; }}
  .tbl th, .tbl td {{
      padding: 4px 6px; border-bottom: 1px solid #eef2f7; text-align: left;
      vertical-align: top;
  }}
  .tbl th {{
      font-size: 9px; text-transform: uppercase; letter-spacing: .04em;
      color: #607080; font-weight: 600; background: #f8fafc;
  }}
  .tbl td.r, .tbl th.r {{ text-align: right; white-space: nowrap; }}

  /* DETAIL TABLE */
  .detail-section {{ margin-top: 12px; }}
  .detail-section h3 {{
      margin: 0 0 6px; font-size: 11px; color: #2c3e50; font-weight: 700;
      border-bottom: 2px solid #2c3e50; padding-bottom: 4px;
  }}
  .detail {{ width: 100%; border-collapse: collapse; font-size: 9.5px; }}
  .detail th {{
      background: #2c3e50; color: #fff; font-weight: 600;
      padding: 5px 6px; text-align: left; font-size: 9px;
      text-transform: uppercase; letter-spacing: .04em;
  }}
  .detail td {{
      padding: 4px 6px; border-bottom: 1px solid #eef2f7; vertical-align: top;
  }}
  .detail tr:nth-child(even) td {{ background: #fafbfc; }}

  .muted {{ color: #94a3b8; font-style: italic; padding: 6px 0; }}

  /* FOOTER */
  .doc-footer {{
      margin-top: 12px; padding-top: 6px; border-top: 1px solid #e2e8f0;
      display: flex; justify-content: space-between;
      font-size: 9px; color: #94a3b8;
  }}

  /* TOOLBAR (screen only) */
  .toolbar {{
      position: fixed; top: 12px; right: 12px; z-index: 9999;
      display: flex; gap: 8px;
  }}
  .toolbar button {{
      background: #1f8de0; color: #fff; border: 0; border-radius: 6px;
      padding: 8px 16px; font-size: 12px; font-weight: 600; cursor: pointer;
      box-shadow: 0 2px 6px rgba(0,0,0,.15);
  }}
  .toolbar button.secondary {{ background: #607080; }}
  @media print {{
      .toolbar {{ display: none !important; }}
      .panel, .kpi {{ break-inside: avoid; }}
      .detail tr {{ page-break-inside: avoid; }}
      .detail thead {{ display: table-header-group; }}
  }}
</style>
</head>
<body>

<div class="toolbar">
  <button onclick="window.print()">🖨️ Print</button>
  <button class="secondary" onclick="window.close()">Close</button>
</div>

<div class="page">

  <div class="doc-header">
    <div class="brand">
      <div class="logo">+</div>
      <div>
        <h1>{escape_html(brand['company'])}</h1>
        <h2>Healthcare Analytics</h2>
      </div>
    </div>
    <div class="meta">
      <div class="title">Outpatient Patient Diagnosis History</div>
      <div>Generated {generated} · by {escape_html(user)}</div>
    </div>
  </div>

  <div class="chips">{filter_chips_html}</div>

  <div class="kpis">{kpis_html}</div>

  <div class="grid-3">
    <div class="panel">
      <h3>Top Diagnoses</h3>
      {diag_table}
    </div>
    <div class="panel">
      <h3>Top Doctors by Patient Visits</h3>
      {doctors_table}
    </div>
    <div class="panel">
      <h3>Age Groups</h3>
      {age_table}
    </div>
    <div class="panel">
      <h3>Sex</h3>
      {sex_table}
    </div>
  </div>

  <div class="detail-section">
    <h3>Encounter Detail ({len(rows)} rows)</h3>
    <table class="detail">
      <thead>
        <tr>
          <th>Date</th><th>Encounter</th><th>Patient</th>
          <th>Age</th><th>Sex</th><th>City</th>
          <th>Doctor</th><th>Diagnosis</th>
          <th>Code</th><th>Type</th>
        </tr>
      </thead>
      <tbody>
        {detail_rows_html}
      </tbody>
    </table>
  </div>

  <div class="doc-footer">
    <div>{escape_html(brand['company'])} · Confidential — for clinical use only</div>
    <div>Outpatient Patient Diagnosis History</div>
  </div>

</div>

<script>
  // Auto-trigger print on load (after layout settles).
  window.addEventListener('load', function () {{
    setTimeout(function () {{ try {{ window.print(); }} catch (e) {{}} }}, 350);
  }});
</script>

</body>
</html>"""
    return html
