// Player webcams along the bottom of the overlay: one per team, showing the
// player the action is on (the app decides who: see lolhud/cams.py).
//
// A player's cam is either a link (a VDO.Ninja view link, say), shown in a
// frame, or a cam connected straight to the HUD from the player's PC (the
// Player Cam client), received peer to peer over the network (WebRTC). The
// app only passes the connection messages along; the video doesn't go
// through it.
//
// League's spectator feed runs behind the real game (2:30 by default), so
// connected cams are held back by the same amount (cams.delay, seconds) to
// show the players' reactions in time with the play: the overlay records each
// cam as it arrives and plays the recording back that far behind. Links
// aren't delayed.
//
// A box with no cam to show (nobody on that team has one, or theirs isn't
// ready yet) shows the team's logo instead.
(function () {
  const ME = Math.random().toString(16).slice(2, 18);
  const SIDES = ["left", "right"];
  const layer = document.createElement("div");
  layer.id = "cams";
  document.body.appendChild(layer);
  const style = document.createElement("style");
  style.textContent = `
    #cams { position: absolute; inset: 0; pointer-events: none; }
    #cams .cam { position: absolute; overflow: hidden; }
    #cams .feed { position: absolute; inset: 0; width: 100%; height: 100%; border: 0; object-fit: cover;
                  opacity: 0; transition: opacity .35s ease; background: transparent; }
    #cams .feed.on { opacity: 1; }
    #cams .nocam { position: absolute; inset: 0; opacity: 0; transition: opacity .35s ease; background: #0b0d10; }
    #cams .nocam.on { opacity: 1; }
    #cams .nocam img { position: absolute; inset: 0; margin: auto; width: 70%; height: 70%; object-fit: contain;
                       filter: drop-shadow(0 2px 6px rgba(0,0,0,.6)); }
    #cams .nocam img:not([src]), #cams .nocam img[src=""] { display: none; }`;
  document.head.appendChild(style);

  const slots = {};   // side -> element
  const logos = {};   // side -> the team logo shown when there's no cam
  const feeds = {};   // "left-1" -> { key, el }
  const peers = {};   // cam (publisher) id -> { pc, video, started, seat }
  const shown = { left: null, right: null };
  let registered = false;

  const sleep = ms => new Promise(r => setTimeout(r, ms));
  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || r.status);
    return data;
  }

  // VDO.Ninja links: fill the frame, no on-screen controls and no sound
  // (the cams shouldn't play into the broadcast audio).
  function linkUrl(link) {
    try {
      const u = new URL(link);
      if (/(^|\.)vdo\.ninja$|(^|\.)obs\.ninja$/.test(u.hostname)) {
        for (const p of ["cover", "cleanoutput", "noaudio"]) if (!u.searchParams.has(p)) u.searchParams.set(p, "");
        return u.toString().replace(/=(&|$)/g, "$1");
      }
      return u.toString();
    } catch (e) { return ""; }
  }

  function setFeed(seat, side, key, make) {
    const f = feeds[seat];
    if (f && f.key === key) return f;
    if (f) f.el.remove();
    delete feeds[seat];
    if (!key) return null;
    const el = make();
    el.classList.add("feed");
    slots[side].appendChild(el);
    return (feeds[seat] = { key, el });
  }

  function drop(id) {
    const p = peers[id];
    if (!p) return;
    delete peers[id];
    if (p.stop) p.stop();
    try { p.pc.close(); } catch (e) { /* already closed */ }
  }

  function gathered(pc) {
    if (pc.iceGatheringState === "complete") return Promise.resolve();
    return new Promise(resolve => {
      const done = () => { if (pc.iceGatheringState === "complete") resolve(); };
      pc.addEventListener("icegatheringstatechange", done);
      setTimeout(resolve, 2500);  // the local network's addresses come quickly
    });
  }

  // A recording format both the recorder and the player handle.
  const TYPES = ["video/webm;codecs=vp8", "video/webm;codecs=vp9", "video/webm"];
  const delayType = () => window.MediaRecorder && window.MediaSource &&
    TYPES.find(t => MediaRecorder.isTypeSupported(t) && MediaSource.isTypeSupported(t));

  // Play a stream `delay` seconds late. Each piece of the recording is handed
  // to the player a second before it's due, so the video has nothing to show
  // (and the cam isn't picked) until the delay has passed. Returns a stop.
  function playDelayed(stream, video, delay) {
    const type = delayType();
    if (!type) { video.srcObject = stream; video.play().catch(() => {}); return () => {}; }
    const source = new MediaSource();
    const url = URL.createObjectURL(source);
    const pieces = [];   // { at, blob }: recorded, not yet handed over
    let buffer = null, busy = false, start = 0, timer = 0, stopped = false;
    const rec = new MediaRecorder(stream, { mimeType: type, videoBitsPerSecond: 1500000 });
    rec.ondataavailable = e => {
      if (!e.data || !e.data.size) return;
      const at = performance.now();
      if (!start) start = at - 250;  // the recording's time 0: its first frame, within a piece
      pieces.push({ at, blob: e.data });
    };

    async function pump() {
      if (stopped || busy || !buffer || buffer.updating || !pieces.length) return;
      if (performance.now() - pieces[0].at < (delay - 1) * 1000) return;
      busy = true;
      const piece = pieces.shift();
      try { buffer.appendBuffer(await piece.blob.arrayBuffer()); }
      catch (e) { if (!stopped) pieces.unshift(piece); busy = false; }
    }
    function tick() {
      if (start && buffer && video.buffered.length) {
        // Keep to the delay (after a stall, say), and let go of what's played.
        const due = (performance.now() - start) / 1000 - delay;
        const from = video.buffered.start(0), to = video.buffered.end(video.buffered.length - 1);
        if (due < from) video.pause();
        else {
          delete video.dataset.wait;
          if (Math.abs(video.currentTime - due) > 0.75 && due < to) video.currentTime = due;
          if (video.paused) video.play().catch(() => {});
          if (!busy && !buffer.updating && video.currentTime - from > 20) buffer.remove(from, video.currentTime - 10);
        }
      }
      pump();
    }
    source.addEventListener("sourceopen", () => {
      if (stopped) return;
      buffer = source.addSourceBuffer(type);
      buffer.addEventListener("updateend", () => { busy = false; pump(); });
    }, { once: true });
    video.dataset.wait = "1";  // not ready to be shown yet
    video.src = url;
    rec.start(250);
    timer = setInterval(tick, 250);
    return () => {
      stopped = true;
      clearInterval(timer);
      try { rec.stop(); } catch (e) { /* already stopped */ }
      URL.revokeObjectURL(url);
    };
  }

  async function connect(id, video, delay) {
    const pc = new RTCPeerConnection({ iceServers: [] });
    const p = peers[id] = { pc, video, started: Date.now(), stop: null };
    pc.addTransceiver("video", { direction: "recvonly" });
    pc.ontrack = e => {
      if (p.stop) return;
      const stream = e.streams[0] || new MediaStream([e.track]);
      if (delay > 0) p.stop = playDelayed(stream, video, delay);
      else { video.srcObject = stream; video.play().catch(() => {}); }
    };
    pc.onconnectionstatechange = () => {
      if (pc.connectionState === "failed" || pc.connectionState === "closed") drop(id);
    };
    try {
      await pc.setLocalDescription(await pc.createOffer());
      await gathered(pc);
      await post("/api/cam/send", { to: id, from: ME, data: { type: "offer", sdp: pc.localDescription.sdp } });
    } catch (e) { drop(id); }
  }

  async function inbox() {
    for (;;) {
      try {
        const r = await post("/api/cam/poll", { id: ME, wait: registered ? 20 : 0 });
        registered = true;
        for (const m of r.messages || []) {
          const p = peers[m.from];
          if (!p) continue;
          if (m.type === "answer" && p.pc.signalingState === "have-local-offer") {
            await p.pc.setRemoteDescription({ type: "answer", sdp: m.sdp }).catch(() => drop(m.from));
          } else if (m.type === "bye") drop(m.from);
        }
      } catch (e) { await sleep(2000); }
    }
  }
  inbox();
  addEventListener("pagehide", () => {
    for (const id of Object.keys(peers)) {
      navigator.sendBeacon && navigator.sendBeacon("/api/cam/send",
        new Blob([JSON.stringify({ to: id, from: ME, data: { type: "bye" } })], { type: "text/plain" }));
    }
  });

  function ready(seat) {
    const f = feeds[seat];
    if (!f) return false;
    return f.el.tagName === "IFRAME" || (f.el.readyState >= 2 && !f.el.dataset.wait);
  }

  window.renderCams = function (s, show) {
    const cfg = s.config, cams = cfg.cams || {}, info = s.cams || {};
    const on = show && cams.enabled !== false;
    layer.style.display = on ? "" : "none";
    const pubs = on ? info.publishers || {} : {};
    const delay = Math.max(0, Number(cams.delay) || 0);
    const wanted = new Set();
    for (const side of SIDES) {
      if (!slots[side]) {
        slots[side] = document.createElement("div");
        slots[side].className = "cam " + side;
        layer.appendChild(slots[side]);
        logos[side] = document.createElement("div");
        logos[side].className = "nocam";
        logos[side].appendChild(document.createElement("img"));
        slots[side].appendChild(logos[side]);
      }
      const [x, y, w, h] = (cams.slots || {})[side] || (side === "left" ? [288, 821, 174, 259] : [1458, 821, 174, 259]);
      Object.assign(slots[side].style, { left: x + "px", top: y + "px", width: w + "px", height: h + "px" });
      for (let slot = 1; slot <= 5; slot++) {
        const seat = `${side}-${slot}`;
        const link = on ? linkUrl(((cfg[side] || {}).cam_links || [])[slot - 1] || "") : "";
        const pub = pubs[seat];
        if (link) {
          setFeed(seat, side, "link:" + link, () => {
            const f = document.createElement("iframe");
            f.allow = "autoplay; fullscreen";
            f.src = link;
            return f;
          });
        } else if (pub) {
          wanted.add(pub.id);
          const f = setFeed(seat, side, `cam:${pub.id}:${delay}`, () => {
            const v = document.createElement("video");
            v.autoplay = v.muted = v.playsInline = true;
            return v;
          });
          const p = peers[pub.id];
          const stuck = p && p.pc.connectionState !== "connected" && Date.now() - p.started > 10000;
          if (stuck || (p && p.video !== f.el)) drop(pub.id);  // or the delay changed
          if (registered && (!peers[pub.id])) connect(pub.id, f.el, delay);
        } else {
          setFeed(seat, side, "", null);
        }
      }
      // The player with focus; otherwise stay put; otherwise anyone with a cam.
      const focus = (info.focus || {})[side];
      let pick = null;
      if (focus && ready(`${side}-${focus}`)) pick = `${side}-${focus}`;
      else if (shown[side] && ready(shown[side])) pick = shown[side];
      else for (let slot = 1; slot <= 5 && !pick; slot++) if (ready(`${side}-${slot}`)) pick = `${side}-${slot}`;
      shown[side] = pick;
      const team = cfg[side] || {}, logo = logos[side], img = logo.firstChild;
      if ((img.getAttribute("src") || "") !== (team.logo || "")) img.setAttribute("src", team.logo || "");
      const tint = `radial-gradient(circle at 50% 45%, ${team.color || "#333"}55 0%, #0b0d10 75%)`;
      if (logo.dataset.tint !== tint) { logo.dataset.tint = tint; logo.style.background = tint; }
      logo.classList.toggle("on", !pick && cams.no_cam_logo !== false);
      for (let slot = 1; slot <= 5; slot++) {
        const f = feeds[`${side}-${slot}`];
        if (f) f.el.classList.toggle("on", `${side}-${slot}` === pick);
      }
    }
    for (const id of Object.keys(peers)) if (!wanted.has(id)) drop(id);
  };
})();
