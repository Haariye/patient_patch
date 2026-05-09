# Outpatient Patient Diagnosis History — Install & Usage

A custom **Frappe Script Report** under the **Patient Patch** app
(`patient_patch.patient_patch.report.outpatient_patient_diagnosis_history`)
for ERPNext Healthcare. It lists outpatient (non‑inpatient) `Patient Encounter`
records broken down by diagnosis line, with a rich filter set, a dashboard of
charts above the table, a printable A4‑landscape report, and a one‑click AI
summary for a single patient.

---

## 1. Files in this drop

```
patient_patch/patient_patch/report/
└── outpatient_patient_diagnosis_history/
    ├── __init__.py
    ├── outpatient_patient_diagnosis_history.json     # Report doctype
    ├── outpatient_patient_diagnosis_history.py       # Server-side
    ├── outpatient_patient_diagnosis_history.js       # Client-side
    └── INSTALL.md                                    # This file
patient_patch/patient_patch/report/__init__.py        # Empty (created)
patient_patch/patient_patch/patches/__init__.py       # Empty (created)
```

These files plug into your **existing** `patient_patch` app — no app rename
or module rename. Module is **`Patient Patch`** to match `modules.txt`.

---

## 2. Install

```bash
cd /path/to/frappe-bench
# Drop the files into apps/patient_patch (preserving the structure above)

bench --site <yoursite> migrate
bench --site <yoursite> clear-cache
bench restart
```

The report appears under **Reports → Outpatient Patient Diagnosis History**,
and is also reachable directly via:

```
/app/query-report/Outpatient Patient Diagnosis History
```

> **Tip — search visibility.** When you create a new Script Report inside
> an app, Frappe ships its JSON the first time the report is opened. If
> the report doesn’t appear immediately, run `bench build` and reload.

---

## 3. Filters

| Filter | Type | Notes |
|---|---|---|
| **From Date** *(required)* | Date | Defaults to one month ago |
| **To Date** *(required)* | Date | Defaults to today |
| **Healthcare Practitioner** | Link → `Healthcare Practitioner` | Standard Link |
| **Patient** | Link → `Patient` | Renders patient name from Frappe’s title field |
| **Diagnosis** | Link → `Diagnosis` | Standard Link to the Diagnosis doctype |
| **City** | Link → `Address` | `Patient.custom_city` is itself a Link to Address, so the picked Address ID is compared directly. The print/chip shows the city label alongside |
| **Age Groups** | MultiSelectList | `0–12 mo`, `1–5 yr`, `6–17 yr`, `18–30 yr`, `31–45 yr`, `46–60 yr`, `60+` |
| **Sex** | Select | `Male` / `Female` / `Other` / `Prefer not to say` |
| **Group By** | Select | `Day` / `Month` / `Year` (drives the trend chart bucketing) |
| **Include encounters with no diagnosis** | Check | OFF by default |

> **Why a multi‑select for age?** Babies under 1 year are clinically
> distinct from older children, and an integer year filter cannot represent
> a 6‑month‑old patient as `0`. The multi‑select cleanly covers the
> 0–12 mo bucket plus all year ranges.

---

## 4. Toolbar buttons

These appear in the report’s inner toolbar.

### Clear Diagnosis
Quick‑clears the Diagnosis filter and refreshes the report.

### Download Report
Builds a polished **A4 landscape** HTML document on the server and opens it in
a new browser tab. The tab auto‑triggers `window.print()`. The page contains:

- branded header (company name from system defaults)
- active‑filter chips
- KPI cards (Patients, Encounters, Diagnoses, and the highlighted diagnosis if any)
- four panels: Top Diagnoses, Top Doctors by Patient Visits, Age Groups, Sex
- a striped detail table with one row per encounter‑diagnosis line
- print‑safe page breaks (`thead` repeats per printed page; cards avoid mid‑page splits)

> **Pop‑up blockers.** If the new window is blocked the report shows a
> friendly hint asking the doctor to allow pop‑ups for the site once.

### Get AI Summary  *(visible only when a single Patient is selected)*
Calls the OpenAI Responses API (same key/path as `clinical_intel_ai.py`) with a
compact, factual context built from the selected patient’s outpatient
encounters in the chosen date range. The response is a short bullet summary
covering visits & doctors, complaints, examination findings, diagnoses,
prescriptions, labs ordered, and the trajectory.

