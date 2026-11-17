let me = null, depts = [], roles = [];
const view = $("#view");
const views = { overview, employees, org, attendance, corrections, leaves, payroll, attrition, reports };
const debounce = (fn, ms = 250) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

async function boot() {
  try { me = (await api("/auth/me")).user; } catch { return; }
  if (me.account_type === "employee") { location.href = "/employee.html"; return; }
  $("#who-name").textContent = me.name;
  $("#who-role").textContent = me.account_type === "admin" ? "Administrator" : "HR";
  $$(".sidebar a.nav").forEach((a) => (a.onclick = () => show(a.dataset.view)));
  if (me.must_change_password) { passwordModal(true); return; }   // temporary password must be replaced first
  startLive();                                                     // real-time notifications (bell + live updates)
  show(location.hash.slice(1) in views ? location.hash.slice(1) : "overview");
}
async function loadOrg() {
  [depts, roles] = await Promise.all([api("/departments").then((r) => r.departments), api("/roles").then((r) => r.roles)]);
}
function show(name) {
  window.liveRefresh = null;     // each view re-registers how it should refresh when live events arrive
  $$(".sidebar a.nav").forEach((a) => a.classList.toggle("active", a.dataset.view === name));
  location.hash = name;
  view.innerHTML = '<div class="empty"><span class="spinner"></span>Loading…</div>';
  views[name]().catch((e) => { view.innerHTML = `<div class="card empty">${esc(e.message)}</div>`; });
}
const bars = (obj, total, cls = "") => Object.entries(obj).map(([k, v]) =>
  `<div class="barrow"><span>${esc(k)}</span><div class="bar ${esc(cls || k)}"><span style="width:${total ? (v / total) * 100 : 0}%"></span></div><b>${esc(v)}</b></div>`).join("");

// ----------------------------------------------------------------- overview
async function overview() {
  view.innerHTML = `<div class="topbar"><h1>Workforce overview</h1></div><div id="ov-stats"></div>
    <div class="card"><div class="row" style="justify-content:space-between"><h2>Generative AI HR insights</h2>
      <button class="btn primary sm" id="btn-org-ai">Generate insights</button></div>
      <div id="org-ai" class="muted">Creates an executive brief from the live statistics above.</div></div>`;
  const drawStats = async () => {
    const s = await api("/reports/summary");
    const box = $("#ov-stats");
    if (!box) return;
    const risk = s.attrition_risk_distribution, rTotal = risk.Low + risk.Moderate + risk.High;
    box.innerHTML = `<div class="grid g4">
      <div class="card stat"><div class="label">Active employees</div><div class="value">${esc(s.active_employees)}</div></div>
      <div class="card stat"><div class="label">Avg attendance (60d)</div><div class="value">${esc(s.avg_attendance_pct)}%</div></div>
      <div class="card stat"><div class="label">Pending leaves</div><div class="value">${esc(s.pending_leaves)}</div>
        <div class="muted small">${esc(s.pending_corrections)} attendance correction(s) pending</div></div>
      <div class="card stat"><div class="label">Net payroll ${esc(s.payroll_month)}</div><div class="value">${inr(s.payroll_total_net_inr)}</div></div></div>
    <div class="grid g2">
      <div class="card"><h2>Headcount by department</h2>${s.active_employees ? bars(s.by_department, s.active_employees, "x") : '<div class="empty">No employees yet. Add the first one on the Employees page.</div>'}</div>
      <div class="card"><h2>Attrition risk (ML)</h2>
        ${rTotal ? bars(risk, rTotal) : '<div class="empty">No predictions yet. Open the Attrition (ML) page and run predictions.</div>'}
        <div class="muted small">Avg overtime ${esc(s.avg_overtime_hours)} h/month · Avg satisfaction ${esc(s.avg_job_satisfaction)}/10 · Avg rating ${esc(s.avg_performance_rating)}/5</div></div></div>`;
  };
  $("#btn-org-ai").onclick = (e) => guard(e.target, async () => { $("#org-ai").innerHTML = renderAI(await api("/genai/hr-insights", { method: "POST" })); });
  await drawStats();
  window.liveRefresh = drawStats;
}

