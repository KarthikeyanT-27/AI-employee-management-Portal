let me = null, lastAI = "";
const view = $("#view");
const views = { dashboard, attendance, leave, payslips, satisfaction, profile };

async function boot() {
  try { me = (await api("/auth/me")).user; } catch { return; }
  if (me.account_type !== "employee") { location.href = "/hr.html"; return; }
  $("#who-name").textContent = me.name;
  $("#who-role").textContent = [me.role, me.department].filter(Boolean).join(" · ") || me.emp_code;
  $$(".sidebar a.nav").forEach((a) => (a.onclick = () => show(a.dataset.view)));
  if (me.must_change_password) { passwordModal(true); return; }   // temporary password must be replaced first
  startLive();                                                     // real-time notifications (bell + live updates)
  show(location.hash.slice(1) in views ? location.hash.slice(1) : "dashboard");
}

function show(name) {
  window.liveRefresh = null;     // each view re-registers how it should refresh when live events arrive
  $$(".sidebar a.nav").forEach((a) => a.classList.toggle("active", a.dataset.view === name));
  location.hash = name;
  view.innerHTML = '<div class="empty"><span class="spinner"></span>Loading…</div>';
  views[name]().catch((e) => { view.innerHTML = `<div class="card empty">${esc(e.message)}</div>`; });
}

// ------------------------------------------------------- correction request
function correctionForm(prefill = {}) {
  openModal("Request attendance correction", `
    <p class="muted small">Use this if you forgot to check out, or checked out by mistake after the undo window. HR will review it.</p>
    <form id="cf"><div class="form-grid">
      <div><label>Date</label><input type="date" id="cf-date" value="${esc(prefill.date || "")}" max="${today()}" required></div>
      <div><label>Correct check-out time</label><input type="time" id="cf-out" value="${esc(prefill.check_out || "")}" required></div>
      <div><label>Check-in time <span class="muted">(only if it must change)</span></label><input type="time" id="cf-in"></div>
      <div></div></div>
      <label>Reason</label><textarea id="cf-reason" rows="2" maxlength="255" placeholder="e.g. Forgot to check out, left at 6:10 pm" required></textarea>
      <div class="err" id="cf-err" role="alert"></div>
      <button class="btn primary" style="margin-top:10px" id="cf-btn">Send to HR</button></form>`);
  $("#cf").onsubmit = (e) => { e.preventDefault();
    guard($("#cf-btn"), async () => {
      try {
        await api("/attendance/corrections", { method: "POST", body: { date: $("#cf-date").value, check_out: $("#cf-out").value,
          check_in: $("#cf-in").value || null, reason: $("#cf-reason").value } });
        closeModal(); toast("Request sent to HR", "ok"); show(location.hash.slice(1) || "dashboard");
      } catch (err) { $("#cf-err").textContent = err.message; }
    }); };
}

