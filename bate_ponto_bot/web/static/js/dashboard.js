document.addEventListener("DOMContentLoaded", () => {
    if (window.lucide) window.lucide.createIcons();

    const escapeHtml = (value) => String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");

    // Search Filter for Data Tables
    const searchInputs = document.querySelectorAll("[data-table-search]");
    searchInputs.forEach(input => {
        const targetTableId = input.getAttribute("data-table-search");
        const table = document.getElementById(targetTableId);
        if (!table) return;

        input.addEventListener("input", (e) => {
            const query = e.target.value.toLowerCase().trim();
            const rows = table.querySelectorAll("tbody tr");

            rows.forEach(row => {
                const text = row.innerText.toLowerCase();
                if (text.includes(query)) {
                    row.style.display = "";
                } else {
                    row.style.display = "none";
                }
            });
        });
    });

    document.querySelectorAll("form[data-confirm]").forEach(form => {
        form.addEventListener("submit", (event) => {
            if (!window.confirm(form.getAttribute("data-confirm"))) {
                event.preventDefault();
            }
        });
    });

    // Events Modal Handling
    const modal = document.getElementById("eventsModal");
    const modalTitle = document.getElementById("modalEntryTitle");
    const modalTimeline = document.getElementById("modalTimeline");
    const closeModalBtns = document.querySelectorAll("[data-close-modal]");

    closeModalBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            if (modal) modal.classList.remove("show");
        });
    });

    if (modal) {
        modal.addEventListener("click", (e) => {
            if (e.target === modal) {
                modal.classList.remove("show");
            }
        });
    }

    const eventButtons = document.querySelectorAll("[data-view-events]");
    eventButtons.forEach(btn => {
        btn.addEventListener("click", async () => {
            const entryId = btn.getAttribute("data-view-events");
            if (!modal || !modalTimeline) return;

            modalTitle.innerText = `Eventos do Registro #${entryId}`;
            modalTimeline.innerHTML = '<div class="timeline-item"><div class="timeline-content">Carregando eventos...</div></div>';
            modal.classList.add("show");

            try {
                const res = await fetch(`/api/entries/${entryId}/events`);
                const data = await res.json();
                
                if (!data.events || data.events.length === 0) {
                    modalTimeline.innerHTML = '<div class="timeline-item"><div class="timeline-content">Nenhum evento registrado para este ponto.</div></div>';
                    return;
                }

                const eventTypeNames = {
                    "started": "Ponto iniciado",
                    "paused": "Ponto pausado",
                    "resumed": "Ponto retomado",
                    "finished": "Ponto finalizado",
                    "auto_absence_closed": "Encerramento automático por ausência",
                    "auto_absence_return_notified": "Notificação de retorno pós-ausência"
                };

                let html = "";
                data.events.forEach(ev => {
                    const title = eventTypeNames[ev.event_type] || `Evento: ${ev.event_type}`;
                    let metaInfo = "";
                    if (ev.metadata && Object.keys(ev.metadata).length > 0) {
                        metaInfo = `<div style="color: var(--text-dim); font-size: 0.75rem; margin-top: 0.25rem;">${escapeHtml(JSON.stringify(ev.metadata))}</div>`;
                    }
                    html += `
                        <div class="timeline-item">
                            <div class="timeline-dot"></div>
                            <div class="timeline-content">
                                <strong>${escapeHtml(title)}</strong>
                                ${metaInfo}
                                <div class="timeline-time">${escapeHtml(ev.occurred_at_formatted)}</div>
                            </div>
                        </div>
                    `;
                });
                modalTimeline.innerHTML = html;
            } catch (err) {
                modalTimeline.innerHTML = `<div class="timeline-item"><div class="timeline-content" style="color: var(--danger)">Erro ao carregar eventos: ${escapeHtml(err.message)}</div></div>`;
            }
        });
    });
});
