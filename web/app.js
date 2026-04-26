// Adjutant frontend — continuous-listen voice loop.
//
// Architecture:
//   getUserMedia → AudioWorklet → 32ms PCM16 frames → WebSocket binary →
//   server VAD → STT → LLM → Kokoro → WAV chunks → decodeAudioData →
//   AudioBufferSourceNode.start(when) for gapless playback.
//
// State: idle → listening → thinking → speaking → (back to listening, or
// interrupted if user starts talking while bot is speaking).

const startBtn = document.getElementById("start-btn");
const muteBtn = document.getElementById("mute-btn");
const stateEl = document.getElementById("state-indicator");
const stateLabelEl = document.getElementById("state-label");
const transcriptEl = document.getElementById("transcript");
const replyEl = document.getElementById("reply");
const spokenEl = document.getElementById("spoken-summary");
const citationsEl = document.getElementById("citations");
const formSection = document.getElementById("form-section");
const netBadge = document.getElementById("net-status");
const fallbackBtn = document.getElementById("fallback-mic");

// Online/offline badge — wired into the telemetry strip's NET cell.
// The wifi-disconnect demo moment: lamp goes red, value flips to OFFLINE.
function updateNetBadge() {
  const online = navigator.onLine;
  netBadge.textContent = online ? "LOCAL" : "OFFLINE";
  netBadge.className = online
    ? "telemetry__val telemetry__val--amber"
    : "telemetry__val telemetry__val--alert";
  const lamp = netBadge.parentElement && netBadge.parentElement.querySelector(".lamp");
  if (lamp) {
    lamp.className = online
      ? "lamp lamp--amber lamp--blink"
      : "lamp lamp--alert";
  }
}
window.addEventListener("online", updateNetBadge);
window.addEventListener("offline", updateNetBadge);
updateNetBadge();

// Tier status — polls /health every 2s and updates HOT/WARM/COLD LEDs.
// Demo moment: kill the cold server → COLD LED red, queries still work.
async function pollTierStatus() {
  try {
    const resp = await fetch("/health", { cache: "no-store" });
    if (!resp.ok) throw new Error(`/health ${resp.status}`);
    const data = await resp.json();
    if (!data.tiers) return;
    for (const tierName of ["HOT", "WARM", "COLD"]) {
      const t = data.tiers[tierName];
      if (!t) continue;
      const led = document.querySelector(
        `#tier-status .tier[data-tier="${tierName}"] .tier-led`
      );
      if (led) led.dataset.status = t.status || "unknown";
      const meta = document.querySelector(
        `#tier-status .tier[data-tier="${tierName}"] .tier-meta`
      );
      if (meta && t.latency_ms != null) {
        const base = tierName === "HOT" ? "on-device · always"
                   : tierName === "WARM" ? "on-base · NIPR"
                   : "cloud · FedRAMP";
        meta.textContent = `${base} · ${Math.round(t.latency_ms)}ms`;
      }
    }
  } catch (err) {
    for (const t of document.querySelectorAll("#tier-status .tier-led")) {
      t.dataset.status = "down";
    }
  }
}
setInterval(pollTierStatus, 2000);
pollTierStatus();

// ---------------------------------------------------------------------------
// State machine + UI
// ---------------------------------------------------------------------------

const STATES = {
  idle:      { color: "var(--state-idle)",      label: "Press Start to begin" },
  listening: { color: "var(--state-listening)", label: "Listening…" },
  speaking_user: { color: "var(--state-listening)", label: "Hearing you…" },
  thinking:  { color: "var(--state-thinking)",  label: "Thinking…" },
  speaking:  { color: "var(--state-speaking)",  label: "Adjutant is speaking" },
  error:     { color: "var(--state-error)",     label: "Error — see console" },
};
let currentState = "idle";

function setState(name) {
  currentState = name;
  const s = STATES[name];
  if (!s) return;
  stateEl.style.background = s.color;
  stateLabelEl.textContent = s.label;
  stateEl.dataset.state = name;
  if (orbHandle) orbHandle.setState(name);
}

// ---------------------------------------------------------------------------
// Audio playback — AudioBufferSourceNode chain for gapless streamed TTS
// ---------------------------------------------------------------------------

let audioCtx = null;
let nextStartTime = 0;
let activeSources = new Set();

