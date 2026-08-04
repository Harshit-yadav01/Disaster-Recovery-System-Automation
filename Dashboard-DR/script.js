// ===========================================================================
// Dashboard controller
// - Guards the page (redirects to login if there is no valid session)
// - Loads live data from the backend and renders every section
// - Builds/updates the Chart.js charts from live data
// - Auto-refreshes on an interval
// ===========================================================================

// ---- Auth guard: must run before anything else ----------------------------
if (!window.api || !window.api.isAuthenticated()) {
    window.location.href = "login.html";
}

// Small helpers ------------------------------------------------------------
const $ = (id) => document.getElementById(id);
const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );

// ---- Renderers -----------------------------------------------------------
function renderCards(cards) {
    $("cardsGrid").innerHTML = cards
        .map(
            (c) => `
        <div class="card">
            <h3>${esc(c.title)}</h3>
            <h1>${esc(c.value)}</h1>
            <p>${esc(c.subtext)}</p>
        </div>`
        )
        .join("");
}

function renderReplication(rep) {
    const box = (s) => `
        <div class="box">
            <h3>${esc(s.name)}</h3>
            <p>${esc(s.array_model)}</p>
            <span class="${esc(s.tone)}">${esc(s.status)}</span>
        </div>`;
    $("replicationBox").innerHTML =
        box(rep.primary) +
        `<div class="arrow"><i class="fa-solid fa-arrow-right"></i></div>` +
        box(rep.recovery);
}

function renderInfra(items) {
    $("infraGrid").innerHTML = items
        .map(
            (i) => `
        <div class="infra-card">
            <i class="fa-solid ${esc(i.icon)}"></i>
            <h3>${esc(i.label)}</h3>
            <h1>${esc(i.value)}</h1>
            <p>${esc(i.subtext)}</p>
        </div>`
        )
        .join("");
}

function renderStorage(items) {
    $("storageGrid").innerHTML = items
        .map(
            (s) => `
        <div class="storage-box">
            <h3>${esc(s.label)}</h3>
            <div class="progress">
                <div class="progress-fill" style="width:${Number(s.percent)}%;"></div>
            </div>
            <p>${esc(s.detail)}</p>
        </div>`
        )
        .join("");
}

function renderTimeline(events) {
    $("timeline").innerHTML = events
        .map(
            (e) => `
        <div class="event">
            <div class="dot"></div>
            <div>
                <h4>${esc(e.title)}</h4>
                <p>${esc(e.detail)}</p>
            </div>
        </div>`
        )
        .join("");
}

function renderReadiness(r) {
    const checks = r.checks.map((c) => `\u2714 ${esc(c)}`).join("<br>");
    $("readinessBox").innerHTML = `
        <div class="circle">
            <div class="inner-circle">${Number(r.percent)}%</div>
        </div>
        <div>
            <h3>${esc(r.headline)}</h3>
            <p>${checks}</p>
        </div>`;
}

function setStatus(source, ok) {
    const el = $("dataStatus");
    if (!el) return;
    if (!ok) {
        el.className = "data-status error";
        el.textContent = "Backend offline";
    }
}

// ---- Data load -----------------------------------------------------------
function showDashboardSections(show) {
    [
        "cardsGrid",
        "repHealthSection",
        "infraSection",
        "storageSection",
        "timelineSection",
        "readinessSection",
    ].forEach((id) => toggleSection(id, show));
}

// Render one section in isolation: reveal it only when its data is present and
// rendering succeeds, so a failure in one section can't blank the others.
function renderSection(sectionId, hasData, render) {
    try {
        if (hasData) { render(); toggleSection(sectionId, true); }
        else { toggleSection(sectionId, false); }
    } catch (err) {
        console.error(`Failed to render ${sectionId}:`, err);
        toggleSection(sectionId, false);
    }
}