// ---------------------------------------------------------------- employees
async function employees() {
  await loadOrg();
  const draw = async () => {
    const q = $("#emp-q").value.trim(), d = $("#emp-dept").value;
    const { employees: list, actor_type } = await api(`/employees?q=${encodeURIComponent(q)}${d ? "&department_id=" + d : ""}`);
    window.actorType = actor_type;
    $("#emp-table").innerHTML = table(["Code", "Name", "Email", "Department", "Role", "Salary", "Type", "Status", ""],
      list.map((e) => [esc(e.emp_code), esc(e.name), esc(e.email), esc(e.department || "-"), esc(e.role || "-"), inr(e.salary),
        esc(e.account_type), badge(e.status),
        e.can_manage ? `<button class="btn sm" data-edit="${e.id}">Edit</button> ${e.status === "active" ? `<button class="btn sm danger" data-off="${e.id}">Deactivate</button>` : ""}` : '<span class="muted small">Admin only</span>']),
      "No employees match.");
    $$("[data-edit]").forEach((b) => (b.onclick = () => empForm(list.find((x) => x.id == b.dataset.edit))));
    $$("[data-off]").forEach((b) => (b.onclick = () => guard(b, async () => {
      if (!confirm("Deactivate this employee? History is kept.")) return;
      await api("/employees/" + b.dataset.off, { method: "DELETE" }); toast("Employee deactivated", "ok"); draw();
    })));
  };
  view.innerHTML = `<div class="topbar"><h1>Employees</h1><button class="btn primary" id="emp-add">+ Add employee</button></div>
    <div class="card"><div class="row"><input id="emp-q" placeholder="Search name, email or code" style="max-width:280px">
      <select id="emp-dept" style="max-width:220px"><option value="">All departments</option>${depts.map((d) => `<option value="${d.id}">${esc(d.name)}</option>`).join("")}</select></div>
      <div id="emp-table" style="margin-top:10px"></div></div>`;
  $("#emp-add").onclick = () => empForm(null);
  $("#emp-q").oninput = debounce(draw); $("#emp-dept").onchange = draw;
  window.refreshEmployees = draw;
  window.liveRefresh = draw;
  draw();
}