// Visualizer infrastructure — analyzer nodes for mic input + bot output.
// Both feed the same RAF loop driving the <canvas id="voice-orb"> on the
// page. Mirrors the ChatGPT / ElevenLabs in-browser orb that pulses with
// whichever side is actively producing sound.
let micAnalyser = null;        // hooked to mic MediaStreamSource
let botAnalyser = null;        // hooked downstream of all BufferSource nodes
let botGain = null;            // shared destination node for bot audio
let orbRafHandle = 0;

function ensureAudioCtx() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    // Single shared bot-side gain. Every TTS BufferSource connects here,
    // and this node connects to BOTH the analyzer (for visualization) AND
    // the destination (so the user actually hears it).
    botGain = audioCtx.createGain();
    botAnalyser = audioCtx.createAnalyser();
    // ChatGPT AVM-grade settings: 512 bins, 0.3 smoothing — phoneme-level
    // responsiveness without jitter. Anything larger blurs syllables; the
    // 0.85 default washes out everything below the word level.
    botAnalyser.fftSize = 512;
    botAnalyser.smoothingTimeConstant = 0.3;
    botGain.connect(botAnalyser);
    botGain.connect(audioCtx.destination);
    botAnalyser.connect(audioCtx.destination);  // analyser is read-only;
                                                 // safe parallel branch
  }
  if (audioCtx.state === "suspended") audioCtx.resume();
  return audioCtx;
}

async function playWavChunk(arrayBuffer) {
  const ctx = ensureAudioCtx();
  let buf;
  try {
    buf = await ctx.decodeAudioData(arrayBuffer.slice(0));
  } catch (err) {
    console.warn("decodeAudioData failed:", err);
    return;
  }
  const src = ctx.createBufferSource();
  src.buffer = buf;
  // Route through the bot gain so the visualizer's botAnalyser can read
  // amplitude in real time.
  src.connect(botGain);
  // L3 polish: 50ms crossfade at chunk boundary to hide concatenation
  // artifacts between Kokoro phrase segments. Each chunk ramps from 0→1
  // over the first 30ms, plays at 1, then ramps 1→0 over the last 30ms.
  // Adjacent chunks overlap their fades so the audio is continuous.
  const startAt = Math.max(ctx.currentTime, nextStartTime - 0.030);
  const fadeIn = 0.030;
  const fadeOut = 0.030;
  const dur = buf.duration;
  const gain = ctx.createGain();
  gain.gain.setValueAtTime(0.0, startAt);
  gain.gain.linearRampToValueAtTime(1.0, startAt + fadeIn);
  gain.gain.setValueAtTime(1.0, startAt + Math.max(0, dur - fadeOut));
  gain.gain.linearRampToValueAtTime(0.0, startAt + dur);
  src.disconnect();
  src.connect(gain);
  gain.connect(botGain);
  src.start(startAt);
  src.onended = () => {
    activeSources.delete(src);
    try { gain.disconnect(); } catch (_) {}
  };
  activeSources.add(src);
  nextStartTime = startAt + dur;
  if (currentState !== "speaking") setState("speaking");
}

function stopAllAudio() {
  for (const s of activeSources) {
    try { s.stop(); } catch (_) {}
  }
  activeSources.clear();
  if (audioCtx) nextStartTime = audioCtx.currentTime;
}

// ---------------------------------------------------------------------------
// Live amplitude-driven orb visualizer
// ---------------------------------------------------------------------------
//
// AnalyserNode-driven canvas pulsing — same pattern OpenAI's open-source
// realtime-console uses (Canvas 2D, getByteFrequencyData) and ElevenLabs
// uses for their widget orb. While listening, the orb breathes with the
// user's voice. While the bot is speaking, it breathes with the bot's
// voice. Idle = barely-perceptible ambient pulse so the user knows it's
// alive.
function getOrCreateMicAnalyser(ctx) {
  if (!micAnalyser) {
    micAnalyser = ctx.createAnalyser();
    micAnalyser.fftSize = 512;             // ChatGPT-AVM responsiveness
    micAnalyser.smoothingTimeConstant = 0.3;
  }
  return micAnalyser;
}

function detachMicAnalyser() {
  micAnalyser = null;
}

