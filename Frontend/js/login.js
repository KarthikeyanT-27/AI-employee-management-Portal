let mode = "employee";

function setTab(t) {
  mode = t;
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === t));
  $("#l-err").textContent = "";
  // Google sign-in is for employees only (HR/Admin always use a password)
  $("#google-btn").classList.toggle("hidden", t !== "employee" || !window.googleEnabled);
}
$$("#tabs button").forEach((b) => (b.onclick = () => setTab(b.dataset.tab)));

// Show the Google button only when the server has Google sign-in configured. Also reveals a cold-start hint:
// free hosting puts an idle server to sleep, so the first request can take up to a minute.
(async () => {
  const note = setTimeout(() => { $("#server-note").textContent = "Waking the server up - this can take up to a minute after a quiet period…"; }, 3000);
  try {
    const h = await (await fetch("/api/health")).json();
    window.googleEnabled = !!h.google_signin;
  } catch (_) { window.googleEnabled = false; }
  clearTimeout(note); $("#server-note").textContent = "";
  const keep = $("#l-err").textContent; setTab(mode); $("#l-err").textContent = keep;
})();
setTab("employee");
const urlError = new URLSearchParams(location.search).get("error");
if (urlError) { $("#l-err").textContent = urlError; history.replaceState(null, "", "/login.html"); }

$("#login-form").onsubmit = async (e) => {
  e.preventDefault();
  $("#l-err").textContent = "";
  await guard($("#l-btn"), async () => {
    try {
      const { user } = await api("/auth/login", { method: "POST", body: { email: $("#l-email").value, password: $("#l-pass").value, portal: mode } });
      location.href = user.account_type === "employee" ? "/employee.html" : "/hr.html";
    } catch (err) { $("#l-err").textContent = err.message; }
  });
};
$("#google-btn").onclick = () => { location.href = "/api/auth/google"; };
