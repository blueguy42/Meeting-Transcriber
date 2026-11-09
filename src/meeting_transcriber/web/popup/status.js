  function icon(state) {
    return state === "done" ? CHECK : state === "now" ? '<span class="spin"></span>' : state === "paused" ? PAUSE_S : state === "cancelled" ? X_S : '<span class="ring"></span>';
  }

  function renderSteps(job) {
    return job.steps.map((st) => {
      const kids = (st.children || []).map((c) =>
        '<div class="kid ' + c.state + '">' + (c.state === "done" ? CHECK_S : c.state === "now" ? '<span class="spin" style="width:11px;height:11px;border-width:2px"></span>' : c.state === "paused" ? PAUSE_S : c.state === "cancelled" ? X_S : '<span class="ring" style="width:11px;height:11px"></span>') +
        "<span>" + esc(c.label) + '</span><span class="r">' + esc(c.sub || "") + "</span></div>").join("");
      return '<div class="step ' + st.state + '"><span class="ic">' + icon(st.state) + "</span><div>" + esc(st.label) + (st.sub ? "<small>" + esc(st.sub) + "</small>" : st.detail && st.state !== "done" ? "<small>" + esc(st.detail).replace(/\n/g, "<br>") + "</small>" : "") + "</div></div>" + kids;
    }).join("");
  }

  // ---- the processing panel: one tab per meeting, newest first. Clicking a tab's row opens or closes it (one open at a time,
  // none is allowed); the icon buttons on its right act without opening it. Until you open or close a tab yourself the
  // meeting being processed is open; after that your choice stays.
  let openJob = null, touched = false;
  const IC = (path, fill) => '<svg width="16" height="16" viewBox="0 0 24 24" fill="' + (fill ? "currentColor" : "none") + '" stroke="' + (fill ? "none" : "currentColor") + '" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + path + "</svg>";
  const I_PAUSE = IC('<rect x="6" y="5" width="4" height="14" rx="1.2"/><rect x="14" y="5" width="4" height="14" rx="1.2"/>', true);
  const I_PLAY = IC('<path d="M8 5.5v13l11-6.5z"/>', true);
  const I_X = IC('<path d="M6 6l12 12M18 6L6 18"/>').replace('stroke-width="1.9"', 'stroke-width="2.4"');
  const I_DOC = IC('<path d="M6 3h9l4 4v14H6z"/><path d="M15 3v4h4"/><path d="M9 12h7M9 16h5"/>');
  const I_FOLDER = IC('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>');
  const I_AGAIN = IC('<path d="M20 12a8 8 0 1 1-2.5-5.8"/><path d="M20 4v5h-5"/>');
  const CHEV_C = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>';
  const CHEV_O = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>';
  const S_FAIL = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#ff453a" stroke-width="2.6" stroke-linecap="round" aria-hidden="true"><path d="M12 5v9M12 18v1"/></svg>';
  const hhmm = (t) => { const d = new Date(t * 1000); return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0"); };

  function tabInfo(j) {  // the status icon, the one-line status and the icon buttons of a meeting's tab
    const cur = j.steps.find((st) => st.state === "now" || st.state === "paused");
    const btn = (act, tip, icon, cls) => ({ act, tip, icon, cls: cls || "" });
    if (j.state === "queued") return { si: '<span class="ring"></span>', line: j.stopping ? "Cancelling…" : "Waiting for the meeting before it", cls: "", btns: j.stopping ? [] : [btn("cancel", "Cancel", I_X, "red")] };
    if (j.state === "paused") return { si: PAUSE_S, line: "Paused at " + (cur ? cur.label : "…"), cls: "amb", btns: [btn("resume", "Resume", I_PLAY, "amb"), btn("cancel", "Cancel", I_X, "red")] };
    if (j.state === "done") return { si: CHECK, line: "Done" + (j.finished ? " · " + hhmm(j.finished) : ""), cls: "grn", btns: [btn("summary", "Open summary", I_DOC), btn("folder", "Show folder", I_FOLDER), btn("dismiss", "Dismiss", I_X)] };
    if (j.state === "failed") return { si: S_FAIL, line: "Failed · " + (j.error || "see the folder"), cls: "red", btns: [btn("again", "Process again", I_AGAIN), btn("folder", "Show folder", I_FOLDER), btn("dismiss", "Dismiss", I_X)] };
    if (j.state === "cancelled") return { si: X_S, line: "Cancelled · the recording is kept", cls: "red", btns: [btn("again", "Process again", I_AGAIN), btn("dismiss", "Dismiss", I_X)] };
    let line = cur ? cur.label : "Working";  // running
    const kid = cur && (cur.children || []).find((c) => c.state === "now" && c.sub);
    if (kid) line += " · " + kid.sub; else if (cur && cur.sub) line += " · " + cur.sub;
    if (j.stopping) return { si: '<span class="spin"></span>', line: j.stopping === "cancel" ? "Stopping…" : "Pausing…", cls: "", btns: [] };
    return { si: '<span class="spin"></span>', line, cls: "", btns: [btn("pause", "Pause", I_PAUSE), btn("cancel", "Cancel", I_X, "red")] };
  }

  function openOf(jobs) {
    if (touched) return jobs.some((j) => j.id === openJob) ? openJob : null;
    if (jobs.length === 1) return jobs[0].id;
    const run = jobs.find((j) => j.state === "active");
    return run ? run.id : null;
  }

  function renderStatus(s) {
    const open = openOf(s.jobs);
    const tabs = s.jobs.slice().reverse().map((j) => {
      const t = tabInfo(j), isOpen = j.id === open;
      const buttons = t.btns.length ? '<div class="ib">' + t.btns.map((b) => '<i class="' + b.cls + '" data-act="' + b.act + '" data-job="' + j.id + '" data-tip="' + esc(b.tip) + '" role="button" aria-label="' + esc(b.tip) + '">' + b.icon + "</i>").join("") + "</div>" : "";
      const head = '<div class="th" tabindex="0" role="button" aria-expanded="' + isOpen + '" data-tab="' + j.id + '" data-tip="' + esc(j.name) + '" data-sub="' + esc(j.when || "") + '"><span class="chev">' + (isOpen ? CHEV_O : CHEV_C) + '</span><span class="si">' + t.si + '</span><div class="tt"><b>' + esc(j.name) + '</b><small class="' + t.cls + '">' + esc(t.line) + "</small></div>" + buttons + "</div>";
      let body = "";
      if (isOpen) {
        if (j.state === "failed") body = '<div class="err">' + esc(j.error || "Processing failed.") + "</div>";
        else body = renderSteps(j) + (j.state === "paused" && j.note ? '<div class="snote">' + esc(j.note) + "</div>" : "");
      }
      return '<div class="tb2" data-job="' + j.id + '">' + head + (body ? '<div class="tbody">' + body + "</div>" : "") + "</div>";
    }).join("");
    const foot = s.canRecord ? '<div class="btns"><button class="b" data-act="record">Start a new recording</button></div>' : "";
    const max = s.maxH ? "max-height:" + Math.max(160, Math.floor(s.maxH)) + "px" : "";
    return '<div class="card status" style="' + max + '"><div class="hd"><span>' + (s.jobs.length > 1 ? s.jobs.length + " meetings" : "Processing") + '</span><button class="x" data-act="close" aria-label="Close status">' + CLOSE + "</button></div>" +
      '<div class="rows">' + tabs + '</div><div class="tfoot">' + foot + '<div class="note">Meetings are processed one at a time. You can record while they run.</div></div></div>';
  }

  function toggleTab(id) {
    const wasOpen = openOf(spec.jobs) === id;  // (before your choice starts to count)
    touched = true;
    openJob = wasOpen ? null : id;
    const scroll = wrap.querySelector(".rows"), keep = scroll ? scroll.scrollTop : 0;
    hov = null;
    wrap.innerHTML = renderStatus(spec);
    const again = wrap.querySelector(".rows");
    if (again) { again.scrollTop = keep; const t = again.querySelector('.tb2[data-job="' + openJob + '"]'); if (t) t.scrollIntoView({ block: "nearest" }); }
    lastJson = "";
    report();
  }