// ----- WebGL2 orb (ElevenLabs / ChatGPT-web port) -----
// Defined in /web/voice-orb.js. We init lazily (first audio interaction)
// and expose the handle so setState + AnalyserNode reads can drive it.
let orbHandle = null;
async function ensureOrb() {
  if (orbHandle) return orbHandle;
  const canvas = document.getElementById("voice-orb");
  if (!canvas) return null;
  try {
    const mod = await import("/web/voice-orb.js");
    orbHandle = mod.initVoiceOrb(canvas);
    if (orbHandle) {
      orbHandle.setState(currentState);
      // Volume polling — runs alongside the orb's own RAF
      const micBuf = new Uint8Array(256);
      const botBuf = new Uint8Array(256);
      function pollVol() {
        const micL = (currentState === "listening" || currentState === "speaking_user")
                     ? readVoiceLevel(micAnalyser, micBuf) : 0;
        const botL = (currentState === "speaking")
                     ? readVoiceLevel(botAnalyser, botBuf) : 0;
        orbHandle.updateVolume(micL, botL);
        requestAnimationFrame(pollVol);
      }
      requestAnimationFrame(pollVol);
    }
  } catch (e) {
    console.warn("[orb] init failed:", e);
  }
  return orbHandle;
}
function readVoiceLevel(analyser, buf) {
  if (!analyser || !analyser.getByteFrequencyData) return 0;
  analyser.getByteFrequencyData(buf);
  let sum = 0;
  const lo = 10, hi = Math.min(40, buf.length);
  for (let i = lo; i < hi; i++) sum += buf[i];
  const avg = sum / (hi - lo) / 255;
  return Math.max(0, Math.min(1, (avg - 0.06) / 0.6));
}

