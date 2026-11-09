  const $ = (id) => document.getElementById(id);
  const ed = $("ed");
  const TIME = /\[(?:\d+:)?\d{1,2}:\d{2}\] ?/g;  // a time, with the space after it
  const SAVE_AFTER = 1000;  // ms of typing inactivity before the notes are saved (closing the panel or stopping saves at once)
  let elapsed = 0, elapsedAt = Date.now(), timeKey = "<cmd>+t", timer = null, lastSize = "";


  // ---- the notes as plain text: a timestamp chip is written out as [12:34], a line break as a newline
  function textOf() {
    let out = "";
    (function walk(n) {
      for (const c of n.childNodes) {
        if (c.nodeType === 3) out += c.data.replace(/ /g, " ");
        else if (c.classList && c.classList.contains("ts")) out += c.dataset.t;
        else if (c.nodeName === "BR") out += "\n";
        else { const block = /^(DIV|P)$/.test(c.nodeName) && out && !out.endsWith("\n"); if (block) out += "\n"; walk(c); }
      }
    })(ed);
    if (ed.lastChild && ed.lastChild.nodeName === "BR") out = out.slice(0, -1);  // the browser's placeholder break at the end
    return out;
  }

  function chip(t) {  // t is "[12:34]"
    const s = document.createElement("span");
    s.className = "ts"; s.contentEditable = "false"; s.dataset.t = t;
    const v = document.createElement("span");
    v.className = "tv"; v.textContent = t;
    s.append(v);
    return s;
  }
  function fill(text) {  // plain text -> editor, times become chips again
    ed.textContent = "";
    let last = 0, m;
    TIME.lastIndex = 0;
    while ((m = TIME.exec(text))) {
      if (m.index > last) ed.append(text.slice(last, m.index));
      ed.append(chip(m[0].trim()));
      if (m[0].endsWith(" ")) ed.append(" ");
      last = m.index + m[0].length;
    }
    if (last < text.length) ed.append(text.slice(last));
  }

  // ---- saving: a moment after you stop typing, and straight away when asked (flush)
  function setSaved(kind, text) { const s = $("saved"); s.className = kind; $("savedText").textContent = text; }
  function flush() {
    clearTimeout(timer); timer = null;
    post({ op: "notes_save", text: textOf() });
    setSaved("", "Saved");
  }
  function changed() {
    if (!ed.textContent.trim() && !ed.querySelector(".ts") && ed.innerHTML !== "") ed.innerHTML = "";  // so the placeholder comes back
    setSaved("pending", "Not saved yet");
    clearTimeout(timer); timer = setTimeout(flush, SAVE_AFTER);
    keepCaretVisible();
  }

  function setCaret(node, off) {  // put the caret somewhere and make sure it is drawn (it can vanish after a removal)
    const r = document.createRange(); r.setStart(node, off); r.collapse(true);
    const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
    ed.focus();
  }
  function caretBox() {  // where the caret is, even on an empty line
    const sel = getSelection();
    if (!sel.rangeCount || !ed.contains(sel.anchorNode)) return null;
    const r = sel.getRangeAt(0).cloneRange(); r.collapse(false);
    let q = r.getClientRects()[0];
    if (q && q.height > 0) return q;
    let el = sel.anchorNode.nodeType === 3 ? sel.anchorNode.parentElement : sel.anchorNode;
    if (el === ed && sel.anchorNode.childNodes[sel.anchorOffset]) el = sel.anchorNode.childNodes[sel.anchorOffset];
    if (el && el.nodeType === 1 && el !== ed) return el.getBoundingClientRect();
    return null;
  }
  // After Enter the browser scrolls the new line to the middle of the box. If the caret had to be scrolled into view from
  // below, it belongs at the bottom edge instead.
  let scrollBefore = 0;
  ed.addEventListener("beforeinput", () => { scrollBefore = ed.scrollTop; });
  function caretToBottom() {
    const q = caretBox(), box = ed.getBoundingClientRect(), pad = 10;
    if (!q) return;
    const bottomInContent = q.bottom - box.top + ed.scrollTop;  // the caret's bottom, measured from the top of the content
    if (bottomInContent > scrollBefore + ed.clientHeight - pad / 2) ed.scrollTop = Math.max(0, bottomInContent - ed.clientHeight + pad);  // it was below the visible part
  }
  ed.addEventListener("input", (e) => {
    if (e.inputType === "insertParagraph" || e.inputType === "insertLineBreak") {
      caretToBottom();
      requestAnimationFrame(caretToBottom);  // the browser's own scroll can land a moment later
      setTimeout(caretToBottom, 60);
    }
  });

  // a new line below the visible part must scroll the box so the caret stays in view
  function keepCaretVisible() {
    const sel = getSelection();
    if (!sel.rangeCount || !ed.contains(sel.anchorNode)) return;
    const r = sel.getRangeAt(0).cloneRange();
    r.collapse(false);
    let rect = r.getClientRects()[0];
    if (!rect || (rect.height === 0 && rect.top === 0)) {  // an empty line has no box of its own: use the end of the text
      const end = ed.getBoundingClientRect();
      if (ed.scrollHeight - ed.scrollTop - ed.clientHeight < 40) ed.scrollTop = ed.scrollHeight;
      return;
    }
    const box = ed.getBoundingClientRect(), pad = 10;
    if (rect.bottom > box.bottom - pad) ed.scrollTop += rect.bottom - box.bottom + pad;
    else if (rect.top < box.top + pad) ed.scrollTop -= box.top + pad - rect.top;
  }
  ed.addEventListener("input", changed);

  // ---- Add time: the recording time at the cursor, as one piece
  function nowSeconds() { return elapsed + (Date.now() - elapsedAt) / 1000; }
  function stamp(sec) {
    const s = Math.max(0, Math.floor(sec)), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
    const two = (n) => String(n).padStart(2, "0");
    return "[" + (h ? h + ":" + two(m) : two(m)) + ":" + two(r) + "]";
  }
  function addTime() {
    const before = getSelection().rangeCount ? getSelection().getRangeAt(0) : null;
    const inside = !!before && ed.contains(before.commonAncestorContainer);  // is there a caret in the notes?
    ed.focus();
    const t = stamp(nowSeconds());
    // Not execCommand("insertHTML"): WebKit splits the line it is in and leaves a second, unstyled copy of the time at the start of
    // the next one, whenever the caret is in a line that already has text and is not the first. The range API puts it exactly at the caret.
    const sel = getSelection();
    let r = sel.rangeCount ? sel.getRangeAt(0) : null;
    if (!inside || !r) { r = document.createRange(); r.selectNodeContents(ed); r.collapse(false); }  // no caret in the notes: the end (focusing would put it at the start)
    r.deleteContents();  // (a selection is replaced, as typing would)
    const gap = document.createTextNode("\u00a0");
    r.insertNode(gap);
    r.insertNode(chip(t));
    ed.normalize();  // the split left empty text nodes and the space beside the text that follows: one text node again (Backspace relies on it)
    setCaret(gap, 1);
    changed();
  }
  $("addtime").addEventListener("mousedown", (e) => e.preventDefault());  // keep the cursor in the text
  $("addtime").addEventListener("click", addTime);

  // ---- a timestamp is removed whole by Backspace or Delete
  // the space that goes with a time (the one Add time puts after it) is removed with it
  function isSpace(c) { return c === " " || c === "\u00a0"; }
  function timeAndSpace(back) {
    const sel = getSelection();
    if (!sel.rangeCount || !sel.isCollapsed || sel.anchorNode.nodeType !== 3) return null;
    const node = sel.anchorNode, off = sel.anchorOffset;
    if (back) {  // caret just after "[12:34]" + space
      const p = node.previousSibling;
      return off === 1 && isSpace(node.data[0]) && p && p.classList && p.classList.contains("ts") ? { chip: p, node } : null;
    }
    const n = node.nextSibling;  // caret just before a time, which has a space after it
    if (off === node.data.length && n && n.classList && n.classList.contains("ts") && n.nextSibling && n.nextSibling.nodeType === 3 && isSpace(n.nextSibling.data[0])) return { chip: n, node: n.nextSibling };
    return null;
  }
  function neighbour(back) {
    const sel = getSelection();
    if (!sel.rangeCount || !sel.isCollapsed) return null;
    let node = sel.anchorNode, off = sel.anchorOffset;
    if (node.nodeType === 3) {
      if (back ? off > 0 : off < node.data.length) return null;
      node = back ? node.previousSibling : node.nextSibling;
    } else {
      node = node.childNodes[back ? off - 1 : off] || null;
    }
    return node && node.classList && node.classList.contains("ts") ? node : null;
  }

  function matches(e, spec) {  // "<cmd>+t" against a key event (physical key, like the global shortcuts)
    const parts = spec.split("+"), key = parts.pop();
    const want = new Set(parts.map((p) => p.replace(/[<>]/g, "")));
    const have = [e.ctrlKey && "ctrl", e.altKey && "alt", e.shiftKey && "shift", e.metaKey && "cmd"].filter(Boolean);
    if (have.length !== want.size || !have.every((m) => want.has(m))) return false;
    const code = /^<f\d+>$/.test(key) ? key.slice(1, -1).toUpperCase() : /^\d$/.test(key) ? "Digit" + key : "Key" + key.toUpperCase();
    return e.code === code;
  }

  ed.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); flush(); post({ op: "notes_close" }); return; }
    if (matches(e, timeKey)) { e.preventDefault(); addTime(); return; }
    if ((e.metaKey || e.ctrlKey) && /^[biu]$/i.test(e.key)) { e.preventDefault(); return; }  // plain text only
    if ((e.key === "Backspace" || e.key === "Delete") && !e.metaKey && !e.altKey && !e.ctrlKey) {
      const back = e.key === "Backspace";
      const both = timeAndSpace(back);
      if (both) {
        e.preventDefault();
        both.chip.remove(); both.node.deleteData(0, 1);
        setCaret(both.node, 0);
        changed(); return;
      }
      const n = neighbour(back);
      if (n) {
        e.preventDefault();
        const parent = n.parentNode, at = Array.prototype.indexOf.call(parent.childNodes, n);
        n.remove();
        setCaret(parent, at);
        changed();
      }
    }
  });

  // plain text only, whatever is pasted or dropped
  ed.addEventListener("paste", (e) => {
    e.preventDefault();
    document.execCommand("insertText", false, (e.clipboardData.getData("text/plain") || "").replace(/\r\n?/g, "\n"));
  });
  ed.addEventListener("dragover", (e) => e.preventDefault());
  ed.addEventListener("drop", (e) => {
    e.preventDefault();
    const t = e.dataTransfer.getData("text/plain");
    if (t) document.execCommand("insertText", false, t);
  });

  $("close").addEventListener("click", () => { flush(); post({ op: "notes_close" }); });

  // ---- size: the panel can be dragged bigger (Python decides how big, see Overlay._on_notes)
  const card = $("card");
  let chrome = null;  // the card's height apart from the text box
  function natural() { return { cw: 320, ch: 190 + chrome }; }
  window.__setSize = function (cw, ch) {
    card.style.width = cw + "px";
    ed.style.height = Math.max(120, ch - chrome) + "px";
    keepCaretVisible();
  };
  let rs = null;
  // grip: both directions; top edge: height only; side edge: width only
  for (const [id, mx, my] of [["grip", 1, 1], ["edgeTop", 0, 1], ["edgeSide", 1, 0]]) {
    const el = $(id);
    el.addEventListener("pointerdown", (e) => {
      e.preventDefault();  // no text selection starts from here
      rs = { x: e.screenX, y: e.screenY, mx, my, el };
      el.setPointerCapture(e.pointerId);
      document.body.classList.add("resizing");
      getSelection().removeAllRanges();
      post({ op: "notes_resize", phase: "start" });
    });
    el.addEventListener("pointermove", (e) => {
      if (rs && rs.el === el) post({ op: "notes_resize", phase: "move", dx: (e.screenX - rs.x) * mx, dy: (e.screenY - rs.y) * my });
    });
    const end = () => { if (!rs || rs.el !== el) return; rs = null; document.body.classList.remove("resizing"); post({ op: "notes_resize", phase: "end" }); };
    el.addEventListener("pointerup", end);
    el.addEventListener("pointercancel", end);
  }
  document.addEventListener("selectstart", (e) => { if (rs) e.preventDefault(); });

  // ---- what Python tells this page
  window.render = function (s) {
    if (s.side) document.body.className = "side-" + s.side;
    if (s.size) window.__setSize(s.size.cw, s.size.ch);
    if (s.reset) { ed.innerHTML = ""; setSaved("", "Saved"); }
    if (typeof s.text === "string") fill(s.text);
    if (s.timeKey) timeKey = s.timeKey;
    if (s.timeLabel) $("timeLabel").textContent = s.timeLabel;
    if ("elapsed" in s) window.__elapsed(s.elapsed);
    report();
  };
  window.__elapsed = function (sec) { elapsed = sec; elapsedAt = Date.now(); };
  window.__focus = function () {
    ed.focus();
    const r = document.createRange(); r.selectNodeContents(ed); r.collapse(false);  // the end: where you left off
    const sel = getSelection(); sel.removeAllRanges(); sel.addRange(r);
  };
  window.__failed = function (msg) { setSaved("fail", msg || "Couldn't save"); };
  window.flush = flush;

  // Python needs to know the smallest the panel can be (its natural size); it never gets smaller than this
  function report() {
    if (chrome === null) chrome = card.offsetHeight - ed.offsetHeight;
    const n = natural(), w = n.cw + 28, h = n.ch + 28;  // 14px of transparent border all round (room for the shadow)
    const size = w + "x" + h;
    if (size !== lastSize) { lastSize = size; post({ op: "size", w, h }); }
  }
  post({ op: "ready" });
  report();
