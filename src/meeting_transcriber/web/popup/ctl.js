  const CTL_PAUSE = '<svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1.2"/><rect x="14" y="5" width="4" height="14" rx="1.2"/></svg>';
  const CTL_PLAY = '<svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5.5v13l11-6.5z"/></svg>';
  const CTL_BIN = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6.5 7l1 13h9l1-13M10 11v6M14 11v6"/></svg>';

  function renderCtl(s) {  // the first button pauses (resumes, when paused), the second cancels the recording
    const side = s.side === "right" ? "right" : "";
    return '<div class="ctlclip ' + side + (s.fresh ? " in" : " ready") + '"><div class="card ctl ' + side + '"><div class="cb ' + (s.paused ? "amb" : "") + '" data-act="pause">' + (s.paused ? CTL_PLAY : CTL_PAUSE) +
      '</div><div class="cb red" data-act="cancel">' + CTL_BIN + "</div></div></div>";
  }