function empForm(e) {
  const isNew = !e;
  const opt = (arr, sel, f) => arr.map((x) => `<option value="${x.id}" ${x.id === sel ? "selected" : ""}>${esc(f(x))}</option>`).join("");
  openModal(isNew ? "Add employee" : "Edit " + e.name, `<form id="ef"><div class="form-grid">
    <div><label>Full name</label><input id="e-name" value="${esc(e?.name)}" required></div>
    <div><label>Email</label><input id="e-email" type="email" value="${esc(e?.email)}" required></div>
    <div><label>${isNew ? "Temporary password (optional)" : "Reset password (optional)"}</label><input id="e-pass" type="text" minlength="8" autocomplete="off" placeholder="Leave blank for Google sign-in">
      <div class="muted small">${isNew ? "Leave blank so this employee can use matching Google sign-in." : "The user must change it at first login if reset."}</div></div>
    <div><label>Account type</label>${window.actorType === "admin"
      ? `<select id="e-type"><option value="employee" ${(e?.account_type || "employee") === "employee" ? "selected" : ""}>Employee</option><option value="hr" ${e?.account_type === "hr" ? "selected" : ""}>HR</option><option value="admin" ${e?.account_type === "admin" ? "selected" : ""}>Admin</option></select>`
      : `<select id="e-type" disabled><option value="employee">Employee</option></select><div class="muted small">Only an administrator can create HR/Admin accounts.</div>`}</div>
    <div><label>Department</label><select id="e-dept"><option value="">-</option>${opt(depts, e?.department_id, (d) => d.name)}</select></div>
    <div><label>Role</label><select id="e-role"><option value="">-</option>${opt(roles, e?.role_id, (r) => `${r.title} (${r.department})`)}</select></div>
    <div><label>Monthly salary (INR)</label><input id="e-sal" type="number" min="0" value="${esc(e?.salary ?? "")}"></div>
    <div><label>Join date</label><input id="e-join" type="date" value="${esc(e?.join_date || today())}"></div>
    <div><label>Prior experience (years)</label><input id="e-exp" type="number" min="0" step="0.5" value="${esc(e?.prior_experience_years ?? 0)}"></div>
    <div><label>HR assessment (1-10)</label><input id="e-hr-sat" type="number" min="1" max="10" step="0.5" value="${esc(e?.hr_satisfaction ?? "")}"></div>
    <div><label>Performance rating (1-5)</label><input id="e-perf" type="number" min="1" max="5" step="0.1" value="${esc(e?.performance_rating ?? 3)}"></div>
    <div><label>Phone</label><input id="e-phone" value="${esc(e?.phone || "")}"></div></div>
    <button class="btn primary" style="margin-top:14px" id="e-save">${isNew ? "Create" : "Save"}</button></form>`, true);
  $("#e-role").onchange = () => { const r = roles.find((x) => x.id == $("#e-role").value); if (r && isNew) { $("#e-sal").value = r.base_salary; $("#e-dept").value = r.department_id; } };
  $("#ef").onsubmit = (ev) => { ev.preventDefault();
    guard($("#e-save"), async () => {
      const body = { name: $("#e-name").value, email: $("#e-email").value, account_type: $("#e-type").value,
        department_id: Number($("#e-dept").value) || null, role_id: Number($("#e-role").value) || null,
        salary: $("#e-sal").value, join_date: $("#e-join").value, prior_experience_years: $("#e-exp").value,
        hr_satisfaction: $("#e-hr-sat").value || null, performance_rating: $("#e-perf").value, phone: $("#e-phone").value };
      if ($("#e-pass").value) body.password = $("#e-pass").value;
      await api(isNew ? "/employees" : "/employees/" + e.id, { method: isNew ? "POST" : "PUT", body });
      closeModal(); toast(isNew ? "Employee created" : "Employee updated", "ok"); window.refreshEmployees?.();
    }); };
}

// ------------------------------------------------------ departments & roles
async function org() {
  await loadOrg();
  view.innerHTML = `<h1>Departments & roles</h1>
    <div class="grid g2">
    <div class="card"><h2>Departments</h2>
      <form id="dep-form" class="row"><input id="dep-name" placeholder="New department" required style="flex:1"><button class="btn primary">Add</button></form>
      ${table(["Department", "Employees", ""], depts.map((d) => [esc(d.name), esc(d.employee_count),
        `<button class="btn sm" data-ren="${d.id}">Rename</button> <button class="btn sm danger" data-deld="${d.id}">Delete</button>`]))}</div>
    <div class="card"><h2>Add role</h2><form id="role-form"><div class="form-grid">
      <div><label>Title</label><input id="rl-title" required></div>
      <div><label>Department</label><select id="rl-dept" required>${depts.map((d) => `<option value="${d.id}">${esc(d.name)}</option>`).join("")}</select></div>
      <div><label>Salary level (1-5)</label><input id="rl-level" type="number" min="1" max="5" value="2" required></div>
      <div><label>Base salary / month</label><input id="rl-sal" type="number" min="0" required></div></div>
      <button class="btn primary" style="margin-top:12px">Add role</button></form></div></div>
    <div class="card"><h2>Roles</h2>${table(["Role", "Department", "Salary level", "Base salary", ""], roles.map((r) =>
      [esc(r.title), esc(r.department), esc(r.salary_level), inr(r.base_salary), `<button class="btn sm danger" data-delr="${r.id}">Delete</button>`]))}
      <div class="muted small">Salary level (1 lowest - 5 highest) is one of the six inputs to the attrition model.</div></div>`;
  $("#dep-form").onsubmit = (e) => { e.preventDefault(); guard(null, async () => { await api("/departments", { method: "POST", body: { name: $("#dep-name").value } }); org(); }); };
  $("#role-form").onsubmit = (e) => { e.preventDefault(); guard(null, async () => {
    await api("/roles", { method: "POST", body: { title: $("#rl-title").value, department_id: Number($("#rl-dept").value), salary_level: $("#rl-level").value, base_salary: $("#rl-sal").value } });
    toast("Role added", "ok"); org(); }); };
  $$("[data-ren]").forEach((b) => (b.onclick = () => guard(null, async () => {
    const n = prompt("New department name"); if (!n) return;
    await api("/departments/" + b.dataset.ren, { method: "PUT", body: { name: n } }); org(); })));
  $$("[data-deld]").forEach((b) => (b.onclick = () => guard(null, async () => {
    if (!confirm("Delete this department?")) return; await api("/departments/" + b.dataset.deld, { method: "DELETE" }); org(); })));
  $$("[data-delr]").forEach((b) => (b.onclick = () => guard(null, async () => {
    if (!confirm("Delete this role?")) return; await api("/roles/" + b.dataset.delr, { method: "DELETE" }); org(); })));
}

