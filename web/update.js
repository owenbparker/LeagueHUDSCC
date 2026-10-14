// Update button for the start screen and the observer client (the GFX
// controller has its own in its top bar). Call updateButton(button, version)
// once, then pass it state.app.update each time the state is fetched.
function updateButton(button, version) {
  let info = null, noteUntil = 0;

  async function post(url) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    const data = await r.json();
    if (data.error) alert(data.error);
    else render(data.app.update);
  }

  function render(u) {
    info = u;
    if (Date.now() > noteUntil) version.textContent = u.current === "dev" ? "dev build" : u.current;
    button.classList.remove("error");
    button.disabled = false;
    button.title = "";
    if (u.status === "downloading") { button.hidden = false; button.disabled = true; button.textContent = `Downloading update ${u.progress}%`; }
    else if (u.status === "installing") { button.hidden = false; button.disabled = true; button.textContent = "Installing - restarting…"; }
    else if (u.status === "error") { button.hidden = false; button.classList.add("error"); button.textContent = "Update failed - retry"; button.title = u.error || ""; }
    else if (u.available && u.can_install) { button.hidden = false; button.textContent = `⬆ Update to ${u.latest}`; button.title = u.notes || ""; }
    else button.hidden = true;
  }

  button.onclick = () => {
    const u = info;
    const notes = u.notes ? `\n\nWhat's new:\n${u.notes.slice(0, 600)}` : "";
    const failed = u.status === "error" ? `The last attempt failed: ${u.error}\n\n` : "";
    if (confirm(`${failed}Install League HUD ${u.latest} now?\n\nThe app will close and reopen (about 10 seconds); the HUD pauses while it restarts. Your settings, logos and output files are kept.${notes}`))
      post("/api/update/install");
  };
  version.title = "Check for updates";
  version.onclick = async () => {
    version.textContent = "checking…";
    await post("/api/update/check");
    if (info && !info.available) { version.textContent = `${info.current} - up to date`; noteUntil = Date.now() + 4000; }
  };
  return render;
}
