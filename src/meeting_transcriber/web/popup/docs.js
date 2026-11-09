  const TRASH = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"/></svg>';
  const WARN = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17v.5"/></svg>';
  const KIND = { pdf: "PDF", docx: "DOCX", pptx: "PPTX", xlsx: "XLSX", txt: "TXT", img: "IMG" };

  function renderDocs(s) {
    const rows = s.docs.map((d) => {
      const st = d.status, icon = st.k === "ok" ? CHECK_S : st.k === "run" ? '<span class="spin" style="width:11px;height:11px;border-width:2px"></span>' : st.k === "wait" ? "" : WARN;
      const bar = st.k === "run" ? '<div class="pb"><i style="width:' + Math.round((d.pct || 0) * 100) + '%"></i></div>' : "";
      return '<div class="dr" data-id="' + esc(d.id) + '"><div class="ft ' + esc(d.kind) + '">' + (KIND[d.kind] || "") + '</div><div class="dm"><div class="dn">' + esc(d.name) + '</div><div class="dd">' + esc(d.meta) + "</div>" + bar +
        '</div><div class="ds ' + (st.k === "run" || st.k === "wait" ? "" : st.k) + '">' + icon + "<span>" + esc(st.text) + '</span></div><button class="tr" data-act="docs_delete" data-id="' + esc(d.id) + '" aria-label="Delete ' + esc(d.name) + '">' + TRASH + "</button></div>";
    }).join("");
    const body = s.docs.length ? rows : '<div class="empty"><b>No documents yet</b>Add an agenda, slides or a report to give Claude more context for the summary.</div>';
    wrap.style.flexDirection = s.side === "right" ? "row-reverse" : "row";
    return '<div class="card docs" style="max-height:' + Math.max(160, Math.floor(s.maxH || 420)) + 'px"><div class="dh"><div><b>Documents</b><span class="n">' + s.docs.length + ' added</span></div><button class="x" data-act="docs_close" aria-label="Close documents">' + CLOSE + "</button></div>" +
      '<div class="rows">' + body + '</div><div class="dfoot"><button class="b" data-act="docs_add">Add documents…</button></div></div>';
  }

  // The list is refreshed every second while files are being read. Redrawing it would replace the very button the pointer is
  // pressing (a click needs the same element on mouse-down and mouse-up), so rows are updated in place unless the set of rows changed.
  function patchDocs(s) {
    const card = wrap.querySelector(".card.docs");
    if (!card) return false;
    const rows = [...card.querySelectorAll(".dr")];
    if (rows.length !== s.docs.length || rows.some((r, i) => r.dataset.id !== s.docs[i].id || (s.docs[i].status.k === "run" && !r.querySelector(".pb")))) return false;
    rows.forEach((r, i) => {
      const d = s.docs[i], st = d.status;
      r.querySelector(".dd").textContent = d.meta;
      const ds = r.querySelector(".ds");
      ds.className = "ds " + (st.k === "run" || st.k === "wait" ? "" : st.k);
      const icon = st.k === "ok" ? CHECK_S : st.k === "run" ? '<span class="spin" style="width:11px;height:11px;border-width:2px"></span>' : st.k === "wait" ? "" : WARN;
      const html = icon + "<span>" + esc(st.text) + "</span>";
      if (ds.dataset.h !== html) { ds.innerHTML = html; ds.dataset.h = html; }
      const pbox = r.querySelector(".pb"), pb = r.querySelector(".pb i");
      if (st.k !== "run" && pbox) pbox.remove();  // finished: the progress bar goes
      else if (pb) pb.style.width = Math.round((d.pct || 0) * 100) + "%";
    });
    card.querySelector(".dh .n").textContent = s.docs.length + " added";
    card.style.maxHeight = Math.max(160, Math.floor(s.maxH || 420)) + "px";
    return true;
  }