// --------------------------------------------------------------- attendance
async function attendance() {
  const { employees: emps } = await api("/employees?status=active");
  const draw = async () => {
    const e = $("#at-emp").value, d = $("#at-date").value;
    const qs = [e && "employee_id=" + e, d && "date=" + d, !d && "days=14"].filter(Boolean).join("&");
    const { attendance: rows } = await api("/attendance?" + qs);
    $("#at-table").innerHTML = table(["Date", "Employee", "Status", "In", "Out", "Overtime (h)"],
      rows.map((r) => [esc(r.date), esc(r.employee), badge(r.status), esc(r.check_in || "-"), esc(r.check_out || "-"), esc(r.overtime_hours)]), "No records for this filter.");
  };
  view.innerHTML = `<h1>Attendance</h1>
    <div class="card"><h2>Mark / correct attendance</h2><form id="mk"><div class="form-grid">
      <div><label>Employee</label><select id="mk-emp" required>${emps.map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("")}</select></div>
      <div><label>Date</label><input type="date" id="mk-date" value="${today()}" max="${today()}" required></div>
      <div><label>Status</label><select id="mk-status"><option>Present</option><option>Late</option><option>Absent</option><option>Leave</option></select></div>
      <div class="row"><div style="flex:1"><label>Check in</label><input type="time" id="mk-in"></div><div style="flex:1"><label>Check out</label><input type="time" id="mk-out"></div></div></div>
      <button class="btn primary" style="margin-top:12px" id="mk-btn">Save record</button></form></div>
    <div class="card"><div class="row"><h2 style="margin:0">Records</h2>
      <select id="at-emp" style="max-width:220px"><option value="">All employees</option>${emps.map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("")}</select>
      <input type="date" id="at-date" style="max-width:170px" title="Leave empty for last 14 days"></div><div id="at-table" style="margin-top:10px"></div></div>`;
  window.liveRefresh = draw;
  $("#at-emp").onchange = draw; $("#at-date").onchange = draw;
  $("#mk").onsubmit = (e) => { e.preventDefault(); guard($("#mk-btn"), async () => {
    await api("/attendance/mark", { method: "POST", body: { employee_id: Number($("#mk-emp").value), date: $("#mk-date").value, status: $("#mk-status").value, check_in: $("#mk-in").value || null, check_out: $("#mk-out").value || null } });
    toast("Attendance saved", "ok"); draw(); }); };
  draw();
}