The button is **hidden** unless `Patient` is set, so it never runs against
multi‑patient datasets.

---

## 5. AI key configuration

The report reads the same key the existing app uses (in priority order):

1. `Healthcare Settings → custom_openai_api_key` (Password field, already
   created by `patient_patch.patient_patch.patches.create_custom_fields` in
   your app).
2. `site_config.json → openai_api_key`.
3. Environment variable `OPENAI_API_KEY`.

If none is set, the **AI Summary** button shows a clear error rather than
calling the API.

---

## 6. Dashboard above the table

When the report has data, a server‑rendered HTML dashboard streams in above
the table:

- **Top Diagnoses** — bar chart of the top 10 diagnoses by encounters
- **Sex Distribution** — donut of unique patients by sex
- **Age Group Distribution** — bar chart matching the filter buckets
- **Diagnosis Trend** — line chart by Day/Month/Year (driven by *Group By*)
- **Top Diagnoses Ranking** — top 10 with patient count, encounter count, %
- **Top Cities** — top 5 cities by unique patients
- **Top Doctors by Patient Visits** — top 8 practitioners (replaces the old
  Districts/Areas card)
- **Doctors — Unique Patients (Top 10)** — full‑width bar chart

When a specific Diagnosis filter is selected, an extra highlighted block
shows unique‑patient and encounter counts for that diagnosis.

---

## 7. Schema compatibility (defensive lookups)

`_resolve_schema()` runs `frappe.get_meta(...).has_field(...)` to pick the
correct fieldname per‑site for fields that have moved across ERPNext
Healthcare versions:

| Logical field | Tries (in order) |
|---|---|
| Patient city | `custom_city`, `city` |
| Encounter type | `encounter_type`, `appointment_type`, `custom_encounter_type` |
| Encounter department | `medical_department`, `department` |
| Encounter remarks | `custom_chief_complaint`, `symptoms`, `physical_examination` |

Diagnosis is read from the **`Patient Encounter Diagnosis`** child table when
present (preferred), else from the free‑text `Patient Encounter.diagnosis`
field.

---

## 8. Roles

The report’s `roles` array in the JSON includes:

- System Manager
- Healthcare Administrator
- Physician
- Healthcare Practitioner
- Nursing User

Adjust to taste; a `bench migrate` will pick the changes up.

---

## 9. Performance

- The main query is parameterised and joins three tables: `Patient Encounter`,
  `Patient`, and (when present) `Patient Encounter Diagnosis`. `pe.encounter_date`,
  `pe.docstatus`, `pe.patient`, and `pe.practitioner` are already indexed in
  standard ERPNext Healthcare schema.
- For very large windows (12+ months on a busy hospital) consider running
  with a `Practitioner` or `Diagnosis` filter, which gives the planner a
  smaller working set.

---

## 10. Whitelisted endpoints

Each is namespaced under
`patient_patch.patient_patch.report.outpatient_patient_diagnosis_history.outpatient_patient_diagnosis_history`:

| Method | Purpose |
|---|---|
| `get_print_html` | Returns the standalone A4‑landscape HTML report |
| `generate_patient_period_summary` | Returns the AI bullet summary for one patient |

---

## 11. Troubleshooting

**“Report not found.”** Run `bench build` and reload the desk; for newly
added reports, also `bench --site <yoursite> migrate` once.

**Pop‑up blocked when downloading the report.** Allow pop‑ups for your ERPNext
site domain once; the file is built server‑side and streamed into the new tab.

**“OpenAI API key is not configured.”** Open **Healthcare Settings** and set
`custom_openai_api_key` (already added as a Password custom field by your
app’s patch). Or set it in `site_config.json` / env.

**Diagnosis dropdown is empty.** Make sure the **Diagnosis** doctype has
records (it's the standard ERPNext Healthcare master). The Link filter is
not date‑scoped — it shows every Diagnosis on the system.

**City dropdown shows Address records (e.g. "John's Home"), not city names.**
That's correct. `Patient.custom_city` is itself a Link to Address, so the
filter compares Address IDs to Address IDs. The active‑filter chip on the
printable report shows the city label alongside, e.g.
*"City: Hargeisa (PAT-Home-001)"*.

**A 6‑month‑old patient doesn’t show up under `0–12 mo`.** The bucket reads
`TIMESTAMPDIFF(MONTH, p.dob, today)`; make sure `Patient.dob` is filled.