// Old Canvas2D startOrb retained as a no-op fallback in case ensureOrb()
// rejects (no WebGL2 support). The page just shows an empty canvas — the
// rest of the demo still works.
function startOrb() {
  ensureOrb();
}
function _legacyCanvas2DOrbDisabled() {
  const canvas = document.getElementById("voice-orb");
  if (!canvas || orbRafHandle) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const cssW = canvas.clientWidth || canvas.width;
  const cssH = canvas.clientHeight || canvas.height || cssW;
  canvas.width  = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  const ctx2d = canvas.getContext("2d");

  // ChatGPT AVM / ElevenLabs construction:
  //   - 3 concentric translucent radial-gradient spheres at scales 1.00 / 0.85 / 0.70
  //   - per-layer phase + opposite rotation directions
  //   - Simplex-noise stand-in via summed sine waves
  //   - Fresnel-style hollow center via radial gradient stops
  //   - Audio-reactive amplitude → outer-radius scale, exponential envelope
  // Adjutant amber palette matching the operator-console brand.
  const STATES = {
    idle:          { c: ["#3a2f10", "#3a2f10", "#3a2f10"], speed: 0.6,  pulseMin: 1.00, pulseMax: 0.0  },
    listening:     { c: ["#E0B341", "#FFB000", "#FFD24A"], speed: 1.0,  pulseMin: 1.02, pulseMax: 0.25 },
    speaking_user: { c: ["#5fb8d6", "#7CC5E0", "#A0DCF0"], speed: 1.1,  pulseMin: 1.02, pulseMax: 0.25 },
    thinking:      { c: ["#FF6A00", "#FF8A2A", "#FFB050"], speed: 1.5,  pulseMin: 1.00, pulseMax: 0.15 },
    speaking:      { c: ["#FFD24A", "#FFC227", "#FFE780"], speed: 1.7,  pulseMin: 1.05, pulseMax: 0.22 },
    error:         { c: ["#d24545", "#e26060", "#f08080"], speed: 0.9,  pulseMin: 1.00, pulseMax: 0.05 },
  };

  const micBuf = new Uint8Array(256);  // matches fftSize=512 → 256 bins
  const botBuf = new Uint8Array(256);

  // Vocal-formant slice (bins ~10–40) — what makes the orb react to speech,
  // not to room hum or sibilance. Same trick OpenAI's 'voice' filter uses.
  function vocalLevel(analyser, buf) {
    if (!analyser) return 0;
    analyser.getByteFrequencyData(buf);
    let sum = 0;
    const lo = 10, hi = Math.min(40, buf.length);
    for (let i = lo; i < hi; i++) sum += buf[i];
    const avg = sum / (hi - lo) / 255;
    // Soft floor + headroom: ignore <6%, saturate at ~66% of max-loud
    return Math.max(0, Math.min(1, (avg - 0.06) / 0.6));
  }

  function hexToRGBA(h, a) {
    const v = h.charAt(0) === "#" ? h.slice(1) : h;
    return `rgba(${parseInt(v.slice(0,2),16)},${parseInt(v.slice(2,4),16)},${parseInt(v.slice(4,6),16)},${a})`;
  }

  let env = 0;
  function tickEnv(target) {
    // Faster attack on rise, slower decay — perceptually correct
    const k = target > env ? 0.35 : 0.10;
    env += (target - env) * k;
    return env;
  }

  function render(now) {
    const t = (now || performance.now()) * 0.001;
    const w = canvas.width, h = canvas.height;
    const cx = w / 2, cy = h / 2;
    const R  = Math.min(w, h) * 0.34;   // contained disc, not a flashlight halo

    const s = STATES[currentState] || STATES.idle;

    const micLvl = (currentState === "listening" || currentState === "speaking_user")
                   ? vocalLevel(micAnalyser, micBuf) : 0;
    const botLvl = (currentState === "speaking") ? vocalLevel(botAnalyser, botBuf) : 0;
    const live = Math.max(micLvl, botLvl);

    const breath = (Math.sin(t * 1.4) * 0.5 + 0.5);
    const target = live > 0 ? live : (s.pulseMax * breath);
    const amp = tickEnv(target);

    // Disc radius — gentle pulse, never balloons past the bounding box
    const wobble = Math.sin(t * s.speed) * 0.012;
    const r = R * (s.pulseMin + wobble + amp * s.pulseMax * 0.5);

    ctx2d.clearRect(0, 0, w, h);

    // ChatGPT-style: a clean defined disc with an interior watercolor wash.
    // Hard edge, premium feel. No additive bloom, no outer halo flashlight.
    ctx2d.save();
    ctx2d.beginPath();
    ctx2d.arc(cx, cy, r, 0, Math.PI * 2);
    ctx2d.clip();

    // 1. Base fill — the disc's primary color
    ctx2d.fillStyle = s.c[1];
    ctx2d.fillRect(cx - r, cy - r, r * 2, r * 2);

    // 2. Three drifting wash blobs — different colors, different speeds, all
    //    inside the clipped disc. Creates the watercolor cloud effect.
    const blobs = [
      { color: s.c[0], speed: 0.18, phase: 0.0, sizeMul: 0.95 },
      { color: s.c[2], speed: 0.23, phase: 2.1, sizeMul: 0.85 },
      { color: s.c[0], speed: 0.31, phase: 4.7, sizeMul: 0.70 },
    ];
    for (const b of blobs) {
      const ph = t * b.speed + b.phase;
      const ox = Math.sin(ph) * r * 0.35;
      const oy = Math.cos(ph * 0.7) * r * 0.35;
      const br = r * b.sizeMul * (1 + Math.sin(ph * 1.3) * 0.1 + amp * 0.15);
      const g = ctx2d.createRadialGradient(cx + ox, cy + oy, 0, cx + ox, cy + oy, br);
      g.addColorStop(0,    hexToRGBA(b.color, 0.55 + amp * 0.20));
      g.addColorStop(0.65, hexToRGBA(b.color, 0.20));
      g.addColorStop(1,    hexToRGBA(b.color, 0));
      ctx2d.fillStyle = g;
      ctx2d.fillRect(cx - r, cy - r, r * 2, r * 2);
    }

    // 3. Top-down brightening — like ChatGPT's lighter cloudy top
    const lift = ctx2d.createLinearGradient(cx, cy - r, cx, cy + r);
    lift.addColorStop(0,    hexToRGBA(s.c[2], 0.45));
    lift.addColorStop(0.45, hexToRGBA(s.c[2], 0.10));
    lift.addColorStop(1,    hexToRGBA(s.c[2], 0));
    ctx2d.fillStyle = lift;
    ctx2d.fillRect(cx - r, cy - r, r * 2, r * 2);

    // 4. Inner edge darken — gives the disc a subtle 3D feel without halo
    const edge = ctx2d.createRadialGradient(cx, cy, r * 0.85, cx, cy, r);
    edge.addColorStop(0, "rgba(0,0,0,0)");
    edge.addColorStop(1, "rgba(0,0,0,0.18)");
    ctx2d.fillStyle = edge;
    ctx2d.fillRect(cx - r, cy - r, r * 2, r * 2);

    ctx2d.restore();

    // 5. A whisper of an outer glow — 4% opacity, just enough to hint at
    //    bleed without the flashlight effect. Drops to 0 at idle.
    if (currentState !== "idle" && currentState !== "muted") {
      const og = ctx2d.createRadialGradient(cx, cy, r, cx, cy, r * 1.18);
      og.addColorStop(0, hexToRGBA(s.c[0], 0.04 + amp * 0.04));
      og.addColorStop(1, "rgba(0,0,0,0)");
      ctx2d.fillStyle = og;
      ctx2d.fillRect(0, 0, w, h);
    }

    orbRafHandle = requestAnimationFrame(render);
  }
  orbRafHandle = requestAnimationFrame(render);
}

