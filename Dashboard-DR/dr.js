// ===========================================================================
// Disaster Recovery panel controller (visualization edition)
//
// - Live site topology map (Primary <-> DR) with reversing replication arrow
// - Step rail, live "array console", verification steps, before/after proof
// - Operation history (in-memory on the server)
//
// Data sources:
//   GET  /api/dr/status            -> live topology when idle
//   POST /api/dr/{op} {dry_run}    -> starts a background job, returns job_id
//   GET  /api/dr/jobs/{id}         -> live steps + both-array snapshots
//   GET  /api/dr/jobs             -> recent operation history
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

    // Verify-step milestones that drive the stepper, per operation.
    const PHASES = {
        failover: [
            { v: "verify stop", label: "Primary Stopped", icon: "fa-circle-stop" },
            { v: "verify failover", label: "DR Promoted", icon: "fa-arrow-up-from-bracket" },
        ],
        failback: [
            { v: "verify recover", label: "Reversed", icon: "fa-arrows-rotate" },
            { v: "verify sync", label: "Synced", icon: "fa-rotate" },
            { v: "verify restore", label: "Restored", icon: "fa-check-double" },
        ],
        start: [{ v: "verify start", label: "Started", icon: "fa-play" }],
        stop: [{ v: "verify stop", label: "Stopped", icon: "fa-circle-stop" }],
        sync: [{ v: "verify sync", label: "Synced", icon: "fa-rotate" }],
    };

    // ---- Topology ----------------------------------------------------------
    function nodeFromStatus(a) {
        const g = a.group || {};
        return {
            host: a.host,
            name: g.name || "-",
            role: g.role || "-",
            status: g.status || "-",
            synced: !!g.all_synced,
            is_primary: !!g.is_primary,
            is_secondary: !!g.is_secondary,
            present: !!a.group,
        };
    }

    function nodeFromSnap(s) {
        if (!s) return null;
        return {
            host: s.host || "-",
            name: s.name || "-",
            role: s.role || "-",
            status: s.status || "-",
            synced: !!s.synced,
            is_primary: !!s.is_primary,
            is_secondary: !!s.is_secondary,
            present: !!s.role,
        };
    }

    function nodeCard(siteLabel, n) {
        if (!n || !n.present) {
            return `<div class="dr-node down">
                <div class="node-site">${siteLabel}</div>
                <div class="node-host">${esc(n ? n.host : "-")}</div>
                <div class="node-icon"><i class="fa-solid fa-server"></i></div>
                <div class="node-name">unreachable / not found</div>
            </div>`;
        }
        let roleCls = "grey", roleText = "\u2014", nodeCls = "";
        if (n.is_primary) { roleCls = "green"; roleText = "SOURCE \u2022 R/W"; nodeCls = "source"; }
        else if (n.is_secondary) { roleCls = "blue"; roleText = "TARGET \u2022 Read-Only"; nodeCls = "target"; }
        const down = (n.status || "").toLowerCase() !== "started";
        return `<div class="dr-node ${nodeCls} ${down ? "down" : ""}">
            <div class="node-site">${siteLabel}</div>
            <div class="node-host">${esc(n.host)}</div>
            <div class="node-icon"><i class="fa-solid fa-server"></i></div>
            <div class="node-name">${esc(n.name)}</div>
            <div class="node-role ${roleCls}">${roleText}</div>
            <div class="node-status">${esc(n.status)} ${n.synced ? "\u2022 Synced" : ""}</div>
        </div>`;
    }

    function linkMarkup(p, d) {
        let cls = "broken", label = "Remote Copy";
        const bothUp = p && d && p.present && d.present;
        if (bothUp && p.is_primary && d.is_secondary) { cls = "flow-right"; label = "Replicating \u2192"; }
        else if (bothUp && d.is_primary && p.is_secondary) { cls = "flow-left"; label = "Reversed \u2190"; }
        else { cls = "broken"; label = "Link stopped"; }
        const synced = bothUp && p.synced && d.synced;
        if (cls !== "broken" && !synced) label = label.replace("Replicating", "Syncing");
        return `<div class="dr-link ${cls}">
            <div class="link-label">${label}</div>
            <div class="link-track"><div class="link-flow"></div></div>
            <div class="link-arrow"><i class="fa-solid fa-arrow-right-long"></i></div>
        </div>`;
    }

    function renderTopology(primary, dr) {
        $("drTopology").innerHTML =
            nodeCard("PRIMARY SITE", primary) + linkMarkup(primary, dr) + nodeCard("DR SITE", dr);
        // Failed-over = the DR array currently holds the group as primary.
        const failedOver = !!(dr && dr.present && dr.is_primary);
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
            const byRole = {};
            (data.arrays || []).forEach((a) => (byRole[a.role_label] = a));
            const p = byRole.primary ? nodeFromStatus(byRole.primary) : null;
            const d = byRole.recovery ? nodeFromStatus(byRole.recovery) : null;
            renderTopology(p, d);
        } catch (err) {
            $("drTopology").innerHTML = `<p class="dr-error">DR status unavailable: ${esc(err.message)}</p>`;
        }
    }

    // ---- Stepper -----------------------------------------------------------
    function renderStepper(job) {
        const phases = PHASES[job.kind] || [];
        const byName = {};
        (job.steps || []).forEach((s) => (byName[s.name] = s));
        let activeAssigned = false;
        const html = phases
            .map((ph, i) => {
                const st = byName[ph.v];
                let cls = "pending", icon = ph.icon;
                if (st && st.ok) { cls = "done"; icon = "fa-check"; }
                else if (st && !st.ok) { cls = "failed"; icon = "fa-xmark"; }
                else if (!activeAssigned && (job.state === "running" || job.state === "pending")) {
                    cls = "active"; activeAssigned = true;
                }
                const conn = i < phases.length - 1
                    ? `<div class="phase-conn ${cls === "done" ? "done" : ""}"></div>` : "";
                return `<div class="dr-phase ${cls}">
                    <div class="phase-dot"><i class="fa-solid ${icon}"></i></div>
                    <div class="phase-label">${esc(ph.label)}</div>
                </div>${conn}`;
            })
            .join("");
        $("drStepper").innerHTML = html;
    }

    // ---- Console -----------------------------------------------------------
    function renderConsole(job) {
        const body = $("drConsoleBody");
        const lines = (job.steps || [])
            .map((s) => {
                if (s.command && s.command !== "showrcopy" && s.command !== "") {
                    const out = s.detail ? `<div class="c-out">${esc(s.detail)}</div>` : "";
                    return `<div class="c-line"><span class="c-prompt">cli%</span> <span class="c-cmd">${esc(s.command)}</span></div>${out}`;
                }
                if (s.command === "showrcopy") {
                    return `<div class="c-verify">&gt; showrcopy &mdash; ${esc(s.detail)}</div>`;
                }
                return "";
            })
            .join("");
        const spinner =
            job.state === "running" || job.state === "pending"
                ? `<div class="c-line"><span class="c-prompt">cli%</span> <i class="fa-solid fa-spinner fa-spin"></i></div>`
                : "";
        body.innerHTML = lines + spinner;
        body.scrollTop = body.scrollHeight;
    }

    // ---- Verification steps ------------------------------------------------
    function renderSteps(job) {
        $("drJobState").textContent = job.state;
        $("drJobState").className = `dr-job-state ${job.state}`;
        const rows = (job.steps || [])
            .map(
                (s) => `
            <div class="dr-step ${s.ok ? "ok" : "fail"}">
                <div class="dr-step-head">
                    <i class="fa-solid ${s.ok ? "fa-circle-check dr-ok" : "fa-circle-xmark dr-fail"}"></i>
                    <span class="dr-step-name">${esc(s.name)}</span>
                </div>
                ${s.detail ? `<div class="dr-step-detail">${esc(s.detail)}</div>` : ""}
            </div>`
            )
            .join("");
        $("drSteps").innerHTML = rows;
    }

    // ---- Topology from the latest snapshot in a running/finished job --------
    function topologyFromJob(job) {
        const withSnap = (job.steps || []).filter((s) => s.snapshot);
        if (!withSnap.length) return;
        const snap = withSnap[withSnap.length - 1].snapshot;
        renderTopology(nodeFromSnap(snap.primary), nodeFromSnap(snap.dr));
    }

    // ---- Before / after proof ---------------------------------------------
    function renderProof(job) {
        const proof = $("drProof");
        if (job.dry_run) {
            proof.hidden = false;
            proof.className = "dr-proof";
            proof.innerHTML = `<div class="proof-head"><i class="fa-solid fa-eye"></i> Preview only &mdash; no changes were applied.</div>`;
            return;
        }
        const snaps = (job.steps || []).filter((s) => s.snapshot).map((s) => s.snapshot);
        if (snaps.length < 2) { proof.hidden = true; return; }
        const before = snaps[0], after = snaps[snaps.length - 1];
        const ok = job.state === "succeeded";
        const headline = {
            failover: ok ? "Failover verified \u2014 DR promoted to Read/Write" : "Failover did not fully complete",
            failback: ok ? "Failback verified \u2014 replication restored to original direction" : "Failback did not fully complete",
        }[job.kind] || (ok ? "Operation verified" : "Operation incomplete");

        const roleTxt = (n) => !n ? "\u2014" : (n.is_primary ? "R/W (Source)" : n.is_secondary ? "Read-Only (Target)" : (n.role || "\u2014"));
        const col = (label, snap) => `
            <div class="proof-col">
                <span class="proof-label">${label}</span>
                <div class="proof-row">Primary: <b>${esc(roleTxt(snap.primary))}</b> (${esc((snap.primary && snap.primary.status) || "-")})</div>
                <div class="proof-row">DR: <b>${esc(roleTxt(snap.dr))}</b> (${esc((snap.dr && snap.dr.status) || "-")})</div>
            </div>`;
        proof.hidden = false;
        proof.className = `dr-proof ${ok ? "" : "fail"}`;
        proof.innerHTML = `
            <div class="proof-head"><i class="fa-solid ${ok ? "fa-shield-halved" : "fa-triangle-exclamation"}"></i> ${esc(headline)}</div>
            <div class="proof-grid">
                ${col("Before", before)}
                <div class="proof-arrow"><i class="fa-solid fa-arrow-right-long"></i></div>
                ${col("After", after)}
            </div>`;
    }

    // ---- Job lifecycle -----------------------------------------------------
    function showJob(op, dryRun) {
        $("drJob").hidden = false;
        $("drProof").hidden = true;
        $("drJobTitle").textContent = `${op.toUpperCase()}${dryRun ? " (dry-run preview)" : ""}`;
        $("drJobState").textContent = "running";
        $("drJobState").className = "dr-job-state running";
        $("drStepper").innerHTML = "";
        $("drConsoleBody").innerHTML = "";
        $("drSteps").innerHTML = "";
    }

    function renderJob(job) {
        renderStepper(job);
        renderConsole(job);
        renderSteps(job);
        topologyFromJob(job);
    }

    async function pollJob(jobId) {
        try {
            const job = await window.api.get(`/dr/jobs/${jobId}`);
            renderJob(job);
            if (job.state === "running" || job.state === "pending") {
                pollTimer = setTimeout(() => pollJob(jobId), 1000);
            } else {
                jobRunning = false;
                renderProof(job);
                loadHistory();
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

    // ---- History -----------------------------------------------------------
    async function loadHistory() {
        try {
            const data = await window.api.get("/dr/jobs?limit=12");
            const jobs = data.jobs || [];
            if (!jobs.length) {
                $("drHistory").innerHTML = `<p class="dr-loading">No operations yet.</p>`;
                return;
            }
            $("drHistory").innerHTML = jobs
                .map((j) => {
                    const t = j.created_at ? new Date(j.created_at).toLocaleString() : "";
                    return `<div class="hist-row" data-id="${esc(j.id)}">
                        <div class="hist-main">
                            <span class="hist-time">${esc(t)}</span>
                            <span class="hist-kind ${esc(j.kind)}">${esc(j.kind)}</span>
                            ${j.dry_run ? `<span class="hist-dry">dry-run</span>` : ""}
                            <span class="hist-state ${esc(j.state)}">${esc(j.state)}</span>
                        </div>
                        <div class="hist-detail" hidden></div>
                    </div>`;
                })
                .join("");
            document.querySelectorAll("#drHistory .hist-row").forEach((row) => {
                row.addEventListener("click", () => toggleHistory(row));
            });
        } catch (err) {
            $("drHistory").innerHTML = `<p class="dr-error">History unavailable: ${esc(err.message)}</p>`;
        }
    }

    async function toggleHistory(row) {
        const detail = row.querySelector(".hist-detail");
        if (!detail.hidden) { detail.hidden = true; detail.innerHTML = ""; return; }
        try {
            const job = await window.api.get(`/dr/jobs/${row.dataset.id}`);
            detail.innerHTML = (job.steps || [])
                .map(
                    (s) => `<div class="dr-step ${s.ok ? "ok" : "fail"}">
                        <div class="dr-step-head"><i class="fa-solid ${s.ok ? "fa-circle-check dr-ok" : "fa-circle-xmark dr-fail"}"></i>
                        <span class="dr-step-name">${esc(s.name)}</span></div>
                        ${s.detail ? `<div class="dr-step-detail">${esc(s.detail)}</div>` : ""}
                        ${s.command && s.command !== "showrcopy" ? `<div class="dr-step-cmd">${esc(s.command)}</div>` : ""}
                    </div>`
                )
                .join("");
            detail.hidden = false;
        } catch (err) {
            detail.innerHTML = `<p class="dr-error">${esc(err.message)}</p>`;
            detail.hidden = false;
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

    function closeModal() { $("drModal").hidden = true; pendingOp = null; }

    function validateModal() {
        const typed = $("drTypeInput").value.trim() === pendingOp;
        const ackOk = $("drAckWrap").hidden || $("drAck").checked;
        $("drConfirm").disabled = !(typed && ackOk);
    }

    // ---- Wiring ------------------------------------------------------------
    const OP_FOR = {
        btnFailover: "failover", btnFailback: "failback",
        btnStart: "start", btnStop: "stop", btnSync: "sync",
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
        const hr = $("drHistoryRefresh");
        if (hr) hr.addEventListener("click", loadHistory);

        document.querySelectorAll(".sidebar li").forEach((li) => {
            const t = li.textContent.trim().toLowerCase();
            if (["failover", "failback", "replication"].includes(t)) {
                li.addEventListener("click", () => $("drPanel").scrollIntoView({ behavior: "smooth" }));
            }
        });

        loadStatus();
        loadHistory();
        statusTimer = setInterval(loadStatus, 30000);
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
})();
