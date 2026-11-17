/* Shared helpers: API wrapper, escaping, formatting, modal, toast */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// Always escape server data before putting it into innerHTML (prevents XSS)
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const inr = (n) => "₹" + Number(n || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 });
const badge = (text, cls) => `<span class="badge ${esc(cls ?? text)}">${esc(text)}</span>`;
// local calendar date (toISOString would give the UTC date, which is "yesterday" for early-morning users in India)
const today = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
const thisMonth = () => today().slice(0, 7);

async function api(path, { method = "GET", body, raw = false } = {}) {
  const res = await fetch("/api" + path, {
    method,
    credentials: "same-origin",
    headers: { "X-Requested-With": "XMLHttpRequest", ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (raw) return res;
  let data = {};
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (res.status === 401 && !path.startsWith("/auth/login")) { location.href = "/login.html"; throw new Error("Session expired"); }
  if (res.status === 403 && data.code === "PASSWORD_CHANGE_REQUIRED") { passwordModal(true); throw new Error(data.error); }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function toast(msg, type = "") {
  const t = document.createElement("div");
  t.className = "toast " + type;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3800);
}

// run an async action with button feedback + error toast
async function guard(btn, fn) {
  const label = btn?.innerHTML;
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>Working…'; }
  try { return await fn(); }
  catch (e) { toast(e.message, "error"); }
  finally { if (btn) { btn.disabled = false; btn.innerHTML = label; } }
}

function openModal(title, html, wide = false, lock = false) {
  closeModal();
  const o = document.createElement("div");
  o.className = "overlay"; o.id = "modal-overlay";
  o.innerHTML = `<div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true">
    <header><h2>${esc(title)}</h2>${lock ? "" : '<button class="btn sm" id="modal-x" aria-label="Close">✕</button>'}</header>
    <div id="modal-body">${html}</div></div>`;
  if (!lock) {
    o.addEventListener("mousedown", (e) => { if (e.target === o) closeModal(); });
    $("#modal-x", o).onclick = closeModal;
  }
  document.body.appendChild(o);
  return $("#modal-body");
}

// Password change dialog. `forced` = first login with a temporary password: cannot be dismissed.
function passwordModal(forced = false) {
  if (forced && $("#pw-modal-form")) return;
  openModal(forced ? "Set your own password" : "Change password", `
    ${forced ? '<div class="notice">You are signed in with a temporary password issued by HR/Admin. Choose a new password to continue.</div>' : ""}
    <form id="pw-modal-form">
      <label>Current password</label><input id="pm-cur" type="password" autocomplete="current-password" required>
      <label>New password</label><input id="pm-new" type="password" autocomplete="new-password" minlength="8" required>
      <label>Confirm new password</label><input id="pm-new2" type="password" autocomplete="new-password" minlength="8" required>
      <div class="muted small" style="margin-top:6px">At least 8 characters with a letter and a number.</div>
      <div class="err" id="pm-err" role="alert"></div>
      <div class="row" style="margin-top:10px"><button class="btn primary" id="pm-btn">Update password</button>
        ${forced ? '<button type="button" class="btn" data-act="logout">Sign out</button>' : ""}</div></form>`, false, forced);
  $("#pw-modal-form").onsubmit = (e) => {
    e.preventDefault();
    if ($("#pm-new").value !== $("#pm-new2").value) { $("#pm-err").textContent = "The new passwords do not match"; return; }
    guard($("#pm-btn"), async () => {
      try {
        await api("/auth/change-password", { method: "POST", body: { current_password: $("#pm-cur").value, new_password: $("#pm-new").value } });
        toast("Password updated", "ok");
        if (forced) location.reload(); else closeModal();
      } catch (err) { $("#pm-err").textContent = err.message; }
    });
  };
}
const closeModal = () => $("#modal-overlay")?.remove();

function table(headers, rows, empty = "Nothing to show yet.") {
  if (!rows.length) return `<div class="empty">${esc(empty)}</div>`;
  return `<div class="table-wrap"><table><thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}

function renderAI(res) {
  const labels = { performance_summary: "Performance summary", attendance_analysis: "Attendance analysis",
                   payroll_productivity: "Payroll & productivity", hr_insights: "HR insights" };
  const src = res.source === "llm" ? `Generated by ${esc(res.model)}` : "Local fallback (no LLM API key configured)";
  const note = res.source === "llm" ? "" :
    `<div class="notice">The AI provider key is not configured on the server, so this text was composed from live data without a language model. An administrator can enable full Generative AI output by setting <b>GROQ_API_KEY</b> in the hosting environment.</div>`;
  const body = res.sections
    ? Object.entries(res.sections).map(([k, v]) => `<h3>${esc(labels[k] || k)}</h3><p>${esc(v)}</p>`).join("")
    : `<p>${esc(res.insights)}</p>`;
  return `${note}<div class="ai-box"><span class="badge">${src}</span>${body}</div>`;
}

// buttons use data-act instead of inline onclick attributes (the Content-Security-Policy forbids inline handlers)
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-act]");
  if (!el) return;
  if (el.dataset.act === "logout") logout();
  else if (el.dataset.act === "password") passwordModal(false);
});

async function logout() { window.stopLive?.(); await api("/auth/logout", { method: "POST" }).catch(() => {}); location.href = "/login.html"; }