async function loadDashboard() {
    let data;
    try {
        data = await window.api.getDashboard();
    } catch (err) {
        console.error("Failed to load dashboard:", err);
        showDashboardSections(false);
        return;
    }
    const has = (v) => Array.isArray(v) ? v.length > 0 : !!v;
    renderSection("cardsGrid", has(data.cards), () => renderCards(data.cards));
    renderSection("repHealthSection", has(data.replication), () => renderReplication(data.replication));
    renderSection("infraSection", has(data.infrastructure), () => renderInfra(data.infrastructure));
    renderSection("storageSection", has(data.storage), () => renderStorage(data.storage));
    renderSection("timelineSection", has(data.timeline), () => renderTimeline(data.timeline));
    renderSection("readinessSection", has(data.readiness), () => renderReadiness(data.readiness));
}

// ---- Live array health (read-only SSH via /api/dr/health) ----------------
function toggleSection(id, show) {
    const el = $(id);
    if (el) el.hidden = !show;
}

function renderHealthPerf(cpu, perf) {
    const grid = $("healthPerfGrid");
    if (!grid) return;
    const cards = [];
    if (cpu) {
        cards.push(`
            <div class="infra-card">
                <i class="fa-solid fa-microchip"></i>
                <h3>CPU Usage</h3>
                <h1>${Number(cpu.percent)}%</h1>
                <p>${cpu.nodes.map((n) => `Node ${esc(n.node)}: ${Number(n.percent)}%`).join(" &bull; ")}</p>
            </div>`);
    }
    if (perf) {
        const mbps = (Number(perf.throughput_kbps) / 1024).toFixed(1);
        const busy = perf.busiest
            ? `Busiest: ${esc(perf.busiest.name)} (${Number(perf.busiest.iops)} IOPS)`
            : "Aggregate";
        cards.push(`
            <div class="infra-card">
                <i class="fa-solid fa-gauge-high"></i>
                <h3>IOPS</h3>
                <h1>${Number(perf.iops)}</h1>
                <p>${Number(perf.vv_count)} volumes</p>
            </div>`);
        cards.push(`
            <div class="infra-card">
                <i class="fa-solid fa-stopwatch"></i>
                <h3>Latency</h3>
                <h1>${Number(perf.latency_ms)} ms</h1>
                <p>Service time</p>
            </div>`);
        cards.push(`
            <div class="infra-card">
                <i class="fa-solid fa-arrows-left-right"></i>
                <h3>Throughput</h3>
                <h1>${mbps} MB/s</h1>
                <p>${busy}</p>
            </div>`);
    }
    grid.innerHTML = cards.join("");
    toggleSection("healthPerfSection", cards.length > 0);
}

function renderHealthCapacity(cap) {
    const grid = $("healthCapacityGrid");
    if (!grid || !cap) {
        toggleSection("healthCapacitySection", false);
        return;
    }
    grid.innerHTML = cap.cpgs
        .map(
            (c) => `
        <div class="storage-box">
            <h3>${esc(c.name)}</h3>
            <div class="progress">
                <div class="progress-fill" style="width:${Number(c.used_pct)}%;"></div>
            </div>
            <p>${Number(c.used_pct)}% &bull; ${esc(c.used_human)} of ${esc(c.total_human)}</p>
        </div>`
        )
        .join("");
    toggleSection("healthCapacitySection", true);
}

function renderHealthAlerts(alerts) {
    if (!alerts || !alerts.length) {
        toggleSection("alertsSection", false);
        return;
    }
    const rows = alerts
        .map(
            (a) => `
        <tr>
            <td>${esc(a.time)}</td>
            <td>${esc(a.message)}</td>
            <td><span class="status ${esc(a.tone)}">${esc(a.severity)}</span></td>
        </tr>`
        )
        .join("");
    $("alertsTable").innerHTML =
        `<tr><th>Time</th><th>Event</th><th>Severity</th></tr>` + rows;
    toggleSection("alertsSection", true);
}

