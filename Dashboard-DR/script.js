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

function renderAlerts(alerts) {
    const rows = alerts
        .map(
            (a) => `
        <tr>
            <td>${esc(a.time)}</td>
            <td>${esc(a.event)}</td>
            <td><span class="status ${esc(a.tone)}">${esc(a.status)}</span></td>
        </tr>`
        )
        .join("");
    $("alertsTable").innerHTML =
        `<tr><th>Time</th><th>Event</th><th>Status</th></tr>` + rows;
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
        return;
    }
    if (source === "alletra") {
        el.className = "data-status live";
        el.textContent = "Live \u2022 HPE Alletra";
    } else {
        el.className = "data-status sim";
        el.textContent = "Simulated data";
    }
}

// ---- Data load -----------------------------------------------------------
async function loadDashboard() {
    try {
        const data = await window.api.getDashboard();
        renderCards(data.cards);
        renderReplication(data.replication);
        renderInfra(data.infrastructure);
        renderStorage(data.storage);
        renderAlerts(data.alerts);
        renderTimeline(data.timeline);
        renderReadiness(data.readiness);
        setStatus(data.source, true);
    } catch (err) {
        console.error("Failed to load dashboard:", err);
        setStatus(null, false);
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
if (window.APP_CONFIG && window.APP_CONFIG.REFRESH_MS) {
    setInterval(loadDashboard, window.APP_CONFIG.REFRESH_MS);
}