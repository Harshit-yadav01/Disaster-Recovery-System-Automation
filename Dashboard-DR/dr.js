// ===========================================================================
// Disaster Recovery panel controller
// - Loads live Remote Copy status (SSH) from /api/dr/status
// - One-click Failover / Failback (+ Start/Stop/Sync) with dry-run + confirm
// - Runs each action as a background job and polls /api/dr/jobs/{id} for live
//   step-by-step progress
// ===========================================================================
(function () {
    if (!window.api || !window.api.isAuthenticated()) return;

    const $ = (id) => document.getElementById(id);
    const esc = (s) =>
        String(s ?? "").replace(/[&<>"']/g, (c) =>
            ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
        );

    let pollTimer = null;
    let statusTimer = null;
    let jobRunning = false;
    let pendingOp = null;

    // ---- Status card -------------------------------------------------------
    function roleBadge(role) {
        const r = (role || "").toLowerCase();
        let cls = "grey";
        if (r.startsWith("primary")) cls = "green";
        else if (r.startsWith("secondary")) cls = "blue";
        return `<span class="dr-badge ${cls}">${esc(role)}</span>`;
    }

    function syncBadge(s) {
        const v = (s || "").toLowerCase();
        let cls = "grey";
        if (v === "synced") cls = "green";
        else if (v === "syncing" || v.startsWith("new")) cls = "amber";
        return `<span class="dr-badge ${cls}">${esc(s)}</span>`;
    }

    function renderStatus(data) {
        const arrays = data.arrays || [];
        let failedOver = false;
        const cards = arrays
            .map((a) => {
                const g = a.group;
                if (!g) {
                    return `<div class="dr-array"><h4>${esc(a.role_label.toUpperCase())} &bull; ${esc(a.host)}</h4>
                        <p class="dr-error">group not found / unreachable</p></div>`;
                }
                if (g.is_reversed) failedOver = true;
                const vols = (g.volumes || [])
                    .map(
                        (v) =>
                            `<tr><td>${esc(v.local_vv)}</td><td>${esc(v.remote_vv)}</td><td>${syncBadge(v.sync_status)}</td></tr>`
                    )
                    .join("");
                const started = (g.status || "").toLowerCase() === "started";
                return `<div class="dr-array">
                    <h4>${esc(a.role_label.toUpperCase())} &bull; ${esc(a.host)}</h4>
                    <div class="dr-array-meta">
                        ${roleBadge(g.role)}
                        <span class="dr-badge ${started ? "green" : "grey"}">${esc(g.status)}</span>
                        <span class="dr-badge grey">${esc(g.mode)}</span>
                    </div>
                    <p class="dr-group-name">${esc(g.name)}</p>
                    <table class="dr-vols"><tr><th>Local</th><th>Remote</th><th>Sync</th></tr>${vols}</table>
                </div>`;
            })
            .join("");
        $("drStatus").innerHTML = `<div class="dr-arrays">${cards || "<p class='dr-error'>No arrays reported.</p>"}</div>`;
        setButtonsState(failedOver);
    }

    function setButtonsState(failedOver) {
        $("btnFailover").disabled = jobRunning || failedOver;
        $("btnFailback").disabled = jobRunning || !failedOver;
        ["btnStart", "btnStop", "btnSync"].forEach((id) => ($(id).disabled = jobRunning));
    }

    async function loadStatus() {
        if (jobRunning) return;
        try {
            const data = await window.api.get("/dr/status");
            renderStatus(data);
        } catch (err) {
            $("drStatus").innerHTML = `<p class="dr-error">DR status unavailable: ${esc(err.message)}</p>`;
        }
    }

    // ---- Running a job -----------------------------------------------------
    function showJob(op, dryRun) {
        $("drJob").hidden = false;
        $("drJobTitle").textContent = `${op.toUpperCase()}${dryRun ? " (dry-run preview)" : ""}`;
        $("drJobState").textContent = "running";
        $("drJobState").className = "dr-job-state running";
        $("drSteps").innerHTML = "";
    }

    function stepIcon(ok) {
        return ok
            ? `<i class="fa-solid fa-circle-check dr-ok"></i>`
            : `<i class="fa-solid fa-circle-xmark dr-fail"></i>`;
    }

    function renderSteps(job) {
        $("drJobState").textContent = job.state;
        $("drJobState").className = `dr-job-state ${job.state}`;
        const rows = (job.steps || [])
            .map(
                (s) => `
            <div class="dr-step ${s.ok ? "ok" : "fail"}">
                <div class="dr-step-head">${stepIcon(s.ok)} <span class="dr-step-name">${esc(s.name)}</span></div>
                ${s.detail ? `<div class="dr-step-detail">${esc(s.detail)}</div>` : ""}
                ${s.command && s.command !== "showrcopy" ? `<div class="dr-step-cmd">${esc(s.command)}</div>` : ""}
            </div>`
            )
            .join("");
        const spinner =
            job.state === "running" || job.state === "pending"
                ? `<div class="dr-step running"><i class="fa-solid fa-spinner fa-spin"></i> working&hellip;</div>`
                : "";
        $("drSteps").innerHTML = rows + spinner;
    }

    async function pollJob(jobId) {
        try {
            const job = await window.api.get(`/dr/jobs/${jobId}`);
            renderSteps(job);
            if (job.state === "running" || job.state === "pending") {
                pollTimer = setTimeout(() => pollJob(jobId), 1000);
            } else {
                jobRunning = false;
                loadStatus();
            }
        } catch (err) {
            jobRunning = false;
            loadStatus();
        }
    }

    async function runOp(op, dryRun) {
        try {
            jobRunning = true;
            setButtonsState(true);
            const res = await window.api.post(`/dr/${op}`, { dry_run: dryRun });
            showJob(op, dryRun);
            pollJob(res.job_id);
        } catch (err) {
            jobRunning = false;
            loadStatus();
            alert(err.message || "Failed to start operation");
        }
    }

    // ---- Confirmation modal (execute only) ---------------------------------
    function modalBody(op) {
        if (op === "failover")
            return "Stops the primary group, then promotes the DR array to Read/Write. No health check is performed on the primary.";
        if (op === "failback")
            return "Runs recover \u2192 sync \u2192 restore on the DR array, returning to the original direction (Primary R/W, DR Read-Only).";
        return `Runs ${op} on the replication group and verifies the result.`;
    }

    function openModal(op) {
        pendingOp = op;
        const isFailover = op === "failover";
        $("drModalTitle").textContent = `Confirm ${op.toUpperCase()}`;
        $("drModalBody").textContent = modalBody(op);
        $("drAckWrap").hidden = !isFailover;
        $("drAck").checked = false;
        $("drTypeWord").textContent = op;
        $("drTypeInput").value = "";
        $("drConfirm").disabled = true;
        $("drModal").hidden = false;
    }

    function closeModal() {
        $("drModal").hidden = true;
        pendingOp = null;
    }

    function validateModal() {
        const typed = $("drTypeInput").value.trim() === pendingOp;
        const ackOk = $("drAckWrap").hidden || $("drAck").checked;
        $("drConfirm").disabled = !(typed && ackOk);
    }

    // ---- Wiring ------------------------------------------------------------
    const OP_FOR = {
        btnFailover: "failover",
        btnFailback: "failback",
        btnStart: "start",
        btnStop: "stop",
        btnSync: "sync",
    };

    function handleBtn(btnId) {
        const op = OP_FOR[btnId];
        if ($("drDryRun").checked) runOp(op, true);
        else openModal(op);
    }

    function init() {
        Object.keys(OP_FOR).forEach((id) => {
            const el = $(id);
            if (el) el.addEventListener("click", () => handleBtn(id));
        });
        $("drCancel").addEventListener("click", closeModal);
        $("drConfirm").addEventListener("click", () => {
            const op = pendingOp;
            closeModal();
            runOp(op, false);
        });
        $("drTypeInput").addEventListener("input", validateModal);
        $("drAck").addEventListener("change", validateModal);

        // Sidebar Failover/Failback/Replication items scroll to the DR panel.
        document.querySelectorAll(".sidebar li").forEach((li) => {
            const t = li.textContent.trim().toLowerCase();
            if (["failover", "failback", "replication"].includes(t)) {
                li.addEventListener("click", () =>
                    $("drPanel").scrollIntoView({ behavior: "smooth" })
                );
            }
        });

        loadStatus();
        statusTimer = setInterval(loadStatus, 30000);
    }

    if (document.readyState === "loading")
        document.addEventListener("DOMContentLoaded", init);
    else init();
})();
