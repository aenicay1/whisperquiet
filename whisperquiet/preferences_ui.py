"""Bundled local Preferences UI served by SettingsServer."""

HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WhisperQuiet Preferences</title>
<style>
:root {
  color-scheme: light;
  --bg: #f4f1ec;
  --panel: #fffdf8;
  --ink: #151719;
  --muted: #686f73;
  --line: #d8d1c8;
  --accent: #0b6b6d;
  --accent-2: #8f4f2f;
  --ok: #1f7a4d;
  --warn: #9a5a12;
  --bad: #a33b36;
  --shadow: 0 16px 42px rgba(20, 20, 20, .08);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  background: var(--bg);
  color: var(--ink);
}
button, input, select {
  font: inherit;
}
.shell {
  display: grid;
  grid-template-columns: 232px minmax(0, 1fr);
  min-height: 100vh;
}
.rail {
  border-right: 1px solid var(--line);
  padding: 24px 16px;
  background: #ece7df;
}
.brand {
  font-weight: 750;
  font-size: 18px;
  margin: 0 0 28px;
}
.nav {
  display: grid;
  gap: 6px;
}
.nav button {
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--ink);
  text-align: left;
  padding: 10px 12px;
  cursor: pointer;
}
.nav button.active {
  background: var(--panel);
  border-color: var(--line);
  box-shadow: 0 1px 0 rgba(255,255,255,.7) inset;
}
.main {
  padding: 28px;
  max-width: 1080px;
  width: 100%;
}
.top {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 22px;
}
h1 {
  margin: 0 0 6px;
  font-size: 28px;
  line-height: 1.12;
}
.sub {
  color: var(--muted);
  font-size: 14px;
}
.status {
  min-width: 240px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  padding: 12px 14px;
  box-shadow: var(--shadow);
}
.status strong {
  display: block;
  font-size: 12px;
  color: var(--muted);
  font-weight: 650;
  margin-bottom: 5px;
}
.status span {
  font-size: 14px;
}
.tab {
  display: none;
}
.tab.active {
  display: block;
}
.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
.panel {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  padding: 18px;
  box-shadow: var(--shadow);
}
.panel.full {
  grid-column: 1 / -1;
}
.panel h2 {
  margin: 0 0 12px;
  font-size: 16px;
}
.row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
  border-top: 1px solid var(--line);
  padding: 12px 0;
}
.row:first-of-type {
  border-top: 0;
  padding-top: 0;
}
.label {
  font-weight: 650;
  font-size: 14px;
}
.hint {
  color: var(--muted);
  font-size: 12px;
  line-height: 1.45;
  margin-top: 3px;
}
.model-list {
  display: grid;
  gap: 12px;
}
.model {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 12px;
}
.model.selected {
  border-color: var(--accent);
  background: #f3fbfa;
}
.model-title {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
  font-weight: 750;
}
.pill {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  border-radius: 999px;
  padding: 2px 8px;
  background: #ebe5dc;
  color: var(--muted);
  font-size: 12px;
  font-weight: 650;
}
.pill.active {
  background: var(--accent);
  color: #fff;
}
.metric {
  color: var(--muted);
  font-size: 12px;
  margin-top: 8px;
}
.actions {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.btn {
  min-height: 34px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  color: var(--ink);
  padding: 6px 11px;
  cursor: pointer;
}
.btn.primary {
  background: var(--accent);
  color: #fff;
  border-color: var(--accent);
}
.btn.danger {
  color: var(--bad);
}
.btn:disabled {
  cursor: default;
  opacity: .52;
}
.field {
  display: flex;
  gap: 8px;
  align-items: center;
}
.field input[type="text"] {
  min-height: 36px;
  flex: 1;
  min-width: 180px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  color: var(--ink);
  padding: 7px 10px;
}
.field input[type="number"],
.field select {
  width: 140px;
  min-height: 34px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #fff;
  color: var(--ink);
  padding: 5px 9px;
}
.terms {
  display: grid;
  gap: 8px;
  margin-top: 14px;
}
.term {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 9px 10px;
  background: #fff;
}
.term span {
  overflow-wrap: anywhere;
}
.empty {
  border: 1px dashed var(--line);
  border-radius: 8px;
  padding: 16px;
  color: var(--muted);
  text-align: center;
}
.toast {
  position: fixed;
  left: 50%;
  bottom: 20px;
  transform: translateX(-50%);
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--ink);
  color: #fff;
  padding: 10px 14px;
  box-shadow: var(--shadow);
  opacity: 0;
  pointer-events: none;
  transition: opacity .16s ease;
  font-size: 14px;
}
.toast.show { opacity: 1; }
@media (max-width: 820px) {
  .shell { grid-template-columns: 1fr; }
  .rail {
    border-right: 0;
    border-bottom: 1px solid var(--line);
    padding: 16px;
  }
  .nav { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .nav button { text-align: center; padding: 9px 6px; }
  .main { padding: 18px; }
  .top { display: grid; }
  .status { min-width: 0; }
  .grid { grid-template-columns: 1fr; }
  .model { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<div class="shell">
  <aside class="rail">
    <div class="brand">WhisperQuiet</div>
    <nav class="nav" aria-label="Preferences">
      <button data-tab="general" class="active">General</button>
      <button data-tab="models">Models</button>
      <button data-tab="dictionary">Dictionary</button>
      <button data-tab="advanced">Advanced</button>
    </nav>
  </aside>
  <main class="main">
    <div class="top">
      <div>
        <h1>Preferences</h1>
        <div class="sub">Local settings for the running menu bar app.</div>
      </div>
      <div class="status">
        <strong>Status</strong>
        <span id="statusText">Connecting...</span>
      </div>
    </div>

    <section id="tab-general" class="tab active">
      <div class="grid">
        <div class="panel">
          <h2>Dictation</h2>
          <div class="row">
            <div>
              <div class="label">Cleanup</div>
              <div class="hint">Rules-based cleanup before text lands.</div>
            </div>
            <input id="cleanup" type="checkbox">
          </div>
          <div class="row">
            <div>
              <div class="label">Injection</div>
              <div class="hint">Paste is faster; keystrokes avoid clipboard changes.</div>
            </div>
            <select id="injectMode">
              <option value="keystrokes">Keystrokes</option>
              <option value="paste">Paste</option>
            </select>
          </div>
        </div>
        <div class="panel">
          <h2>Storage</h2>
          <div class="row">
            <div>
              <div class="label">Keep audio</div>
              <div class="hint">Saved locally for dogfooding and benchmarks.</div>
            </div>
            <input id="keepAudio" type="checkbox">
          </div>
          <div class="row">
            <div>
              <div class="label">Keep transcripts</div>
              <div class="hint">Raw and cleaned pairs stay on this Mac.</div>
            </div>
            <input id="keepTranscripts" type="checkbox">
          </div>
        </div>
      </div>
    </section>

    <section id="tab-models" class="tab">
      <div class="panel full">
        <h2>Models</h2>
        <div id="models" class="model-list"></div>
      </div>
    </section>

    <section id="tab-dictionary" class="tab">
      <div class="panel full">
        <h2>Dictionary</h2>
        <div class="field">
          <input id="termInput" type="text" placeholder="Add a name, acronym, or phrase">
          <button id="addTerm" class="btn primary">Add</button>
        </div>
        <div class="field" style="margin-top:10px">
          <input id="termSearch" type="text" placeholder="Search">
        </div>
        <div id="terms" class="terms"></div>
      </div>
    </section>

    <section id="tab-advanced" class="tab">
      <div class="grid">
        <div class="panel">
          <h2>Performance</h2>
          <div class="row">
            <div>
              <div class="label">Idle unload seconds</div>
              <div class="hint">0 keeps the model warm all day.</div>
            </div>
            <input id="idleUnload" type="number" min="0" step="30">
          </div>
          <div class="row">
            <div>
              <div class="label">Partial update seconds</div>
              <div class="hint">Lower is more responsive and uses more compute.</div>
            </div>
            <input id="streamInterval" type="number" min="0.3" max="2" step="0.1">
          </div>
        </div>
        <div class="panel">
          <h2>Retention</h2>
          <div class="row">
            <div>
              <div class="label">Audio limit MB</div>
              <div class="hint">Local WAV retention cap.</div>
            </div>
            <input id="audioMb" type="number" min="0" step="64">
          </div>
          <div class="row">
            <div>
              <div class="label">Audio days</div>
              <div class="hint">Old files are pruned after this many days.</div>
            </div>
            <input id="audioDays" type="number" min="0" step="1">
          </div>
        </div>
      </div>
    </section>
  </main>
</div>
<div id="toast" class="toast"></div>
<script>
const TOKEN = "__WQ_TOKEN__";
let state = null;
let toastTimer = null;

const $ = (id) => document.getElementById(id);

function toast(text) {
  const el = $("toast");
  el.textContent = text;
  el.classList.add("show");
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 1800);
}

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-WQ-Token": TOKEN},
    body: JSON.stringify(body)
  };
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function setTab(name) {
  document.querySelectorAll(".nav button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === name);
  });
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("active", tab.id === `tab-${name}`);
  });
  if (location.hash !== `#${name}`) history.replaceState(null, "", `#${name}`);
}

