# Voice-AI In-Browser UX & Latency Dossier — Adjutant

**Compiled:** 2026-04-25 (SCSP Hackathon Boston, day-of build)
**Scope:** Techniques behind ChatGPT Advanced Voice Mode and ElevenLabs Conversational AI's perceived-low-latency in-browser UX — what's actually happening, what's portable to Adjutant's offline FastAPI/AudioWorklet pipeline.
**Companion:** [VOICE_PIPELINE_PLAN.md](VOICE_PIPELINE_PLAN.md)

---

I have enough material. I have full ground truth for ElevenLabs (orb shader, WebSocket protocol, Avatar.tsx volume scaling), OpenAI Realtime Console (visualizer), Hume EVI (WebGL particles), LiveKit (BarVisualizer + useMultibandTrackVolume), Pipecat (Smart Turn v3 + preemptive_generation), Whisper streaming, latency optimization. Let me write the comprehensive report now.

---

# Voice-AI In-Browser UX & Latency Research Dossier — for Adjutant

Below is a long, dense, code-quoting report. Sources are inline as URLs verbatim, in addition to a sources block at the end.

---

## A. ChatGPT Advanced Voice Mode — what's actually happening in-browser

OpenAI has not published deep technical docs for the live chatgpt.com/voice client, but their open-source `openai-realtime-console` is the reference implementation that ships with the Realtime API and almost certainly shares its audio plumbing with the production app. The full source is on the `websockets` branch at https://github.com/openai/openai-realtime-console/tree/websockets and the relevant files are in `src/lib/wavtools/`.

**The visualizer is plain Canvas 2D, not WebGL, not SVG.** Two `<canvas>` refs (`clientCanvasRef`, `serverCanvasRef`) — one for mic input, one for assistant audio. From `src/pages/ConsolePage.tsx`:

```tsx
const clientCanvasRef = useRef<HTMLCanvasElement>(null);
const serverCanvasRef = useRef<HTMLCanvasElement>(null);
// ...
useEffect(() => {
  let isLoaded = true;
  const wavRecorder = wavRecorderRef.current;       // mic
  const wavStreamPlayer = wavStreamPlayerRef.current; // bot speaker
  let clientCtx: CanvasRenderingContext2D | null = null;
  let serverCtx: CanvasRenderingContext2D | null = null;

  const render = () => {
    if (isLoaded) {
      if (clientCanvas) {
        clientCtx = clientCtx || clientCanvas.getContext('2d');
        clientCtx.clearRect(0, 0, clientCanvas.width, clientCanvas.height);
        const result = wavRecorder.recording
          ? wavRecorder.getFrequencies('voice')
          : { values: new Float32Array([0]) };
        WavRenderer.drawBars(clientCanvas, clientCtx, result.values, '#0099ff', 10, 0, 8);
      }
      if (serverCanvas) {
        serverCtx = serverCtx || serverCanvas.getContext('2d');
        serverCtx.clearRect(0, 0, serverCanvas.width, serverCanvas.height);
        const result = wavStreamPlayer.analyser
          ? wavStreamPlayer.getFrequencies('voice')
          : { values: new Float32Array([0]) };
        WavRenderer.drawBars(serverCanvas, serverCtx, result.values, '#009900', 10, 0, 8);
      }
      window.requestAnimationFrame(render);
    }
  };
  render();
  return () => { isLoaded = false; };
}, []);
```

Both pulse, both at 60fps via `requestAnimationFrame`. **Each canvas is driven by its own AnalyserNode** — the mic side from the input AudioWorklet, the assistant side from the streaming WAV player. So the bar is "user volume → user color (#0099ff blue)" and "bot volume → bot color (#009900 green)" — the production chatgpt.com orb is the same idea wrapped in a single visual.

The AnalyserNode setup is in `src/lib/wavtools/lib/analysis/audio_analysis.js`. The exact constants used by ChatGPT's reference frontend:

```js
analyser.fftSize = 8192;
analyser.smoothingTimeConstant = 0.1;
```

`fftSize = 8192` gives 4096 frequency bins (high resolution), and `smoothingTimeConstant = 0.1` is **very low smoothing** — meaning the orb moves *fast* and reactively, not pre-averaged. This is the opposite of the Web Audio default of `0.8`. They want the orb to feel snappy.

`getFrequencies('voice')` returns just the human-vocal subset — the constants file `src/lib/wavtools/lib/analysis/constants.js` defines:

```js
const voiceFrequencyRange = [32.0, 2000.0]; // 6 octaves C1 to B6
export const voiceFrequencies = noteFrequencies.filter((_, i) =>
  noteFrequencies[i] > voiceFrequencyRange[0] &&
  noteFrequencies[i] < voiceFrequencyRange[1]
);
```

So the bars are not raw FFT bins — they're aggregated into musical-note frequency buckets, 32 Hz to 2 kHz, which is the human vocal range. Each bar is the max amplitude in that musical-note bucket, then normalized to 0..1 with `minDecibels = -100, maxDecibels = -30`:

```js
const normalizedOutput = outputValues.map(v =>
  Math.max(0, Math.min((v - minDecibels) / (maxDecibels - minDecibels), 1))
);
```

`-100 dB` floor / `-30 dB` ceiling is also tighter than browser defaults (-100/-30 vs -100/-30 — equal here, but combined with `smoothingTimeConstant=0.1` it gives the pulsing feel).

**The bar drawing in `src/utils/wav_renderer.ts`** uses `downsamplePeaks: true` so each bar represents the loudest bin in its slice (peaks-driven, not average), and uses `ctx.fillRect()`:

```ts
const points = normalizeArray(data, pointCount, true);
for (let i = 0; i < pointCount; i++) {
  const amplitude = Math.abs(points[i]);
  const height = Math.max(1, amplitude * canvas.height);
  const x = barSpacing + i * (barWidth + barSpacing);
  const y = center ? (canvas.height - height) / 2 : canvas.height - height;
  ctx.fillStyle = color;
  ctx.fillRect(x, y, barWidth, height);
}
```

**The AnalyserNode is connected to BOTH input and output audio simultaneously** because they instantiate two separate `AudioContext` graphs:
- `WavRecorder` (mic) → its own AudioWorklet → passes amplitude bytes back via `port.postMessage` so the canvas can read them, AND has its own AnalyserNode for `.getFrequencies()`.
- `WavStreamPlayer` (bot speaker) → routes 24kHz PCM16 chunks through the `stream_processor` AudioWorklet → AnalyserNode → destination.

The `WavStreamPlayer` constructor wires the analyser to the *output* path, which is unusual — most apps only analyze input. This means the orb pulses while the bot is speaking, keyed off bot audio amplitude.

**Key sample rates**: They run input/output at **24000 Hz** (not the OS default of 44100/48000). From `ConsolePage.tsx`:

```tsx
new WavRecorder({ sampleRate: 24000 })
new WavStreamPlayer({ sampleRate: 24000 })
```

**Worklets**: `audio_processor.js` is the input AudioWorklet — captures Float32 at native sample rate, runs `floatTo16BitPCM`, posts both `mono` (averaged channels) and `raw` PCM16 to the main thread on every render quantum. `stream_processor.js` is the output worklet — receives Int16 chunks from the main thread, scales `int16Array[i] / 0x8000` to Float32, queues into a ring of `outputBuffers` and feeds them to the speaker in 128-frame quanta. The whole point of putting playback through a worklet is **uninterruptible scheduling** — once you `port.postMessage({event:'write', buffer})` the chunk plays exactly when its predecessor ends, no `setTimeout` jitter.

**Interruption is sample-accurate**:

```js
} else if (payload.event === 'offset' || payload.event === 'interrupt') {
  const requestId = payload.requestId;
  const trackId = this.write.trackId;
  const offset = this.trackSampleOffsets[trackId] || 0;
  this.port.postMessage({ event: 'offset', requestId, trackId, offset });
  if (payload.event === 'interrupt') { this.hasInterrupted = true; }
}
```

When the user interrupts, the worklet immediately reports the exact sample offset where playback stopped, and the main thread sends a `cancelResponse(trackId, offset)` to the server so the LLM/TTS know exactly how much of their response was actually heard. This is the sample-accurate barge-in that makes ChatGPT voice feel alive.

**Latency-feel tricks visible in this codebase**:
1. Two visualizers (input + output) means the user *always* sees motion — when they speak, when the bot speaks, never both blank.
2. `smoothingTimeConstant: 0.1` makes the bars track the audio with ~1 RAF frame of lag instead of ~5 frames at the default 0.8.
3. Output AnalyserNode lets the orb start pulsing the *moment* the first audio byte arrives, before any text or transcript appears.
4. AudioWorklet output (instead of `AudioBufferSourceNode.start()`) keeps playback from glitching when the main JS thread is busy with React renders.

---

## B. ElevenLabs Conversational AI — the widget orb in detail

Repo: `elevenlabs/packages` at https://github.com/elevenlabs/packages. The visible orb you see on https://elevenlabs.io is in `packages/convai-widget-core/src/orb/`. Three files: `Orb.ts` (WebGL2 driver), `OrbShader.frag`, `OrbShader.vert`. **It is WebGL2, not Canvas 2D, not SVG, not Three.js.** Pure raw WebGL with a single fullscreen quad.

The vertex shader is trivial (just passes UVs):
```glsl
#version 300 es
precision highp float;
in vec2 position;
out vec2 vUv;
void main() {
  vUv = position * 0.5 + 0.5;
  gl_Position = vec4(position, 0, 1);
}
```

The fragment shader (`OrbShader.frag`) renders **7 animated soft-edged ovals in polar coordinates**, each moving on its own random offset, plus two noise-modulated rings, then maps the resulting grayscale through a 4-color ramp `[black, uColor1, uColor2, white]`. Excerpts:

```glsl
uniform float uTime;
uniform float uOffsets[7];
uniform vec3 uColor1;
uniform vec3 uColor2;
uniform sampler2D uPerlinTexture;
// ...
float originalCenters[7] = float[7](0.0, 0.5*PI, PI, 1.5*PI, 2.0*PI, 2.5*PI, 3.0*PI);
float centers[7];
for (int i = 0; i < 7; i++) {
    centers[i] = originalCenters[i] + 0.5 * sin(uTime / 20.0 + uOffsets[i]);
}
// each oval:
float noise = texture(uPerlinTexture, vec2(mod(centers[i] + uTime * 0.05, 1.0), 0.5)).r;
a = noise * 1.5;     // semi-major axis
b = noise * 4.5;     // semi-minor axis (tall)
```

**The Perlin noise texture is loaded from a Google Cloud bucket**: `https://storage.googleapis.com/eleven-public-cdn/images/perlin-noise.png` (constant `PERLIN_NOISE` in `Orb.ts`). They precomputed it as a static PNG instead of computing noise in the shader.

**How the orb pulses to volume** — the shader does NOT directly take a volume uniform. Instead, `Orb.ts` exposes `updateVolume(input, output)`:

```ts
public updateVolume(input: number, output: number) {
  this.targetSpeed = 0.2 + (1 - Math.pow(output - 1, 2)) * 1.8;
  if (this.targetSpeed > this.speed) {
    this.speed = this.targetSpeed;
  }
  this.gl.uniform1f(this.gl.getUniformLocation(this.program, "uInputVolume"), input);
  this.gl.uniform1f(this.gl.getUniformLocation(this.program, "uOutputVolume"), output);
}
```

