// patient_patch/public/js/visit_detail.js
// Handles: Free Follow-Up detection via Fee Validity,
//          Jawaab popup dialog (red alert, redirects to print),
//          standard appointment buttons,
//          Jawaab queue series in custom_jawaab_queue (daily reset),
//          Normal queue in position_in_queue (pauses/resumes around Jawaab),
//          Print button visibility,
//          Exhausted visits → full normal payment flow,
//          ref_appointments cap enforcement (cannot exceed max_visits).
//
// SOURCE OF TRUTH FOR VISIT COUNT:
//   ref_appointments.length on Fee Validity is the real used count.
//   The `visited` field can drift, so we always use ref_appointments.length.

// ─────────────────────────────────────────────────────────────────────────────
// HELPER: Fetch full Fee Validity doc including ref_appointments child table.
// Returns null if no active Fee Validity found within date range.
// ─────────────────────────────────────────────────────────────────────────────
async function get_active_fee_validity(patient, practitioner) {
    if (!patient || !practitioner) return null;

    const today = frappe.datetime.get_today();

    const list = await frappe.db.get_list('Fee Validity', {
        filters: {
            patient: patient,
            practitioner: practitioner,
            status: 'Active',
            start_date: ['<=', today],
            valid_till: ['>=', today]
        },
        fields: ['name', 'max_visits', 'visited', 'valid_till'],
        limit: 1
    });

    if (!list || !list.length) return null;

    const fv_doc = await frappe.db.get_doc('Fee Validity', list[0].name);
    if (!fv_doc) return null;

    const used_visits = fv_doc.ref_appointments
        ? fv_doc.ref_appointments.length
        : 0;

    return {
        name: fv_doc.name,
        max_visits: fv_doc.max_visits,
        visited: fv_doc.visited,
        valid_till: fv_doc.valid_till,
        used_visits: used_visits,
        remaining: fv_doc.max_visits - used_visits,
        has_visits_left: used_visits < fv_doc.max_visits,
        ref_appointments: fv_doc.ref_appointments || []
    };
}

// ─────────────────────────────────────────────────────────────────────────────
// HELPER: Show all standard payment buttons exactly as a normal appointment.
// ─────────────────────────────────────────────────────────────────────────────
function show_standard_payment_buttons(frm) {
    if (frm.doc.invoiced || frm.doc.is_free_follow_up) return;
    if (frm.is_new() || frm.doc.status === 'Cancelled') return;

    frappe.db.get_single_value('Healthcare Settings', 'show_payment_popup')
        .then(async function (val) {
            let fv_check = [];
            try {
                let res = await frappe.call(
                    'healthcare.healthcare.doctype.fee_validity.fee_validity.get_fee_validity',
                    {
                        appointment_name: frm.doc.name,
                        date: frm.doc.appointment_date,
                        ignore_status: true
                    }
                );
                fv_check = res.message || [];
            } catch (e) {
                fv_check = [];
            }

            if (val && !fv_check.length) {
                frm.add_custom_button(__('Make Payment'), function () {
                    make_payment(frm, val);
                });
            }
        });
}