// ------------------------------------------------------------------- leaves
async function leaves() {
  const { leaves: rows } = await api("/leaves");
  const pending = rows.filter((l) => l.status === "Pending"), done = rows.filter((l) => l.status !== "Pending");
  view.innerHTML = `<h1>Leave approvals</h1>
    <div class="card"><h2>Pending (${pending.length})</h2>${table(["Employee", "From", "To", "Days", "Type", "Reason", ""],
      pending.map((l) => [esc(l.employee), esc(l.start_date), esc(l.end_date), l.days, esc(l.leave_type), esc(l.reason || ""),
        `<button class="btn sm primary" data-ap="${l.id}">Approve</button> <button class="btn sm danger" data-rj="${l.id}">Reject</button>`]), "No pending requests.")}</div>
    <div class="card"><h2>History</h2>${table(["Employee", "From", "To", "Days", "Type", "Status"],
      done.map((l) => [esc(l.employee), esc(l.start_date), esc(l.end_date), l.days, esc(l.leave_type), badge(l.status)]), "No decisions yet.")}</div>`;
  const decide = (id, decision) => guard(null, async () => { await api(`/leaves/${id}/decision`, { method: "PUT", body: { decision } }); toast("Leave " + decision.toLowerCase(), "ok"); leaves(); });
  $$("[data-ap]").forEach((b) => (b.onclick = () => decide(b.dataset.ap, "Approved")));
  $$("[data-rj]").forEach((b) => (b.onclick = () => decide(b.dataset.rj, "Rejected")));
  window.liveRefresh = leaves;
}

// ------------------------------------------------------------- corrections
async function corrections() {
  const [{ corrections: rows }, { missed }, { audit }] = await Promise.all([
    api("/attendance/corrections"), api("/attendance/missed"), api("/attendance/audit")]);
  const pending = rows.filter((c) => c.status === "Pending"), done = rows.filter((c) => c.status !== "Pending").slice(0, 30);
  view.innerHTML = `<h1>Attendance corrections</h1>
    <div class="card"><h2>Pending requests (${pending.length})</h2>
      <div class="muted small" style="margin-bottom:6px">Employees ask for these when they forgot to check out or checked out by mistake. Approving updates the attendance record and recalculates overtime.</div>
      ${table(["Employee", "Date", "Check-in", "Check-out", "Reason", ""], pending.map((c) => [esc(c.employee), esc(c.date), esc(c.check_in || "(unchanged)"), esc(c.check_out), esc(c.reason),
        `<button class="btn sm primary" data-ap="${c.id}">Approve</button> <button class="btn sm danger" data-rj="${c.id}">Reject</button>`]), "No pending requests.")}</div>
    <div class="card"><h2>Missed check-outs (last 14 days)</h2>
      <div class="muted small" style="margin-bottom:6px">Checked in but never checked out. These days count as present; overtime is not counted until the check-out is set.</div>
      ${table(["Employee", "Date", "Check-in", "Status", ""], missed.map((m) => [esc(m.employee), esc(m.date), esc(m.check_in), badge(m.status),
        `<button class="btn sm" data-fix="${m.id}">Set check-out</button>`]), "No missed check-outs. 🎉")}</div>
    <div class="card"><h2>Recent decisions</h2>${table(["Employee", "Date", "Check-out", "Reason", "Status"],
      done.map((c) => [esc(c.employee), esc(c.date), esc(c.check_out), esc(c.reason), badge(c.status)]), "No decisions yet.")}</div>
    <div class="card"><h2>Correction audit history</h2>${table(["When", "Actor", "Action", "Correction"],
      (audit || []).map((a) => [esc(new Date(a.created_at).toLocaleString()), esc(a.actor || "-"),
        esc(a.action), esc(a.entity_id)]), "No audit entries yet.")}</div>`;
  const decide = (id, decision) => guard(null, async () => { await api(`/attendance/corrections/${id}/decision`, { method: "PUT", body: { decision } }); toast("Request " + decision.toLowerCase(), "ok"); corrections(); });
  $$("[data-ap]").forEach((b) => (b.onclick = () => decide(b.dataset.ap, "Approved")));
  $$("[data-rj]").forEach((b) => (b.onclick = () => decide(b.dataset.rj, "Rejected")));
  $$("[data-fix]").forEach((b) => (b.onclick = () => {
    const m = missed.find((x) => x.id == b.dataset.fix);
    openModal(`Set check-out - ${m.employee}, ${m.date}`, `<form id="sf"><label>Check-out time</label><input type="time" id="sf-out" required>
      <button class="btn primary" style="margin-top:12px" id="sf-btn">Save</button></form>`);
    $("#sf").onsubmit = (ev) => { ev.preventDefault(); guard($("#sf-btn"), async () => {
      await api("/attendance/mark", { method: "POST", body: { employee_id: m.employee_id, date: m.date, status: m.status, check_in: m.check_in, check_out: $("#sf-out").value } });
      closeModal(); toast("Check-out saved", "ok"); corrections(); }); };
  }));
  window.liveRefresh = corrections;
}

