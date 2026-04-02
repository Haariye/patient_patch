
frappe.ui.form.on('Patient Appointment', {
    onload(frm) {
        style_sections();
    },
    refresh(frm) {
        style_sections();
    }
});

function style_sections() {
    setTimeout(() => {
        $('.section-head').each(function () {
            const sectionTitle = $(this).text().trim().toLowerCase();
            let bgColor = "#f0f0f0";

			if (sectionTitle.includes("payment")) bgColor = "#cce5ff";   // light blue
			else if (sectionTitle.includes("appointment")) bgColor = "#f0f0f0"; // lighter blue (distinct from payment)
			else if (sectionTitle.includes("billing")) bgColor = "#ffe5b4";   // peach
			else if (sectionTitle.includes("more")) bgColor = "#dcdcf3";      // light lavender-pink
			else if (sectionTitle.includes("details")) bgColor = "#d1f7c4";    // light lavender


            $(this).css({
                "background-color": bgColor,
                "padding": "8px 12px",
                "border-radius": "4px",
                "font-weight": "bold",
                "margin-top": "12px"
            });
        });
    }, 300);
}