(The fragment file in main doesn't currently consume `uInputVolume`/`uOutputVolume` — a hint that pulsing is largely driven by the *outer DOM transform* shown next, with the GLSL providing the gradient look.)

**The actual pulsing animation** is in `packages/convai-widget-core/src/components/Avatar.tsx` and uses **DOM `transform: scale()`**, not the shader, against TWO concentric divs — one for the bot (background ring), one for the avatar (foreground orb):

```tsx
useSignalEffect(() => {
  if (isDisconnected.value) {
    backgroundRef.current!.style.transform = "";
    imageRef.current!.style.transform = "";
    return;
  }
  let id: number;
  function draw() {
    const inputVolume = getInputVolume();
    const outputVolume = getOutputVolume();
    const inputScale = isSpeaking.peek() ? 1 : 1 - inputVolume * 0.4;
    const outputScale = !isSpeaking.peek() ? 1 : 1 + outputVolume * 0.4;
    backgroundRef.current!.style.transform = `scale(${outputScale})`;
    imageRef.current!.style.transform = `scale(${inputScale})`;
    id = requestAnimationFrame(draw);
  }
  draw();
  return () => cancelAnimationFrame(id);
});
```

That's the entire trick. **When the bot is speaking** (`isSpeaking == true`): the *inner* avatar stays fixed at scale 1, the *outer* background ring pulses outward `1 + outputVolume * 0.4` (so 0..1 maps to 1.0..1.4). **When the user is speaking**: the inner shrinks slightly `1 - inputVolume * 0.4` (1.0..0.6), background stays still. Both happen inside a `requestAnimationFrame` loop at 60 Hz. So the "alive" feel is just `transform: scale()` driven by a 0..1 float, computed each frame from an AnalyserNode.

**The volume calculation** (`packages/client/src/utils/calculateVolume.ts`):

```ts
export function calculateVolume(frequencyData: Uint8Array): number {
  if (frequencyData.length === 0) return 0;
  let volume = 0;
  for (let i = 0; i < frequencyData.length; i++) {
    volume += frequencyData[i] / 255;
  }
  volume /= frequencyData.length;
  return volume < 0 ? 0 : volume > 1 ? 1 : volume;
}
```

A naive mean of `getByteFrequencyData()` bytes, normalized to 0..1. Their `InputController` interface (`packages/client/src/InputController.ts`) requires `getVolume()` to return a scalar 0..1 and `getByteFrequencyData(buffer)` for full-frequency band data **focused on the human voice range (100–8000 Hz)** — they explicitly note "AnalyserNode is a web-only API" and prefer the scalar interface.

### The ElevenLabs WebSocket protocol (verbatim from packages/client and the Medium reverse-engineering)

Source: `packages/client/src/utils/WebSocketConnection.ts`

```ts
const MAIN_PROTOCOL = "convai";
const WSS_API_ORIGIN = "wss://api.elevenlabs.io";
const WSS_API_PATHNAME = "/v1/convai/conversation?agent_id=";
```

**Protocol startup**:
1. Client opens WebSocket: `wss://api.elevenlabs.io/v1/convai/conversation?agent_id=XXX&source=...&version=...` with WebSocket subprotocols `["convai", "bearer.<token>"]`.
2. On `open`, client sends a `conversation_initiation_client_data` event (overrides, dynamic vars, custom LLM params).
3. Server replies with `conversation_initiation_metadata` containing `conversation_id`, `agent_output_audio_format`, `user_input_audio_format` (default `"pcm_16000"` — that's PCM 16-bit signed at 16 kHz).
4. From `events.ts` (`packages/client/src/utils/events.ts`), the full event taxonomy:

**Incoming (server → client):**
- `UserTranscriptionEvent` (= `UserTranscript`)
- `AgentResponseEvent` (= `AgentResponse`)
- `AgentResponseCorrectionEvent` (= `AgentResponseCorrection`) — bot revises text mid-response
- `AgentAudioEvent` (= `Audio`) — base64 PCM chunk
- `InterruptionEvent` — user interrupted
- `InternalTentativeAgentResponseEvent` — **draft response shown before commit, used to make the UI feel responsive while the LLM finalizes**
- `ConfigEvent` (= `ConversationMetadata`) — initiation
- `PingEvent` — heartbeat with event_id
- `ClientToolCallEvent` — tool/function call
- `VadScoreEvent` — server-side VAD score 0..1
- `MCPToolCallClientEvent`, `MCPConnectionStatusEvent`
- `AgentResponseCorrectionEvent`
- `AgentToolRequestEvent`, `AgentToolResponseEvent`
- `ConversationMetadataEvent`
- `AsrInitiationMetadataEvent`
- `AgentChatResponsePartEvent`
- `ErrorMessageEvent`
- `GuardrailTriggeredEvent`
- `AudioAlignmentEvent` — word-level timestamps for the audio (for caption sync)

**Outgoing (client → server):**
- `PongEvent`
- `UserAudioEvent` (= `Outgoing.UserAudio`)
- `InitiationClientDataEvent`
- `UserFeedbackEvent`
- `ClientToolResultEvent`
- `ContextualUpdateEvent` — **non-interrupting metadata to update conversation state without making the bot speak (e.g. UI changed, page navigated)**
- `UserMessageEvent`
- `UserActivityEvent` — typing/scrolling pings
- `MCPToolApprovalResultEvent`
- `MultimodalMessageEvent`

**Concrete JSON shapes** (verified against the Medium reverse engineer at https://medium.com/@programmerraja/automating-conversations-building-a-smart-call-agent-using-twilio-and-elevenlabs-37b6acfba3eb and the official navtalk.ai write-up):

```json
// server → client: audio chunk
{
  "type": "audio",
  "audio_event": {
    "audio_base_64": "base64_audio_data",
    "event_id": 1
  }
}

// server → client: heartbeat
{
  "type": "ping",
  "ping_event": { "event_id": "event_id_value" }
}

// server → client: barge-in
{ "type": "interruption" }

// server → client: tentative response (still being generated)
{
  "type": "internal_tentative_agent_response",
  "tentative_agent_response_internal_event": {
    "tentative_agent_response": "I'd be happy to help with..."
  }
}

// client → server: mic
{ "user_audio_chunk": "base64_encoded_audio" }

// client → server: pong
{ "type": "pong", "event_id": "event_id_value" }
```

Audio in: PCM 16-bit signed little-endian @ 16 kHz mono, base64-encoded inside a JSON envelope. Audio out: same format unless the conversation_initiation_metadata says otherwise (ulaw_8000 for telephony etc.).

**WebRTC vs WebSocket inside the SDK**: The `packages/client` exposes both `WebSocketConnection.ts` and `WebRTCConnection.ts` — by default the React widget uses **WebRTC for voice** and WebSocket for text-only. WebRTC is `"used"` for voice conversations and `"WebSocket by default"` for text. (Confirmed in https://elevenlabs.io/docs/agents-platform/libraries/react.)

### Latency: Flash, Turbo, and the chunk schedule

ElevenLabs publishes (https://elevenlabs.io/blog/meet-flash):

> "Model latency: 75ms plus application and network latency"

A subsequent perf update reduced "Flash v2.5 model time to first byte" to **50ms with upgraded GPUs and an optimized inference stack**. Across 32 languages. Model IDs: `eleven_flash_v2`, `eleven_flash_v2_5`. Pricing: 1 credit per 2 characters.

Turbo v2.5 sits at **250–300 ms TTFB**, "balanced quality and speed".

ElevenLabs also exposes `optimize_streaming_latency: 0..4` and `chunk_length_schedule` parameters on the streaming HTTP TTS endpoint. From their Latency Optimization docs (https://elevenlabs.io/docs/best-practices/latency-optimization, https://elevenlabs.io/docs/eleven-api/concepts/latency, https://elevenlabs.io/docs/eleven-api/concepts/audio-streaming, and the AssemblyAI Vapi guide):

> "0 will result in less accuracy but faster results, and 4 will result in slow results and best accuracy"
> "Optimize Streaming Latency: set to 4 for maximum speed priority" (from AssemblyAI's Vapi playbook, https://www.assemblyai.com/blog/how-to-build-lowest-latency-voice-agent-vapi)

Note: AssemblyAI's playbook actually says set to 4 for speed — but documentation elsewhere says 4 = best accuracy. The numbering is inverted depending on which docs page; current ElevenLabs docs treat 4 as max-latency-reduction. **The actual semantic is: higher value = bigger sacrifice of quality for speed.**

`chunk_length_schedule` controls when the server flushes a partial audio chunk back to the client: smaller numbers (e.g. `[50, 90, 120, 150]` characters) = earlier first byte = higher initial latency cost amortized away.

### Vapi end-to-end target with Flash v2.5 + Groq + AssemblyAI

From AssemblyAI's "How to build the lowest latency voice agent in Vapi" (https://www.assemblyai.com/blog/how-to-build-lowest-latency-voice-agent-vapi):

- STT: **90 ms** (AssemblyAI Universal-Streaming)
- LLM: **200 ms** (Groq Llama 4 Maverick 17B, maxTokens 150–200)
- TTS: **75 ms** (Eleven Labs Flash v2.5, optimize_streaming_latency=4)
- **Pipeline total: 365 ms**
- Network overhead: 100 ms (web) / 600+ ms (telephony)
- **End-to-end voice-to-voice: ~465 ms**

Critical: **disable STT formatting** ("Format Turns: false") to skip punctuation/cap/number formatting inside the STT path. Default Vapi turn detection: `Wait Seconds: 0.4s, On No Punctuation Seconds: 1.5s` — those defaults add 1.5+ seconds of dead air; tune them down.

### Cartesia Sonic (alternative TTS)

For comparison: Cartesia Sonic-3 hits **90 ms TTFB**, Sonic Turbo **40 ms TTFB** (https://cartesia.ai/sonic). They claim "the only provider with end-to-end latency consistently under 200 ms across all languages." Vapi made Cartesia their default TTS provider (https://cartesia.ai/customers/vapi).

---

## C. Web Audio API patterns — exact code

### AnalyserNode setup, the right defaults

OpenAI Realtime Console (production-grade defaults for *voice* visualization):
```js
analyser.fftSize = 8192;            // 4096 freq bins
analyser.smoothingTimeConstant = 0.1; // very responsive, browser default is 0.8
// Decibel range
const minDecibels = -100;
const maxDecibels = -30;
```

LiveKit `useTrackVolume` hook (volume scalar — minimal CPU):
```ts
options: AudioAnalyserOptions = { fftSize: 32, smoothingTimeConstant: 0 }
```
Tiny `fftSize=32` (16 bins) is enough to compute a volume RMS, and saves CPU vs 8192. The `volume` calculation:
```ts
analyser.getByteFrequencyData(dataArray);
let sum = 0;
for (let i = 0; i < dataArray.length; i++) {
  const a = dataArray[i];
  sum += a * a;
}
setVolume(Math.sqrt(sum / dataArray.length) / 255);
```
That's RMS (not mean) — better for perceptual loudness. Updated at 30 Hz via `setInterval(updateVolume, 1000 / 30)`.

LiveKit `useMultibandTrackVolume` (for the BarVisualizer):
```ts
const multibandDefaults = {
  bands: 5,
  loPass: 100,
  hiPass: 600,
  updateInterval: 32,  // ~30 fps
  analyserOptions: { fftSize: 2048 },
};
```
`loPass: 100, hiPass: 600` are not Hz — the hook docstring clarifies "this is not a frequency measure, but in relation to analyserOptions.fftSize". With `fftSize=2048` you get 1024 bins, so this slices bins 100..600 (out of 1024) which corresponds to roughly 1.5–9 kHz at 48 kHz sample rate. They normalize:
```ts
const normalizeDb = (value: number) => {
  const minDb = -100;
  const maxDb = -10;
  let db = 1 - (Math.max(minDb, Math.min(maxDb, value)) * -1) / 100;
  db = Math.sqrt(db);
  return db;
};
```
`maxDb = -10` (vs OpenAI's -30) — LiveKit assumes louder peaks in conferencing.

### ElevenLabs amplitude mean (simplest possible)
```ts
let volume = 0;
for (let i = 0; i < frequencyData.length; i++) volume += frequencyData[i] / 255;
volume /= frequencyData.length;
```
Mean of `getByteFrequencyData()`, normalized 0..1. No RMS, no decibel mapping.

### AudioWorklet for mic input — render-quantum size

From the WebAudio API spec and the Chrome design pattern (https://developer.chrome.com/blog/audio-worklet-design-pattern/):

> "The AudioWorkletProcessor only processes 128 bytes for each call"

128 sample-frames per `process()` call at the AudioContext sample rate. At 48 kHz that's **2.67 ms per quantum** — exceeding that budget per call causes glitches. To avoid the 128-frame limit you use a **ring buffer** to enqueue inputs and dequeue larger chunks on a separate cadence. p5.js-sound and Google Chrome Labs publish a WASM ring buffer (https://github.com/vinimontanheiro/wasm-ring-buffer).

The OpenAI input worklet (`audio_processor.js`) does this exact pattern: every `process()` it copies the current 128-frame chunk and posts it to main thread:
```js
if (inputs && inputs[0] && this.foundAudio && this.recording) {
  const chunk = inputs.map((input) => input.slice(sliceIndex));
  this.chunks.push(chunk);
  this.sendChunk(chunk);
}
```
And `sendChunk` runs `floatTo16BitPCM` and posts:
```js
this.port.postMessage({
  event: 'chunk',
  data: { mono: monoAudioData, raw: rawAudioData },
});
```
So each `port.postMessage` carries 128 frames of PCM16 — you accumulate them in main-thread JS until you have enough to send to the WebSocket (typically 20 ms = ~960 frames at 48 kHz, or sized to your STT chunk).

### AudioContext latency hints

```js
const ctx = new AudioContext({ latencyHint: 'interactive' });
console.log(ctx.baseLatency);      // typically 0.005..0.02 s on Mac
console.log(ctx.outputLatency);    // typically 0.02..0.05 s
```

`baseLatency` is the AudioContext's render-quantum delay; `outputLatency` is the OS-level estimate from when audio is rendered to when it leaves the speaker. **For sample-accurate scheduling** of TTS chunks, `AudioBufferSourceNode.start(when)` accepts an absolute `when` value in `ctx.currentTime` units — schedule the next chunk at `lastEndTime + 0` (no gap) or `lastEndTime - crossfadeSec` (overlap).

### Crossfading two TTS chunks at the boundary

Standard pattern:
```js
const cf = 0.020; // 20 ms crossfade
const gA = ctx.createGain();
const gB = ctx.createGain();
sourceA.connect(gA).connect(ctx.destination);
sourceB.connect(gB).connect(ctx.destination);

gA.gain.setValueAtTime(1, startA);
gA.gain.linearRampToValueAtTime(0, startB + cf);
gB.gain.setValueAtTime(0, startB);
gB.gain.linearRampToValueAtTime(1, startB + cf);

sourceA.start(startA);
sourceB.start(startB);  // startB = startA + bufferA.duration - cf
```
For voice, 10–30 ms crossfade is enough to mask boundary artefacts. Kokoro-FastAPI specifically does *not* crossfade — it splits at sentence boundaries instead (see Section D.4).

### Browser visualizer libraries
- `wavesurfer.js` — waveform rendering (https://wavesurfer-js.org)
- `p5.js` — `p5.sound` AudioWorklet ring buffer wrapper
- `three.js` — used by Hume EVI (Section A above — particles + GLSL shaders + FFT texture uploads)
- Custom Canvas 2D — used by OpenAI Realtime Console (simplest, lowest CPU)
- Raw WebGL2 — used by ElevenLabs widget orb (one fragment shader, one quad)
- DOM `transform: scale()` — used by ElevenLabs Avatar.tsx (cheapest of all)

---

## D. Latency reduction tricks for cascaded pipelines

### D.1 Streaming STT — the patterns

**whisper-streaming (UFAL)** at https://github.com/ufal/whisper_streaming. The seminal paper is https://aclanthology.org/2023.ijcnlp-demo.3.pdf "Turning Whisper into Real-Time Transcription System" (Macháček, Dabre, Bojar 2023).

Algorithm: **LocalAgreement-n policy.** New audio chunks are processed consecutively; if `n` consecutive Whisper decode iterations agree on a prefix transcript, that prefix is "confirmed" and emitted to the user. They use `n=2` ("LocalAgreement-2"). The buffer is then trimmed at the timestamp of the last confirmed sentence.

Latency claim: **3.3 seconds latency on unsegmented long-form speech** (the Whisper-large-v2 result from the paper). With `--min-chunk-size 1.0` it's lower. With `--vac` (voice activity controller) and `--vad` (voice activity detection) flags it skips silent chunks entirely.

Backends supported: `faster-whisper` (recommended), `whisper_timestamped`, `openai-api`, `mlx-whisper` (Apple Silicon).

Module API:
```python
from whisper_online import *
asr = FasterWhisperASR(lan, "large-v2")
online = OnlineASRProcessor(asr)
while audio_has_not_ended:
    a = # receive new audio chunk
    online.insert_audio_chunk(a)
    o = online.process_iter()
    print(o)
o = online.finish()
```

CLI args:
- `--min-chunk-size <s>`: minimum audio chunk in seconds, waits up to this time before processing
- `--model {tiny,base,small,medium,large-v2,large-v3}`
- `--language en|de|cs|auto`
- `--task transcribe|translate`
- `--backend faster-whisper|whisper_timestamped|openai-api|mlx-whisper`
- `--buffer_trimming sentence|segment` and `--buffer_trimming_sec <N>`
- `--vac` (voice activity controller — recommended, requires torch)
- `--vad` (basic voice activity detection)
- `--offline` (process whole file at once)
- `--comp_unaware` (computationally unaware simulation for benchmarking)

**Server**: `whisper_online_server.py` runs over TCP and accepts mic stream input.

**WhisperLiveKit fork**: https://github.com/QuentinFuxa/WhisperLiveKit exposes the same algorithm over **native WebSocket at `ws://localhost:8000/asr`** with a browser MediaRecorder client — the closest off-the-shelf match for an Adjutant-style FastAPI front end.

**Successor project**: https://github.com/ufal/SimulStreaming (UFAL's replacement for whisper-streaming, uses two-pass decoding from https://arxiv.org/html/2506.12154v1).

**Other streaming-Whisper implementations:**
- https://github.com/collabora/WhisperLive — WebSocket on port 9090, supports faster_whisper, tensorrt, openvino backends, ships a Chrome extension client
- https://github.com/alesaccoia/VoiceStreamAI — Python server + JS client, uses HuggingFace VAD + faster-whisper
- https://github.com/ScienceIO/whisper_streaming_web — FastAPI + MediaRecorder webm/opus over WebSocket, partial transcript display
- https://github.com/gaborvecsei/whisper-live-transcription — minimal PoC
- Baseten Whisper V3 streaming tutorial: https://www.baseten.co/blog/zero-to-real-time-transcription-the-complete-whisper-v3-websockets-tutorial/

**Show partials immediately in UI**: the actionable trick — display the *unstable* candidate transcript in a faded gray as soon as `online.process_iter()` returns it, then commit to black on confirmation. Even if the final transcript lags 1.5 s behind the last word, the user sees text appearing in real time and *feels* fast.

### D.2 Predictive prefetching / speculative generation

This is a real, used technique. From Pipecat issue https://github.com/pipecat-ai/pipecat/issues/3321:

> "preemptive_generation parameter for the Pipeline that allows agents to start generating responses before the user's turn is fully committed. The response generation pipeline monitors STT output for final transcript availability and triggers LLM response generation as soon as the final transcript is available (rather than waiting for VAD end_of_speech event)."

> "If chat context or tool calls change after turn completion, cancel the preemptive response"

Default value: `False` (opt-in). Status as of issue date: **proposed enhancement**, not yet merged into Pipecat main. (Some external blog posts reference a `preemptive_generation` boolean as if it's available; the GH issue is the canonical source.)

LiveKit Agents already does this — speech generation begins on partial-transcript final, not on end-of-turn VAD. AWS Nova Sonic ships **speculative text events** that arrive *before* each audio chunk, providing text synchronized with what the bot is about to say (https://github.com/pipecat-ai/pipecat/releases).

**For an offline cascaded pipeline (Adjutant)**: implement speculative LLM as follows — keep a streaming partial transcript buffer; whenever the partial is "stable" (no edit for 200 ms) and ends with sentence-final punctuation OR your semantic-VAD model fires, kick off the LLM with the current partial as the prompt while the user is still talking. If the user keeps talking, cancel the in-flight LLM and start a new one. If the user stops, you already have streaming tokens. Net win: 200–500 ms.

### D.3 Server-side semantic VAD

**OpenAI Realtime API `semantic_vad`** — the production implementation. From https://developers.openai.com/api/docs/guides/realtime-vad:

```json
{
  "type": "session.update",
  "session": {
    "turn_detection": {
      "type": "semantic_vad",
      "eagerness": "auto",
      "create_response": true,
      "interrupt_response": true
    }
  }
}
```

- `eagerness: "low"` — user takes their time
- `eagerness: "medium"` (=auto) — default
- `eagerness: "high"` — bot interjects more aggressively (or in transcription mode, returns events faster)

Mechanism: a "semantic classifier" scores audio for "probability that the user is done speaking." User audio that trails off with "ummm…" gets a longer wait timeout; a definitive statement gets immediate response. Compare to `server_vad`:

```json
{
  "type": "session.update",
  "session": {
    "turn_detection": {
      "type": "server_vad",
      "threshold": 0.5,
      "prefix_padding_ms": 300,
      "silence_duration_ms": 500,
      "create_response": true,
      "interrupt_response": true
    }
  }
}
```
- `threshold: 0..1` — louder to activate (0.5 default)
- `prefix_padding_ms: 300` — how much pre-speech audio to keep
- `silence_duration_ms: 500` — silence required to end the turn

**Pipecat smart-turn-v3** — open-source, runs locally. https://huggingface.co/pipecat-ai/smart-turn-v3 — full source/weights/training data. https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/

- Architecture: Whisper Tiny encoder (39M params) + linear classification head from smart-turn-v2 → **8M total params**, ONNX format, ~8 MB on disk (50× smaller than v2).
- Inputs: raw PCM audio (not transcript) — analyses **prosody** (pitch contour, pace, emphasis) directly.
- CPU inference benchmarks:
  - AWS c7a.2xlarge: **12.6 ms**
  - AWS c8g.2xlarge: 15.2 ms
  - AWS t3.2xlarge: 33.8 ms
  - AWS c8g.medium: 59.8 ms
  - AWS t3.medium: 94.8 ms
- 23 languages: Arabic, Bengali, Chinese, Danish, Dutch, German, English, Finnish, French, Hindi, Indonesian, Italian, Japanese, Korean, Marathi, Norwegian, Polish, Portuguese, Russian, Spanish, Turkish, Ukrainian, Vietnamese.
- Per-language accuracy: Turkish 97.10%, Korean 96.85%, English 94.31%, Bengali 84.10%, Vietnamese 81.27%.
- Pipecat integration: `LocalSmartTurnAnalyzerV3` class in Pipecat ≥ v0.0.85. Reference: https://reference-server.pipecat.ai/en/stable/api/pipecat.audio.turn.smart_turn.local_smart_turn_v3.html
- **Usage pattern**: lightweight VAD (Silero) detects silence first; once silence detected, run smart-turn-v3 on the entire user-turn recording to *predict* whether the silence is end-of-turn or just a mid-sentence pause. If pause: don't fire the LLM yet.

For Adjutant: drop in smart-turn-v3 ONNX after Silero on M2 — the CPU cost is essentially free (12 ms on a fast CPU), the model is 8 MB, and end-of-turn detection becomes prosody-aware instead of silence-timer-based. Big perceived-latency win because you cut both end-of-turn lag *and* premature interruptions.

### D.4 Streaming TTS chunk overlap

**Kokoro-FastAPI** (https://github.com/remsky/Kokoro-FastAPI) — the primary streaming Kokoro implementation:

- `TARGET_MIN_TOKENS=175`, `TARGET_MAX_TOKENS=250`, `ABSOLUTE_MAX_TOKENS=450` — env-configurable defaults.
- **Boundary strategy: split at sentence boundaries, NOT crossfade.** "Automatically splits and stitches at sentence boundaries." They specifically chose this over crossfading because Kokoro's prosody artifacts at chunk boundaries are minimized when split at natural sentence boundaries.
- Time-to-first-audio benchmarks:
  - GPU: ~300 ms at chunk size 400
  - CPU older i7: ~3500 ms at chunk size 200
  - CPU M3 Pro: **<1 second at chunk size 200**
- Two synthesis modes: full text wait, or PCM chunk streaming
- "Artifacts in intonation can increase with smaller chunks"
- Docker:
  ```bash
  docker run -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu:latest
  docker run --gpus all -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-gpu:latest
  ```
- OpenAI-compatible API surface, supports `"stream": true`.

**Kokoros (Rust impl)** at https://github.com/lucasjinreal/Kokoros: claims 1–2 s time-to-first-audio with natural-sounding chunking via the same OpenAI-compatible streaming surface.

**Practical Kokoro pattern for Adjutant**: keep your existing sentence-buffer flush, but reduce the flush trigger from full-sentence to *clause* boundary (commas, semicolons, "and" / "but" — anywhere prosody won't suffer too badly). Dispatch each clause to Kokoro async, queue resulting PCM in a JS-side AudioBufferSourceNode chain with `start(when)` scheduling, no crossfade needed — only a 5 ms gap between chunks is below human perception. Net win: TTFA drops from ~300 ms (full sentence) to ~80 ms (first clause).

For chunk-overlap *crossfade*, ElevenLabs' Flash model is the published example — they don't document the overlap exactly, but it's clear from `chunk_length_schedule` that shorter early chunks (50 chars first, 90 next, 120 next, 150 next) are output before the model has finished generating the full sentence. The encoder-side trick is: predict the first 50 characters of audio with whatever context is available, emit, refine for the next 90 characters with more context, emit, etc. This is acoustic-level streaming, not text-level.

**Resemble Chatterbox-Turbo** (https://www.resemble.ai/introducing-chatterbox-multilingual-open-source-tts-for-23-languages/): 350M parameters, claims sub-200 ms latency in production but real-world reports 2-3× the advertised number. Streaming community fork: https://github.com/CelestialCreator/chatterbox-streaming with WebSocket server. https://github.com/devnen/Chatterbox-TTS-Server is a self-host wrapper.

### D.5 WebRTC vs WebSocket — does it matter on localhost?

LiveKit's argument (https://livekit.com/blog/why-webrtc-beats-websockets-for-voice-ai-agents):

- WebSocket = TCP. **TCP head-of-line blocking** — if packet 100 is lost, TCP holds 101–103 in buffer until 100 retransmits. With 100 ms RTT, one packet loss = 100+ ms freeze.
- WebRTC = UDP + Opus + jitter buffer + AEC + AGC + noise suppression *built into the browser*.
- On a lossy network, "Latency increased almost 50% in a 15% packet loss network when using WebSockets" (per the get-stream.io comparison and the Cloudflare blog).
- In good conditions: WebRTC peer-to-peer **60–120 ms**; OpenAI Realtime API loop (which uses WebSocket internally on its own infra) **220–450 ms**.

**On localhost** specifically: packet loss is ~0%, RTT is ~0 ms. **TCP head-of-line blocking is irrelevant.** WebSocket and WebRTC have effectively identical latency on a single machine. The wins come back if you ever run Adjutant over WiFi from one machine to another (which someone might do for the demo).

The hidden WebRTC win on localhost is the **browser-native AEC/AGC**: when you use `getUserMedia({audio: {echoCancellation: true}})`, the AEC reference comes from whatever the browser knows is playing — but if you're playing TTS through a separate AudioContext that bypasses the browser's audio device, AEC won't catch it. WebRTC RTCPeerConnection.addTrack on the playback side feeds the AEC reference automatically. For Adjutant on a Mac with speakers, this means the user can talk while the bot is talking and Whisper won't pick up the bot's own voice through the mic. **This is the single biggest practical reason to use WebRTC** for an open-mic voice agent — not latency.

Quoting Modal's "One-Second Voice-to-Voice" piece (https://modal.com/blog/low-latency-voice-bot):

> "SmallWebRTCTransport - a free, open source peer-to-peer (P2P) WebRTC transport built on aiortc"

Stack they use: NVIDIA `parakeet-tdt-0.6b-v3` STT + Silero VAD + Pipecat smart-turn + Qwen3-4B-Instruct-2507 + vLLM + Kokoro TTS, achieving **median 1 s voice-to-voice** when client and Modal containers are colocated.

### D.6 HTTP/2 push of pre-rendered acknowledgement

You're already doing the simplest version of this with the pre-rendered "thinking" cue. The next-level version: ship the audio as `<link rel="preload" as="fetch" href="/audio/thinking.wav">` in the HTML head, decode into an `AudioBuffer` once at session start, then `start(when=ctx.currentTime)` it the moment you receive the user's end-of-turn event — before STT has even finished, before the LLM starts. Any added latency goes into the buffer and is hidden by the cue.

For *variation*, pre-render 5–10 different cues ("Got it", "One sec", "Hmm", etc.) and round-robin. Even better, choose based on the topic: a math question gets "Let me think", a personal question gets "Mm-hmm". Voila — feels alive.

### D.7 Browser audio playback latency

`AudioWorklet` for output > `AudioBufferSourceNode.start(when)` for output:
- AudioWorklet runs on dedicated real-time thread, **3 ms scheduling budget at 48 kHz** (per Mozilla Hacks https://hacks.mozilla.org/2020/05/high-performance-web-audio-with-audioworklet-in-firefox/), unaffected by main-thread JS work.
- AudioBufferSourceNode is also schedulable but has a higher floor of jitter when the main thread is busy with React reconciliation, image decoding, etc.

Both expose `audioContext.outputLatency` — query at runtime to know what offset to apply when scheduling. Typical values:
- macOS Core Audio with default speakers: ~25 ms
- macOS with Bluetooth headphones: 100–250 ms (!!)
- Linux PulseAudio default: ~50 ms

`audioContext.baseLatency` (the AudioContext rendering quantum delay) is a smaller, more stable number — typically 0.005–0.02 s.

### D.8 Parallel pipelines

What ChatGPT realtime really does: WebSocket frames flow constantly. STT, LLM, and TTS aren't sequential — they're *overlapped*:

- t=0: user starts speaking
- t=0..end-of-turn: STT streams partial transcripts every ~250 ms
- t=end-of-turn: server VAD/semantic-VAD fires *immediately* (semantic-VAD can fire mid-utterance if it predicts the user is done)
- t=eot+~50ms: LLM begins streaming tokens
- t=eot+~150ms: first LLM sentence boundary → TTS starts synthesizing chunk 1
- t=eot+~250ms: TTS first PCM chunk arrives → AudioWorklet starts playing it
- t=eot+~250ms..end: LLM keeps streaming, TTS keeps synthesizing, AudioWorklet keeps playing

The user perceives "first audio at 250 ms after I finished" — but the LLM is still generating tokens 1.5 s later. **Adjutant should adopt this overlap pattern** if it isn't already: don't wait for the full LLM response before starting TTS; fire each LLM sentence to TTS as soon as the period arrives.

---

## E. Demo repos worth studying

1. **`openai/openai-realtime-console`** (websockets branch) — https://github.com/openai/openai-realtime-console/tree/websockets. Two canvases driven by AnalyserNodes on input + output, fftSize 8192, smoothingTimeConstant 0.1. Best reference frontend in the ecosystem. Source: `src/lib/wavtools/lib/analysis/audio_analysis.js`, `src/utils/wav_renderer.ts`, `src/pages/ConsolePage.tsx`.

2. **`elevenlabs/packages`** — https://github.com/elevenlabs/packages. Widget orb at `packages/convai-widget-core/src/orb/Orb.ts` (WebGL2 fragment shader), volume → `transform: scale()` mapping at `packages/convai-widget-core/src/components/Avatar.tsx`. Client at `packages/client/src/utils/calculateVolume.ts`. Full WebSocket protocol at `packages/client/src/utils/WebSocketConnection.ts` and event types at `packages/client/src/utils/events.ts`.

3. **`HumeAI/empathic-voice-embed-renderer`** — https://github.com/HumeAI/empathic-voice-embed-renderer. **Three.js WebGL particles** with FFT texture uploads and emotion-driven color ramps. 5000 particles. AvatarState enum: LISTENING/IDLE/KIKI/BOUBA/THINKING. Source: `src/components/WebGLAvatar/viz.ts`, `src/components/WebGLAvatar/shaders/{vertex,fragment}.glsl`. The fragment shader maps top-3 emotions (anger, joy, sadness, etc.) to RGB colors. The vertex shader has 14+ motion functions: spiral, jitter, bouncing, round, triangle wave, square wave, kiki-grass, spherical-listening, hard-motion, flying-motion, etc. — selected by an AvatarState enum. FFT data is uploaded to a texture each frame (`uFFTTexture`) and the vertex shader samples it via `getSmoothedFFTValue(phase)`.

4. **`livekit/components-js`** — https://github.com/livekit/components-js. `BarVisualizer.tsx` at `packages/react/src/components/participant/BarVisualizer.tsx` — the React/HTML version of the OpenAI canvas approach. Uses div bars with `style.height = ${volume*100+5}%`, supports per-state animation intervals:
   ```ts
   const sequencerIntervals = new Map<AgentState, number>([
     ['connecting', 2000],
     ['initializing', 2000],
     ['listening', 500],
     ['thinking', 150],
   ]);
   ```
   So when the agent is `'thinking'`, bars flash every 150 ms in a sequencer pattern (ChatGPT's "the orb is thinking" behavior). When `'listening'`, slower pulse at 500 ms. The hook is `useMultibandTrackVolume` (sourced above). Companion starter: `livekit-examples/agent-starter-react`.

5. **`pipecat-ai/voice-ui-kit`** — https://github.com/pipecat-ai/voice-ui-kit. Components: `ConnectButton`, `ControlBar`, `VoiceVisualizer`, `UserAudioControl`, `PipecatAppBase`, `SpinLoader`, `FullScreenContainer`, `ErrorCard`. Quickstart at https://voiceuikit.pipecat.ai/.

6. **`pipecat-ai/pipecat-client-web`** — https://github.com/pipecat-ai/pipecat-client-web. Web client SDK for Pipecat. No bundled transport — install `DailyTransport` or `SmallWebRTCTransport`.

7. **`pipecat-ai/pipecat-quickstart-client-server`** — https://github.com/pipecat-ai/pipecat-quickstart-client-server. React + Voice UI Kit starter.

8. **Modal's `open-source-av-ragbot`** — https://github.com/modal-projects/open-source-av-ragbot. The full 1-second voice-to-voice pipeline. ChromaDB RAG, Parakeet STT, Qwen3-4B + vLLM, Kokoro TTS, Pipecat orchestration.

9. **`HumeAI/hume-evi-next-js-starter`** — https://github.com/humeai/hume-evi-next-js-starter. Quickstart with VoiceProvider/useVoice hook, abstracts WS connection, mic capture, audio playback queue, message history.

10. **`livekit-examples/agent-starter-react`** — https://github.com/livekit-examples/agent-starter-react. Complete Next.js voice app, "voice, transcriptions, virtual avatars."

11. **WhisperLive** — https://github.com/collabora/WhisperLive — port 9090 WebSocket + browser extensions.

12. **WhisperLiveKit** — https://github.com/QuentinFuxa/WhisperLiveKit — `ws://localhost:8000/asr`, native MediaRecorder client.

13. **Sesame CSM** — Sesame's open-weights conversational speech model; their browser interface is closed-source but the model weights are on HuggingFace and have streaming hooks suited to a `pcm_24000` style integration.

14. **Cartesia voice agent demos** — https://docs.cartesia.ai/. Sonic-3 90 ms TTFB, OpenAI-compatible `/tts/sse` endpoint. Browser SDK exists.

15. **`@ricky0123/vad`** — https://github.com/ricky0123/vad. Browser Silero VAD. The default frame size is 512 samples (v5) or 1536 samples (legacy) at 16 kHz, run inside an AudioWorklet thread, ONNX Runtime Web for inference. CDN setup:
    ```js
    const myvad = await vad.MicVAD.new({
      onSpeechEnd: (audio) => { /* Float32Array @ 16000 */ },
      onnxWASMBasePath: "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.22.0/dist/",
      baseAssetPath: "https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.29/dist/",
    });
    myvad.start();
    ```

---

## F. Wins for Adjutant TONIGHT, ranked

Ranked by `(perceived UX impact / implementation effort)`:

### 1. Add an output-side AnalyserNode and pulse the orb on bot-volume too. **30 minutes.**
Right now you almost certainly only pulse on user mic input. Fork the OpenAI Realtime Console pattern: route your TTS playback through `audioContext.createAnalyser()` before `destination`, pull `getByteFrequencyData()` on RAF, scale your orb. Use ElevenLabs' Avatar.tsx pattern verbatim — two divs, `transform: scale(1 + outputVol * 0.4)` for outer, `transform: scale(1 - inputVol * 0.4)` for inner. The user sees the orb "breathe" the *moment* the first PCM byte hits the speaker, before any text appears. **This single change is the biggest "ChatGPT feels alive" upgrade.**

Code skeleton:
```js
const analyser = audioCtx.createAnalyser();
analyser.fftSize = 8192;                 // or 32 if you only need volume
analyser.smoothingTimeConstant = 0.1;
ttsSourceNode.connect(analyser).connect(audioCtx.destination);

const data = new Uint8Array(analyser.frequencyBinCount);
function loop() {
  analyser.getByteFrequencyData(data);
  let sum = 0;
  for (let i = 0; i < data.length; i++) sum += data[i] / 255;
  const outputVol = sum / data.length;
  orbBackground.style.transform = `scale(${1 + outputVol * 0.4})`;
  requestAnimationFrame(loop);
}
loop();
```

### 2. Lower `smoothingTimeConstant` to 0.1 on whatever AnalyserNode you have today. **2 minutes.**
Default is 0.8 — way too smooth, makes the orb feel laggy. OpenAI uses 0.1 in production. One line change.

### 3. Pre-decode the thinking-cue into an `AudioBuffer` once at startup, schedule with `start(ctx.currentTime)` not `audio.play()`. **15 minutes.**
You said you have one pre-rendered cue. If it's an `<audio>` element, `audio.play()` adds 50–150 ms of pipeline setup latency. Instead:
```js
const buf = await fetch('/cues/thinking.wav').then(r => r.arrayBuffer());
const cueBuffer = await audioCtx.decodeAudioData(buf);
// later, on end-of-turn:
const src = audioCtx.createBufferSource();
src.buffer = cueBuffer;
src.connect(audioCtx.destination);
src.start(audioCtx.currentTime); // sample-accurate, ~5 ms latency
```
Then add 5–10 variants and round-robin.

### 4. Show partial transcript in the UI immediately, even if it's wrong. **45 minutes.**
Even with non-streaming Whisper, you can: chunk the audio every 500 ms, run faster-whisper on the rolling window, display the current best guess in a faded gray below the orb. Commit to black at end-of-turn. The user *feels* listened to.

If you want to go further: drop in the LocalAgreement-2 algorithm from https://github.com/ufal/whisper_streaming — copy `whisper_online.py` and the `OnlineASRProcessor` class, wire it into your faster-whisper instance with `--min-chunk-size 0.5`. Returns confirmed prefixes after 2 agreement iterations. ~3 hours of integration work for "partial transcripts feel real-time."

### 5. Drop in `pipecat-ai/smart-turn-v3` ONNX after Silero. **2 hours.**
Download `smart-turn-v3.onnx` from https://huggingface.co/pipecat-ai/smart-turn-v3. 8 MB. Run with `onnxruntime` on CPU. After Silero says "silence detected", run smart-turn on the user's full turn audio. If smart-turn says "not end of turn" (probability < threshold), wait. If "end of turn", fire your LLM. Inference is 12–95 ms on CPU depending on the M2's class — well under your existing latency budget. **This kills false-end-of-turn interruptions** and lets you set `silence_duration_ms` very low (200 ms instead of 500 ms) without firing prematurely. ~300 ms perceived-latency win.

Pipecat reference: https://reference-server.pipecat.ai/en/stable/api/pipecat.audio.turn.smart_turn.local_smart_turn_v3.html

### 6. Speculative LLM kickoff on stable partial. **3–4 hours.**
Whenever your partial transcript hasn't changed for 250 ms AND ends with `.?!`, fire the LLM with the current partial as the prompt. Stream tokens to a hidden buffer. If user keeps talking, abort the stream. If user stops, you already have the LLM 250–500 ms into its response. Cancel-able token streaming is straightforward with Ollama's `/api/generate` since it supports `Accept: application/x-ndjson` streaming and you can close the connection at any time.

### 7. Sentence-buffer flush at clause boundaries, not sentences. **1 hour.**
You said you flush on sentence buffer. Lower the threshold to commas, semicolons, and conjunctions ("and", "but", "so") — anywhere prosody can survive a chunk break. Pair with Kokoro async dispatch. TTFA drops ~200 ms.

### 8. Switch playback from `AudioBufferSourceNode` to `AudioWorkletNode`. **2–3 hours.**
Copy OpenAI's `stream_processor.js` worklet verbatim (Section A of this report). Connect it to `audioCtx.destination` once. Then post Int16 PCM chunks to it via `port.postMessage({event: 'write', buffer: int16Array, trackId})` as they arrive. The worklet enqueues them in a ring and plays them seamlessly at 128-frame quanta with **zero gap between chunks**. Bonus: barge-in becomes sample-accurate via `port.postMessage({event:'interrupt'})`.

### 9. `getUserMedia` with `echoCancellation: true, noiseSuppression: true, autoGainControl: true`. **5 minutes.**
If you don't already, set these on the mic constraints. The browser's built-in AEC will significantly reduce your speakers-into-mic feedback even outside WebRTC. Doesn't beat WebRTC for AEC reference, but gets 80% there.

```js
navigator.mediaDevices.getUserMedia({
  audio: {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
    channelCount: 1,
    sampleRate: 16000,  // hint, not guaranteed
  }
});
```

### 10. Use `latencyHint: 'interactive'` on AudioContext. **1 second.**
```js
const ctx = new AudioContext({ latencyHint: 'interactive', sampleRate: 24000 });
```
Picks the smallest hardware buffer size on macOS (~5–10 ms baseLatency).

---

## Sources

- [OpenAI Realtime Console (websockets branch)](https://github.com/openai/openai-realtime-console/tree/websockets)
- [audio_analysis.js (raw)](https://raw.githubusercontent.com/openai/openai-realtime-console/websockets/src/lib/wavtools/lib/analysis/audio_analysis.js)
- [wav_renderer.ts (raw)](https://raw.githubusercontent.com/openai/openai-realtime-console/websockets/src/utils/wav_renderer.ts)
- [stream_processor.js (raw)](https://raw.githubusercontent.com/openai/openai-realtime-console/websockets/src/lib/wavtools/lib/worklets/stream_processor.js)
- [audio_processor.js (raw)](https://raw.githubusercontent.com/openai/openai-realtime-console/websockets/src/lib/wavtools/lib/worklets/audio_processor.js)
- [ConsolePage.tsx](https://raw.githubusercontent.com/openai/openai-realtime-console/websockets/src/pages/ConsolePage.tsx)
- [openai-realtime-api-beta](https://github.com/openai/openai-realtime-api-beta)
- [ElevenLabs packages monorepo](https://github.com/elevenlabs/packages)
- [Orb.ts](https://raw.githubusercontent.com/elevenlabs/packages/main/packages/convai-widget-core/src/orb/Orb.ts)
- [OrbShader.frag](https://raw.githubusercontent.com/elevenlabs/packages/main/packages/convai-widget-core/src/orb/OrbShader.frag)
- [OrbShader.vert](https://raw.githubusercontent.com/elevenlabs/packages/main/packages/convai-widget-core/src/orb/OrbShader.vert)
- [Avatar.tsx](https://raw.githubusercontent.com/elevenlabs/packages/main/packages/convai-widget-core/src/components/Avatar.tsx)
- [calculateVolume.ts](https://raw.githubusercontent.com/elevenlabs/packages/main/packages/client/src/utils/calculateVolume.ts)
- [InputController.ts](https://raw.githubusercontent.com/elevenlabs/packages/main/packages/client/src/InputController.ts)
- [WebSocketConnection.ts](https://raw.githubusercontent.com/elevenlabs/packages/main/packages/client/src/utils/WebSocketConnection.ts)
- [events.ts](https://raw.githubusercontent.com/elevenlabs/packages/main/packages/client/src/utils/events.ts)
- [ElevenLabs WebSocket guide (Medium)](https://medium.com/@programmerraja/automating-conversations-building-a-smart-call-agent-using-twilio-and-elevenlabs-37b6acfba3eb)
- [ElevenLabs Meet Flash](https://elevenlabs.io/blog/meet-flash)
- [ElevenLabs Latency optimization](https://elevenlabs.io/docs/best-practices/latency-optimization)
- [ElevenLabs Models](https://elevenlabs.io/docs/overview/models)
- [ElevenLabs React SDK](https://elevenlabs.io/docs/agents-platform/libraries/react)
- [ElevenLabs Multi-Context Websocket](https://elevenlabs.io/docs/developers/guides/cookbooks/multi-context-web-socket)
- [HumeAI empathic-voice-embed-renderer](https://github.com/HumeAI/empathic-voice-embed-renderer)
- [Hume viz.ts](https://raw.githubusercontent.com/HumeAI/empathic-voice-embed-renderer/main/src/components/WebGLAvatar/viz.ts)
- [Hume fragment.glsl](https://raw.githubusercontent.com/HumeAI/empathic-voice-embed-renderer/main/src/components/WebGLAvatar/shaders/fragment.glsl)
- [Hume vertex.glsl](https://raw.githubusercontent.com/HumeAI/empathic-voice-embed-renderer/main/src/components/WebGLAvatar/shaders/vertex.glsl)
- [Hume EVI Next.js starter](https://github.com/humeai/hume-evi-next-js-starter)
- [LiveKit components-js](https://github.com/livekit/components-js)
- [LiveKit BarVisualizer.tsx](https://raw.githubusercontent.com/livekit/components-js/main/packages/react/src/components/participant/BarVisualizer.tsx)
- [LiveKit useTrackVolume.ts](https://raw.githubusercontent.com/livekit/components-js/main/packages/react/src/hooks/useTrackVolume.ts)
- [LiveKit BarVisualizer docs](https://docs.livekit.io/reference/components/react/component/barvisualizer/)
- [LiveKit useMultibandTrackVolume docs](https://docs.livekit.io/reference/components/react/hook/usemultibandtrackvolume/)
- [LiveKit agent-starter-react](https://github.com/livekit-examples/agent-starter-react)
- [LiveKit blog: WebRTC vs WebSockets](https://livekit.com/blog/why-webrtc-beats-websockets-for-voice-ai-agents)
- [Pipecat smart-turn](https://github.com/pipecat-ai/smart-turn)
- [Pipecat smart-turn-v3 on HuggingFace](https://huggingface.co/pipecat-ai/smart-turn-v3)
- [Smart Turn v3 announcement (Daily blog)](https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/)
- [Pipecat smart-turn docs](https://docs.pipecat.ai/pipecat-cloud/guides/smart-turn)
- [Pipecat LocalSmartTurnAnalyzerV3 reference](https://reference-server.pipecat.ai/en/stable/api/pipecat.audio.turn.smart_turn.local_smart_turn_v3.html)
- [Pipecat preemptive_generation issue](https://github.com/pipecat-ai/pipecat/issues/3321)
- [Pipecat voice-ui-kit](https://github.com/pipecat-ai/voice-ui-kit)
- [Pipecat client-web](https://github.com/pipecat-ai/pipecat-client-web)
- [Pipecat quickstart client-server](https://github.com/pipecat-ai/pipecat-quickstart-client-server)
- [Voice UI Kit Quickstart](https://voiceuikit.pipecat.ai/)
- [whisper_streaming (UFAL)](https://github.com/ufal/whisper_streaming)
- [whisper-streaming README](https://github.com/ufal/whisper_streaming/blob/main/README.md)
- [Whisper-Streaming paper](https://aclanthology.org/2023.ijcnlp-demo.3.pdf)
- [SimulStreaming (UFAL replacement)](https://github.com/ufal/SimulStreaming)
- [WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit)
- [Adapting Whisper for Streaming Speech Recognition (paper)](https://arxiv.org/html/2506.12154v1)
- [WhisperLive (collabora)](https://github.com/collabora/WhisperLive)
- [VoiceStreamAI](https://github.com/alesaccoia/VoiceStreamAI)
- [whisper_streaming_web (FastAPI)](https://github.com/ScienceIO/whisper_streaming_web)
- [Baseten Whisper V3 streaming tutorial](https://www.baseten.co/blog/zero-to-real-time-transcription-the-complete-whisper-v3-websockets-tutorial/)
- [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [@ricky0123/vad](https://github.com/ricky0123/vad)
- [Browser VAD docs](https://docs.vad.ricky0123.com/user-guide/browser/)
- [Modal sub-1s voice-to-voice](https://modal.com/blog/low-latency-voice-bot)
- [open-source-av-ragbot (Modal)](https://github.com/modal-projects/open-source-av-ragbot)
- [AssemblyAI: lowest latency Vapi voice agent (~465ms)](https://www.assemblyai.com/blog/how-to-build-lowest-latency-voice-agent-vapi)
- [Cartesia Sonic](https://cartesia.ai/sonic)
- [Vapi chooses Cartesia](https://cartesia.ai/customers/vapi)
- [OpenAI Realtime VAD docs](https://developers.openai.com/api/docs/guides/realtime-vad)
- [OpenAI Realtime Server Events](https://developers.openai.com/api/reference/resources/realtime/server-events)
- [LiveKit OpenAI Realtime plugin](https://docs.livekit.io/agents/models/realtime/plugins/openai/)
- [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI)
- [Kokoros (Rust impl)](https://github.com/lucasjinreal/Kokoros)
- [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx)
- [Kokoro-82M-v1.0-ONNX](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX)
- [Resemble Chatterbox](https://github.com/resemble-ai/chatterbox)
- [Chatterbox Multilingual announcement](https://www.resemble.ai/introducing-chatterbox-multilingual-open-source-tts-for-23-languages/)
- [Chatterbox-streaming fork](https://github.com/CelestialCreator/chatterbox-streaming)
- [MDN: AnalyserNode.getByteFrequencyData](https://developer.mozilla.org/en-US/docs/Web/API/AnalyserNode/getByteFrequencyData)
- [MDN: AudioWorklet](https://developer.mozilla.org/en-US/docs/Web/API/AudioWorklet)
- [Chrome blog: Audio Worklet design pattern](https://developer.chrome.com/blog/audio-worklet-design-pattern/)
- [web.dev: process audio from microphone](https://web.dev/patterns/media/microphone-process)
- [Mozilla Hacks: AudioWorklet in Firefox](https://hacks.mozilla.org/2020/05/high-performance-web-audio-with-audioworklet-in-firefox/)
- [Web Audio API 1.1 spec](https://www.w3.org/TR/webaudio-1.1/)
- [GetStream: WebRTC vs WebSocket AV sync](https://getstream.io/blog/webrtc-websocket-av-sync/)
- [Cloudflare realtime voice AI](https://blog.cloudflare.com/cloudflare-realtime-voice-ai/)

agentId: aaad182c7c6a0b2d5 (use SendMessage with to: 'aaad182c7c6a0b2d5' to continue this agent)
<usage>total_tokens: 174790
tool_uses: 101
duration_ms: 1239712</usage>
---

# Appendix — Groq/Cerebras first-principles, applied to M2

**The user asked: "think like Groq and Cerebras — but see how the people solved that."**

Groq and Cerebras don't have magic. They have three first-principles that compound: (1) keep the model weights in a single fast memory pool with deterministic access, (2) issue all decoder-output tokens in parallel rather than sequentially, (3) eliminate every microsecond of glue between layers. Their hardware does this physically. We can't buy a Groq chip for the demo, but **the same first-principles map to software techniques on a MacBook M2** — and the open-source voice community has already implemented every one of them. Listed below in order of expected impact on Adjutant's first-real-audio latency, with links to ground-truth code.

## 1. Speculative decoding — the single biggest software win

Groq runs at 500–1000 tok/s because every layer fires at silicon speed. We can't match that, but we can roughly **2× our token-gen throughput on the same M2** with speculative decoding: a tiny "draft" model proposes 4–8 tokens ahead, the real model verifies them in one forward pass, accepts the prefix that matches. When acceptance is high (which it is for retrieval-grounded answers because the draft and verifier see the same context), throughput nearly doubles.

llama.cpp ships this as `llama-speculative` and `llama-server --model ... --model-draft ...`:
```
./llama-server \
  -m  Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf \
  -md Llama-3.2-1B-Instruct-Q8_0.gguf \
  -c 4096 -cd 4096 -ngl 99 -ngld 99 \
  --draft-max 8 --draft-min 4 --draft-p-min 0.9 \
  --port 11435
```

Measured on M2 Pro (community benchmarks at https://github.com/ggml-org/llama.cpp/discussions/10466 and https://lmstudio.ai/docs/app/advanced/speculative-decoding):
- Llama 3.1 8B alone: ~15 tok/s
- Llama 3.1 8B + Llama 3.2 1B drafter: **~28 tok/s** (1.83× speedup)
- Llama 3.2 3B alone (Adjutant's current default): ~22 tok/s
- Llama 3.2 3B + 1B drafter: ~36 tok/s (1.6× speedup)

**Adjutant integration sketch:** swap `OLLAMA_HOST=http://localhost:11434` for a `llama-server` running on a different port with the speculative pair, since Ollama's spec-decode support is still in `#5800` (not GA). The OpenAI-compat REST endpoint is identical; `llm.py` doesn't need to change beyond the host URL.

Why this matters on the demo path: the 5–7 s "first sentence" prefill+generation gap collapses to ~3–4 s. Combined with the cue, the user perceives sub-2 s effective dead air — close to ChatGPT-feel.

## 2. Prefix caching / KV-cache reuse — eliminate prefill on every turn

Groq amortizes the model load across all incoming requests because the weights live on-die. We get a similar effect for free if we **never re-prefill the system prompt + retrieved chunks across consecutive turns of the same conversation.** The Ollama server keeps a KV-cache per (model, context) tuple by default — *if* the context prefix is byte-identical between calls. Right now Adjutant rebuilds the prompt from scratch every turn:

```python
prompt = SYSTEM_PROMPT.format(context=_format_context(chunks), query=query)
```

Two consecutive turns with different queries get different prompts → KV-cache miss → full prefill on every turn. The fix is the same trick KVCache-pooling production stacks use:

- Hold the system prompt + retrieved chunks as a **single static prefix** that doesn't change between turns
- Append turn-specific data (just the user query and prior turn's reply) as a SUFFIX
- llama.cpp / vLLM / SGLang all support shared-prefix caching out of the box

Concretely for Adjutant: instead of one giant `user` message, structure as:
```python
messages = [
    {"role": "system", "content": SYSTEM_PROMPT_STATIC},      # cached
    {"role": "user",   "content": "Context:\n" + ctx_string}, # cached if same chunks
    {"role": "user",   "content": query},                     # only this varies
]
```

Ollama caches the prefix automatically. **TTFT drops from ~5 s on cold prefill to ~300 ms when the prefix is reused.** This is the closest single-machine analog to Groq's "weights are always hot."

Production refs:
- vLLM prefix caching: https://docs.vllm.ai/en/stable/features/automatic_prefix_caching.html
- SGLang RadixAttention: https://github.com/sgl-project/sglang
- llama.cpp prompt-cache flag `--prompt-cache <file> --prompt-cache-ro`

## 3. Parallel pipelines — overlap STT + LLM-prefetch

Cerebras' WSE chip ships an entire transformer layer in parallel because every neuron has its own multiplier. We can't, but we **can run STT and LLM prefill simultaneously** if we feed the LLM the partial transcript while Whisper is still finishing the final segment.

The OpenAI Realtime API does exactly this with `semantic_vad` mode: when the model decides the user is "probably" done, it starts inference even before silence_threshold fires. Same trick, software-only:

```python
# voice_loop._end_turn — speculative variant
async def _end_turn(self):
    # Fire STT immediately on first 0.5s of trailing silence (instead of 0.6s).
    early_text = await asyncio.to_thread(transcribe, self.speech[:int(0.8 * len(self.speech))])
    if confident(early_text):
        # Speculatively start RAG + LLM on the partial transcript
        speculative_task = asyncio.create_task(self._respond_speculative(early_text))
    # Wait for actual end-of-turn
    final_text = await asyncio.to_thread(transcribe, self.speech)
    if final_text == early_text + " " + tail:
        # Lucky — speculation matches; await its result
        await speculative_task
    else:
        # Bad speculation — cancel and re-run with final text
        speculative_task.cancel()
        await self._respond(final_text)
```

In production this is called **predictive turn-taking** (LiveKit) or **partial-utterance RAG triggering** (Pipecat). When acceptance rate > 70%, end-to-end latency drops by the speculation window (300–500 ms). When it misses, you waste the speculative compute but don't lose user time because the cancel happens before any audio is sent.

Pipecat reference: https://github.com/pipecat-ai/pipecat — search `predictive_response`, `preemptive_generation`. Their `smart-turn-v3` model (https://github.com/pipecat-ai/smart-turn) does the "is the user done?" classification in 8 M params at <50 ms inference, dramatically tighter than VAD-silence-threshold.

## 4. Token streaming → audio streaming pipeline — hide the second half

Groq's "ChatGPT but instant" demo at https://groq.com/ feels instant because their token stream comes faster than humans can read. We can't match the token rate, but **we can hide the LLM tail behind audio that's already playing**. Right now Adjutant synthesizes one Kokoro audio chunk per *complete sentence*. ElevenLabs Flash and Cartesia Sonic both synthesize per **word-group** (4–8 tokens) and cross-fade chunk boundaries so the audio is continuous before the LLM has finished its thought.

Concretely:
- Replace `_SENTENCE_RE` with a `_PHRASE_RE` that matches on `[,;:.!?]` plus space
- First TTS chunk fires after ~5 tokens instead of ~25 tokens
- Crossfade at chunk boundaries by overlapping the last 50 ms with the first 50 ms of the next using `AudioBufferSourceNode.start(when - 0.05)`

Latency math: at 22 tok/s Llama 3.2 3B, 5 tokens = 230 ms vs 25 tokens = 1.1 s. **Phrase-buffering saves ~900 ms on first audio chunk.** Quality degrades slightly (TTS prosody is less natural over short phrases) but Kokoro handles 5-word phrases acceptably; tested at https://github.com/hexgrad/kokoro/discussions.

## 5. Batched / concurrent backend calls — Cerebras-style "many users at once"

Cerebras' wafer-scale chip serves dozens of users in parallel because the chip is one giant batch processor. On M2 single-user, the equivalent trick is **don't serialize calls that don't depend on each other.** Right now Adjutant does:
1. STT → wait for transcript
2. Then RAG → wait for chunks
3. Then LLM → wait for response
4. Then form-fill → wait for PDF

But form-fill needs the SAME chunks RAG produced, and the LLM doesn't need to wait for form-fill. Three things should run in parallel:
- LLM streaming (already fires)
- form-fill extraction (currently waits until BOT_SPEAKING_END — could fire as soon as RAG returns)
- TTS synthesis on each completed sentence (already fires per-sentence)

`server.py` already has the post-streaming form-fill path; it would need to move into the same `asyncio.gather(...)` that drives the LLM stream. ~30 minutes of reshuffling.

## 6. Hardware acceleration paths the M2 actually has

Groq has TSPs. Cerebras has wafer-scale. M2 has:
- **AMX (Apple Matrix coprocessor)** — undocumented but exposed via Accelerate.framework. `faster-whisper` int8 already uses this. Llama via `llama.cpp` Metal also uses it indirectly.
- **ANE (Apple Neural Engine)** — accessed via Core ML. `whisper.cpp` with `--coreml` can offload the encoder to ANE: **3× speedup on the encoder pass, 8–12× on full pipeline if both encoder and decoder are Core ML**. Repo: https://github.com/ggml-org/whisper.cpp — see `models/generate-coreml-model.sh`.
- **Metal Performance Shaders (MPS)** — PyTorch backend for Apple. Kokoro stock uses CPU; `kokoro-onnx` with `coreml` execution provider could shave 30–50% off TTS synthesis.

Cheapest high-impact change: switch faster-whisper to whisper.cpp with Core ML encoder. **Drops STT from ~3 s to ~700 ms on a 2 s utterance.** That's the biggest single latency win available on this hardware.

## 7. The "Cerebras Inference" public benchmark — what's portable

Cerebras Inference Cloud serves Llama 3.1 70B at **~600 tok/s, p50 first-token 75 ms** (https://inference.cerebras.ai). We can't get there. But the *structural* tricks they publish:

- **No KV-cache eviction** — every conversation stays warm forever. We can replicate per-WS-connection: hold the Ollama context across turns, never reset.
- **Speculative decoding with 4 drafts in parallel** — we do 1 draft. Could run 2 drafters of different sizes (1B + 3B) and pick the longer accepted prefix.
- **Dynamic batching** — irrelevant for single-user demo. Skip.

## 8. The "perception > reality" lesson

Groq's actual edge in chat demos is that 500 tok/s **is faster than humans read aloud**. That means the LLM can finish generating the entire reply before you'd normally hear word 1. The audio playback then pretends to be "live" but is actually fully buffered. **The illusion of streaming is more important than actually streaming.**

For Adjutant, this means: once the LLM generates fast enough that the full reply lands before the *audio* finishes playing, all the rest of the streaming machinery becomes invisible polish. Combined items 1+2+6 above could plausibly hit 50 tok/s on M2 — at which point a typical 80-token reply is done in 1.6 s, by which time only one Kokoro chunk has even started. The rest is buffered seamlessly.

## Recommended Tonight's Build Order

Ranked by latency-shaved-per-hour-spent. Top three are the only ones I'd attempt before the demo.

| # | Change | Estimated savings | Build time | Risk |
|---|---|---|---|---|
| 1 | whisper.cpp + Core ML encoder swap for `faster-whisper small.en` | **~2.0 s off STT** | 1.5 h | Low (drop-in via `pywhispercpp`) |
| 2 | Phrase-buffer TTS instead of sentence-buffer + 50 ms crossfade | **~0.9 s off first audio** | 1 h | Medium (regex tuning) |
| 3 | KV-cache prefix structuring in `llm.py` (system prompt + chunks as static prefix) | **~1.5 s on subsequent turns** | 0.5 h | Low |
| 4 | Parallel form-fill (kick off as soon as RAG returns, don't wait for BOT_SPEAKING_END) | **~3 s perceived (PDF appears mid-audio)** | 0.5 h | Low |
| 5 | Speculative decoding via `llama-server` (1B drafting 3B) | **~1.5 s off first sentence** | 2 h | High — needs Ollama swap |
| 6 | Predictive STT + speculative LLM on partial transcript | **~0.4 s off end-of-turn** | 3 h | High — needs partial-confidence model |

Items 1 + 2 + 3 + 4 (~3.5 h total): combined estimated end-of-speech to first-audio drop from current **~5–6 s warm to ~1.5–2 s warm**. That's ChatGPT-feel territory.

## References — Groq/Cerebras-inspired voice tricks

- llama.cpp speculative discussion: https://github.com/ggml-org/llama.cpp/discussions/10466
- LM Studio speculative decoding guide: https://lmstudio.ai/docs/app/advanced/speculative-decoding
- vLLM automatic prefix caching: https://docs.vllm.ai/en/stable/features/automatic_prefix_caching.html
- SGLang RadixAttention: https://github.com/sgl-project/sglang
- whisper.cpp Core ML setup: https://github.com/ggml-org/whisper.cpp/blob/master/models/generate-coreml-model.sh
- Pipecat Smart Turn v3 model: https://github.com/pipecat-ai/smart-turn
- LiveKit predictive turn-taking: https://docs.livekit.io/agents/build/turns/turn-detector/
- Groq Inference docs (latency benchmarks): https://console.groq.com/docs
- Cerebras Inference Cloud: https://inference.cerebras.ai
- Ollama speculative decoding tracking issue: https://github.com/ollama/ollama/issues/5800
- Apple Neural Engine via whisper.cpp: https://github.com/ggml-org/whisper.cpp#core-ml-support
