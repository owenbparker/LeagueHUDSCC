// The scoreboard and bottom third (design/lol-hud-SCC27.psd): the artwork
// along the bottom (scoreboard, webcam spaces, sponsor holder, minimap
// frame) and, for each player, champion, ultimate, items, trinket, summoner
// spells, keystone, role, level / health / mana bars, name, CS, KDA and
// vision score, with the gold difference between the laners in the middle.
// Positions come from the PSD (tools/scoreboard_from_psd.py ->
// assets/scoreboard/layout.json). Drawn under the webcams.
//
// Riot's live game data has no health, mana or other players' gold: the
// health bar empties while a player is dead, the level bar fills to level 18,
// and gold is the HUD's estimate.
(function () {
  const BASE = "assets/scoreboard/";
  const layer = document.createElement("div");
  layer.id = "scoreboard";
  document.body.appendChild(layer);
  const style = document.createElement("style");
  style.textContent = `
    @font-face { font-family: "SCC Franklin Cond"; src: local("Franklin Gothic Medium Cond"), local("FranklinGothic-MediumCond"),
                 url("draft/saira-condensed.woff2") format("woff2"); font-weight: 100 900; }
    #scoreboard { position: absolute; left: 0; top: 0; width: 1920px; height: 1080px; pointer-events: none;
                  opacity: 0; transition: opacity .5s ease; font-family: "SCC Franklin Cond", "Saira Condensed", "Arial Narrow", sans-serif;
                  font-weight: 500; color: #fff; }
    #scoreboard.on { opacity: 1; }
    #scoreboard .frame { position: absolute; left: 0; width: 1920px; }
    #scoreboard .abs { position: absolute; overflow: hidden; }
    #scoreboard img.slot { position: absolute; object-fit: cover; display: block; }
    #scoreboard img.slot[src=""], #scoreboard img.slot:not([src]) { display: none; }
    #scoreboard .champ.dead img { filter: grayscale(1) brightness(.45); }
    #scoreboard .champ .respawn { position: absolute; inset: 0; display: none; align-items: center; justify-content: center;
                                  font-size: 24px; font-weight: 700; text-shadow: 0 1px 3px #000; }
    #scoreboard .champ.dead .respawn { display: flex; }
    #scoreboard .champ .lvl { position: absolute; bottom: 0; min-width: 17px; height: 15px; padding: 0 2px; background: rgba(0,0,0,.8);
                              font-size: 12px; line-height: 15px; text-align: center; }
    #scoreboard .champ.left .lvl { right: 0; } #scoreboard .champ.right .lvl { left: 0; }
    #scoreboard img.ult { border-radius: 50%; }
    #scoreboard img.ult.locked { filter: grayscale(1) brightness(.5); }
    #scoreboard .bar { position: absolute; background-repeat: no-repeat; background-size: 100% 100%; transition: width .6s ease; }
    #scoreboard .bar.right { background-position: right; }
    #scoreboard .t { position: absolute; white-space: nowrap; line-height: 1; }
    #scoreboard .row.dead .t.name { opacity: .55; }
    #scoreboard .diff { font-weight: 600; }`;
  document.head.appendChild(style);

  let layout = null, built = false;
  const rows = [], diffs = [];
  fetch(BASE + "layout.json", { cache: "no-store" }).then(r => r.json()).then(l => { layout = l; }).catch(() => {});

  const el = (tag, cls, parent) => { const n = document.createElement(tag); if (cls) n.className = cls; parent.appendChild(n); return n; };
  function place(n, r) {
    const top = layout.top;
    Object.assign(n.style, { left: r[0] + "px", top: top + r[1] + "px", width: r[2] + "px", height: r[3] + "px" });
    return n;
  }
  function slot(r, cls) { return place(el("img", "slot " + (cls || ""), layer), r); }
  function text(t, cls, parent) {
    // t: {x, y (baseline), size, anchor}
    const n = el("div", "t " + (cls || ""), parent || layer);
    const shift = { start: "0", middle: "-50%", end: "-100%" }[t.anchor || "start"];
    Object.assign(n.style, { left: t.x + "px", top: layout.top + t.y - t.size * 0.8 + "px", fontSize: t.size + "px",
                             transform: `translateX(${shift})` });
    return n;
  }
  const setSrc = (img, src) => { if ((img.getAttribute("src") || "") !== (src || "")) img.setAttribute("src", src || ""); };
  const setText = (n, v) => { v = v == null ? "" : String(v); if (n.textContent !== v) n.textContent = v; };

  function build() {
    const frame = el("img", "frame", layer);
    frame.src = BASE + "frame.png";
    frame.style.top = layout.top + "px";
    layout.players.forEach((p, i) => {
      const side = i < 5 ? "left" : "right";
      const row = { side };
      const champ = place(el("div", "abs champ " + side, layer), p.champ);
      row.champBox = champ;
      row.champ = el("img", "slot", champ);
      Object.assign(row.champ.style, { left: 0, top: 0, width: "100%", height: "100%" });
      row.respawn = el("div", "respawn", champ);
      row.lvl = el("div", "lvl", champ);
      row.ult = slot(p.ult, "ult");
      row.items = p.items.map(r => slot(r));
      row.trinket = slot(p.ward);
      row.spells = p.spells.map(r => slot(r));
      row.keystone = slot(p.keystone);
      row.role = slot(p.role);
      row.role.style.objectFit = "contain";
      row.bars = {};
      for (const kind of ["xp", "hp", "mana"]) {
        const r = p.bars[kind];
        if (!r) continue;
        const b = place(el("div", "bar " + side, layer), r);
        b.style.backgroundImage = `url("${BASE}bar_${kind}.png")`;
        if (side === "right") { b.style.left = ""; b.style.right = 1920 - (r[0] + r[2]) + "px"; }
        b.dataset.full = r[2];
        row.bars[kind] = b;
      }
      row.name = text(p.name, "name");
      row.cs = text(p.cs);
      row.kda = text(p.kda);
      row.vision = text(p.vision);
      rows.push(row);
    });
    for (const g of layout.gold_diff) diffs.push(text(g, "diff"));
    built = true;
  }

  function lighten(hex, amount) {
    const n = parseInt((hex || "#888888").slice(1), 16);
    const ch = shift => Math.round(((n >> shift) & 255) + (255 - ((n >> shift) & 255)) * amount);
    return "#" + [16, 8, 0].map(sh => ch(sh).toString(16).padStart(2, "0")).join("");
  }
  const bar = (b, share) => { if (b) b.style.width = Math.max(0, Math.min(1, share)) * Number(b.dataset.full) + "px"; };

  window.BOTTOM_THIRD = null;
  window.renderScoreboard = function (s, on) {
    if (!layout) { window.BOTTOM_THIRD = null; return; }
    if (!built) build();
    layer.classList.toggle("on", !!on);
    const sp = layout.sponsor_logo;
    window.BOTTOM_THIRD = on && sp ? { sponsor: [sp[0], layout.top + sp[1], sp[2], sp[3]], default_sponsor: BASE + "sponsor.png" } : null;
    if (!on) return;
    const sb = (s.detail || {}).scoreboard || { players: [], gold_diff: [] };
    rows.forEach((row, i) => {
      const p = sb.players[i] || {};
      const here = !!p.present;
      setSrc(row.champ, p.icon);
      row.champBox.classList.toggle("dead", !!p.dead);
      setText(row.respawn, p.dead && p.respawn ? p.respawn : "");
      setText(row.lvl, here ? p.level : "");
      row.lvl.style.display = here ? "" : "none";
      setSrc(row.ult, p.ult);
      row.ult.classList.toggle("locked", (p.level || 0) < 6);
      (p.items || []).forEach((src, k) => setSrc(row.items[k], src));
      if (!p.items) row.items.forEach(img => setSrc(img, ""));
      setSrc(row.trinket, p.trinket);
      (p.spells || ["", ""]).forEach((src, k) => setSrc(row.spells[k], src));
      setSrc(row.keystone, p.keystone);
      setSrc(row.role, here ? `draft/scc27/role_${p.role}.png` : "");
      bar(row.bars.xp, here ? (p.level || 0) / 18 : 0);
      bar(row.bars.hp, here && !p.dead ? 1 : 0);
      bar(row.bars.mana, here && !p.dead ? 1 : 0);
      setText(row.name, p.name);
      setText(row.cs, here ? p.cs : "");
      setText(row.kda, p.kda);
      setText(row.vision, here ? p.vision : "");
    });
    const cfg = s.config || {};
    diffs.forEach((n, i) => {
      const d = sb.gold_diff[i] || 0;
      const lead = Math.abs(d) >= 100 ? (d > 0 ? "left" : "right") : "";
      setText(n, (lead ? (lead === "left" ? "◂ " : "") : "") + (Math.abs(d) / 1000).toFixed(1) + "K" + (lead === "right" ? " ▸" : ""));
      n.style.color = lead ? lighten((cfg[lead] || {}).color, .45) : "#fff";
    });
  };
})();
