// Rink replay player for NHL Skating Load.
// Receives a game from the server ("replay_load"), animates it on a canvas, and
// switches to the post-game stats when the replay finishes (or on "Skip to stats").
(function () {
  const COL = { focus: "#D66450", other: "#4A92D0", puck: "#111", ice: "#F4F6F3", line: "#C9CFC8",
                red: "#C8102E", blue: "#1F4E99", text: "#F1EEE2", muted: "#8E9081" };
  const RINK_W = 200, RINK_H = 85, PAD = 4;
  let G = null, gt = 0, playing = false, speed = 10, last = null, evIdx = 0, flashes = [], marks = [];
  let canvas, ctx, scale = 4, raf = null, holdUntil = 0;

  function $(id) { return document.getElementById(id); }
  function teamColor(side) { return G && G[side].abbrev === G.focus ? COL.focus : COL.other; }

  // ---------------------------------------------------------------- drawing
  function X(x) { return (x + RINK_W / 2 + PAD) * scale; }
  function Y(y) { return (RINK_H / 2 - y + PAD) * scale; }   // +y is up on screen

  function roundRect(x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y); ctx.arcTo(x + w, y, x + w, y + r, r);
    ctx.lineTo(x + w, y + h - r); ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
    ctx.lineTo(x + r, y + h); ctx.arcTo(x, y + h, x, y + h - r, r);
    ctx.lineTo(x, y + r); ctx.arcTo(x, y, x + r, y, r); ctx.closePath();
  }

  function drawRink() {
    ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
    roundRect(X(-100), Y(42.5), 200 * scale, 85 * scale, 28 * scale);
    ctx.fillStyle = COL.ice; ctx.fill();
    ctx.save(); ctx.clip();
    const vline = (x, color, w) => { ctx.fillStyle = color; ctx.fillRect(X(x) - w * scale / 2, Y(42.5), w * scale, 85 * scale); };
    vline(0, COL.red, 1); vline(-25, COL.blue, 1); vline(25, COL.blue, 1);
    vline(-89, COL.red, 0.3); vline(89, COL.red, 0.3);
    ctx.strokeStyle = COL.blue; ctx.lineWidth = 0.3 * scale;
    ctx.beginPath(); ctx.arc(X(0), Y(0), 15 * scale, 0, 2 * Math.PI); ctx.stroke();
    ctx.strokeStyle = COL.red;
    for (const [fx, fy] of [[-69, 22], [-69, -22], [69, 22], [69, -22]]) {
      ctx.beginPath(); ctx.arc(X(fx), Y(fy), 15 * scale, 0, 2 * Math.PI); ctx.stroke();
    }
    ctx.fillStyle = COL.red;
    for (const [fx, fy] of [[-69, 22], [-69, -22], [69, 22], [69, -22], [-20, 22], [-20, -22], [20, 22], [20, -22], [0, 0]]) {
      ctx.beginPath(); ctx.arc(X(fx), Y(fy), 1 * scale, 0, 2 * Math.PI); ctx.fill();
    }
    // creases and nets
    for (const s of [-1, 1]) {
      ctx.fillStyle = "rgba(74,146,208,0.35)";
      ctx.beginPath(); ctx.arc(X(s * 89), Y(0), 6 * scale, s > 0 ? Math.PI / 2 : -Math.PI / 2, s > 0 ? 1.5 * Math.PI : Math.PI / 2); ctx.fill();
      ctx.strokeStyle = COL.red; ctx.lineWidth = 0.4 * scale;
      ctx.strokeRect(s > 0 ? X(89) : X(-89 - 3.3), Y(3), 3.3 * scale, 6 * scale);
    }
    ctx.restore();
    roundRect(X(-100), Y(42.5), 200 * scale, 85 * scale, 28 * scale);
    ctx.strokeStyle = "#5B6650"; ctx.lineWidth = 0.8 * scale; ctx.stroke();
  }

  function posAt(runs, t) {
    const s = Math.floor(t), f = t - s;
    for (const [start, xy] of runs) {
      const n = xy.length / 2;
      if (s >= start && s < start + n) {
        const i = s - start, j = Math.min(i + 1, n - 1);
        return [xy[2 * i] + (xy[2 * j] - xy[2 * i]) * f, xy[2 * i + 1] + (xy[2 * j + 1] - xy[2 * i + 1]) * f];
      }
    }
    return null;
  }

  // ---------------------------------------------------------------- frame
  // 8-bit mode: everything except text is drawn on a small low-res buffer, then scaled up
  // with smoothing off, so the rink, players and arrows come out as chunky pixels.
  const LOW = 1.5;                            // buffer pixels per foot in 8-bit mode
  const PIXEL_FONT = "'Press Start 2P', monospace", CLEAN_FONT = "Segoe UI, sans-serif";
  let retro = true, buf = null, bufCtx = null, mainCtx = null, HI = 4;
  const DIM = 0.32;                           // opacity of everyone except the followed player

  function liveNow() {
    const KMH = 1.09728;   // ft/s -> km/h
    const live = [];
    for (const pid in G.tracks) {
      const xy = posAt(G.tracks[pid], gt);
      if (!xy) continue;
      const nxt = posAt(G.tracks[pid], Math.min(gt + 1, G.T)) || xy;
      let vx = nxt[0] - xy[0], vy = nxt[1] - xy[1];
      if (Math.hypot(vx, vy) > 33) { vx = 0; vy = 0; }   // whistle: players reset for a faceoff, not skating
      live.push([pid, xy, vx, vy, Math.hypot(vx, vy) * KMH]);
    }
    return live;
  }

  function draw() {
    if (!G || !mainCtx) return;
    const live = liveNow();
    const focusOn = live.find(l => +l[0] === focusPid);
    const alphaFor = pid => (focusOn && +pid !== focusPid ? DIM : 1);

    // ---- pass 1: graphics (low-res buffer in 8-bit mode)
    if (retro) { ctx = bufCtx; scale = LOW; } else { ctx = mainCtx; scale = HI; }
    drawRink();
    for (const m of marks) {
      if (m.x == null) continue;
      ctx.globalAlpha = m.type === "goal" ? 0.95 : 0.35;
      ctx.fillStyle = teamColor(m.side);
      ctx.beginPath();
      if (m.type === "goal") { star(X(m.x), Y(m.y), 2.6 * scale); } else if (retro) { ctx.rect(X(m.x) - 1, Y(m.y) - 1, 2, 2); } else { ctx.arc(X(m.x), Y(m.y), 1.1 * scale, 0, 2 * Math.PI); }
      ctx.fill();
    }
    flashes = flashes.filter(f => gt - f.t < 6);
    for (const f of flashes) {
      if (f.x == null) continue;
      ctx.strokeStyle = f.type === "goal" ? "#E3C770" : teamColor(f.side);
      ctx.globalAlpha = Math.max(1 - (gt - f.t) / 6, 0); ctx.lineWidth = retro ? 1 : 0.6 * scale;
      const rr = (2 + (gt - f.t) * 2.5) * scale;
      ctx.beginPath();
      if (retro) ctx.rect(X(f.x) - rr, Y(f.y) - rr, 2 * rr, 2 * rr); else ctx.arc(X(f.x), Y(f.y), rr, 0, 2 * Math.PI);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
    // speed vectors
    for (const [pid, xy, vx, vy, kmh] of live) {
      if (G.players[pid].pos === "G" || kmh < 4) continue;
      const len = Math.hypot(vx, vy), k = Math.min(len * 1.0, 22) / len;   // ~1 s of travel, capped
      ctx.globalAlpha = alphaFor(pid);
      arrow(X(xy[0]), Y(xy[1]), X(xy[0] + vx * k), Y(xy[1] + vy * k), bandColor(kmh));
    }
    // player sprites
    for (const [pid, xy] of live) {
      if (+pid === focusPid) continue;
      const p = G.players[pid];
      ctx.globalAlpha = alphaFor(pid);
      sprite(X(xy[0]), Y(xy[1]), (p.pos === "G" ? 3.2 : 2.8) * scale, teamColor(p.side), p.pos === "G");
    }
    ctx.globalAlpha = 1;
    if (focusOn) focusSprite(focusOn[1], focusOn[4]);
    // puck
    const s = Math.floor(gt), f = gt - s, a = G.puck[Math.min(s, G.T)], b = G.puck[Math.min(s + 1, G.T)];
    const px = a[0] + (b[0] - a[0]) * f, py = a[1] + (b[1] - a[1]) * f;
    ctx.fillStyle = COL.puck;
    if (retro) { ctx.fillRect(Math.round(X(px)) - 1, Math.round(Y(py)) - 1, 3, 3); }
    else { ctx.strokeStyle = "#fff"; ctx.lineWidth = 0.3 * scale; ctx.beginPath(); ctx.arc(X(px), Y(py), 1.1 * scale, 0, 2 * Math.PI); ctx.fill(); ctx.stroke(); }

    // ---- upscale the buffer
    ctx = mainCtx; scale = HI;
    if (retro) {
      ctx.imageSmoothingEnabled = false;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(buf, 0, 0, canvas.width, canvas.height);
    }

    // ---- pass 2: text, always crisp at full resolution
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    const font = retro ? PIXEL_FONT : CLEAN_FONT;
    for (const [pid, xy] of live) {
      if (+pid === focusPid) continue;
      const p = G.players[pid];
      ctx.globalAlpha = alphaFor(pid);
      ctx.fillStyle = "#fff";
      ctx.font = retro ? `${Math.max(7, 1.9 * scale)}px ${font}` : `700 ${Math.max(9, 2.4 * scale)}px ${font}`;
      ctx.fillText(p.num ?? "", X(xy[0]) + (retro ? 0.15 * scale : 0), Y(xy[1]) + (retro ? 0.35 * scale : 0.2 * scale));
    }
    ctx.globalAlpha = 1;
    if (focusOn) focusLabel(focusOn[1], focusOn[4]);
    drawGoalFx();
  }

  // a skater: pixel "jersey" block in 8-bit mode, a disc otherwise
  function sprite(cx, cy, r, color, goalie) {
    if (retro) {
      const w = Math.max(5, Math.round(2 * r)), x0 = Math.round(cx - w / 2), y0 = Math.round(cy - w / 2);
      ctx.fillStyle = "#101410"; ctx.fillRect(x0 - 1, y0 - 1, w + 2, w + 2);            // outline
      ctx.fillStyle = color; ctx.fillRect(x0, y0, w, w);
      ctx.fillStyle = "rgba(255,255,255,0.35)"; ctx.fillRect(x0, y0, w, 1);                // highlight row
      if (goalie) { ctx.fillStyle = "#ffffff"; ctx.fillRect(x0, y0 + w - 2, w, 2); }       // pads
      return;
    }
    ctx.fillStyle = color; ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 0.35 * scale;
    ctx.beginPath();
    if (goalie) ctx.rect(cx - r, cy - r, 2 * r, 2 * r); else ctx.arc(cx, cy, r, 0, 2 * Math.PI);
    ctx.fill(); ctx.stroke();
  }

  function star(cx, cy, r) {
    for (let i = 0; i < 10; i++) {
      const rr = i % 2 ? r * 0.45 : r, a = Math.PI / 2 + i * Math.PI / 5;
      const x = cx + rr * Math.cos(a), y = cy - rr * Math.sin(a);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    }
    ctx.closePath();
  }

  // ---------------------------------------------------------------- GOAL! animation
  let goalFx = null;
  const FX_MS = 3200;
  function flashBoard() {
    const b = document.querySelector(".rp-board"); if (!b) return;
    b.classList.remove("goal-flash"); void b.offsetWidth; b.classList.add("goal-flash");
    setTimeout(() => b.classList.remove("goal-flash"), FX_MS);
  }

  function drawGoalFx() {
    if (!goalFx) return;
    const age = performance.now() - goalFx.t0;
    if (age > FX_MS) { goalFx = null; return; }
    const e = goalFx.e, W = canvas.width, H = canvas.height;
    const team = e.side ? G[e.side].abbrev : "";
    const col = e.side ? teamColor(e.side) : "#E3C770";
    const step = Math.floor(age / 140);
    const cycle = ["#F6D45B", "#FFFFFF", "#FF5A3C", col];
    // pixel confetti in the team colour and gold
    let seed = 7 + Math.floor(e.t);
    const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    const px = Math.max(4, Math.round(HI * 0.9));
    for (let i = 0; i < 90; i++) {
      const x0 = rnd() * W, speedY = 0.25 + rnd() * 0.6, y = ((rnd() * H) + age * speedY * HI / 25) % H;
      ctx.fillStyle = i % 3 ? col : "#F6D45B";
      ctx.globalAlpha = 0.85;
      ctx.fillRect(Math.round(x0 / px) * px, Math.round(y / px) * px, px, px);
    }
    ctx.globalAlpha = 1;
    // banner across the rink
    const bh = H * 0.34, by = H / 2 - bh / 2;
    ctx.fillStyle = "rgba(14,18,12,0.86)"; ctx.fillRect(0, by, W, bh);
    ctx.fillStyle = cycle[step % cycle.length]; ctx.fillRect(0, by, W, Math.max(3, HI * 0.6)); ctx.fillRect(0, by + bh - Math.max(3, HI * 0.6), W, Math.max(3, HI * 0.6));
    const font = retro ? PIXEL_FONT : CLEAN_FONT;
    const pop = Math.min(1, age / 220);                       // quick zoom-in
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.font = `${retro ? "" : "900 "}${(retro ? 11 : 14) * HI * pop}px ${font}`;
    ctx.fillStyle = "#000"; ctx.fillText("GOAL!", W / 2 + HI * 0.9, H / 2 - bh * 0.12 + HI * 0.9);
    ctx.fillStyle = cycle[step % cycle.length]; ctx.fillText("GOAL!", W / 2, H / 2 - bh * 0.12);
    const who = (e.text.match(/GOAL (#\d+ [^ (]+)/) || [])[1] || "";
    ctx.font = `${retro ? "" : "700 "}${(retro ? 2.4 : 3.2) * HI}px ${font}`;
    ctx.fillStyle = "#F1EEE2";
    ctx.fillText(`${team}  ${who.toUpperCase()}   ${G.away.abbrev} ${e.as_ ?? ""} - ${e.hs ?? ""} ${G.home.abbrev}`, W / 2, H / 2 + bh * 0.3);
  }

  // ---------------------------------------------------------------- speed bands + selected player
  const BANDS = [[0, "#1B1F1A"], [20, "#2E9E5B"], [29, "#D7263D"]];   // normal / fast / high-speed (>= 18 mph)
  function bandColor(kmh) { let c = BANDS[0][1]; for (const [t, col] of BANDS) if (kmh >= t) c = col; return c; }

  function arrow(x0, y0, x1, y1, color) {
    const ang = Math.atan2(y1 - y0, x1 - x0), h = (retro ? 2.6 : 2.4) * scale;
    const w = retro ? 1.4 : 0.85 * scale;                    // shaft width
    const head = () => {
      ctx.beginPath(); ctx.moveTo(x1 + Math.cos(ang) * h * 0.25, y1 + Math.sin(ang) * h * 0.25);
      ctx.lineTo(x1 - h * Math.cos(ang - 0.55), y1 - h * Math.sin(ang - 0.55));
      ctx.lineTo(x1 - h * Math.cos(ang + 0.55), y1 - h * Math.sin(ang + 0.55));
      ctx.closePath();
    };
    ctx.lineCap = retro ? "square" : "round"; ctx.lineJoin = "round";
    // dark outline first so green and red read clearly on the ice
    ctx.strokeStyle = "rgba(10,13,8,0.7)"; ctx.lineWidth = w + (retro ? 1 : 0.6 * scale);
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
    head(); ctx.stroke();
    ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = w;
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
    head(); ctx.fill();
  }

  let focusPid = null, focusImg = null, focusInfo = null, focusPix = null;
  function setFocus(msg) {
    focusPid = msg && msg.pid ? +msg.pid : null;
    focusInfo = msg || null;
    focusImg = null;
    if (msg && msg.headshot) {
      const img = new Image();
      img.onload = () => {
        focusImg = img;
        // 8-bit portrait: downsample to 40x40 once; drawn later at full size with smoothing off
        focusPix = document.createElement("canvas"); focusPix.width = focusPix.height = 40;
        const pc = focusPix.getContext("2d"); pc.imageSmoothingEnabled = true;
        pc.drawImage(img, img.width * 0.12, 0, img.width * 0.76, img.height * 0.76, 0, 0, 40, 40);
        if (G) draw();
      };
      img.src = msg.headshot;
    }
    if (G) draw();
  }

  const FOCUS_R = 6.5;   // ft: the followed player is drawn much bigger than everyone else
  function focusSprite(xy, kmh) {
    const p = G.players[focusPid] || {};
    const cx = X(xy[0]), cy = Y(xy[1]), r = FOCUS_R * scale, ring = (retro ? 1.4 : 1.0) * scale;
    if (retro) {
      const w = Math.round(2 * r), x0 = Math.round(cx - r), y0 = Math.round(cy - r), b = Math.max(2, Math.round(ring));
      ctx.fillStyle = "#101410"; ctx.fillRect(x0 - b - 1, y0 - b - 1, w + 2 * b + 2, w + 2 * b + 2);
      ctx.fillStyle = bandColor(kmh); ctx.fillRect(x0 - b, y0 - b, w + 2 * b, w + 2 * b);
      ctx.fillStyle = teamColor(p.side); ctx.fillRect(x0, y0, w, w);
      ctx.strokeStyle = teamColor(p.side); ctx.lineWidth = 1; ctx.strokeRect(x0 + 0.5, y0 + 0.5, w - 1, w - 1);
      return;
    }
    ctx.beginPath(); ctx.arc(cx, cy, r + ring, 0, 2 * Math.PI); ctx.fillStyle = bandColor(kmh); ctx.fill();
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, 2 * Math.PI); ctx.fillStyle = teamColor(p.side); ctx.fill();
    if (focusImg) {
      ctx.save(); ctx.beginPath(); ctx.arc(cx, cy, r - 0.3 * scale, 0, 2 * Math.PI); ctx.clip();
      const w = 2 * r * 1.25;
      ctx.drawImage(focusImg, cx - w / 2, cy - r * 1.05, w, w);
      ctx.restore();
    }
    ctx.lineWidth = 0.5 * scale; ctx.strokeStyle = "#ffffff";
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, 2 * Math.PI); ctx.stroke();
  }

  function focusLabel(xy, kmh) {
    const p = G.players[focusPid] || {};
    const cx = X(xy[0]), cy = Y(xy[1]), r = (FOCUS_R + 1.4) * scale;
    if (retro && focusPix) {   // portrait at full resolution, still pixel-art
      const fr = FOCUS_R * scale, inset = Math.max(1, (HI / LOW));
      ctx.imageSmoothingEnabled = false;
      ctx.drawImage(focusPix, cx - fr + inset, cy - fr + inset, 2 * fr - 2 * inset, 2 * fr - 2 * inset);
    }
    if (!focusImg) {   // no headshot: big number in the sprite
      ctx.fillStyle = "#fff"; ctx.font = retro ? `${3.2 * scale}px ${PIXEL_FONT}` : `800 ${4.2 * scale}px ${CLEAN_FONT}`;
      ctx.fillText(p.num ?? "", cx + (retro ? 0.3 * scale : 0), cy + (retro ? 0.5 * scale : 0.2 * scale));
    }
    const label = `#${p.num ?? ""} ${(p.name || "").split(" ").slice(-1)[0].toUpperCase()}  ${kmh.toFixed(0)} KM/H`;
    ctx.font = retro ? `${Math.max(8, 1.9 * scale)}px ${PIXEL_FONT}` : `700 ${Math.max(11, 2.5 * scale)}px ${CLEAN_FONT}`;
    const tw = ctx.measureText(label).width + 3 * scale, th = 5 * scale;
    let bx = cx - tw / 2, by = cy - r - th - 1.2 * scale;
    if (by < 2) by = cy + r + 1.2 * scale;
    bx = Math.max(2, Math.min(bx, canvas.width - tw - 2));
    ctx.fillStyle = retro ? "#101410" : "rgba(30,36,25,0.88)";
    if (retro) { ctx.fillRect(bx, by, tw, th); } else { roundRectXY(bx, by, tw, th, 1.2 * scale); ctx.fill(); }
    ctx.strokeStyle = bandColor(kmh) === BANDS[0][1] ? "#E3C770" : bandColor(kmh);
    ctx.lineWidth = (retro ? 0.6 : 0.35) * scale;
    if (retro) ctx.strokeRect(bx, by, tw, th); else ctx.stroke();
    ctx.fillStyle = retro ? "#E3C770" : "#F1EEE2";
    ctx.fillText(label, bx + tw / 2, by + th / 2 + (retro ? 0.3 * scale : 0.1 * scale));
  }

  function roundRectXY(x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }

  function pickAt(ev) {
    if (!G) return;
    const rect = canvas.getBoundingClientRect(), k = canvas.width / rect.width;
    const mx = (ev.clientX - rect.left) * k, my = (ev.clientY - rect.top) * k;
    let best = null, bd = 1e9;
    for (const pid in G.tracks) {
      const xy = posAt(G.tracks[pid], gt); if (!xy || G.players[pid].pos === "G") continue;
      const d = Math.hypot(X(xy[0]) - mx, Y(xy[1]) - my);
      if (d < bd) { bd = d; best = pid; }
    }
    if (best && bd < 6 * scale && window.Shiny) Shiny.setInputValue("rink_pick", `${best}:${Date.now()}`, { priority: "event" });
  }

  // ---------------------------------------------------------------- scoreboard + ticker
  function clockText(t) {
    const per = Math.min(Math.floor(t / 1200) + 1, G.n_per);
    const inPer = t - (per - 1) * 1200;
    const len = per <= 3 ? 1200 : 300;
    const rem = Math.max(len - inPer, 0);
    const m = Math.floor(rem / 60), s = Math.floor(rem % 60);
    return [per <= 3 ? `P${per}` : (per === 4 ? "OT" : `OT${per - 3}`), `${m}:${String(s).padStart(2, "0")}`];
  }

  function situation(t) {
    let cur = [0, 5, 5];
    for (const s of G.sit) { if (s[0] <= t) cur = s; else break; }
    const [_, a, h] = cur;
    if (a === h) return a === 5 ? "5 on 5" : `${a} on ${h}`;
    const pp = h > a ? G.home.abbrev : G.away.abbrev;
    return `${Math.max(a, h)} on ${Math.min(a, h)} · ${pp} power play`;
  }

  function updateBoard() {
    let hs = 0, as = 0;
    for (const e of G.events) { if (e.t > gt) break; if (e.type === "goal") { hs = e.hs ?? hs; as = e.as_ ?? as; } }
    const [per, clk] = clockText(gt);
    $("rp-away").textContent = G.away.abbrev; $("rp-home").textContent = G.home.abbrev;
    $("rp-away").style.color = teamColor("away"); $("rp-home").style.color = teamColor("home");
    $("rp-score").textContent = `${as} – ${hs}`;
    $("rp-clock").textContent = `${per}  ${clk}`;
    $("rp-sit").textContent = situation(gt);
    $("rp-scrub").value = gt;
  }

  function rebuildTicker() {
    const box = $("rp-ticker"); box.innerHTML = "";
    const past = G.events.filter(e => e.t <= gt).slice(-8).reverse();
    for (const e of past) {
      const row = document.createElement("div");
      row.className = "rp-ev" + (e.type === "goal" ? " rp-goal" : "");
      const team = e.side ? G[e.side].abbrev : "";
      row.innerHTML = `<span class="rp-dot" style="background:${e.side ? teamColor(e.side) : COL.muted}"></span>` +
        `<span class="rp-when">${e.period <= 3 ? "P" + e.period : "OT"} ${e.clock}</span><span class="rp-team">${team}</span><span>${e.text}</span>`;
      box.appendChild(row);
    }
  }

  function resync() {
    marks = G.events.filter(e => e.t <= gt && ["goal", "shot-on-goal", "missed-shot", "blocked-shot"].includes(e.type));
    evIdx = G.events.findIndex(e => e.t > gt); if (evIdx < 0) evIdx = G.events.length;
    flashes = []; goalFx = null; holdUntil = 0;
    rebuildTicker(); updateBoard(); draw();
  }

  // ---------------------------------------------------------------- loop
  function tick(now) {
    if (!playing) { raf = null; return; }
    if (last == null) last = now;
    if (now >= holdUntil) gt = Math.min(gt + (now - last) / 1000 * speed, G.T);
    last = now;
    let newEv = false;
    while (evIdx < G.events.length && G.events[evIdx].t <= gt) {
      const e = G.events[evIdx++];
      flashes.push(e); newEv = true;
      if (["goal", "shot-on-goal", "missed-shot", "blocked-shot"].includes(e.type)) marks.push(e);
      if (e.type === "goal") { gt = e.t; holdUntil = now + 3200; goalFx = { t0: now, e }; flashBoard(); }  // pause + GOAL!
    }
    if (newEv) rebuildTicker();
    updateBoard(); draw();
    if (gt >= G.T) { finish(); return; }
    raf = requestAnimationFrame(tick);
  }

  function setPlaying(p) {
    playing = p; last = null;
    $("rp-play").textContent = p ? "❚❚ PAUSE" : "▶ PLAY";
    if (p && !raf) raf = requestAnimationFrame(tick);
  }

  function finish() {
    setPlaying(false);
    $("rp-stage").style.display = "none";
    $("rp-stats").style.display = "block";
    const mobile = window.matchMedia("(max-width: 767.98px)").matches;
    if (window.Shiny) Shiny.setInputValue("replay_done", { gid: G.id, mobile, at: Date.now() }, { priority: "event" });
    if (mobile) {   // phones open the player's report: bring it into view once it has rendered
      let tries = 0;
      const go = () => { const h = document.getElementById("hdr"), el = h && (h.firstElementChild || h);
        if (el && el.getBoundingClientRect().height > 0 && h.textContent.trim()) el.scrollIntoView({ behavior: "smooth", block: "start" });
        else if (tries++ < 20) setTimeout(go, 250); };
      setTimeout(go, 300);
    } else window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function showReplay(restart) {
    $("rp-stage").style.display = "block";
    $("rp-stats").style.display = "none";
    if (restart) { gt = 0; resync(); }
  }

  function resize() {
    const wrap = $("rp-canvas-wrap"); if (!wrap || !canvas) return;
    const w = wrap.clientWidth;
    const dpr = window.devicePixelRatio || 1;
    HI = scale = (w / (RINK_W + 2 * PAD)) * dpr;
    canvas.width = Math.round((RINK_W + 2 * PAD) * scale);
    canvas.height = Math.round((RINK_H + 2 * PAD) * scale);
    buf.width = (RINK_W + 2 * PAD) * LOW; buf.height = (RINK_H + 2 * PAD) * LOW;
    canvas.style.width = w + "px";
    canvas.style.height = Math.round(w * (RINK_H + 2 * PAD) / (RINK_W + 2 * PAD)) + "px";
    if (G) draw();
  }

  function init() {
    canvas = $("rp-canvas"); if (!canvas) return false;
    ctx = mainCtx = canvas.getContext("2d");
    buf = document.createElement("canvas"); bufCtx = buf.getContext("2d");
    const rt = $("rp-retro");
    if (rt) {
      retro = rt.checked;
      $("rp-stage").classList.toggle("retro", retro);
      rt.onchange = e => { retro = e.target.checked; $("rp-stage").classList.toggle("retro", retro); if (G) draw(); };
    }
    if (document.fonts) document.fonts.load("10px 'Press Start 2P'").then(() => { if (G) draw(); });
    $("rp-play").onclick = () => setPlaying(!playing);
    $("rp-speed").onchange = e => { speed = +e.target.value; };
    $("rp-scrub").oninput = e => { gt = +e.target.value; resync(); };
    $("rp-skip").onclick = () => finish();
    $("rp-restart").onclick = () => { showReplay(true); };
    document.addEventListener("click", e => { if (e.target && e.target.id === "rp-again") { showReplay(true); setPlaying(true); } });
    canvas.addEventListener("click", pickAt);
    window.addEventListener("resize", resize);
    new ResizeObserver(resize).observe($("rp-canvas-wrap"));
    resize();
    return true;
  }

  function load(msg) {
    if (!canvas && !init()) { setTimeout(() => load(msg), 200); return; }
    setPlaying(false);
    G = msg;
    $("rp-scrub").max = G.T; gt = 0;
    $("rp-title").textContent = `${G.away.name} @ ${G.home.name} · ${G.date}`;
    showReplay(false);
    resize(); resync();
  }

  if (window.Shiny) {
    Shiny.addCustomMessageHandler("replay_load", load);
    Shiny.addCustomMessageHandler("replay_focus", setFocus);
  } else {
    document.addEventListener("shiny:connected", () => { Shiny.addCustomMessageHandler("replay_load", load); Shiny.addCustomMessageHandler("replay_focus", setFocus); });
  }
})();
