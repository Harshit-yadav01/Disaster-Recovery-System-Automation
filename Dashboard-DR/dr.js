// ===========================================================================
// Disaster Recovery controller (tab views)
//
// Tabs: Dashboard | Replication | Failover | Failback
//   - Replication : shows replication is active + live per-array status
//   - Failover    : Execute Failover button, staged process, live array status
//   - Failback    : Execute Failback button, staged process, live array status
//
// The per-array status mirrors `dr_ctl.py status` (system, group, role, status,
// mode, sync per volume) so promotion is visible. No CLI console.
// ===========================================================================
(function () {
    if (!window.api || !window.api.isAuthenticated()) return;

    const $ = (id) => document.getElementById(id);
    const esc = (s) =>
        String(s ?? "").replace(/[&<>"']/g, (c) =>
            ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
        );

    let latestStatus = null;
    let jobRunning = false;
    let pendingOp = null;
    let statusTimer = null;

    const CONT = {
        failover: { stages: "foStages", status: "foStatus", dry: "foDry" },
        failback: { stages: "fbStages", status: "fbStatus", dry: "fbDry" },
    };

    // ---- Badges ------------------------------------------------------------
    function roleClass(c) {
        return c.is_primary ? "green" : c.is_secondary ? "blue" : "grey";
    }
    function syncBadge(s) {
        const v = (s || "").toLowerCase();
        let cls = "grey";
        if (v === "synced") cls = "green";
        else if (v === "syncing" || v.startsWith("new")) cls = "amber";
        return `<span class="sync-badge ${cls}">${esc(s)}</span>`;
    }

    // ---- Per-array status card (dr_ctl.py status equivalent) ---------------
    function statCard(c) {
        if (!c) return "";
        const nodeCls = c.is_primary ? "source" : c.is_secondary ? "target" : "";
        const vols = (c.volumes || [])
            .map(
                (v) =>
                    `<tr><td>${esc(v.local_vv)} &rarr; ${esc(v.remote_vv)}</td><td>${syncBadge(v.sync_status)}</td></tr>`
            )
            .join("");
        return `<div class="stat-card ${nodeCls}">
            <div class="stat-head"><span class="stat-site">${esc(c.label)}</span><span class="stat-host">${esc(c.host)}</span></div>
            ${c.system_status ? `<div class="stat-line">System: <b>${esc(c.system_status)}</b></div>` : ""}
            <div class="stat-line">Group: <b>${esc(c.name || "-")}</b></div>
            <div class="stat-line">Role: <span class="role-badge ${roleClass(c)}">${esc(c.role || "-")}</span>
                &nbsp; Status: <b>${esc(c.status || "-")}</b>${c.mode ? ` &nbsp; Mode: ${esc(c.mode)}` : ""}</div>
            <div class="stat-line">All Synced: <b>${c.synced ? "yes" : "no"}</b>${c.volumes ? ` (${c.volumes.length} volume${c.volumes.length === 1 ? "" : "s"})` : ""}</div>
            ${vols ? `<table class="stat-vols">${vols}</table>` : ""}
        </div>`;
    }

    function cardsFromStatus(data) {
        const out = {};
        (data.arrays || []).forEach((a) => {
            const g = a.group || {};
            out[a.role_label] = {
                label: a.role_label === "primary" ? "PRIMARY SITE" : "DR SITE",
                host: a.host,
                system_status: a.system_status,
                name: g.name,
                role: g.role,
                status: g.status,
                mode: g.mode,
                synced: !!g.all_synced,
                is_primary: !!g.is_primary,
                is_secondary: !!g.is_secondary,
                volumes: g.volumes || [],
            };
        });
        return out;
    }

    function cardsFromSnapshot(snap) {
        const mk = (label, s) =>
            !s || !s.role
                ? null
                : {
                      label, host: s.host, name: s.name, role: s.role, status: s.status,
                      mode: null, system_status: null, synced: !!s.synced,
                      is_primary: !!s.is_primary, is_secondary: !!s.is_secondary, volumes: null,
                  };
        return { primary: mk("PRIMARY SITE", snap.primary), recovery: mk("DR SITE", snap.dr) };
    }

    function renderStatusInto(containerId, cards) {
        const el = $(containerId);
        if (!el) return;
        const html = (cards.primary ? statCard(cards.primary) : "") + (cards.recovery ? statCard(cards.recovery) : "");
        el.innerHTML = html || `<p class="dr-error">No array data available.</p>`;
    }

    function renderBanner(cards) {
        const b = $("repBanner");
        if (!b) return;
        const p = cards.primary, d = cards.recovery;
        if (p && d && p.is_primary && d.is_secondary) {
            b.className = "rep-banner active";
            b.innerHTML = `<i class="fa-solid fa-circle-check"></i> Replication ACTIVE &mdash; ${esc(p.host)} (Primary) &rarr; ${esc(d.host)} (Secondary), ${p.synced && d.synced ? "in sync" : "syncing"}.`;
        } else if (d && d.is_primary) {
            b.className = "rep-banner failed";
            b.innerHTML = `<i class="fa-solid fa-triangle-exclamation"></i> Failed over &mdash; DR ${esc(d.host)} is currently Primary (R/W). Run Failback to return to normal.`;
        } else {
            b.className = "rep-banner down";
            b.innerHTML = `<i class="fa-solid fa-plug-circle-xmark"></i> Replication not active / arrays unreachable.`;
        }
    }

    async function loadStatus() {
        try {
            latestStatus = await window.api.get("/dr/status");
            const cards = cardsFromStatus(latestStatus);
            renderBanner(cards);
            ["repStatus", "foStatus", "fbStatus"].forEach((id) => renderStatusInto(id, cards));
        } catch (err) {
            const b = $("repBanner");
            if (b) { b.className = "rep-banner down"; b.innerHTML = `<i class="fa-solid fa-plug-circle-xmark"></i> Status unavailable: ${esc(err.message)}`; }
            ["repStatus", "foStatus", "fbStatus"].forEach((id) => {
                const el = $(id); if (el) el.innerHTML = `<p class="dr-error">Status unavailable: ${esc(err.message)}</p>`;
            });
        }
    }

    // ---- Staged process view ----------------------------------------------
    const STAGES = {
        failover: [
            { action: "stop primary", verify: "verify stop", label: "Stop replication on Primary", desc: "Quiesce the primary group before promotion" },
            { action: "failover DR", verify: "verify failover", label: "Promote DR to Primary (R/W)", desc: "Fail over the DR group and verify the role change" },
        ],
        failback: [
            { action: "recover", verify: "verify recover", label: "Reverse replication", desc: "DR becomes source; original Primary becomes target" },
            { action: "sync", verify: "verify sync", label: "Synchronize DR \u2192 Primary", desc: "Copy DR changes back to the original Primary" },
            { action: "restore", verify: "verify restore", label: "Restore original direction", desc: "Primary back to R/W, DR back to Read-Only" },
        ],
    };

    function renderStages(containerId, job) {
        const stages = STAGES[job.kind] || [];
        const byName = {};
        (job.steps || []).forEach((s) => (byName[s.name] = s));
        let activeSet = false;
        const rows = stages
            .map((st, i) => {
                const v = byName[st.verify], a = byName[st.action];
                let cls = "pending", state = "Pending";
                if (v && v.ok) { cls = "done"; state = "Done"; }
                else if ((v && !v.ok) || (a && !a.ok)) { cls = "failed"; state = "Failed"; }
                else if (!activeSet && (job.state === "running" || job.state === "pending")) { cls = "active"; state = "In progress"; activeSet = true; }
                const inner = cls === "done" ? `<i class="fa-solid fa-check"></i>`
                    : cls === "failed" ? `<i class="fa-solid fa-xmark"></i>`
                    : cls === "active" ? `<i class="fa-solid fa-spinner fa-spin"></i>`
                    : (i + 1);
                return `<div class="stage ${cls}">
                    <div class="stage-num">${inner}</div>
                    <div class="stage-body"><h4>${esc(st.label)}</h4><p>${esc(st.desc)}</p></div>
                    <span class="stage-state">${state}</span>
                </div>`;
            })
            .join("");

        let notes = "";
        if (job.dry_run) notes += `<div class="stage-note info"><i class="fa-solid fa-eye"></i> Preview only &mdash; no changes applied.</div>`;
        const pre = (job.steps || []).find((s) => s.name === "precondition" && !s.ok);
        if (pre) notes += `<div class="stage-note warn"><i class="fa-solid fa-triangle-exclamation"></i> ${esc(pre.detail)}</div>`;
        if (!job.dry_run && (job.state === "succeeded" || job.state === "failed")) {
            const ok = job.state === "succeeded";
            notes += `<div class="stage-note ${ok ? "ok" : "warn"}"><i class="fa-solid ${ok ? "fa-circle-check" : "fa-triangle-exclamation"}"></i> ${esc(job.kind)} ${ok ? "completed successfully." : "did not complete."}</div>`;
        }
        $(containerId).innerHTML = notes + rows;
    }

    // ---- Run + poll --------------------------------------------------------
    async function pollJob(jobId, cont) {
        try {
            const job = await window.api.get(`/dr/jobs/${jobId}`);
            renderStages(cont.stages, job);
            const snaps = (job.steps || []).filter((s) => s.snapshot);
            if (snaps.length) renderStatusInto(cont.status, cardsFromSnapshot(snaps[snaps.length - 1].snapshot));
            if (job.state === "running" || job.state === "pending") {
                setTimeout(() => pollJob(jobId, cont), 1000);
            } else {
                jobRunning = false;
                setTimeout(loadStatus, 600);
            }
        } catch (err) {
            jobRunning = false;
        }
    }

    async function runOp(op, dryRun) {
        const cont = CONT[op];
        try {
            jobRunning = true;
            $(cont.stages).innerHTML =
                `<div class="stage active"><div class="stage-num"><i class="fa-solid fa-spinner fa-spin"></i></div>
                 <div class="stage-body"><h4>Starting ${esc(op)}${dryRun ? " (preview)" : ""}&hellip;</h4></div></div>`;
            const res = await window.api.post(`/dr/${op}`, { dry_run: dryRun });
            pollJob(res.job_id, cont);
        } catch (err) {
            jobRunning = false;
            $(cont.stages).innerHTML =
                `<div class="stage failed"><div class="stage-num"><i class="fa-solid fa-xmark"></i></div>
                 <div class="stage-body"><h4>Could not start ${esc(op)}</h4><p>${esc(err.message)}</p></div></div>`;
        }
    }

    // ---- Confirmation modal (execute only) ---------------------------------
    function modalBody(op) {
        if (op === "failover")
            return "Stops the primary group, then promotes the DR array to Read/Write. No health check is performed on the primary.";
        if (op === "failback")
            return "Runs recover \u2192 sync \u2192 restore on the DR array, returning to the original direction (Primary R/W, DR Read-Only).";
        return `Runs ${op} and verifies the result.`;
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

    function onExec(op, dryId) {
        if ($(dryId).checked) runOp(op, true);
        else openModal(op);
    }

    // ---- Tab navigation ----------------------------------------------------
    const VIEW_FOR = {
        dashboard: "dashboard", replication: "replication",
        failover: "failover", failback: "failback",
        monitoring: "placeholder", reports: "placeholder", settings: "placeholder",
    };
    function showView(name) {
        document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
        const el = $("view-" + name);
        if (el) el.classList.add("active");
    }

    function init() {
        const items = document.querySelectorAll(".sidebar li");
        items.forEach((li) => {
            const t = li.textContent.trim().toLowerCase();
            li.addEventListener("click", () => {
                items.forEach((x) => x.classList.remove("active"));
                li.classList.add("active");
                const view = VIEW_FOR[t] || "dashboard";
                if (view === "placeholder") $("phTitle").textContent = li.textContent.trim();
                showView(view);
                window.scrollTo({ top: 0, behavior: "smooth" });
                if (["replication", "failover", "failback"].includes(view)) loadStatus();
            });
        });

        const bf = $("btnFailover"); if (bf) bf.addEventListener("click", () => onExec("failover", "foDry"));
        const bb = $("btnFailback"); if (bb) bb.addEventListener("click", () => onExec("failback", "fbDry"));
        const rr = $("repRefresh"); if (rr) rr.addEventListener("click", loadStatus);

        $("drCancel").addEventListener("click", closeModal);
        $("drConfirm").addEventListener("click", () => { const op = pendingOp; closeModal(); runOp(op, false); });
        $("drTypeInput").addEventListener("input", validateModal);
        $("drAck").addEventListener("change", validateModal);

        loadStatus();
        statusTimer = setInterval(() => { if (!jobRunning) loadStatus(); }, 30000);
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
})();
