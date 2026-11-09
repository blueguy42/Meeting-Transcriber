  const SVG = (w, body) => '<svg width="' + w + '" height="' + w + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + body + "</svg>";
  const ICON_MIC = (w) => SVG(w, '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>');
  const ICON_SYS = (w) => SVG(w, '<path d="M4 10v4h4l5 4V6L8 10z"/><path d="M16.5 9a4 4 0 0 1 0 6M19 6.5a8 8 0 0 1 0 11"/>');

  function renderToast(s) {  // s.lines: [{who: "Mic" | "System" | "Mic and System", device}]; two lines: one toast for both
    const one = s.lines.length === 1 ? s.lines[0].who : "Mic and System";
    const icon = one === "Mic" ? ICON_MIC(18) : one === "System" ? ICON_SYS(18) : '<span class="two">' + ICON_MIC(13) + "<i></i>" + ICON_SYS(13) + "</span>";
    const text = s.lines.map((l) => '<div><span class="l">' + esc(l.who) + ":</span> <b>" + esc(l.device) + "</b></div>").join("");
    return '<div class="card toast"><span class="ic">' + icon + '</span><div style="line-height:1.4">' + text + "</div></div>";
  }