// ---------------------------------------------------------------- dashboard
async function dashboard() {
  const [att, pay, leaves] = await Promise.all([api("/attendance/me?days=30"), api("/payroll/me"), api("/leaves/me")]);
  const t = att.today, s = att.stats;
  const latest = pay.payroll[0];
  const missing = att.missing_checkouts || [];
  view.innerHTML = `
    <div class="topbar"><div><h1>Welcome, ${esc(me.name.split(" ")[0])}</h1>
      <div class="muted">${esc(me.emp_code)} · ${esc(me.role || "No role assigned")} · ${esc(me.department || "")}</div></div></div>
    ${missing.length ? `<div class="notice"><b>You forgot to check out on ${missing.length} day(s):</b>
      ${missing.map((m) => `<span style="white-space:nowrap">${esc(m.date)} <button class="btn sm" data-fix="${esc(m.date)}">Fix</button></span>`).join(" &nbsp; ")}
      <div class="small">Those days still count as present, but overtime is not counted until HR approves your correction.</div></div>` : ""}
    <div class="card">
      <h2>Today</h2>
      <div class="row">
        <button class="btn primary" id="btn-in" ${t?.check_in ? "disabled" : ""}>Check in</button>
        <button class="btn" id="btn-out" ${!t?.check_in || t?.check_out ? "disabled" : ""}>Check out</button>
        ${att.can_undo ? `<button class="btn" id="btn-undo">Undo check-out (${esc(att.undo_minutes_left)} min left)</button>` : ""}
        <span class="muted">${t ? `${badge(t.status)} In: ${esc(t.check_in || "-")} · Out: ${esc(t.check_out || "-")} · Overtime: ${esc(t.overtime_hours)}h` : "You haven't checked in yet."}</span>
      </div>
      ${t?.check_out && !att.can_undo ? `<div class="muted small" style="margin-top:8px">Checked out by mistake? The ${esc(att.undo_window_minutes)}-minute undo window has passed.
        <a href="#" id="lnk-corr">Request a correction</a>.</div>` : ""}
      ${att.can_undo ? `<div class="muted small" style="margin-top:8px">Checked out by mistake? You can undo it for ${esc(att.undo_window_minutes)} minutes after checking out.</div>` : ""}
    </div>
    <div class="grid g4">
      <div class="card stat"><div class="label">Attendance (30d)</div><div class="value">${esc(s.attendance_pct)}%</div></div>
      <div class="card stat"><div class="label">Late arrivals</div><div class="value">${esc(s.late)}</div></div>
      <div class="card stat"><div class="label">Leave requests</div><div class="value">${leaves.leaves.length}</div></div>
      <div class="card stat"><div class="label">Latest net pay</div><div class="value">${latest ? inr(latest.net_salary) : "-"}</div>
        <div class="muted small">${esc(latest?.month || "")}</div></div>
    </div>
    <div class="card">
      <div class="row" style="justify-content:space-between"><h2>AI performance & attendance summary</h2>
        <button class="btn sm" id="btn-ai">Generate</button></div>
      <div id="ai-out" class="muted">Click Generate to create a summary from your latest records.</div>
    </div>`;
  if (lastAI) $("#ai-out").innerHTML = lastAI;
  window.liveRefresh = dashboard;
  $("#btn-in").onclick = (e) => guard(e.target, async () => { await api("/attendance/check-in", { method: "POST" }); toast("Checked in", "ok"); dashboard(); });
  $("#btn-out").onclick = (e) => guard(e.target, async () => {
    const r = await api("/attendance/check-out", { method: "POST" });
    toast(r.can_undo ? `Checked out. Made a mistake? You can undo it for ${r.undo_minutes_left} minutes.` : "Checked out", "ok"); dashboard();
  });
  $("#btn-undo")?.addEventListener("click", (e) => guard(e.target, async () => { const r = await api("/attendance/undo-checkout", { method: "POST" }); toast(r.message, "ok"); dashboard(); }));
  $("#lnk-corr")?.addEventListener("click", (e) => { e.preventDefault(); correctionForm({ date: today(), check_out: "" }); });
  $$("[data-fix]").forEach((b) => (b.onclick = () => correctionForm({ date: b.dataset.fix })));
  $("#btn-ai").onclick = (e) => guard(e.target, async () => { lastAI = renderAI(await api("/genai/me")); $("#ai-out").innerHTML = lastAI; });
}

// --------------------------------------------------------------- attendance
async function attendance() {
  const [{ attendance: rows, stats, missing_checkouts: missing }, { corrections }] =
    await Promise.all([api("/attendance/me?days=60"), api("/attendance/corrections/me")]);
  view.innerHTML = `<div class="topbar"><h1>My attendance</h1><button class="btn" id="btn-corr">Request correction</button></div>
    <div class="grid g4" style="margin:12px 0">
      <div class="card stat"><div class="label">Attendance</div><div class="value">${esc(stats.attendance_pct)}%</div></div>
      <div class="card stat"><div class="label">Present days</div><div class="value">${esc(stats.present)}</div></div>
      <div class="card stat"><div class="label">Late</div><div class="value">${esc(stats.late)}</div></div>
      <div class="card stat"><div class="label">Absent</div><div class="value">${esc(stats.absent)}</div></div></div>
    ${(missing || []).length ? `<div class="notice">Missing check-out on: ${missing.map((m) => esc(m.date)).join(", ")}. Use “Request correction” to fix.</div>` : ""}
    <div class="card"><h2>Last 60 days</h2>${table(["Date", "Status", "Check in", "Check out", "Overtime (h)", "Note"],
      rows.map((r) => [esc(r.date), badge(r.status), esc(r.check_in || "-"),
        r.missing_checkout ? '<span class="badge Pending">Missing</span>' : esc(r.check_out || "-"), esc(r.overtime_hours), esc(r.note || "")]), "No attendance records yet.")}</div>
    <div class="card"><h2>My correction requests</h2>${table(["Date", "Check-out", "Check-in", "Reason", "Status"],
      corrections.map((c) => [esc(c.date), esc(c.check_out), esc(c.check_in || "-"), esc(c.reason), badge(c.status)]), "No correction requests.")}</div>`;
  $("#btn-corr").onclick = () => correctionForm({ date: (missing && missing[0]?.date) || "" });
  window.liveRefresh = attendance;
}

