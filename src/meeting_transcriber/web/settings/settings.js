"use strict";
const $ = (id) => document.getElementById(id);
const FIELDS = ["language", "model", "compress_audio", "default_prompt", "my_label", "others_label",
                "meetings_dir", "claude_path", "claude_model"];
const CHECKS = ["auto_summarize", "export_docx", "start_muted_mic", "start_muted_system", "diarize", "overlay_in_captures", "use_notes", "use_documents", "documents_ocr"];
for (let n = 1; n <= 12; n++) $("diarize_speakers").add(new Option(n === 1 ? "1 other person" : n + " other people", String(n)));
const syncDiarize = () => { $("diarize_speakers").disabled = !$("diarize").checked; };
$("diarize").addEventListener("change", syncDiarize);
let state = null;            // last snapshot from the app
let currentPrompt = null;
const drafts = {};           // unsaved prompt edits, by name

// ---- bridge to the app (no network involved: a WebKit message handler)
let nextId = 1; const pending = {};
window.__reply = (id, ok, payload) => { const p = pending[id]; delete pending[id]; if (p) (ok ? p.res(payload) : p.rej(new Error(payload))); };
function call(op, args = {}) {
  return new Promise((res, rej) => {
    const id = nextId++; pending[id] = { res, rej };
    window.webkit.messageHandlers.bridge.postMessage(JSON.stringify({ id, op, args }));
  });
}

function say(text, kind = "") { const s = $("status"); s.textContent = text; s.className = kind; }
async function run(fn, okText) {
  try { const r = await fn(); if (okText) say(okText, "ok"); return r; }
  catch (e) { say(e.message, "err"); return null; }
}

// ---- dialogs (confirm()/prompt() do nothing in an embedded web view, so we draw our own)
function ask({ title, body = "", ok = "OK", input = null, danger = false }) {
  const d = $("dlg"), inp = $("dlgInput");
  $("dlgTitle").textContent = title; $("dlgBody").textContent = body; $("dlgOk").textContent = ok;
  $("dlgOk").classList.toggle("danger", danger);
  inp.hidden = input === null; inp.value = input ?? "";
  return new Promise((resolve) => {
    d.addEventListener("close", () => resolve(d.returnValue === "ok" ? (input === null ? true : inp.value) : null), { once: true });
    d.returnValue = ""; d.showModal(); if (input !== null) inp.focus();
  });
}

// ---- tabs
const TABS = ["recording", "transcription", "summaries", "styles", "shortcuts"];
function showTab(name) {
  document.querySelectorAll("nav button").forEach((x) => x.setAttribute("aria-selected", x.dataset.tab === name));
  for (const t of TABS) $("tab-" + t).hidden = t !== name;
  say("");
}
document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
$("editStyles").addEventListener("click", () => showTab("styles"));

// ---- hotkey recorder
const SYMBOL = { ctrl: "⌃", alt: "⌥", shift: "⇧", cmd: "⌘" };
function prettyKey(spec) {
  if (!spec) return "";
  return spec.split("+").map((p) => { const m = p.match(/^<(.+)>$/); const n = m ? m[1] : p;
    return SYMBOL[n] || (m ? n.toUpperCase() : n.toUpperCase()); }).join("");
}
function keySpec(e) {
  let key = null;
  if (/^Key[A-Z]$/.test(e.code)) key = e.code.slice(3).toLowerCase();
  else if (/^Digit[0-9]$/.test(e.code)) key = e.code.slice(5);
  else if (/^F([1-9]|1[0-9]|20)$/.test(e.code)) key = "<" + e.code.toLowerCase() + ">";
  if (!key) return null;
  const mods = [e.ctrlKey && "<ctrl>", e.altKey && "<alt>", e.shiftKey && "<shift>", e.metaKey && "<cmd>"].filter(Boolean);
  return mods.length ? [...mods, key].join("+") : null;
}
const HOTKEYS = ["hotkey_record", "hotkey_screenshot", "hotkey_notes", "hotkey_documents", "hotkey_mute_mic", "hotkey_mute_system", "hotkey_notes_time"];
for (const id of HOTKEYS) {
  const el = $(id);
  el.addEventListener("focus", () => { el.classList.add("recording"); el.value = "Press keys…"; });
  el.addEventListener("blur", () => { el.classList.remove("recording"); el.value = prettyKey(el.dataset.spec); });
  el.addEventListener("keydown", (e) => {
    e.preventDefault();
    if (e.key === "Escape") { el.blur(); return; }
    if (["Control", "Alt", "Shift", "Meta"].includes(e.key)) return;
    const spec = keySpec(e);
    if (!spec) { say("Use at least one modifier (Ctrl, Option, Shift, Command) with a letter, digit or F-key.", "err"); return; }
    el.dataset.spec = spec; el.blur(); say("");
  });
}