function bindTabs() {
  document.querySelectorAll(".nav button").forEach((btn) => {
    btn.addEventListener("click", () => setTab(btn.dataset.tab));
  });
  const initial = (location.hash || "#general").slice(1);
  if (["general", "models", "dictionary", "advanced"].includes(initial)) setTab(initial);
}

function render() {
  $("statusText").textContent = state.status.title || "Unknown";
  const settings = state.settings || {};
  $("cleanup").checked = !!settings.cleanup_enabled;
  $("keepAudio").checked = !!settings.keep_audio;
  $("keepTranscripts").checked = !!settings.keep_transcripts;
  $("injectMode").value = settings.inject_mode || "keystrokes";
  $("idleUnload").value = settings.model_idle_unload_s ?? 0;
  $("streamInterval").value = settings.stream_interval ?? 0.7;
  $("audioMb").value = settings.audio_retention_mb ?? 512;
  $("audioDays").value = settings.audio_retention_days ?? 30;
  renderModels();
  renderTerms();
}

function renderModels() {
  const box = $("models");
  box.innerHTML = "";
  (state.model.choices || []).forEach((choice) => {
    const row = document.createElement("div");
    row.className = "model" + (choice.selected ? " selected" : "");
    const disabled = choice.selected || state.model.busy;
    row.innerHTML = `
      <div>
        <div class="model-title">
          <span>${escapeHtml(choice.label)}</span>
          ${choice.selected ? '<span class="pill active">Active</span>' : ''}
        </div>
        <div class="hint">${escapeHtml(choice.description || "")}</div>
        <div class="metric">${choice.memory_mb} MB active memory measured here / ${choice.download_mb} MB download cache</div>
        <div class="metric">${escapeHtml(choice.repo)}</div>
      </div>
      <div class="actions">
        <button class="btn primary" ${disabled ? "disabled" : ""}>Use</button>
      </div>`;
    row.querySelector("button").addEventListener("click", async () => {
      await save({model_profile: choice.id});
      toast("Switching model");
      await load();
    });
    box.appendChild(row);
  });
}

