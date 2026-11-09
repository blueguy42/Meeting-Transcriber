  const $ = (id) => document.getElementById(id);
  const CIRC = 2 * Math.PI * 22;
  const GLYPHS = {
    wave: '<path d="M4 10v4M8 6v12M12 3v18M16 8v8M20 11v2"/>',
    doc: '<path d="M7 3h7l4 4v14H7z"/><path d="M10 12h5M10 16h5"/>',
    check: '<path d="M5 12.5l4.5 4.5L19 7.5" stroke="#30d158" stroke-width="2.6"/>',
    bang: '<path d="M12 5v9M12 18v1" stroke="#ff453a" stroke-width="2.6"/>',
    clock: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
    pause: '<path d="M9 6v12M15 6v12" stroke="#ffb340" stroke-width="2.6"/>',
    cancel: '<path d="M8 8l8 8M16 8l-8 8" stroke="#a9aab4" stroke-width="2.6"/>',
  };
  let state = { recording: false, elapsed: 0, micMuted: false, sysMuted: false, busy: 0, job: null, hidden: false, side: "right" };
  let lastSize = "";

  const mmss = (s) => String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");

  function setRing(color, fraction, track) {
    $("ring-track").setAttribute("stroke", track);
    $("ring-arc").setAttribute("stroke", fraction > 0 ? color : "transparent");  // a round cap would draw a dot at 0
    $("ring-arc").setAttribute("stroke-dasharray", (CIRC * fraction) + " " + CIRC);
  }

  window.render = function (s) {
    state = s;
    const job = s.job, rec = !!s.recording;
    document.body.classList.toggle("hidden-mode", !!s.hidden);
    $("bar").classList.toggle("recording", rec);

    // the big ring: record button, or the progress of the meeting being processed
    const showJob = !rec && job;
    $("dot").style.display = showJob ? "none" : "";
    $("glyph").style.display = showJob ? "block" : "none";
    let label = rec ? "Stop and summarize" : "Start recording", timer = "Ready";
    const paused = rec && !!s.paused;  // a real pause: amber, and the timer stands still
    $("dot").style.background = paused ? "#ffb340" : "";
    if (rec) {
      setRing(paused ? "#ffb340" : "#ff453a", 1, paused ? "#ffb340" : "#ff453a");
      timer = mmss(s.elapsed);
    } else if (!job) {
      setRing("#f2f2f5", 0, "#f2f2f5");
    } else if (job.stage === "done") {
      setRing("#30d158", 1, "#3a3b42"); $("glyph").innerHTML = GLYPHS.check; timer = "Done"; label = "Show result";
    } else if (job.stage === "paused") {
      setRing("#ffb340", Math.max(0.03, job.progress), "#3a3b42"); $("glyph").innerHTML = GLYPHS.pause; timer = "Paused"; label = "Show processing status";
    } else if (job.stage === "failed") {
      setRing("#ff453a", 1, "#3a3b42"); $("glyph").innerHTML = GLYPHS.bang; timer = "Failed"; label = "Show error";
    } else if (job.stage === "cancelled") {  // the only meeting left was cancelled: its tab is still there, so the ring says so
      setRing("#8d8e98", 1, "#3a3b42"); $("glyph").innerHTML = GLYPHS.cancel; timer = "Cancelled"; label = "Show processing status";
    } else {
      setRing("#64a8ff", Math.max(0.03, job.progress), "#3a3b42");
      $("glyph").innerHTML = job.stage === "transcribing" ? GLYPHS.wave : job.stage === "queued" ? GLYPHS.clock : GLYPHS.doc;
      timer = job.stage === "transcribing" ? Math.round(job.progress * 100) + "%" : job.stage === "queued" ? "Waiting" : job.stage === "reading" ? "Reading" : job.stage === "summarizing" ? "Writing" : "Saving";
      label = "Show processing status";
    }
    $("rec").setAttribute("aria-label", label);
    $("timer").textContent = timer;
    $("timer").style.color = paused || (!rec && job && job.stage === "paused") ? "#ffb340" : !rec && job && !["done", "failed", "cancelled"].includes(job.stage) ? "#64a8ff" : "";
    const b = $("badge");
    b.style.display = rec && s.busy > 0 ? "flex" : "none";
    b.textContent = s.busy;

    $("perm").style.display = s.perm ? "flex" : "none";
    $("perm-warn").style.display = s.restart ? "none" : "block";
    $("perm-restart").style.display = s.restart ? "block" : "none";
    $("perm").setAttribute("aria-label", s.restart ? "Restart to apply the permission" : "Permissions needed");
    $("hide-chev").innerHTML = s.side === "left" ? '<path d="M15 6l-6 6 6 6"/>' : '<path d="M9 6l6 6-6 6"/>';  // points at the edge it collapses to
    $("more").classList.toggle("active", !!s.menuOpen);  // lit while its menu is open
    $("shot").classList.toggle("off", !rec || paused);  // no screenshots while paused: the screen could show private things
    $("shot").setAttribute("aria-disabled", rec && !paused ? "false" : "true");
    $("notes").classList.toggle("off", !rec);
    $("notes").classList.toggle("active", !!s.notesOpen);  // lit while its panel is open
    $("notes").setAttribute("aria-disabled", rec ? "false" : "true");
    for (const id of ["docadd", "doclist"]) { $(id).classList.toggle("off", !rec); $(id).setAttribute("aria-disabled", rec ? "false" : "true"); }
    $("doclist").classList.toggle("active", !!s.docsOpen);  // lit while its list is open
    const db = $("docdot"), total = s.docsCount || 0, ready = s.docsReady || 0;
    db.style.display = rec && total > 0 ? "flex" : "none";  // this meeting has documents
    db.textContent = ready === total ? String(total) : ready + "/" + total;
    db.classList.toggle("busy", ready !== total);
    $("ndot").style.display = rec && s.notesLines > 0 ? "block" : "none";  // this meeting has notes
    for (const [id, muted, what] of [["mic", s.micMuted, "microphone"], ["sys", s.sysMuted, "system audio"]]) {
      $(id).classList.toggle("muted", !!muted);
      $(id).classList.toggle("off", paused);  // greyed while paused; the mute you had comes back on resume
      $(id).setAttribute("aria-label", (muted ? "Unmute " : "Mute ") + what);
    }

    // the slim edge tab shown while the bar is hidden
    const tab = $("tab");
    tab.className = "tab " + (s.side === "left" ? "edge-left" : "edge-right");
    $("tab-chev").innerHTML = s.side === "left" ? '<path d="M9 6l6 6-6 6"/>' : '<path d="M15 6l-6 6 6 6"/>';
    const dot = $("tab-dot");
    if (rec) { dot.style.cssText = "background:" + (paused ? "#ffb340" : "#ff453a"); }
    else if (job && job.stage === "done") { dot.style.cssText = "background:#30d158"; }
    else if (job && job.stage === "failed") { dot.style.cssText = "background:#ff453a"; }
    else if (job && job.stage === "paused") { dot.style.cssText = "background:#ffb340"; }
    else if (job && job.stage === "cancelled") { dot.style.cssText = "background:#8d8e98"; }
    else if (job) { dot.style.cssText = "background:#64a8ff"; }
    else { dot.style.cssText = "border:2px solid #8d8e98"; }
    $("tab-text").textContent = rec ? mmss(s.elapsed) : timer;
    $("tab-text").style.color = !rec && !job ? "#a9aab4" : "";
    $("tab-mic").style.display = s.micMuted ? "block" : "none";
    $("tab-sys").style.display = s.sysMuted ? "block" : "none";

    applyHover();
    const el = s.hidden ? tab : $("bar");
    const r = el.getBoundingClientRect();
    const size = Math.ceil(r.width) + "x" + Math.ceil(r.height) + (s.hidden ? "h" : "");
    if (size !== lastSize) { lastSize = size; post({ op: "size", w: Math.ceil(r.width), h: Math.ceil(r.height) }); }
    postHit();
  };

  $("rec").onclick = (e) => {
    hideTip();
    if (e.target.closest("#badge")) { post({ op: "status" }); return; }
    if (!state.recording && state.job) post({ op: "status" });
    else post({ op: "toggle" });
  };
  $("shot").onclick = () => { hideTip(); if (state.recording && !state.paused) post({ op: "shot" }); };
  $("notes").onclick = () => { hideTip(); if (state.recording) post({ op: "notes" }); };
  $("docadd").onclick = () => { hideTip(); if (state.recording) post({ op: "docs_add" }); };
  $("doclist").onclick = () => { hideTip(); if (state.recording) post({ op: "docs" }); };
  $("mic").onclick = () => { hideTip(); if (!state.paused) post({ op: "mute_mic" }); };
  $("sys").onclick = () => { hideTip(); if (!state.paused) post({ op: "mute_system" }); };
  $("perm").onclick = () => { hideTip(); post({ op: "perm" }); };
  $("quit").onclick = () => { hideTip(); post({ op: "quit" }); };
  $("hide").onclick = () => { hideTip(); post({ op: "hide" }); };
  $("more").onclick = () => { hideTip(); const r = $("more").getBoundingClientRect(); post({ op: "more", top: r.top, bottom: r.bottom }); };
  $("tab").onclick = () => { if (suppressClick) return; hideTip(); post({ op: "unhide" }); };

  // tooltips are drawn by Python in their own small panel; here we only say which button is hovered.
  // Python reports the mouse position (window.__hover) because WebKit does not track it while the panel is not key.
  function hideTip() { post({ op: "tip_hide" }); }

  // Which button the pointer is over is worked out by Python from the mouse position (the page does not reliably
  // hear about the mouse while the panel is not the key window). Python sends the id; here we only light it up.
  let hoveredId = null;
  function applyHover() {
    document.querySelectorAll(".hov").forEach((e) => e.classList.remove("hov"));
    if (hoveredId) { const el = document.querySelector('[data-tip="' + hoveredId + '"]'); if (el) el.classList.add("hov"); }
  }
  window.__setHover = function (id) { hoveredId = id; applyHover(); };
  // ...and Python needs to know where the buttons are, to tell which one that is.
  function postHit() {
    const items = [];
    document.querySelectorAll("[data-tip]").forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) items.push({ id: el.dataset.tip, x: r.x, y: r.y, w: r.width, h: r.height });
    });
    post({ op: "hit", items });
  }

  // Drag the bar by anything that is not a button, or the hidden tab (the page cannot move the window itself).
  // The tab is a button, so it only counts as a drag after a few pixels; a plain click shows the bar again.
  let drag = null, dragStarted = false, suppressClick = false;
  function startDrag(el, e, threshold) {
    drag = { x: e.screenX, y: e.screenY, threshold };
    dragStarted = false;
    el.setPointerCapture(e.pointerId);
  }
  function moveDrag(e) {
    if (!drag) return;
    const dx = e.screenX - drag.x, dy = e.screenY - drag.y;
    if (!dragStarted) {
      if (Math.hypot(dx, dy) < drag.threshold) return;
      dragStarted = true;
      document.body.classList.add("dragging");
      hideTip();
      post({ op: "drag", phase: "start" });
    }
    post({ op: "drag", phase: "move", dx, dy });
  }
  function endDrag() {
    if (!drag) return;
    const was = dragStarted;
    drag = null; dragStarted = false;
    document.body.classList.remove("dragging");
    if (was) { post({ op: "drag", phase: "end" }); suppressClick = true; setTimeout(() => { suppressClick = false; }, 0); }
  }
  for (const [el, threshold, ignoreButtons] of [[$("bar"), 0, true], [$("tab"), 4, false]]) {
    el.addEventListener("pointerdown", (e) => { if (!(ignoreButtons && e.target.closest("button"))) startDrag(el, e, threshold); });
    el.addEventListener("pointermove", moveDrag);
    el.addEventListener("pointerup", endDrag);
    el.addEventListener("pointercancel", endDrag);
  }

  // a clicked button must not stay focused (it would keep looking selected until you click elsewhere)
  document.addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) b.blur(); }, true);
  document.addEventListener("pointerup", (e) => { const b = e.target.closest("button"); if (b) setTimeout(() => b.blur(), 0); }, true);

  post({ op: "ready" });