// ---- general settings
function parseVocab(text) { return text.split(/[\n,]/).map((s) => s.trim()).filter(Boolean); }
function fill(values) {
  for (const k of FIELDS) {
    const el = $(k);
    if (el.tagName === "SELECT" && ![...el.options].some((o) => o.value === values[k])) {
      el.add(new Option(values[k] + " (custom)", values[k]));
    }
    el.value = values[k] ?? "";
  }
  for (const k of CHECKS) $(k).checked = !!values[k];
  $("diarize_speakers").value = String(values.diarize_speakers ?? 0);
  syncDiarize();
  $("vocabulary").value = values.vocabulary.join(", ");
  const scale = $("screenshot_scale"), px = state.screen_px;
  scale.innerHTML = "";
  for (const p of [...new Set([100, 75, 50, 33, 25, values.screenshot_scale])].sort((a, b) => b - a)) {
    const label = document.createElement("label"), radio = document.createElement("input"), pct = document.createElement("span");
    radio.type = "radio"; radio.name = "screenshot_scale"; radio.value = p; radio.checked = p === values.screenshot_scale;
    pct.className = "pct"; pct.textContent = p + "%";
    if (px) label.append(radio, px.map((n) => Math.round(n * p / 100)).join(" × ") + " px", pct);
    else label.append(radio, p + "% of the screen");  // display size unknown: no pixels to show
    scale.append(label);
  }
  for (const k of HOTKEYS) { $(k).dataset.spec = values[k]; $(k).value = prettyKey(values[k]); }
}
function collect() {
  const v = {};
  for (const k of FIELDS) v[k] = $(k).value;
  for (const k of CHECKS) v[k] = $(k).checked;
  v.vocabulary = parseVocab($("vocabulary").value);
  v.diarize_speakers = Number($("diarize_speakers").value);
  v.screenshot_scale = Number(document.querySelector('input[name="screenshot_scale"]:checked')?.value);
  for (const k of HOTKEYS) v[k] = $(k).dataset.spec;
  return v;
}
function render(snap) {
  state = snap;
  const sel = $("default_prompt"); sel.innerHTML = "";
  for (const p of snap.prompts) sel.add(new Option(p.name, p.name));
  fill(snap.values);
  $("claudeFound").textContent = snap.claude_found ? "Detected: " + snap.claude_found : "Claude CLI not found - install it or set the path.";
  renderPrompts();
}
$("save").addEventListener("click", () => run(async () => {
  render(await call("save_values", { values: collect() }));
}, "Saved. Shortcuts and the floating bar are updated; other changes apply from the next recording."));
$("choose").addEventListener("click", () => run(async () => { const p = await call("choose_folder"); if (p) $("meetings_dir").value = p; }));
$("openDir").addEventListener("click", () => run(() => call("open_folder", { path: $("meetings_dir").value })));
$("resetAll").addEventListener("click", async () => {
  if (!(await ask({ title: "Reset settings to defaults?", body: "Every setting goes back to its default. Your current settings are kept as config.toml.bak. Your summary styles are not affected.", ok: "Reset", danger: true }))) return;
  run(async () => render(await call("reset_settings")), "Settings reset to defaults.");
});