// ─────────────────────────────────────────────────────────────────────────────
// HELPER: Show the Jawaab popup dialog.
// Opens immediately with a loading state, then populates data.
// This ensures the dialog is always visible even on slow connections.
// ─────────────────────────────────────────────────────────────────────────────
function show_jawaab_dialog(frm, fv) {

    // ── Build dialog immediately — no waiting for data ─────────────────
    // Data is already passed in as fv from refresh, so render right away
    const valid_till_display = fv ? frappe.datetime.str_to_user(fv.valid_till) : '...';
    const remaining = fv ? (fv.max_visits - fv.used_visits) : '...';
    const used = fv ? fv.used_visits : '...';
    const max = fv ? fv.max_visits : '...';

    const dialog = new frappe.ui.Dialog({
        title: __('⚠️ Free Follow-Up Visit Detected'),
        fields: [
            {
                fieldtype: 'HTML',
                fieldname: 'jawaab_alert',
                options: `
                    <div id="jawaab-dialog-body" style="
                        background: #fff2f0;
                        border: 2px solid #ff4d4f;
                        border-radius: 8px;
                        padding: 20px 24px;
                        margin-bottom: 8px;
                    ">
                        <div style="
                            color: #cf1322;
                            font-size: 16px;
                            font-weight: 700;
                            margin-bottom: 12px;
                            display: flex;
                            align-items: center;
                            gap: 8px;
                        ">
                            🚫 &nbsp; This patient has FREE remaining visits!
                        </div>
                        <table style="width:100%; font-size:14px; color:#333; border-collapse:collapse;">
                            <tr>
                                <td style="padding: 5px 0; color:#888; width:50%;">Patient</td>
                                <td style="padding: 5px 0; font-weight:600;">${frm.doc.patient_name || frm.doc.patient}</td>
                            </tr>
                            <tr>
                                <td style="padding: 5px 0; color:#888;">Practitioner</td>
                                <td style="padding: 5px 0; font-weight:600;">${frm.doc.practitioner}</td>
                            </tr>
                            <tr>
                                <td style="padding: 5px 0; color:#888;">Fee Validity Valid Till</td>
                                <td style="padding: 5px 0; font-weight:600; color:#cf1322;">${valid_till_display}</td>
                            </tr>
                            <tr>
                                <td style="padding: 5px 0; color:#888;">Visits Used</td>
                                <td style="padding: 5px 0; font-weight:600;">${used} of ${max}</td>
                            </tr>
                            <tr>
                                <td style="padding: 5px 0; color:#888;">Remaining Free Visits</td>
                                <td style="padding: 5px 0; font-weight:700; color:#389e0d; font-size:15px;">${remaining}</td>
                            </tr>
                        </table>
                        <div style="
                            margin-top: 14px;
                            padding: 10px 14px;
                            background: #fff1b8;
                            border-left: 4px solid #faad14;
                            border-radius: 4px;
                            color: #7c5400;
                            font-size: 13px;
                        ">
                            ⚠️ Please confirm this as a <strong>Jawaab (Free Follow-Up)</strong> visit.
                            Clicking <strong>"Confirm Jawaab"</strong> will save this appointment as a free follow-up.
                        </div>
                    </div>
                `
            }
        ],
        primary_action_label: __('✅ Confirm Jawaab'),
        primary_action: async function () {

            // Disable button immediately to prevent double-clicks
            dialog.get_primary_btn().prop('disabled', true).text(__('Saving...'));

            try {
                // Use fv already in memory — no extra fetch needed
                // Only re-fetch if fv is somehow null
                const latest_fv = fv || await get_active_fee_validity(
                    frm.doc.patient,
                    frm.doc.practitioner
                );

                if (!latest_fv || !latest_fv.has_visits_left) {
                    dialog.hide();
                    frappe.msgprint({
                        title: __('No Visits Remaining'),
                        message: __('All visits for this Fee Validity have been used. This appointment will proceed as a regular paid appointment.'),
                        indicator: 'orange'
                    });
                    frm._jawaab_dialog_shown = false;
                    frm.refresh();
                    return;
                }

                const new_visited = parseInt(latest_fv.used_visits, 10) + 1;

                // Set values on the form
                frm.set_value('is_free_follow_up', 1);
                frm.set_value('fee_validity_ref', latest_fv.name);
                frm.set_value('invoiced', 0);
                frm.set_value('billing_item', null);
                frm.set_value('paid_amount', 0);

                // Update visited count on Fee Validity
                await frappe.db.set_value(
                    'Fee Validity',
                    latest_fv.name,
                    'visited',
                    new_visited
                );

                // Save the appointment
                await frm.save();

                dialog.hide();

                frappe.show_alert({
                    message: __('Jawaab Applied — {0}/{1} visits used', [
                        new_visited,
                        latest_fv.max_visits
                    ]),
                    indicator: 'green'
                });

                // Reload so all fields display correctly and print button appears
                frm.reload_doc();

            } catch (err) {
                dialog.get_primary_btn().prop('disabled', false).text(__('✅ Confirm Jawaab'));
                frappe.msgprint({
                    title: __('Error Applying Jawaab'),
                    message: __('Something went wrong. Please try again.'),
                    indicator: 'red'
                });
                frm._jawaab_dialog_shown = false;
            }
        }
    });

    // Style the dialog header red immediately before show
    dialog.show();

    // Apply red styling right after show — no delay needed
    dialog.$wrapper.find('.modal-header').css({
        'background-color': '#fff2f0',
        'border-bottom': '2px solid #ff4d4f'
    });
    dialog.$wrapper.find('.modal-title').css('color', '#cf1322');
}