function stopOrb() {
  if (orbRafHandle) {
    cancelAnimationFrame(orbRafHandle);
    orbRafHandle = 0;
  }
  const canvas = document.getElementById("voice-orb");
  if (canvas) {
    const c = canvas.getContext("2d");
    c.clearRect(0, 0, canvas.width, canvas.height);
  }
}

// ---------------------------------------------------------------------------
// Mic capture — AudioWorklet → WebSocket
// ---------------------------------------------------------------------------

let micStream = null;
let workletNode = null;
let ws = null;
let pingInterval = null;

async function startVoice() {
  if (ws) return;
  setState("listening");
  transcriptEl.textContent = "";

  try {
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
  } catch (err) {
    setState("error");
    transcriptEl.textContent = `Mic error: ${err.message}`;
    return;
  }

  const ctx = ensureAudioCtx();
  await ctx.audioWorklet.addModule("/web/audio_worklet.js");

  workletNode = new AudioWorkletNode(ctx, "capture");
  const source = ctx.createMediaStreamSource(micStream);
  source.connect(workletNode);
  // Also tap the mic stream into an AnalyserNode for the visualizer orb.
  // Cheap (no extra processing) and lets the orb pulse with the user's
  // voice while they speak — the ChatGPT-style "I can hear you" feedback.
  source.connect(getOrCreateMicAnalyser(ctx));
  startOrb();
  // Worklet does NOT need to connect to destination — it only produces port
  // messages. Connecting would echo the mic to speakers.

  // Open WebSocket.
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws/voice`);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    console.log("WS open");
    pingInterval = setInterval(() => {
      if (ws?.readyState === 1) ws.send(JSON.stringify({ type: "PING" }));
    }, 15000);
    workletNode.port.onmessage = (e) => {
      if (ws?.readyState === 1) ws.send(e.data);
    };
  };
  ws.onmessage = (ev) => {
    if (typeof ev.data === "string") {
      handleEvent(JSON.parse(ev.data));
    } else {
      playWavChunk(ev.data);
    }
  };
  ws.onerror = (err) => {
    console.error("WS error", err);
    setState("error");
  };
  ws.onclose = () => {
    console.log("WS closed");
    cleanup();
  };

  startBtn.disabled = true;
  muteBtn.disabled = false;
}

function muteMic() {
  if (ws?.readyState === 1) ws.send(JSON.stringify({ type: "MUTE" }));
  stopAllAudio();
}

function cleanup() {
  if (pingInterval) clearInterval(pingInterval);
  pingInterval = null;
  if (workletNode) try { workletNode.disconnect(); } catch (_) {}
  workletNode = null;
  if (micStream) {
    micStream.getTracks().forEach((t) => t.stop());
    micStream = null;
  }
  detachMicAnalyser();
  stopOrb();
  ws = null;
  startBtn.disabled = false;
  muteBtn.disabled = true;
  setState("idle");
}

// ---------------------------------------------------------------------------
// Server event handler
// ---------------------------------------------------------------------------

function handleEvent(ev) {
  switch (ev.type) {
    case "USER_SPEAKING_START":
      setState("speaking_user");
      transcriptEl.textContent = "(hearing you…)";
      break;
    case "USER_DONE":
      setState("thinking");
      break;
    case "USER_SILENT":
      setState("listening");
      transcriptEl.textContent = "Didn't catch that — try again.";
      break;
    case "TRANSCRIPT":
      transcriptEl.textContent = `You: "${ev.text}"`;
      break;
    case "BOT_SPEAKING_START":
      setState("speaking");
      replyEl.hidden = false;
      spokenEl.textContent = "";
      formSection.hidden = true;
      formSection.innerHTML = "";
      renderCitations(ev.citations || []);
      break;
    case "BOT_SPEAKING_END":
      spokenEl.textContent = ev.spoken_summary || "";
      // Wait until queued audio finishes, then go listening.
      const waitForAudio = () => {
        if (audioCtx && audioCtx.currentTime < nextStartTime) {
          setTimeout(waitForAudio, 100);
        } else {
          setState("listening");
        }
      };
      waitForAudio();
      break;
    case "PDF_READY":
      renderPdf(ev);
      break;
    case "INTERRUPT":
      stopAllAudio();
      setState("speaking_user");
      break;
    case "ERROR":
      console.error("Server error:", ev.message);
      setState("error");
      transcriptEl.textContent = `Error: ${ev.message}`;
      break;
    case "PONG":
      // ignore
      break;
    default:
      console.log("unknown event:", ev);
  }
}

function renderCitations(citations) {
  citationsEl.innerHTML = "";
  for (const c of citations) {
    const li = document.createElement("li");
    const head = c.section ? `${c.source} — ${c.section}` : c.source;
    const tier = c.tier || "HOT";
    const badge = `<span class="citation-tier-badge" data-tier="${tier}">${tier}</span>`;
    li.innerHTML = `${badge}<strong>${head}</strong><br><span>${c.quote}</span>`;
    citationsEl.appendChild(li);
  }
}

function renderPdf(ev) {
  formSection.hidden = false;
  const block = document.createElement("div");
  block.className = "form-block";
  const missingTxt = ev.missing_fields?.length
    ? ` · need: ${ev.missing_fields.join(", ")}`
    : "";
  block.innerHTML = `
    <h4>${ev.form_id}</h4>
    <p class="form-meta">Filled${missingTxt}</p>
    <iframe class="form-pdf" src="${ev.pdf_url}" title="${ev.form_id} preview"></iframe>
    <a class="form-download" href="${ev.pdf_url}" download>Download ${ev.form_id}</a>
  `;
  formSection.appendChild(block);
}

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

startBtn.addEventListener("click", startVoice);
muteBtn.addEventListener("click", muteMic);
muteBtn.disabled = true;

// Push-to-talk fallback — used if WS path breaks at the venue. Kept simple:
// hold to record, release to send to the legacy /voice HTTP endpoint.
let fallbackRec = null;
let fallbackChunks = [];

if (fallbackBtn) {
  const beginFallback = async () => {
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      fallbackRec = new MediaRecorder(s, { mimeType: "audio/webm" });
      fallbackChunks = [];
      fallbackRec.ondataavailable = (e) => fallbackChunks.push(e.data);
      fallbackRec.onstop = async () => {
        const blob = new Blob(fallbackChunks, { type: "audio/webm" });
        const fd = new FormData();
        fd.append("file", blob, "input.webm");
        const r = await fetch("/voice", { method: "POST", body: fd });
        const data = await r.json();
        spokenEl.textContent = data.spoken_summary || "";
        replyEl.hidden = false;
        renderCitations(data.citations || []);
        if (data.audio_url) {
          const a = new Audio(data.audio_url);
          a.play();
        }
        for (const f of data.forms || []) {
          renderPdf({
            form_id: f.form_id,
            pdf_url: f.pdf_url,
            missing_fields: f.missing_fields,
          });
        }
      };
      fallbackRec.start();
      fallbackBtn.classList.add("recording");
      transcriptEl.textContent = "Listening (fallback)…";
    } catch (err) {
      transcriptEl.textContent = `Fallback mic error: ${err.message}`;
    }
  };
  const endFallback = () => {
    if (fallbackRec?.state === "recording") fallbackRec.stop();
    fallbackBtn.classList.remove("recording");
    fallbackRec?.stream?.getTracks().forEach((t) => t.stop());
    transcriptEl.textContent = "Transcribing…";
  };
  fallbackBtn.addEventListener("mousedown", beginFallback);
  fallbackBtn.addEventListener("mouseup", endFallback);
  fallbackBtn.addEventListener("touchstart", (e) => { e.preventDefault(); beginFallback(); });
  fallbackBtn.addEventListener("touchend",   (e) => { e.preventDefault(); endFallback(); });
}

setState("idle");

// Boot the WebGL orb on page load so visitors see a live idle disc
// before they ever click the mic. Non-blocking; gracefully no-ops on
// browsers without WebGL2.
ensureOrb();