// ---- prompts
function dirty(name) { return name in drafts && drafts[name] !== state.prompts.find((p) => p.name === name)?.text; }
function renderPrompts() {
  const list = $("promptList"); list.innerHTML = "";
  if (!state.prompts.some((p) => p.name === currentPrompt)) currentPrompt = state.prompts[0]?.name ?? null;
  for (const p of state.prompts) {
    const b = document.createElement("button");
    b.textContent = p.name + (dirty(p.name) ? " •" : "");
    if (p.builtin) { const t = document.createElement("span"); t.className = "tag"; t.textContent = "built-in"; b.append(t); }
    b.setAttribute("aria-current", p.name === currentPrompt);
    b.addEventListener("click", () => { stash(); currentPrompt = p.name; renderPrompts(); });
    list.append(b);
  }
  const p = state.prompts.find((x) => x.name === currentPrompt);
  $("promptText").value = p ? (drafts[p.name] ?? p.text) : "";
  $("resetPrompt").hidden = !p?.builtin; $("renamePrompt").hidden = !p || p.builtin; $("deletePrompt").hidden = !p || p.builtin;
}
function stash() {
  if (currentPrompt) { const v = $("promptText").value; const orig = state.prompts.find((p) => p.name === currentPrompt)?.text;
    if (v === orig) delete drafts[currentPrompt]; else drafts[currentPrompt] = v; }
}
$("promptText").addEventListener("input", () => { stash(); renderPromptsKeepFocus(); });
function renderPromptsKeepFocus() { // update the list's unsaved marker without touching the textarea
  [...$("promptList").children].forEach((b, i) => { const p = state.prompts[i];
    b.firstChild.textContent = p.name + (dirty(p.name) ? " •" : ""); });
}
$("savePrompt").addEventListener("click", () => run(async () => {
  const name = currentPrompt; render(await call("save_prompt", { name, text: $("promptText").value })); delete drafts[name]; renderPrompts();
}, "Style saved. It is used for the next summary."));
$("resetPrompt").addEventListener("click", async () => {
  const name = currentPrompt;
  if (!(await ask({ title: `Reset the "${name}" style to its default?`, body: "Your edited version is kept as " + name + ".md.bak.", ok: "Reset", danger: true }))) return;
  run(async () => { delete drafts[name]; render(await call("reset_prompt", { name })); }, "Style reset to default.");
});
$("newPrompt").addEventListener("click", async () => {
  const name = await ask({ title: "New style", body: "It starts as a copy of your default style.", ok: "Create", input: "" });
  if (!name) return;
  run(async () => { stash(); render(await call("new_prompt", { name })); currentPrompt = name.trim(); renderPrompts(); }, "Style created.");
});
$("renamePrompt").addEventListener("click", async () => {
  const old = currentPrompt, name = await ask({ title: `Rename "${old}"`, ok: "Rename", input: old });
  if (!name || name === old) return;
  run(async () => { const d = drafts[old]; delete drafts[old]; render(await call("rename_prompt", { old, new: name })); currentPrompt = name.trim(); if (d !== undefined) drafts[currentPrompt] = d; renderPrompts(); }, "Renamed.");
});
$("deletePrompt").addEventListener("click", async () => {
  const name = currentPrompt;
  if (!(await ask({ title: `Delete the "${name}" style?`, body: "This removes the style file.", ok: "Delete", danger: true }))) return;
  run(async () => { delete drafts[name]; render(await call("delete_prompt", { name })); }, "Deleted.");
});

// ---- start
window.start = () => run(async () => render(await call("load")));
window.addEventListener("load", () => { if (window.webkit?.messageHandlers?.bridge) window.start(); });