frappe.ui.form.on('Patient Appointment', {

    // ─────────────────────────────────────────────
    // SETUP
    // ─────────────────────────────────────────────
    setup: function (frm) {
        frm.custom_make_buttons = {
            'Vital Signs': 'Vital Signs',
            'Patient Encounter': 'Patient Encounter'
        };
    },

    // ─────────────────────────────────────────────
    // ONLOAD
    // ─────────────────────────────────────────────
    onload: function (frm) {
        if (frm.is_new()) {
            frm.set_value('appointment_time', null);
            frm.disable_save();
        }
    },

    // ─────────────────────────────────────────────
    // REFRESH
    // ─────────────────────────────────────────────
    refresh: async function (frm) {

        // Primary action button
        if (frm.is_new()) {
            frm.page.clear_primary_action();
        } else {
            frm.page.set_primary_action(__('Save'), () => frm.save());
        }

        // ── Print button visibility ────────────────────────────────────
        const print_allowed = frm.doc.invoiced || frm.doc.is_free_follow_up;
        setTimeout(function () {
            if (!print_allowed) {
                frm.page.wrapper.find('.btn-print, [data-label="Print"]').hide();
                frm.page.wrapper
                    .find('.page-head .menu-btn-group .dropdown-menu a')
                    .filter(function () {
                        return $(this).text().trim() === 'Print';
                    }).parent().hide();
            } else {
                frm.page.wrapper.find('.btn-print, [data-label="Print"]').show();
                frm.page.wrapper
                    .find('.page-head .menu-btn-group .dropdown-menu a')
                    .filter(function () {
                        return $(this).text().trim() === 'Print';
                    }).parent().show();
            }
        }, 300);

        // Skip further button logic for new or cancelled appointments
        if (frm.is_new() || frm.doc.status === 'Cancelled') return;

        const already_cleared = frm.doc.invoiced || frm.doc.is_free_follow_up;

        // If already cleared show Consultation + Vital Signs and stop
        if (already_cleared) {
            frm.remove_custom_button(__('Jawaab'));
            frm.remove_custom_button(__('Make Payment'));

            frm.add_custom_button(__('Consultation'), function () {
                frappe.model.open_mapped_doc({
                    method: 'healthcare.healthcare.doctype.patient_appointment.patient_appointment.make_encounter',
                    frm: frm
                });
            }, __('Create'));

            frm.add_custom_button(__('Vital Signs'), function () {
                frappe.model.open_mapped_doc({
                    method: 'healthcare.healthcare.doctype.patient_appointment.patient_appointment.make_vital_signs',
                    frm: frm
                });
            }, __('Create'));

            return;
        }

        // ── Fetch Fee Validity using ref_appointments as source of truth
        const fv = await get_active_fee_validity(frm.doc.patient, frm.doc.practitioner);

        // ── JAWAAB MODE ────────────────────────────────────────────────
        // Fee Validity exists AND visits are still available
        if (fv && fv.has_visits_left) {

            frm.remove_custom_button(__('Consultation'), __('Create'));
            frm.remove_custom_button(__('Vital Signs'), __('Create'));
            frm.remove_custom_button(__('Make Payment'));

            // Guard: only open once per form session to prevent
            // repeated popups from multiple refresh() calls
            if (!frm._jawaab_dialog_shown) {
                frm._jawaab_dialog_shown = true;
                show_jawaab_dialog(frm, fv);
            }

            frm.page.set_indicator(
                __('Fee Validity Active — {0}/{1} visits used', [fv.used_visits, fv.max_visits]),
                'blue'
            );

            return;
        }

        // ── EXHAUSTED or NO FEE VALIDITY → FULL NORMAL PAYMENT MODE ───
        frm.remove_custom_button(__('Jawaab'));
        frm.remove_custom_button(__('Consultation'), __('Create'));
        frm.remove_custom_button(__('Vital Signs'), __('Create'));

        if (fv && !fv.has_visits_left) {
            frm.page.set_indicator(
                __('Fee Validity Full — {0}/{0} visits used (valid till {1})', [
                    fv.max_visits,
                    frappe.datetime.str_to_user(fv.valid_till)
                ]),
                'orange'
            );

            if (frm.doc.fee_validity_ref) {
                frm.set_value('fee_validity_ref', null);
                frm.set_value('is_free_follow_up', 0);
            }
        }

        show_standard_payment_buttons(frm);
    },

    // ─────────────────────────────────────────────
    // BEFORE SAVE — ref_appointments cap enforcement
    // ─────────────────────────────────────────────
    before_save: async function (frm) {
        if (!frm.doc.fee_validity_ref) return;
        if (!frm.doc.is_free_follow_up) return;

        const fv_doc = await frappe.db.get_doc('Fee Validity', frm.doc.fee_validity_ref);
        if (!fv_doc) return;

        const used_visits = fv_doc.ref_appointments
            ? fv_doc.ref_appointments.length
            : 0;

        const already_linked = fv_doc.ref_appointments &&
            fv_doc.ref_appointments.some(function (r) {
                return r.appointment === frm.doc.name;
            });

        if (!already_linked && used_visits >= fv_doc.max_visits) {
            frappe.validated = false;
            frappe.msgprint({
                title: __('Fee Validity Full'),
                message: __('This patient\'s Fee Validity (valid till {0}) has reached the maximum of {1} visits. This appointment cannot be saved as a free follow-up. Please process it as a regular paid appointment.', [
                    frappe.datetime.str_to_user(fv_doc.valid_till),
                    fv_doc.max_visits
                ]),
                indicator: 'red'
            });

            frm.set_value('is_free_follow_up', 0);
            frm.set_value('fee_validity_ref', null);
        }
    },

    // ─────────────────────────────────────────────
    // AFTER SAVE — Queue Assignment
    // ─────────────────────────────────────────────
    after_save: function (frm) {

        // ── JAWAAB queue series → custom_jawaab_queue ─────────────────
        if (frm.doc.is_free_follow_up) {

            if (frm.doc.custom_jawaab_queue &&
                String(frm.doc.custom_jawaab_queue).startsWith('JAWAAB')) {
                return;
            }

            if (!frm.doc.appointment_date) return;

            frappe.call({
                method: 'frappe.client.get_list',
                args: {
                    doctype: 'Patient Appointment',
                    filters: {
                        appointment_date: frm.doc.appointment_date,
                        is_free_follow_up: 1
                    },
                    fields: ['name', 'custom_jawaab_queue'],
                    limit_page_length: 0
                },
                callback: function (r) {
                    let max_num = 0;

                    if (r.message && r.message.length) {
                        r.message.forEach(function (appt) {
                            if (appt.custom_jawaab_queue &&
                                String(appt.custom_jawaab_queue).startsWith('JAWAAB')) {
                                const num = parseInt(
                                    String(appt.custom_jawaab_queue).replace('JAWAAB', '')
                                );
                                if (!isNaN(num) && num > max_num) {
                                    max_num = num;
                                }
                            }
                        });
                    }

                    const next_jawaab = 'JAWAAB' + (max_num + 1);

                    frappe.db.set_value(
                        'Patient Appointment',
                        frm.doc.name,
                        'custom_jawaab_queue',
                        next_jawaab
                    ).then(function () {
                        frm.reload_doc();
                    });
                }
            });

            return;
        }

        // ── Normal numeric queue series → position_in_queue ───────────
        if (frm.doc.position_in_queue && frm.doc.position_in_queue > 0) {
            return;
        }

        if (!frm.doc.practitioner || !frm.doc.appointment_type || !frm.doc.appointment_date) {
            frappe.msgprint(__(
                'Please fill all mandatory fields: Practitioner, Appointment Type, and Appointment Date.'
            ));
            return;
        }

        frappe.call({
            method: 'frappe.client.get_list',
            args: {
                doctype: 'Patient Appointment',
                filters: {
                    practitioner: frm.doc.practitioner,
                    appointment_type: frm.doc.appointment_type,
                    appointment_date: frm.doc.appointment_date,
                    is_free_follow_up: 0
                },
                fields: ['name'],
                limit_page_length: 0
            },
            callback: function (r) {
                const next_position = r.message ? r.message.length : 1;

                frappe.db.set_value(
                    'Patient Appointment',
                    frm.doc.name,
                    'position_in_queue',
                    next_position
                ).then(function () {
                    frm.reload_doc();
                });
            }
        });
    },

    // ─────────────────────────────────────────────
    // VALIDATE
    // ─────────────────────────────────────────────
    validate: async function (frm) {
        if (!frm.doc.patient || !frm.doc.practitioner) return;

        if (frm.doc.is_free_follow_up) {
            frm.set_value('billing_item', null);
            frm.set_value('paid_amount', 0);
            frm.set_value('invoiced', 0);
            return;
        }

        if (frm.doc.fee_validity_ref) {
            const fv_doc = await frappe.db.get_doc('Fee Validity', frm.doc.fee_validity_ref);

            if (fv_doc) {
                const used_visits = fv_doc.ref_appointments
                    ? fv_doc.ref_appointments.length
                    : 0;

                const already_linked = fv_doc.ref_appointments &&
                    fv_doc.ref_appointments.some(function (r) {
                        return r.appointment === frm.doc.name;
                    });

                if (!already_linked && used_visits >= fv_doc.max_visits) {
                    frm.set_value('fee_validity_ref', null);
                    frm.set_value('is_free_follow_up', 0);
                    frappe.show_alert({
                        message: __('Fee Validity is full ({0}/{0} visits). Appointment set for regular payment.', [fv_doc.max_visits]),
                        indicator: 'orange'
                    });
                }
            }
        }
    }

});