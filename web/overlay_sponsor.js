// The sponsor box: covers the spectator's observer controls (camera mode and
// fog of war, bottom left) with the background loop, tinted in both teams'
// colours like the HUD's lines, and the sponsors' logos in turn on top.
// Set up in the control panel's Sponsors section (config "sponsor_box").
(function () {
  const box = document.createElement("div");
  box.id = "sponsor-box";
  const style = document.createElement("style");
  style.textContent = `
    #sponsor-box { position: absolute; overflow: hidden; display: none; isolation: isolate; background: #0b0d10; }
    #sponsor-box video, #sponsor-box .still { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
    #sponsor-box .tint { position: absolute; inset: 0; mix-blend-mode: multiply; }
    #sponsor-box .logo { position: absolute; inset: 0; margin: auto; object-fit: contain; opacity: 0;
                         transition: opacity .6s ease; filter: drop-shadow(0 1px 3px rgba(0,0,0,.6)); }
    #sponsor-box .logo.on { opacity: 1; }
    #sponsor-box.outline { outline: 2px dashed #ffcf66; outline-offset: -2px; }`;
  document.head.appendChild(style);
  const video = document.createElement("video");
  video.muted = video.loop = video.autoplay = video.playsInline = true;
  const still = document.createElement("img");   // a static background instead of the loop
  still.className = "still";
  const tint = document.createElement("div");
  tint.className = "tint";
  const logos = [document.createElement("img"), document.createElement("img")];
  for (const l of logos) l.className = "logo";
  box.append(video, still, tint, ...logos);
  document.body.appendChild(box);

  const urlOf = p => "/" + String(p).split("/").map(encodeURIComponent).join("/");
  const lighten = (hex, amount) => {
    const n = parseInt((hex || "#888888").slice(1), 16);
    const ch = shift => Math.round(((n >> shift) & 255) + (255 - ((n >> shift) & 255)) * amount);
    return "#" + [16, 8, 0].map(sh => ch(sh).toString(16).padStart(2, "0")).join("");
  };
  let rotation = { key: "", i: 0, timer: null, front: 0 };

  // Logos shown in white, like the design's own (the GFX Engine's sponsors
  // say which); only in the bottom third's holder.
  let whiteOnes = new Set();

  function rotate(list, seconds) {
    const key = JSON.stringify([list, seconds, [...whiteOnes]]);
    if (key === rotation.key) return;
    rotation.key = key;
    clearInterval(rotation.timer);
    rotation.i = 0;
    for (const l of logos) l.classList.remove("on");
    if (!list.length) return;
    const show = () => {
      const img = logos[rotation.front = 1 - rotation.front];
      const p = list[rotation.i++ % list.length];
      img.src = urlOf(p);
      img.style.filter = whiteOnes.has(p) ? "brightness(0) invert(1) drop-shadow(0 1px 3px rgba(0,0,0,.6))" : "";
      img.classList.add("on");
      logos[1 - rotation.front].classList.remove("on");
    };
    show();
    if (list.length > 1) rotation.timer = setInterval(show, Math.max(1, Number(seconds) || 5) * 1000);
  }

  window.renderSponsorBox = function (s) {
    const cfg = s.config, b = cfg.sponsor_box || {};
    const on = b.enabled !== false;
    box.style.display = on ? "block" : "none";
    if (!on) { video.pause(); return; }
    // With the bottom third's frame (overlay_scoreboard.js): just the logos,
    // in the design's sponsor holder; the design's own logo when none are set.
    const framed = window.BOTTOM_THIRD && window.BOTTOM_THIRD.sponsor;
    if (framed) {
      const [fx, fy, fw, fh] = framed;
      Object.assign(box.style, { left: fx + "px", top: fy + "px", width: fw + "px", height: fh + "px", background: "transparent" });
      box.classList.remove("outline");
      video.pause();
      video.style.display = still.style.display = tint.style.display = "none";
      for (const img of logos) Object.assign(img.style, { width: "100%", height: "100%" });
      whiteOnes = new Set(b.white || []);
      rotate((b.sponsors || []).length ? b.sponsors : [window.BOTTOM_THIRD.default_sponsor], b.seconds);
      return;
    }
    box.style.background = "";
    const [x, y, w, h] = b.rect || [0, 964, 304, 116];
    Object.assign(box.style, { left: x + "px", top: y + "px", width: w + "px", height: h + "px" });
    box.classList.toggle("outline", !!b.outline);
    // The background: the default loop, the HUD's video ("hud"), a still
    // image ("image", b.bg_image) or just the team colours ("colours").
    const DEFAULT = "profiles/scc-2026/bg_loop.mp4";
    const mode = ["hud", "image", "colours"].includes(b.bg_video) ? b.bg_video : "";
    const useImage = mode === "image" && b.bg_image, useVideo = mode === "" || mode === "hud";
    if (useVideo) {
      const url = urlOf(mode === "hud" ? cfg.bg_video || DEFAULT : DEFAULT);
      if (video.dataset.src !== url) { video.dataset.src = url; video.src = url; video.play().catch(() => {}); }
      else if (video.paused) video.play().catch(() => {});
      const rate = Math.min(4, Math.max(0.0625, Number(b.bg_speed) || 1));
      if (video.playbackRate !== rate) video.playbackRate = rate;
    } else if (!video.paused) video.pause();
    video.style.display = useVideo ? "" : "none";
    if (useImage && still.dataset.src !== b.bg_image) { still.dataset.src = b.bg_image; still.src = urlOf(b.bg_image); }
    still.style.display = useImage ? "" : "none";
    const tinted = b.bg_tint !== false, colours = mode === "colours" || (mode === "image" && !b.bg_image);
    const bright = Math.min(3, Math.max(0, Number(b.bg_brightness ?? 1)));
    const look = (tinted ? "grayscale(1) brightness(1.6) " : "") + (bright !== 1 ? `brightness(${bright})` : "");
    video.style.filter = still.style.filter = look;
    const l = lighten(cfg.left.color, .35), r = lighten(cfg.right.color, .35);
    if (colours) {
      // No picture: the team colours themselves, left to right.
      tint.style.display = "";
      tint.style.mixBlendMode = "normal";
      tint.style.filter = bright !== 1 ? `brightness(${bright})` : "";
      tint.style.background = `linear-gradient(to right, ${cfg.left.color} 0%, ${cfg.left.color} 25%, ${cfg.right.color} 75%, ${cfg.right.color} 100%)`;
    } else {
      tint.style.display = tinted ? "" : "none";
      tint.style.mixBlendMode = "multiply";
      tint.style.filter = "";
      tint.style.background = `linear-gradient(to right, ${l} 0%, ${l} 30%, ${r} 70%, ${r} 100%)`;
    }
    // Logos: a share of the box, centred.
    const size = Math.min(1, Math.max(0.2, Number(b.logo_size) || 0.75));
    for (const img of logos) Object.assign(img.style, { width: size * 100 + "%", height: size * 100 + "%" });
    whiteOnes = new Set();
    rotate(b.sponsors || [], b.seconds);
  };
})();