function renderHealthReplication(repl) {
    const table = $("healthReplTable");
    if (!table || !repl || !repl.groups.length) {
        toggleSection("healthReplSection", false);
        return;
    }
    const rows = repl.groups
        .map((g) => {
            const tone =
                g.status === "Started" && g.all_synced
                    ? "green"
                    : g.status === "Stopped"
                    ? "warning"
                    : "blue";
            const rpo =
                g.mode === "Sync" && g.status === "Started" && g.all_synced
                    ? "0s (synchronous)"
                    : g.last_sync
                    ? esc(g.last_sync)
                    : "&mdash;";
            return `
        <tr>
            <td>${esc(g.name)}</td>
            <td>${esc(g.role)}</td>
            <td><span class="status ${tone}">${esc(g.status)}</span></td>
            <td>${Number(g.synced)}/${Number(g.total)} synced</td>
            <td>${rpo}</td>
        </tr>`;
        })
        .join("");
    table.innerHTML =
        `<tr><th>Group</th><th>Role</th><th>Status</th><th>Volumes</th><th>RPO / Last Sync</th></tr>` +
        rows;
    toggleSection("healthReplSection", true);
}

function setLive(host, ts) {
    const el = $("dataStatus");
    if (!el) return;
    const t = ts ? new Date(ts) : new Date();
    const stamp = t.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
    });
    el.className = "data-status live";
    el.textContent = `Live \u2022 ${host} \u2022 ${stamp}`;
}

async function loadHealth() {
    try {
        const h = await window.api.getHealth();
        renderHealthPerf(h.cpu, h.performance);
        renderHealthCapacity(h.capacity);
        renderHealthAlerts(h.alerts);
        renderHealthReplication(h.replication);
        setLive(h.host, h.generated_at);
    } catch (err) {
        console.error("Failed to load live health:", err);
        renderHealthPerf(null, null);
        renderHealthCapacity(null);
        renderHealthReplication(null);
        setStatus(null, false);
    }
}

async function refreshAll() {
    const icon = document.querySelector("#refreshBtn i");
    if (icon) icon.classList.add("fa-spin");
    try {
        await Promise.all([loadDashboard(), loadHealth()]);
    } finally {
        if (icon) icon.classList.remove("fa-spin");
    }
}

// ===================== PROFILE AVATAR =====================
// Shows initials from the logged-in user, and lets them upload a photo.
(function initAvatar() {
    const avatar = document.getElementById("avatar");
    if (!avatar) return;

    const initialsEl = document.getElementById("avatarInitials");
    const imgEl = document.getElementById("avatarImg");
    const upload = document.getElementById("avatarUpload");

    // Derive initials from the stored user (falls back to "DR Admin")
    const user = (localStorage.getItem("drUser") || "DR Admin").trim();
    const initials = user
        .split(/[\s@._-]+/)
        .filter(Boolean)
        .slice(0, 2)
        .map((part) => part.charAt(0).toUpperCase())
        .join("");
    initialsEl.textContent = initials || "DR";

    // Restore a previously uploaded photo
    const savedPhoto = localStorage.getItem("drAvatar");
    if (savedPhoto) {
        imgEl.src = savedPhoto;
        imgEl.hidden = false;
        initialsEl.hidden = true;
    }

    // Click avatar -> open file picker
    avatar.addEventListener("click", () => upload.click());

    // Handle upload -> preview + persist
    upload.addEventListener("change", () => {
        const file = upload.files && upload.files[0];
        if (!file || !file.type.startsWith("image/")) return;

        const reader = new FileReader();
        reader.onload = (e) => {
            imgEl.src = e.target.result;
            imgEl.hidden = false;
            initialsEl.hidden = true;
            localStorage.setItem("drAvatar", e.target.result);
        };
        reader.readAsDataURL(file);
    });
})();

// ---- Logout + boot -------------------------------------------------------
(function initLogout() {
    const btn = document.getElementById("logoutBtn");
    if (btn) btn.addEventListener("click", () => {
        if (confirm("Are you sure you want to sign out?")) window.api.logout();
    });
})();

loadDashboard();
loadHealth();
const refreshBtn = document.getElementById("refreshBtn");
if (refreshBtn) refreshBtn.addEventListener("click", refreshAll);
if (window.APP_CONFIG && window.APP_CONFIG.REFRESH_MS) {
    setInterval(refreshAll, window.APP_CONFIG.REFRESH_MS);
}