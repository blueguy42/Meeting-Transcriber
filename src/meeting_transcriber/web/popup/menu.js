  function renderMenu(s) {
    const items = s.items.map((it, i) => {
      if (it.divider) return '<div class="sep"></div>';
      const label = esc(it.label), hint = it.hint ? '<span class="k" style="margin:0">' + esc(it.hint) + "</span>" : "";
      if (it.sub) {
        return '<div class="row' + (subOpen ? " open" : "") + '" role="menuitem" tabindex="0" data-sub="1"><span>' + label + '</span><span class="k" style="margin:0">' + (s.side === "right" ? "‹" : "›") + "</span></div>";
      }
      return '<div class="row" role="menuitem" tabindex="0" data-id="' + esc(it.id) + '"><span>' + label + "</span>" + hint + "</div>";
    }).join("");
    const menu = '<div class="card menu" role="menu">' + items + "</div>";
    // The submenu is always laid out (just hidden), so the panel never resizes when it opens: resizing a web view
    // flickers. Python lets clicks pass through the empty parts of the panel (see "rects").
    let sub = "";
    const withSub = s.items.find((x) => x.sub);
    if (withSub) {
      sub = '<div class="card sub" role="menu">' + withSub.sub.map((o) =>
        '<div class="row" role="menuitemradio" tabindex="0" data-id="' + esc(o.id) + '"><span>' + esc(o.label) + "</span><span class=\"k\" style=\"margin:0\">" + (o.checked ? "✓" : "") + "</span></div>").join("") + "</div>";
    }
    wrap.style.flexDirection = s.side === "right" ? "row-reverse" : "row";
    return menu + sub;
  }

  function setSub(open) {
    if (open === subOpen) return;
    subOpen = open;
    const row = wrap.querySelector(".row[data-sub]"), sub = wrap.querySelector(".sub");
    if (row) row.classList.toggle("open", open);
    if (sub) sub.classList.toggle("on", open);
    postRects();
  }
