  function renderTip(s) {
    if (s.items) {  // a heading and a bulleted list
      return '<div class="card tip"><div class="tt">' + esc(s.text) + "</div>" + s.items.map((i) => '<div class="li">' + esc(i) + "</div>").join("") + "</div>";
    }
    if (s.sub) return '<div class="card tip" style="max-width:340px;white-space:normal"><div class="tt" style="margin-bottom:2px">' + esc(s.text) + '</div><div class="ts">' + esc(s.sub) + "</div></div>";  // a meeting's full title and when it was recorded
    return '<div class="card tip">' + esc(s.text) + (s.hotkey ? '<span class="k">' + esc(s.hotkey) + "</span>" : "") + "</div>";
  }
