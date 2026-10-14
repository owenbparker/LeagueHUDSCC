// Player webcams along the bottom of the overlay: one per team, showing the
// player the action is on (the app decides who: see lolhud/cams.py).
//
// A player's cam is either a link (a VDO.Ninja view link, say), shown in a
// frame, or a cam connected straight to the HUD from the player's PC (the
// Player Cam client), received peer to peer over the network (WebRTC). The
// app only passes the connection messages along; the video doesn't go
// through it.
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
    #cams .feed.on { opacity: 1; }`;
  document.head.appendChild(style);

  const slots = {};   // side -> element
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

  async function connect(id, video) {
    const pc = new RTCPeerConnection({ iceServers: [] });
    peers[id] = { pc, video, started: Date.now() };
    pc.addTransceiver("video", { direction: "recvonly" });
    pc.ontrack = e => {
      video.srcObject = e.streams[0] || new MediaStream([e.track]);
      video.play().catch(() => {});
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
    return f.el.tagName === "IFRAME" || f.el.readyState >= 2;
  }

  window.renderCams = function (s, show) {
    const cfg = s.config, cams = cfg.cams || {}, info = s.cams || {};
    const on = show && cams.enabled !== false;
    layer.style.display = on ? "" : "none";
    const pubs = on ? info.publishers || {} : {};
    const wanted = new Set();
    for (const side of SIDES) {
      if (!slots[side]) {
        slots[side] = document.createElement("div");
        slots[side].className = "cam " + side;
        layer.appendChild(slots[side]);
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
          const f = setFeed(seat, side, "cam:" + pub.id, () => {
            const v = document.createElement("video");
            v.autoplay = v.muted = v.playsInline = true;
            return v;
          });
          const p = peers[pub.id];
          const stuck = p && p.pc.connectionState !== "connected" && Date.now() - p.started > 10000;
          if (stuck) drop(pub.id);
          if (registered && (!peers[pub.id])) connect(pub.id, f.el);
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
      for (let slot = 1; slot <= 5; slot++) {
        const f = feeds[`${side}-${slot}`];
        if (f) f.el.classList.toggle("on", `${side}-${slot}` === pick);
      }
    }
    for (const id of Object.keys(peers)) if (!wanted.has(id)) drop(id);
  };
})();