// -------------------------------------------------------------------- leave
async function leave() {
  view.innerHTML = `<h1>Leave</h1>
    <div class="card"><h2>Apply for leave</h2>
      <form id="leave-form"><div class="form-grid">
        <div><label>From</label><input type="date" id="lv-start" min="${today()}" required></div>
        <div><label>To</label><input type="date" id="lv-end" min="${today()}" required></div>
        <div><label>Type</label><select id="lv-type"><option>Casual</option><option>Sick</option><option>Earned</option><option>Unpaid</option></select></div>
        <div><label>Reason</label><input id="lv-reason" maxlength="255"></div></div>
        <button class="btn primary" style="margin-top:12px" id="lv-btn">Submit request</button></form></div>
    <div class="card"><h2>My requests</h2><div id="lv-list"></div></div>`;
  const drawLeaves = async () => {
    const { leaves } = await api("/leaves/me");
    const box = $("#lv-list");
    if (box) box.innerHTML = table(["From", "To", "Days", "Type", "Reason", "Status"],
      leaves.map((l) => [esc(l.start_date), esc(l.end_date), l.days, esc(l.leave_type), esc(l.reason || ""), badge(l.status)]), "No leave requests yet.");
  };
  $("#leave-form").onsubmit = (e) => {
    e.preventDefault();
    guard($("#lv-btn"), async () => {
      await api("/leaves", { method: "POST", body: { start_date: $("#lv-start").value, end_date: $("#lv-end").value, leave_type: $("#lv-type").value, reason: $("#lv-reason").value } });
      toast("Leave request submitted - HR has been notified", "ok"); $("#leave-form").reset(); await drawLeaves();
    });
  };
  await drawLeaves();
  window.liveRefresh = drawLeaves;
}

// ----------------------------------------------------------------- payslips
async function payslips() {
  const { payroll } = await api("/payroll/me");
  view.innerHTML = `<h1>Payslips</h1><div class="card">${table(["Month", "Base", "Paid days", "LOP", "Overtime", "Bonus", "PF", "Tax", "Net pay"],
    payroll.map((p) => [esc(p.month), inr(p.base_salary), `${esc(p.paid_days)}/${esc(p.working_days)}`, inr(p.lop_deduction), inr(p.overtime_pay),
      inr(p.bonus), inr(p.pf), inr(p.tax), `<b>${inr(p.net_salary)}</b>`]), "No payslips generated yet. HR generates them monthly.")}</div>`;
  window.liveRefresh = payslips;
}

// ------------------------------------------------------------------ profile
async function profile() {
  me = (await api("/auth/me")).user;
  view.innerHTML = `<h1>My profile</h1>
    <div class="card"><form id="pf"><div class="form-grid">
      <div><label>Full name</label><input id="p-name" value="${esc(me.name)}" required></div>
      <div><label>Email (read-only)</label><input value="${esc(me.email)}" disabled></div>
      <div><label>Phone</label><input id="p-phone" value="${esc(me.phone || "")}"></div>
      <div><label>Address</label><input id="p-addr" value="${esc(me.address || "")}"></div>
      <div><label>Department</label><input value="${esc(me.department || "-")}" disabled></div>
      <div><label>Role</label><input value="${esc(me.role || "-")}" disabled></div>
      <div><label>Joined</label><input value="${esc(me.join_date)}" disabled></div>
      </div>
      <button class="btn primary" style="margin-top:12px" id="p-btn">Save changes</button></form></div>
    <div class="card"><h2>Password</h2><div class="muted small" style="margin-bottom:8px">Your account was created by HR. You can change your password at any time.</div>
      <button class="btn" data-act="password">Change password</button></div>`;
  $("#pf").onsubmit = (e) => { e.preventDefault();
    guard($("#p-btn"), async () => {
      await api("/profile", { method: "PUT", body: { name: $("#p-name").value, phone: $("#p-phone").value, address: $("#p-addr").value } });
      toast("Profile updated", "ok"); boot();
    }); };
}

async function satisfaction() {
  const data = await api("/satisfaction");
  const latest = data.surveys?.[0];
  view.innerHTML = `<h1>Employee satisfaction</h1>
    <div class="card"><p class="muted">Your response is private and helps the attrition model identify workplace risks. HR sees only the separate HR assessment.</p>
      <form id="sf"><label>How satisfied are you with your job? (1-10)</label>
      <input id="s-score" type="number" min="1" max="10" step="0.5" value="${esc(latest?.score ?? me.job_satisfaction ?? 7)}" required>
      <label>Optional comment</label><textarea id="s-comment" maxlength="500" rows="3"></textarea>
      <button class="btn primary" style="margin-top:10px" id="s-btn">Submit survey</button></form></div>
    <div class="card"><h2>Recent responses</h2>${table(["Submitted", "Score", "Comment"],
      (data.surveys || []).map((s) => [esc(new Date(s.submitted_at).toLocaleDateString()), esc(s.score), esc(s.comment || "-")]),
      "No survey responses yet.")}</div>`;
  $("#sf").onsubmit = (e) => { e.preventDefault(); guard($("#s-btn"), async () => {
    await api("/satisfaction", { method: "POST", body: { score: $("#s-score").value, comment: $("#s-comment").value } });
    toast("Survey submitted", "ok"); show("satisfaction");
  }); };
}

boot();
