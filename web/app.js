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
    botAnalyser.fftSize = 256;
    botAnalyser.smoothingTimeConstant = 0.85;
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
    micAnalyser.fftSize = 256;
    micAnalyser.smoothingTimeConstant = 0.85;
  }
  return micAnalyser;
}

function detachMicAnalyser() {
  micAnalyser = null;
}

function startOrb() {
  const canvas = document.getElementById("voice-orb");
  if (!canvas || orbRafHandle) return;
  const dpr = window.devicePixelRatio || 1;
  const cssSize = canvas.clientWidth || canvas.width;
  canvas.width = cssSize * dpr;
  canvas.height = cssSize * dpr;
  const ctx2d = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  const cx = w / 2;
  const cy = h / 2;
  const baseR = Math.min(w, h) * 0.22;
  const maxR  = Math.min(w, h) * 0.46;

  // Reusable byte buffers for the analyzers.
  const micBuf = new Uint8Array(128);
  const botBuf = new Uint8Array(128);
  let phase = 0;

  function levelOf(analyser, buf) {
    if (!analyser) return 0;
    analyser.getByteFrequencyData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i];
    return (sum / buf.length) / 255;  // 0..1
  }

  function render() {
    phase += 0.04;
    ctx2d.clearRect(0, 0, w, h);

    // Whichever side is louder drives the pulse; idle ambient if both quiet.
    const micLvl = levelOf(micAnalyser, micBuf);
    const botLvl = levelOf(botAnalyser, botBuf);
    const live   = Math.max(micLvl, botLvl);
    const ambient = 0.05 + 0.04 * (Math.sin(phase) * 0.5 + 0.5);
    const lvl = Math.max(live, ambient);

    const stateColor = (
      currentState === "speaking"      ? "#4d7c0f" :
      currentState === "thinking"      ? "#d97706" :
      currentState === "speaking_user" ? "#2563eb" :
      currentState === "listening"     ? "#2563eb" :
      currentState === "error"         ? "#b91c1c" :
                                          "#3a4250"
    );

    const r = baseR + (maxR - baseR) * lvl;

    // Outer halo — radial gradient that gets brighter with amplitude
    const halo = ctx2d.createRadialGradient(cx, cy, baseR * 0.4, cx, cy, r * 1.4);
    halo.addColorStop(0,   `${stateColor}cc`);
    halo.addColorStop(0.5, `${stateColor}33`);
    halo.addColorStop(1,   `${stateColor}00`);
    ctx2d.fillStyle = halo;
    ctx2d.beginPath();
    ctx2d.arc(cx, cy, r * 1.4, 0, Math.PI * 2);
    ctx2d.fill();

    // Inner solid orb
    const orbGrad = ctx2d.createRadialGradient(
      cx - r * 0.3, cy - r * 0.3, 0,
      cx, cy, r
    );
    orbGrad.addColorStop(0, "rgba(255,255,255,0.92)");
    orbGrad.addColorStop(0.45, stateColor);
    orbGrad.addColorStop(1, "rgba(20,26,34,0.9)");
    ctx2d.fillStyle = orbGrad;
    ctx2d.beginPath();
    ctx2d.arc(cx, cy, r, 0, Math.PI * 2);
    ctx2d.fill();

    orbRafHandle = requestAnimationFrame(render);
  }
  render();
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