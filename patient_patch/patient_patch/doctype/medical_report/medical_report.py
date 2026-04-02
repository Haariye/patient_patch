"""
Medical Report DocType.

This DocType represents a manual medical report for a patient.  It stores basic
patient demographics, encounter information and recommendations.  Values for
patient details (name, ID, age, sex) are fetched from the linked Patient.  A
server method could be added later to populate diagnosis, treatment and
recommendation fields automatically.
"""

import frappe
from frappe.model.document import Document


class MedicalReport(Document):
    """DocType class for Medical Report.

    This class currently inherits directly from Frappe's base Document.  It does
    not override any hooks.  Additional validation or methods (for example,
    automatic population from encounters or AI recommendation generation) can be
    added here in the future.
    """

    pass
