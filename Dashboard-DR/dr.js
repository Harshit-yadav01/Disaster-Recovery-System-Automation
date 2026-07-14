// ===========================================================================
// Disaster Recovery controller (tab views)
//
// Tabs: Dashboard | Replication | DR Operations | Monitoring | Reports | Settings
//   - Replication   : shows replication is active + live per-array status
//   - DR Operations : one state-driven flow panel. From the live /dr/status the
//                     valid next action is enabled (Failover -> Reverse Sync ->
//                     Restore); a plain-English banner says where you are.
//                     Each action runs as a background job with staged progress
//                     and a live topology snapshot. Dry-run + typed confirm.
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
    let monInterval = null;
    let monSyncChart = null;
    const monHistory = [];

    // Single container for the unified DR Operations panel. The backend allows
    // only one DR job at a time, so all three actions share one stages/status area.
    const DROP = { stages: "dropStages", status: "dropStatus" };

    // Friendly display names for the internal op keys used by the API/backend.
    const OP_LABEL = { failover: "Failover", revert: "Revert Failover", recover: "Reverse Sync", restore: "Restore", failback: "Failback" };

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
        const volCount = (c.volumes || []).length;
        const vols = (c.volumes || [])
            .map(
                (v) =>
                    `<tr><td>${esc(v.local_vv)} &rarr; ${esc(v.remote_vv)}</td><td>${syncBadge(v.sync_status)}</td></tr>`
            )
            .join("");
        const volSection = vols
            ? `<div class="stat-vols-toggle" onclick="this.classList.toggle('open');this.nextElementSibling.hidden=!this.nextElementSibling.hidden">
                 <span><i class="fa-solid fa-layer-group"></i>&nbsp; Volumes (${volCount})</span>
                 <i class="fa-solid fa-chevron-down"></i>
               </div>
               <div class="stat-vols-wrap" hidden><table class="stat-vols">${vols}</table></div>`
            : "";
        return `<div class="stat-card ${nodeCls}">
            <div class="stat-head"><span class="stat-site">${esc(c.label)}</span><span class="stat-host">${esc(c.host)}</span></div>
            ${c.system_status ? `<div class="stat-line">System: <b>${esc(c.system_status)}</b></div>` : ""}
            <div class="stat-line">Group: <b>${esc(c.name || "-")}</b></div>
            <div class="stat-line">Role: <span class="role-badge ${roleClass(c)}">${esc(c.role || "-")}</span>
                &nbsp; Status: <b>${esc(c.status || "-")}</b>${c.mode ? ` &nbsp; Mode: ${esc(c.mode)}` : ""}</div>
            <div class="stat-line">All Synced: <b>${c.synced ? "yes" : "no"}</b>${c.volumes ? ` (${volCount} volume${volCount === 1 ? "" : "s"})` : ""}</div>
            ${volSection}
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

    // ---- DR Operations: live state machine + banner + button gating --------
    function drState(status) {
        const arrays = (status && status.arrays) || [];
        const p = arrays.find((a) => a.role_label === "primary") || null;
        const d = arrays.find((a) => a.role_label === "recovery") || null;
        const pg = (p && p.group) || null;
        const dg = (d && d.group) || null;
        let name = "unknown";
        if (dg && dg.is_primary) {
            // DR has taken over. Once the reverse replication is back in sync and
            // the original primary is Secondary again, Restore becomes valid.
            name = dg.all_synced && pg && pg.is_secondary ? "reverse-synced" : "failed-over";
        } else if (pg && pg.is_primary && (!dg || dg.is_secondary)) {
            name = "normal";
        }
        return { name, p, d, pg, dg };
    }

    function setDropButtons(canF, canRevert, canR, canS) {
        const running = jobRunning;
        const map = { failover: canF, revert: canRevert, recover: canR, restore: canS };
        [["btnFailover", canF], ["btnRevert", canRevert], ["btnRecover", canR], ["btnRestore", canS]].forEach(([id, on]) => {
            const b = $(id); if (b) b.disabled = !on || running;
        });
        document.querySelectorAll("#dropFlow .flow-step").forEach((el) => {
            el.classList.toggle("ready", !!map[el.dataset.op] && !running);
        });
    }

    function updateDrOps(status) {
        const banner = $("dropBanner");
        if (!banner) return;
        const s = drState(status);
        const pHost = s.p ? esc(s.p.host) : "primary";
        const dHost = s.d ? esc(s.d.host) : "DR";
        let cls = "info", icon = "fa-circle-info", msg = "";
        let canF = false, canRevert = false, canR = false, canS = false;
        switch (s.name) {
            case "normal": {
                const synced = s.pg && s.pg.all_synced;
                cls = "active"; icon = "fa-circle-check";
                msg = `Replication is active &mdash; Primary <b>${pHost}</b> &rarr; DR <b>${dHost}</b>, ${synced ? "in sync" : "syncing"}. You can start a <b>Failover</b> (step 1).`;
                canF = true; break;
            }
            case "failed-over": {
                cls = "warn"; icon = "fa-triangle-exclamation";
                msg = `Failed over &mdash; DR <b>${dHost}</b> is now Primary (R/W). Choose a path: <b>Revert Failover</b> (2a) to discard DR changes and undo, or <b>Reverse Sync</b> (2b) to keep them and copy back to <b>${pHost}</b>.`;
                canRevert = true; canR = true; break;
            }
            case "reverse-synced": {
                cls = "active"; icon = "fa-circle-check";
                msg = `Reverse sync complete &mdash; DR <b>${dHost}</b> changes are synced back to <b>${pHost}</b>. Run <b>Restore</b> (step 3) to return to normal.`;
                canR = true; canS = true; break;
            }
            default: {
                cls = "down"; icon = "fa-plug-circle-xmark";
                msg = `Array state unavailable or replication not active. Open the Replication tab, then refresh.`;
            }
        }
        banner.className = "dr-banner " + cls;
        banner.innerHTML = `<i class="fa-solid ${icon}"></i> <span>${msg}</span>`;
        setDropButtons(canF, canRevert, canR, canS);
    }

    async function loadStatus() {
        try {
            latestStatus = await window.api.get("/dr/status");
            const cards = cardsFromStatus(latestStatus);
            renderBanner(cards);
            ["repStatus", "dropStatus"].forEach((id) => renderStatusInto(id, cards));
            updateDrOps(latestStatus);
        } catch (err) {
            const b = $("repBanner");
            if (b) { b.className = "rep-banner down"; b.innerHTML = `<i class="fa-solid fa-plug-circle-xmark"></i> Status unavailable: ${esc(err.message)}`; }
            const db = $("dropBanner");
            if (db) { db.className = "dr-banner down"; db.innerHTML = `<i class="fa-solid fa-plug-circle-xmark"></i> Array state unavailable: ${esc(err.message)}`; }
            setDropButtons(false, false, false, false);
            ["repStatus", "dropStatus"].forEach((id) => {
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
        revert: [
            { action: "reverse", verify: "verify revert", label: "Reverse & discard DR writes", desc: "Revert the failover; DR volumes' writes since failover are discarded" },
        ],
        recover: [
            { action: "recover", verify: "verify recover", label: "Reverse replication", desc: "DR becomes source; original Primary becomes target" },
            { action: "sync", verify: "verify sync", label: "Synchronize DR \u2192 Primary", desc: "Copy DR changes back to the original Primary" },
        ],
        restore: [
            { action: "restore", verify: "verify restore", label: "Restore original direction", desc: "Primary back to R/W, DR back to Read-Only" },
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
            notes += `<div class="stage-note ${ok ? "ok" : "warn"}"><i class="fa-solid ${ok ? "fa-circle-check" : "fa-triangle-exclamation"}"></i> ${esc(OP_LABEL[job.kind] || job.kind)} ${ok ? "completed successfully." : "did not complete."}</div>`;
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
                if (!job.dry_run && window._drPushNotif) {
                    const ok = job.state === "succeeded";
                    const label = OP_LABEL[job.kind] || job.kind;
                    window._drPushNotif(
                        `${label} ${ok ? "succeeded" : "failed"}`,
                        ok ? `${label} completed successfully.` : `${label} did not complete — check the steps.`,
                        ok ? "ok" : "error"
                    );
                }
                setTimeout(loadStatus, 600);
            }
        } catch (err) {
            jobRunning = false;
        }
    }

    async function runOp(op, dryRun) {
        const cont = DROP;
        const label = OP_LABEL[op] || op;
        try {
            jobRunning = true;
            setDropButtons(false, false, false, false);
            $(cont.stages).innerHTML =
                `<div class="stage active"><div class="stage-num"><i class="fa-solid fa-spinner fa-spin"></i></div>
                 <div class="stage-body"><h4>Starting ${esc(label)}${dryRun ? " (preview)" : ""}&hellip;</h4></div></div>`;
            const res = await window.api.post(`/dr/${op}`, { dry_run: dryRun });
            pollJob(res.job_id, cont);
        } catch (err) {
            jobRunning = false;
            $(cont.stages).innerHTML =
                `<div class="stage failed"><div class="stage-num"><i class="fa-solid fa-xmark"></i></div>
                 <div class="stage-body"><h4>Could not start ${esc(label)}</h4><p>${esc(err.message)}</p></div></div>`;
            loadStatus();
        }
    }

    // ---- Confirmation modal (execute only) ---------------------------------
    function modalBody(op) {
        if (op === "failover")
            return "Stops the primary group, then promotes the DR array to Read/Write. No health check is performed on the primary.";
        if (op === "revert")
            return "Reverts the failover on the DR array (setrcopygroup reverse -local -current). This DISCARDS any data written to the DR volumes since the failover and returns to the original Primary. This cannot be undone.";
        if (op === "recover")
            return "Reverses replication (recover \u2192 sync): the DR array becomes the source and copies its changes back to the original Primary, waiting until fully synced.";
        if (op === "restore")
            return "Returns the group to its natural direction (Primary R/W, DR Read-Only). Run only after Reverse Sync has completed.";
        return `Runs ${op} and verifies the result.`;
    }
    function openModal(op) {
        pendingOp = op;
        const needsAck = op === "failover" || op === "revert";
        $("drModalTitle").textContent = `Confirm ${(OP_LABEL[op] || op).toUpperCase()}`;
        $("drModalBody").textContent = modalBody(op);
        $("drAckWrap").hidden = !needsAck;
        if (needsAck && $("drAckText")) {
            $("drAckText").textContent = op === "revert"
                ? "I understand DR changes since the failover will be permanently discarded."
                : "I confirm the Primary site is failed / inaccessible.";
        }
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

    // ---- Monitoring --------------------------------------------------------
    function syncedCount(g) {
        return g && g.volumes ? g.volumes.filter((v) => String(v.sync_status).toLowerCase() === "synced").length : 0;
    }
    function primaryGroup(drst) {
        const a = ((drst && drst.arrays) || []).find((x) => x.group && x.group.is_primary);
        return a ? a.group : null;
    }
    async function loadMonitoring() {
        let dash = null, drst = null, jobs = { jobs: [] };
        try { drst = await window.api.get("/dr/status"); } catch (e) {}
        try { dash = await window.api.get("/dashboard"); } catch (e) {}
        try { jobs = await window.api.get("/dr/jobs?limit=6"); } catch (e) {}
        renderMonTiles(dash, drst);
        renderMonVolumes(drst);
        updateSyncChart(drst);
        renderMonStorage(dash);
        renderMonEvents(jobs.jobs || []);
    }
    function renderMonTiles(dash, drst) {
        const g = primaryGroup(drst);
        const tot = g && g.volumes ? g.volumes.length : 0, syn = syncedCount(g);
        const failedOver = ((drst && drst.arrays) || []).some((a) => a.group && a.group.is_primary && String(a.group.role || "").toLowerCase().endsWith("-rev"));
        const readiness = dash && dash.readiness ? dash.readiness.percent + "%" : "-";
        const storage = dash && dash.cards ? (dash.cards.find((c) => /storage/i.test(c.title)) || {}).value : "-";
        const tiles = [
            { label: "Replication", value: failedOver ? "Failed over" : (syn === tot && tot > 0 ? "In sync" : "Attention") },
            { label: "Protected Volumes", value: `${syn}/${tot}` },
            { label: "DR Readiness", value: readiness },
            { label: "Storage Usage", value: storage || "-" },
        ];
        $("monTiles").innerHTML = tiles.map((t) => `<div class="mon-tile"><span class="mon-tile-label">${esc(t.label)}</span><span class="mon-tile-value">${esc(t.value)}</span></div>`).join("");
    }
    function renderMonVolumes(drst) {
        const g = primaryGroup(drst);
        if (!g || !g.volumes) { $("monVolumes").innerHTML = `<p class="dr-loading">No volume data.</p>`; return; }
        $("monVolumes").innerHTML = `<table class="stat-vols"><tr><th>Local</th><th>Remote</th><th>Sync</th></tr>` +
            g.volumes.map((v) => `<tr><td>${esc(v.local_vv)}</td><td>${esc(v.remote_vv)}</td><td>${syncBadge(v.sync_status)}</td></tr>`).join("") + `</table>`;
    }
    function renderMonStorage(dash) {
        const items = (dash && dash.storage) || [];
        if (!items.length) { $("monStorage").innerHTML = `<p class="dr-loading">No capacity data.</p>`; return; }
        $("monStorage").innerHTML = items.map((s) => `<div class="mon-bar"><div class="mon-bar-top"><span>${esc(s.label)}</span><span>${esc(s.detail)}</span></div><div class="progress"><div class="progress-fill" style="width:${Number(s.percent)}%"></div></div></div>`).join("");
    }
    function renderMonEvents(jobs) {
        if (!jobs.length) { $("monEvents").innerHTML = `<p class="dr-loading">No recent operations.</p>`; return; }
        $("monEvents").innerHTML = jobs.map((j) => {
            const t = j.created_at ? new Date(j.created_at).toLocaleString() : "";
            return `<div class="mon-event"><span class="hist-time">${esc(t)}</span><span class="hist-kind ${esc(j.kind)}">${esc(j.kind)}</span><span class="hist-state ${esc(j.state)}">${esc(j.state)}</span></div>`;
        }).join("");
    }
    function updateSyncChart(drst) {
        const g = primaryGroup(drst);
        const tot = g && g.volumes ? g.volumes.length : 0, syn = syncedCount(g);
        const pct = tot ? Math.round((syn / tot) * 100) : 0;
        monHistory.push({ label: new Date().toLocaleTimeString(), pct });
        if (monHistory.length > 20) monHistory.shift();
        const el = $("monSyncChart");
        if (!el || typeof Chart === "undefined") return;
        const labels = monHistory.map((p) => p.label), data = monHistory.map((p) => p.pct);
        if (monSyncChart) { monSyncChart.data.labels = labels; monSyncChart.data.datasets[0].data = data; monSyncChart.update(); }
        else {
            monSyncChart = new Chart(el, {
                type: "line",
                data: { labels, datasets: [{ label: "% Synced", data, borderColor: "#01A982", backgroundColor: "rgba(1,169,130,.15)", fill: true, tension: 0.35 }] },
                options: { scales: { y: { min: 0, max: 100, ticks: { color: "#5a6b7a" } }, x: { ticks: { color: "#5a6b7a" } } }, plugins: { legend: { labels: { color: "#33414f" } } } },
            });
        }
    }

    // ---- Reports -----------------------------------------------------------
    let reportJobs = [];
    function durationOf(j) {
        if (!j.created_at || !j.finished_at) return "-";
        const ms = new Date(j.finished_at) - new Date(j.created_at);
        if (ms < 0) return "-";
        const s = Math.round(ms / 1000);
        return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
    }
    async function loadReports() {
        try { const d = await window.api.get("/dr/jobs?limit=50"); reportJobs = d.jobs || []; }
        catch (e) { $("rptTable").innerHTML = `<p class="dr-error">${esc(e.message)}</p>`; return; }
        const fo = reportJobs.filter((j) => j.kind === "failover").length;
        const fb = reportJobs.filter((j) => j.kind === "failback").length;
        const succ = reportJobs.filter((j) => j.state === "succeeded").length;
        const rate = reportJobs.length ? Math.round((succ / reportJobs.length) * 100) : 0;
        $("rptSummary").innerHTML = `<div class="mon-tiles">
            <div class="mon-tile"><span class="mon-tile-label">Total operations</span><span class="mon-tile-value">${reportJobs.length}</span></div>
            <div class="mon-tile"><span class="mon-tile-label">Failovers</span><span class="mon-tile-value">${fo}</span></div>
            <div class="mon-tile"><span class="mon-tile-label">Failbacks</span><span class="mon-tile-value">${fb}</span></div>
            <div class="mon-tile"><span class="mon-tile-label">Success rate</span><span class="mon-tile-value">${rate}%</span></div></div>`;
        if (!reportJobs.length) { $("rptTable").innerHTML = `<p class="dr-loading">No operations recorded yet.</p>`; return; }
        $("rptTable").innerHTML = `<table class="rpt-table"><tr><th>Time</th><th>Operation</th><th>Mode</th><th>Result</th><th>Duration (RTO)</th></tr>` +
            reportJobs.map((j) => `<tr><td>${esc(j.created_at ? new Date(j.created_at).toLocaleString() : "")}</td>
            <td><span class="hist-kind ${esc(j.kind)}">${esc(j.kind)}</span></td>
            <td>${j.dry_run ? "dry-run" : "execute"}</td>
            <td><span class="hist-state ${esc(j.state)}">${esc(j.state)}</span></td>
            <td>${esc(durationOf(j))}</td></tr>`).join("") + `</table>`;
    }
    function exportCsv() {
        if (!reportJobs.length) return;
        const rows = [["Time", "Operation", "Mode", "Result", "DurationSeconds", "JobId"]];
        reportJobs.forEach((j) => {
            const dur = (j.created_at && j.finished_at) ? Math.max(0, Math.round((new Date(j.finished_at) - new Date(j.created_at)) / 1000)) : "";
            rows.push([j.created_at || "", j.kind, j.dry_run ? "dry-run" : "execute", j.state, dur, j.id]);
        });
        const csv = rows.map((r) => r.map((x) => `"${String(x).replace(/"/g, '""')}"`).join(",")).join("\n");
        const blob = new Blob([csv], { type: "text/csv" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob); a.download = `dr-report-${Date.now()}.csv`; a.click();
        URL.revokeObjectURL(a.href);
    }

    // ---- Settings ----------------------------------------------------------
    async function loadSettings() {
        let drst = null, health = null;
        try { drst = await window.api.get("/dr/status"); } catch (e) {}
        try { health = await window.api.get("/health"); } catch (e) {}
        const arrays = (drst && drst.arrays) || [];
        const grp = (drst && drst.group) || "Intern_Automation";
        const rows = arrays.map((a) => `<div class="set-row"><span>${a.role_label === "primary" ? "Primary array" : "DR array"}</span><b>${esc(a.host)}</b></div>`).join("") || "<p class='dr-loading'>Unavailable.</p>";
        $("setConn").innerHTML = rows +
            `<div class="set-row"><span>Remote Copy group</span><b>${esc(grp)}</b></div>` +
            `<div class="set-row"><span>Data provider</span><b>${esc(health ? health.provider : "-")}</b></div>` +
            `<div class="set-row"><span>Service</span><b>${esc(health ? health.status : "-")}</b></div>`;
        $("setAccount").innerHTML = `<div class="set-row"><span>Signed in as</span><b>${esc(localStorage.getItem("drUser") || "admin")}</b></div>`;
        const dd = localStorage.getItem("drDryRunDefault");
        $("setDryDefault").checked = dd === null ? true : dd === "true";
        $("setTheme").value = localStorage.getItem("drTheme") || "light";
    }
    function applyTheme(t) { document.body.classList.toggle("dark", t === "dark"); }
    function applyDryDefault() {
        const dd = localStorage.getItem("drDryRunDefault");
        const on = dd === null ? true : dd === "true";
        if ($("dropDry")) $("dropDry").checked = on;
    }

    // ---- Tab navigation ----------------------------------------------------
    const VIEW_FOR = {
        dashboard: "dashboard", replication: "replication",
        "dr operations": "droperations",
        monitoring: "monitoring", reports: "reports", settings: "settings",
    };
    function showView(name) {
        document.querySelectorAll(".view").forEach((v) => v.classList.remove("active"));
        const el = $("view-" + name);
        if (el) el.classList.add("active");
        clearInterval(monInterval); monInterval = null;
        if (name === "monitoring") { loadMonitoring(); monInterval = setInterval(loadMonitoring, 10000); }
        else if (name === "reports") { loadReports(); }
        else if (name === "settings") { loadSettings(); }
        else if (["replication", "droperations"].includes(name)) { loadStatus(); }
    }

    function init() {
        applyTheme(localStorage.getItem("drTheme") || "light");
        applyDryDefault();
        const items = document.querySelectorAll(".sidebar li");
        items.forEach((li) => {
            const t = li.textContent.trim().toLowerCase();
            li.addEventListener("click", () => {
                items.forEach((x) => x.classList.remove("active"));
                li.classList.add("active");
                showView(VIEW_FOR[t] || "dashboard");
                window.scrollTo({ top: 0, behavior: "smooth" });
            });
        });

        const bf = $("btnFailover"); if (bf) bf.addEventListener("click", () => onExec("failover", "dropDry"));
        const brv = $("btnRevert"); if (brv) brv.addEventListener("click", () => onExec("revert", "dropDry"));
        const brc = $("btnRecover"); if (brc) brc.addEventListener("click", () => onExec("recover", "dropDry"));
        const brs = $("btnRestore"); if (brs) brs.addEventListener("click", () => onExec("restore", "dropDry"));
        const rr = $("repRefresh"); if (rr) rr.addEventListener("click", loadStatus);
        const dor = $("dropRefresh"); if (dor) dor.addEventListener("click", loadStatus);
        const mr = $("monRefresh"); if (mr) mr.addEventListener("click", loadMonitoring);
        const rpt = $("rptRefresh"); if (rpt) rpt.addEventListener("click", loadReports);
        const rc = $("rptCsv"); if (rc) rc.addEventListener("click", exportCsv);
        const rp = $("rptPrint"); if (rp) rp.addEventListener("click", () => window.print());
        const sr = $("setRefresh"); if (sr) sr.addEventListener("click", loadSettings);
        const sl = $("setLogout"); if (sl) sl.addEventListener("click", () => window.api.logout());
        const sd = $("setDryDefault"); if (sd) sd.addEventListener("change", () => { localStorage.setItem("drDryRunDefault", sd.checked); applyDryDefault(); });
        const sth = $("setTheme"); if (sth) sth.addEventListener("change", () => { localStorage.setItem("drTheme", sth.value); applyTheme(sth.value); });

        $("drCancel").addEventListener("click", closeModal);
        $("drConfirm").addEventListener("click", () => { const op = pendingOp; closeModal(); runOp(op, false); });
        $("drTypeInput").addEventListener("input", validateModal);
        $("drAck").addEventListener("change", validateModal);

        loadStatus();
        statusTimer = setInterval(() => { if (!jobRunning) loadStatus(); }, 30000);

        // ---- Bell notifications -----------------------------------------
        const NOTIF_KEY = "drNotifications";
        function loadNotifs() { try { return JSON.parse(localStorage.getItem(NOTIF_KEY) || "[]"); } catch { return []; } }
        function saveNotifs(n) { localStorage.setItem(NOTIF_KEY, JSON.stringify(n.slice(0, 50))); }
        window._drPushNotif = function(title, body, type) {
            const n = loadNotifs();
            n.unshift({ id: Date.now(), title, body, type: type || "info", time: new Date().toISOString(), unread: true });
            saveNotifs(n); renderNotifs();
        };
        function renderNotifs() {
            const badge = $("bellBadge"), list = $("notifList");
            if (!badge || !list) return;
            const n = loadNotifs();
            const unread = n.filter((x) => x.unread).length;
            badge.hidden = unread === 0;
            if (!n.length) { list.innerHTML = `<li class="notif-empty">No notifications</li>`; return; }
            list.innerHTML = n.map((x) => {
                const t = x.time ? new Date(x.time).toLocaleTimeString() : "";
                return `<li class="notif-item ${x.type}${x.unread ? " unread" : ""}" data-id="${x.id}">`
                    + `<div class="notif-item-title">${esc(x.title)}</div>`
                    + `<div class="notif-item-body">${esc(x.body)}</div>`
                    + `<div class="notif-item-time">${esc(t)}</div></li>`;
            }).join("");
            list.querySelectorAll(".notif-item").forEach((el) => el.addEventListener("click", () => {
                saveNotifs(loadNotifs().map((x) => x.id === Number(el.dataset.id) ? {...x, unread: false} : x));
                renderNotifs();
            }));
        }
        const bellBtn = $("bellBtn"), drop = $("notifDrop"), clr = $("notifClear");
        if (bellBtn) bellBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            drop.hidden = !drop.hidden;
            if (!drop.hidden) { saveNotifs(loadNotifs().map((x) => ({...x, unread: false}))); renderNotifs(); }
        });
        if (clr) clr.addEventListener("click", (e) => { e.stopPropagation(); saveNotifs([]); renderNotifs(); });
        document.addEventListener("click", (e) => { if (drop && !drop.hidden && !drop.contains(e.target) && e.target !== bellBtn) drop.hidden = true; });
        renderNotifs();
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
})();
