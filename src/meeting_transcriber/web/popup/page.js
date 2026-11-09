  // The processing panel is redrawn every second while a step runs. A click needs the same button on mouse-down and
  // mouse-up, so while a button is held down the redraw waits until it is released.
  let pressing = false, deferred = null, pressTimer = null;
  function release() {
    pressing = false;
    clearTimeout(pressTimer);
    if (deferred) { const d = deferred; deferred = null; setTimeout(() => window.render(d), 0); }
  }
  document.addEventListener("mousedown", () => { pressing = true; clearTimeout(pressTimer); pressTimer = setTimeout(release, 1500); }, true);
  document.addEventListener("mouseup", () => setTimeout(release, 0), true);

  let lastJson = "";
  window.render = function (s) {
    if (pressing && s.kind === "status") { deferred = s; return; }
    if (s.kind === "docs" || s.kind === "status" || s.kind === "ctl") {
      const json = JSON.stringify({ ...s, fresh: false });
      if (!s.fresh && json === lastJson && spec && spec.kind === s.kind) { report(); return; }  // nothing changed: leave the buttons alone (a fresh one always draws, to slide in)
      lastJson = json;
      if (s.kind === "docs" && spec && spec.kind === "docs" && patchDocs(s)) { spec = { ...s, fresh: false }; report(); return; }
    } else {
      lastJson = "";
    }
    if (s.fresh || s.kind !== "menu") subOpen = false;
    spec = { ...s, fresh: false };
    hov = null;
    const scroll = wrap.querySelector(".rows"), keep = scroll ? scroll.scrollTop : 0;
    wrap.innerHTML = s.kind === "tip" ? renderTip(s) : s.kind === "toast" ? renderToast(s) : s.kind === "ctl" ? renderCtl(s) : s.kind === "menu" ? renderMenu(s) : s.kind === "docs" ? renderDocs(s) : renderStatus(s);
    const again = wrap.querySelector(".rows");
    if (again && keep) again.scrollTop = keep;  // the list is redrawn every second while files are being read: stay where you were
    report();
  };

  function report() {
    const r = wrap.getBoundingClientRect();
    let h = Math.ceil(r.height);
    // The processing panel's window is always as tall as it can ever be: opening or closing a tab only changes what is drawn
    // inside it, never the window (resizing a web view flickers). Python lets clicks through everywhere but the card.
    if (spec && spec.kind === "status" && spec.maxH) h = Math.max(h, Math.floor(spec.maxH) + 28);
    lastSize = Math.ceil(r.width) + "x" + h;
    post({ op: "size", w: Math.ceil(r.width), h });
    postRects();
  }

  // The bar has slid all the way out (Python says so, a moment after it started): the buttons respond from now on, and their
  // places are measured again (they were measured while the bar was still moving).
  window.__ready = function () {
    const c = wrap.querySelector(".ctlclip");
    if (c && !c.classList.contains("out")) { c.classList.add("ready"); postRects(); }
  };
  window.__slideOut = function () { const c = wrap.querySelector(".ctlclip"); if (c) c.classList.add("out"); };
  window.__fade = function () { const t = wrap.querySelector(".toast"); if (t) t.classList.add("fade"); };
  window.addEventListener("resize", () => postRects());
  wrap.addEventListener("scroll", () => postRects(), true);  // scrolling the documents list moves the rows  // the panel was resized to fit: positions changed

  // What Python needs to know about this page: for the menu, which parts are really menu (everywhere else a click
  // must reach the app behind); for any page, where the rows and buttons are, so it can tell which one the pointer
  // is over (the page does not reliably hear about the mouse while its panel is not the key window).
  function postRects() {
    if (!spec) return;
    const list = [];
    if (spec.kind === "menu" || spec.kind === "status") {
      wrap.querySelectorAll(".card").forEach((el) => {
        if (getComputedStyle(el).visibility === "hidden") return;
        const r = el.getBoundingClientRect();
        list.push({ x: r.x, y: r.y, w: r.width, h: r.height });
      });
    }
    const rows = [];
    wrap.querySelectorAll(".row, .b, .x, .tr, .cb, .th, .ib i").forEach((el, i) => {
      el.dataset.hi = i;
      const card = el.closest(".card");
      if (!card || getComputedStyle(card).visibility === "hidden") return;
      // (the sliding bar is measured where it will rest, not where the animation has it right now)
      let r = el.getBoundingClientRect();
      if (spec.kind === "ctl") {
        const m = new DOMMatrix(getComputedStyle(el.closest(".ctl")).transform);  // take the slide's offset back out
        r = { x: r.x - m.m41, y: r.y - m.m42, width: r.width, height: r.height, top: r.top - m.m42, bottom: r.bottom - m.m42 };
      }
      const box = el.closest(".rows");
      if (box) {  // a row scrolled out of the list can not be hovered or clicked
        const b = box.getBoundingClientRect();
        if (r.bottom < b.top + 2 || r.top > b.bottom - 2) return;
      }
      rows.push({ i, x: r.x, y: r.y, w: r.width, h: r.height, tip: el.dataset.tip || "", sub: el.dataset.sub || "", tab: el.classList.contains("th") });
    });
    rows.sort((a, b) => a.w * a.h - b.w * b.h);  // a tab's icon buttons sit inside its row: the smaller one wins when the pointer is on both
    post({ op: "rects", rects: list, rows });
  }

  // Python found the pointer over item i (-1: none). In the menu, "Summary style" opens its submenu, another menu
  // row closes it, a submenu row (or the gap between the two) leaves it as it is.
  window.__setHoverRow = function (i) {
    if (hov) hov.classList.remove("hov");
    hov = i >= 0 ? wrap.querySelector('[data-hi="' + i + '"]') : null;
    if (hov) {
      hov.classList.add("hov");
      if (spec && spec.kind === "menu" && !hov.closest(".sub")) setSub(!!hov.dataset.sub);
    }
  };

  wrap.addEventListener("click", (e) => {
    const row = e.target.closest(".row, .b, .x, .tr, .cb, .th, .ib i");
    if (!row || !spec) return;
    if (spec.kind === "status" && row.dataset.tab) { toggleTab(Number(row.dataset.tab)); return; }
    if (spec.kind === "ctl") {
      const clip = wrap.querySelector(".ctlclip");  // the buttons only respond once the bar has fully slid out (and not while it slides back)
      if (row.dataset.act && clip && clip.classList.contains("ready") && !clip.classList.contains("out")) post({ op: "ctl_" + row.dataset.act });
      return;
    }
    if (spec.kind === "docs") { if (row.dataset.act) post({ op: row.dataset.act, id: row.dataset.id || null }); return; }
    if (row.dataset.sub) { setSub(true); return; }
    if (spec.kind === "menu" && row.dataset.id) post({ op: "menu_click", id: row.dataset.id });
    else if (row.dataset.act) post({ op: "status_" + row.dataset.act, id: row.dataset.job ? Number(row.dataset.job) : null });
  });
  wrap.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && (e.target.classList.contains("row") || e.target.classList.contains("th"))) { e.preventDefault(); e.target.click(); }
  });

  document.addEventListener("click", (e) => { const b = e.target.closest("button, .row"); if (b) b.blur(); }, true);

  post({ op: "ready" });
