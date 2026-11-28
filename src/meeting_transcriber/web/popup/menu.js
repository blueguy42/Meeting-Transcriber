  function renderMenu(s) {
    let n = 0;  // the submenus are numbered from 1 in the order of their rows
    const items = s.items.map((it) => {
      if (it.divider) return '<div class="sep"></div>';
      const label = esc(it.label), hint = it.hint ? '<span class="k" style="margin:0">' + esc(it.hint) + "</span>" : "";
      if (it.sub) {
        n++;
        return '<div class="row' + (subOpen === n ? " open" : "") + '" role="menuitem" tabindex="0" data-sub="' + n + '"><span>' + label + '</span><span class="k" style="margin:0">' + (s.side === "right" ? "‹" : "›") + "</span></div>";
      }
      return '<div class="row" role="menuitem" tabindex="0" data-id="' + esc(it.id) + '"><span>' + label + "</span>" + hint + "</div>";
    }).join("");
    const menu = '<div class="card menu" role="menu">' + items + "</div>";
    // The submenus are always laid out (just hidden), one on top of the other beside the menu, so the panel never resizes when one
    // opens: resizing a web view flickers. Each is moved down to the level of its own row (see alignSubs). Python lets clicks pass
    // through the empty parts of the panel (see "rects").
    const withSub = s.items.filter((x) => x.sub);
    const subs = withSub.map((w, k) => '<div class="card sub' + (subOpen === k + 1 ? " on" : "") + '" role="menu" data-subid="' + (k + 1) + '">' + w.sub.map((o) =>
      '<div class="row" role="' + ("checked" in o ? "menuitemradio" : "menuitem") + '" tabindex="0" data-id="' + esc(o.id) + '"><span>' + esc(o.label) + "</span>" + ("checked" in o ? '<span class="k" style="margin:0">' + (o.checked ? "✓" : "") + "</span>" : "") + "</div>").join("") + "</div>").join("");
    wrap.style.flexDirection = s.side === "right" ? "row-reverse" : "row";
    return menu + (withSub.length ? '<div class="subs">' + subs + "</div>" : "");
  }

  // each submenu sits level with the row that opens it (the first one 7px down, as it always did)
  function alignSubs() {
    const first = wrap.querySelector(".row[data-sub]");
    if (!first) return;
    const top = first.getBoundingClientRect().top;
    wrap.querySelectorAll(".sub").forEach((card) => {
      const row = wrap.querySelector('.row[data-sub="' + card.dataset.subid + '"]');
      if (row) card.style.marginTop = (7 + row.getBoundingClientRect().top - top) + "px";
    });
  }

  function setSub(n) {  // n: the number of the submenu to show, 0 for none
    if (n === subOpen) return;
    subOpen = n;
    wrap.querySelectorAll(".row[data-sub]").forEach((row) => row.classList.toggle("open", Number(row.dataset.sub) === n));
    wrap.querySelectorAll(".sub").forEach((card) => card.classList.toggle("on", Number(card.dataset.subid) === n));
    postRects();
  }