// ------------------------------------------------------------------ payroll
async function payroll() {
  const { employees: emps } = await api("/employees?status=active");
  const draw = async () => {
    const m = $("#pr-month").value;
    const { payroll: rows, total_net } = await api("/payroll?month=" + m);
    $("#pr-total").textContent = rows.length ? `${rows.length} payslips · total net ${inr(total_net)}` : "";
    $("#pr-table").innerHTML = table(["Employee", "Base", "Paid days", "LOP", "OT hrs", "OT pay", "Bonus", "PF", "Tax", "Net"],
      rows.map((p) => [esc(p.employee), inr(p.base_salary), `${esc(p.paid_days)}/${esc(p.working_days)}`, inr(p.lop_deduction), esc(p.overtime_hours),
        inr(p.overtime_pay), inr(p.bonus), inr(p.pf), inr(p.tax), `<b>${inr(p.net_salary)}</b>`]), "No payroll generated for this month.");
  };
  view.innerHTML = `<h1>Payroll</h1>
    <div class="card"><h2>Generate payroll</h2><div class="muted small" style="margin-bottom:8px">Net = base − loss-of-pay + overtime (1.5×) + bonus − PF (12%) − tax (5% above ₹50,000). Re-running a month recalculates it.</div>
      <div class="row"><input type="month" id="pr-month" value="${thisMonth()}" style="max-width:170px">
      <select id="pr-emp" style="max-width:240px"><option value="">All active employees</option>${emps.map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join("")}</select>
      <input id="pr-bonus" type="number" min="0" placeholder="Bonus (single employee)" style="max-width:200px">
      <button class="btn primary" id="pr-go">Generate</button></div></div>
    <div class="card"><div class="row" style="justify-content:space-between"><h2 style="margin:0">Payslips</h2><span class="muted" id="pr-total"></span></div><div id="pr-table" style="margin-top:10px"></div></div>`;
  $("#pr-month").onchange = draw;
  window.liveRefresh = draw;
  $("#pr-go").onclick = (e) => guard(e.target, async () => {
    const body = { month: $("#pr-month").value };
    if ($("#pr-emp").value) body.employee_id = Number($("#pr-emp").value);
    if ($("#pr-bonus").value) body.bonus = $("#pr-bonus").value;
    const r = await api("/payroll/generate", { method: "POST", body }); toast(`Generated ${r.generated} payslip(s)`, "ok"); draw();
  });
  draw();
}

// ---------------------------------------------------------------- attrition
const FEATURE_LABELS = { attendance_pct: "Attendance %", salary_level: "Salary level", overtime_hours: "Overtime h/month",
  years_experience: "Experience (yrs)", job_satisfaction: "Job satisfaction", leave_frequency: "Leaves (12 mo)" };

