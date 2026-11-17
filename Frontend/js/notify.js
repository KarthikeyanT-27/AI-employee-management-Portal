/* Real-time notifications, shared by the employee and HR dashboards.
 *
 *  - Live delivery over Server-Sent Events (GET /api/notifications/stream); the browser reconnects by itself.
 *  - If the stream is unavailable (proxy, firewall, server busy) it falls back to polling every 15 s,
 *    and keeps trying to go live again, so notifications are never lost.
 *  - Notifications are stored on the server, so anything that arrived while you were offline is in the bell.
 *  - "Silent" events (e.g. an employee checked in) add nothing to the bell; they only refresh the open page
 *    through window.liveRefresh, which each dashboard view sets.
 */
(function () {
  const POLL_MS = 15000, RETRY_LIVE_MS = 60000, ICONS = { leave: "🏖️", correction: "🕒", payroll: "💰", attendance: "📅", attrition: "📉", system: "🔔" };
  let es = null, pollTimer = null, retryTimer = null, refreshTimer = null, failures = 0, live = false, started = false;
  let items = [], unread = 0, lastId = 0;

  const ago = (iso) => {
    if (!iso) return "";
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + " min ago";
    if (s < 86400) return Math.floor(s / 3600) + " h ago";
    return new Date(iso).toLocaleDateString();
  };

  // ------------------------------------------------------------------ bell UI
  function mountBell() {
    if ($("#bell")) return;
    const btn = document.createElement("button");
    btn.id = "bell"; btn.type = "button"; btn.className = "bell-btn";
    btn.innerHTML = '<span class="bell-ico">🔔</span> Notifications <span class="bell-count hidden" id="bell-count">0</span><span class="live-dot" id="live-dot" title="Connecting…"></span>';
    const spacer = $(".sidebar .spacer");
    spacer ? spacer.before(btn) : $(".sidebar").appendChild(btn);
    btn.onclick = openPanel;
  }

  function paintBadge() {
    const c = $("#bell-count");
    if (c) { c.textContent = unread > 99 ? "99+" : unread; c.classList.toggle("hidden", unread <= 0); }
    const d = $("#live-dot");
    if (d) { d.classList.toggle("on", live); d.title = live ? "Live updates connected" : "Live updates reconnecting (checking every 15 s)"; }
    document.title = (unread > 0 ? `(${unread}) ` : "") + document.title.replace(/^\(\d+\)\s/, "");
  }

  function panelHtml() {
    const canDesktop = "Notification" in window && Notification.permission === "default";
    const list = items.length
      ? items.map((n) => `<div class="notif ${n.is_read ? "" : "unread"}" data-nid="${esc(n.id)}" tabindex="0">
          <div class="notif-ico">${ICONS[n.kind] || ICONS.system}</div>
          <div class="notif-body"><div class="notif-title">${esc(n.title)}</div><div class="muted small">${esc(n.message)}</div>
          <div class="muted small">${esc(ago(n.created_at))}</div></div></div>`).join("")
      : '<div class="empty">You are all caught up. New activity appears here instantly.</div>';
    return `<div id="notif-panel"><div class="row" style="justify-content:space-between;margin-bottom:8px">
        <span class="muted small">${live ? "● Live" : "○ Reconnecting"} · ${unread} unread</span>
        <span class="row">${canDesktop ? '<button class="btn sm" id="notif-desktop">Enable desktop alerts</button>' : ""}
        <button class="btn sm" id="notif-all" ${unread ? "" : "disabled"}>Mark all read</button></span></div>${list}</div>`;
  }

  function openPanel() {
    openModal("Notifications", panelHtml());
    wirePanel();
  }

  function wirePanel() {
    $("#notif-all")?.addEventListener("click", async () => {
      await api("/notifications/read-all", { method: "POST" }).catch(() => {});
      items.forEach((n) => (n.is_read = true)); unread = 0; paintBadge(); rerenderPanel();
    });
    $("#notif-desktop")?.addEventListener("click", async () => { await Notification.requestPermission(); rerenderPanel(); });
    $$("#notif-panel .notif").forEach((el) => {
      const go = async () => {
        const n = items.find((x) => String(x.id) === el.dataset.nid);
        if (!n) return;
        if (!n.is_read) { n.is_read = true; unread = Math.max(0, unread - 1); paintBadge(); api(`/notifications/${n.id}/read`, { method: "POST" }).catch(() => {}); }
        closeModal();
        if (n.link && typeof views !== "undefined" && typeof views[n.link] === "function") show(n.link);
      };
      el.onclick = go;
      el.onkeydown = (e) => { if (e.key === "Enter") go(); };
    });
  }

  function rerenderPanel() {
    const p = $("#notif-panel");
    if (p) { p.outerHTML = panelHtml(); wirePanel(); }
  }

  // ------------------------------------------------------------ incoming data
  function refreshView() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => {
      const modal = $("#modal-overlay");
      if (modal && !$("#notif-panel")) return;            // do not disturb a form the user is filling in
      if (typeof window.liveRefresh === "function") Promise.resolve(window.liveRefresh()).catch(() => {});
    }, 500);
  }

  function desktopAlert(n) {
    if (!("Notification" in window) || Notification.permission !== "granted" || !document.hidden) return;
    try { new Notification(n.title, { body: n.message, tag: "emp-" + n.id }); } catch (_) { /* some browsers need a service worker */ }
  }

  function handle(n) {
    if (n.silent) { refreshView(); return; }
    if (items.some((x) => x.id === n.id)) return;
    items.unshift(n); items = items.slice(0, 50);
    lastId = Math.max(lastId, n.id);
    if (!n.is_read) unread++;
    paintBadge(); rerenderPanel();
    toast(`${n.title}: ${n.message}`, "ok");
    desktopAlert(n);
    refreshView();
  }

  async function load() {
    const data = await api("/notifications?limit=30");
    items = data.notifications; unread = data.unread;
    lastId = items.reduce((m, n) => Math.max(m, n.id), lastId);
    paintBadge(); rerenderPanel();
  }

  async function poll() {
    try {
      const data = await api(`/notifications?limit=50&after_id=${lastId}`);
      unread = data.unread;
      data.notifications.slice().reverse().forEach(handle);
      paintBadge();
    } catch (_) { /* offline: try again next tick */ }
  }

  // --------------------------------------------------------- connection logic
  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(poll, POLL_MS);
    clearTimeout(retryTimer);
    retryTimer = setTimeout(() => { stopPolling(); connect(); }, RETRY_LIVE_MS);     // try to go live again
  }
  function stopPolling() { clearInterval(pollTimer); pollTimer = null; clearTimeout(retryTimer); }

  function connect() {
    if (!window.EventSource) { startPolling(); return; }
    es?.close();
    es = new EventSource("/api/notifications/stream");
    es.addEventListener("ready", () => {
      const wasDown = failures > 0;
      failures = 0; live = true; stopPolling(); paintBadge(); rerenderPanel();
      if (wasDown) poll();                                   // catch up on anything missed while disconnected
    });
    es.addEventListener("notification", (e) => { try { handle(JSON.parse(e.data)); } catch (_) { /* ignore malformed */ } });
    es.onerror = () => {
      live = false; failures++; paintBadge();
      if (es.readyState === EventSource.CLOSED || failures >= 3) { es.close(); es = null; startPolling(); }
    };
  }

  window.startLive = async function () {
    if (started) return;
    started = true;
    mountBell();
    await load().catch(() => {});
    connect();
    document.addEventListener("visibilitychange", () => { if (!document.hidden) { poll(); refreshView(); } });
  };
  window.stopLive = function () { es?.close(); es = null; stopPolling(); started = false; };
})();