function normalizeTerm(term) {
  return term.trim().replace(/\.+$/, "");
}

function renderTerms() {
  const box = $("terms");
  const search = ($("termSearch").value || "").toLowerCase();
  const terms = (state.dictionary.terms || []).filter((term) => {
    return !search || term.toLowerCase().includes(search);
  });
  box.innerHTML = "";
  if (!terms.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No dictionary entries";
    box.appendChild(empty);
    return;
  }
  terms.forEach((term) => {
    const row = document.createElement("div");
    row.className = "term";
    row.innerHTML = `<span>${escapeHtml(term)}</span><button class="btn danger">Remove</button>`;
    row.querySelector("button").addEventListener("click", async () => {
      const next = (state.dictionary.terms || []).filter((t) => t !== term);
      await save({vocabulary: next});
      state.dictionary.terms = next;
      renderTerms();
      toast("Dictionary updated");
    });
    box.appendChild(row);
  });
}

async function addTerm() {
  const input = $("termInput");
  const term = normalizeTerm(input.value);
  if (!term) return;
  const existing = state.dictionary.terms || [];
  const next = existing.some((t) => t.toLowerCase() === term.toLowerCase())
    ? existing
    : existing.concat([term]);
  await save({vocabulary: next});
  input.value = "";
  state.dictionary.terms = next;
  renderTerms();
  toast("Dictionary updated");
}

async function save(payload) {
  await api("/api/state", payload);
  Object.assign(state.settings || {}, payload);
}

async function load() {
  state = await api("/api/state");
  render();
}

function bindControls() {
  $("addTerm").addEventListener("click", addTerm);
  $("termInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") addTerm();
  });
  $("termSearch").addEventListener("input", renderTerms);
  $("cleanup").addEventListener("change", () => save({cleanup_enabled: $("cleanup").checked}).then(() => toast("Saved")));
  $("keepAudio").addEventListener("change", () => save({keep_audio: $("keepAudio").checked}).then(() => toast("Saved")));
  $("keepTranscripts").addEventListener("change", () => save({keep_transcripts: $("keepTranscripts").checked}).then(() => toast("Saved")));
  $("injectMode").addEventListener("change", () => save({inject_mode: $("injectMode").value}).then(() => toast("Saved")));
  $("idleUnload").addEventListener("change", () => saveNumber("model_idle_unload_s", $("idleUnload"), 0, 86400));
  $("streamInterval").addEventListener("change", () => saveNumber("stream_interval", $("streamInterval"), 0.3, 2));
  $("audioMb").addEventListener("change", () => saveNumber("audio_retention_mb", $("audioMb"), 0, 100000));
  $("audioDays").addEventListener("change", () => saveNumber("audio_retention_days", $("audioDays"), 0, 3650));
}

async function saveNumber(key, input, min, max) {
  let value = Number(input.value);
  if (!Number.isFinite(value)) value = min;
  value = Math.max(min, Math.min(max, value));
  input.value = value;
  await save({[key]: value});
  toast("Saved");
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[ch]);
}

bindTabs();
bindControls();
load().catch((err) => {
  $("statusText").textContent = err.message || "Offline";
  toast("Could not connect");
});
</script>
</body>
</html>
"""