async function attrition() {
  const [{ predictions }, info, ai] = await Promise.all([api("/ml/predictions"), api("/ml/model-info"), api("/genai/status")]);
  const dist = { Low: 0, Moderate: 0, High: 0 };
  predictions.forEach((p) => dist[p.risk_label]++);
  const imp = info.feature_importance || {}, maxImp = Math.max(0, ...Object.values(imp));
  const m = info.metrics || {};
  view.innerHTML = `<div class="topbar"><h1>Attrition risk (Random Forest)</h1>
      <button class="btn primary" id="run-all">Run predictions for all employees</button></div>
    ${info.ready ? "" : `<div class="notice">${esc(info.error)}</div>`}
    <div class="grid g3">
      ${["Low", "Moderate", "High"].map((k) => `<div class="card stat"><div class="label">${k} risk</div><div class="value">${dist[k]}</div></div>`).join("")}</div>
    <div class="card"><h2>Employees by predicted risk</h2>${table(["Employee", "Department", "Risk", "P(High)", "Attendance", "Satisfaction", "Overtime", "Leaves", ""],
      predictions.map((p) => [esc(p.employee), esc(p.department || "-"), badge(p.risk_label),
        `<div class="bar High" style="width:90px"><span style="width:${(p.probabilities.High || 0) * 100}%"></span></div> <span class="small">${Math.round((p.probabilities.High || 0) * 100)}%</span>`,
        esc(p.features.attendance_pct) + "%", esc(p.features.job_satisfaction), esc(p.features.overtime_hours) + " h", esc(p.features.leave_frequency),
        `<button class="btn sm" data-ai="${p.employee_id}">AI report</button>`]),
      "No predictions yet - click “Run predictions for all employees”.")}</div>
    <div class="grid g2">
      <div class="card"><h2>Model performance</h2><div class="muted small">Held-out test set (${esc(info.metrics ? "20% split" : "")})</div>
        <div class="grid g3" style="margin-top:8px"><div class="stat"><div class="label">Accuracy</div><div class="value">${m.accuracy ? Math.round(m.accuracy * 1000) / 10 + "%" : "-"}</div></div>
        <div class="stat"><div class="label">Macro F1</div><div class="value">${m.macro_f1 ? Math.round(m.macro_f1 * 1000) / 10 + "%" : "-"}</div></div>
        <div class="stat"><div class="label">5-fold CV</div><div class="value">${m.cv_accuracy_mean ? Math.round(m.cv_accuracy_mean * 1000) / 10 + "%" : "-"}</div></div></div>
        <div class="muted small" style="margin-top:8px">Trained on the bundled dataset in ML/employee_attrition.csv. Retrain with ML/train_model.py.</div></div>
      <div class="card"><h2>Feature importance</h2>${Object.entries(imp).sort((a, b) => b[1] - a[1]).map(([k, v]) =>
        `<div class="barrow"><span>${esc(FEATURE_LABELS[k] || k)}</span><div class="bar"><span style="width:${maxImp ? (v / maxImp) * 100 : 0}%"></span></div><b>${Math.round(v * 100)}%</b></div>`).join("")}</div></div>
    ${ai.configured ? "" : `<div class="notice">Generative AI: no LLM API key is configured on the server (set GROQ_API_KEY in the hosting environment). “AI report” uses the labelled local fallback until you add one.</div>`}`;

  window.liveRefresh = attrition;
  $("#run-all").onclick = (e) => guard(e.target, async () => { const r = await api("/ml/predict-all", { method: "POST" }); toast(`Scored ${r.count} employees`, "ok"); attrition(); });
  $$("[data-ai]").forEach((b) => (b.onclick = () => guard(b, async () => {
    const res = await api("/genai/employee/" + b.dataset.ai, { method: "POST", body: {} });
    const r = res.risk ? `<p>${badge(res.risk.risk_label)} <span class="muted">Random Forest attrition prediction</span></p>` : "";
    openModal("AI report - " + res.employee.name, r + renderAI(res), true);
  })));
}

// ------------------------------------------------------------------ reports
async function reports() {
  const dl = (kind, extra = "") => `<a class="btn" href="/api/reports/export/${kind}${extra}" download>Download ${kind} CSV</a>`;
  view.innerHTML = `<h1>HR reports & records</h1><div class="card"><h2>Export (CSV)</h2>
    <div class="row">${dl("employees")} ${dl("attendance")} ${dl("payroll", "?month=" + thisMonth())} ${dl("attrition")}</div>
    <div class="muted small" style="margin-top:8px">Exports are generated live from the database. Payroll export covers the current month.</div></div>`;
}

boot();
