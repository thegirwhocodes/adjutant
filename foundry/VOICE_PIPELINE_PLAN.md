# Adjutant — Seamless Voice Pipeline Plan

**Goal:** Upgrade Adjutant from clunky push-to-talk + sequential STT→LLM→TTS (~6 s end-to-end) to ChatGPT Advanced Voice Mode-feel: continuous listening, ~1 s turn-end-to-first-audio, streaming sentence-by-sentence playback, interruptible, fully offline on M2 16 GB.

**Companion docs:** [BUILD_PLAN.md](BUILD_PLAN.md) (founder schedule), [CODE_PLAN.md](CODE_PLAN.md) (existing engineering plan). This plan slots in as a Phase 4 alternate / post-hackathon upgrade — it is NOT the demo-floor path. The demo floor stays push-to-talk + working PDF.

---

## What "seamless" actually means (anchor before we build)

Three properties separate ChatGPT AVM from Siri / Alexa / generic chatbot voice:

1. **Streaming both directions.** User speaks → bot starts replying before user is fully done parsing. Bot's reply audio starts within ~1 second of user finishing. ChatGPT measures 232–800 ms in published numbers.
2. **Interruptible.** User can speak over the bot; bot stops mid-sentence and listens. Acoustic echo cancellation in browser + server-side cancel-in-flight signal.
3. **Continuous listening.** No "hold to talk" button. VAD gates turns. Bot detects when user stops talking semantically, not just acoustically.

Not what we're building (deferred — too risky for hackathon):
- True end-to-end speech-to-speech models (Moshi, GLM-4-Voice, Step-Audio 2). Architecturally elegant but fragile on M2 in 30 hours.
- WebRTC / SFU stack (LiveKit). Adds infra; loopback latency on `ws://localhost` is fine.

---

## Reality check from the research

| Component | What ChatGPT does | What Adjutant can do offline on M2 |
|---|---|---|
| Audio architecture | Native multimodal (audio tokens in/out, no text intermediate) | Cascaded streaming pipeline. ~95% of the feel; ~5% of the integration risk. |
| Transport | WebSocket / WebRTC, PCM16 24 kHz | FastAPI WebSocket, PCM16 16 kHz |
| VAD | Server-VAD with `silence_duration_ms` tunable; semantic-VAD optional | Silero VAD (ONNX, MIT, <1 ms/chunk on CPU) |
| STT | Native audio encoder | faster-whisper `small.en` int8 (already in repo) — keep |
| LLM | gpt-realtime | Llama 3.2 3B via Ollama, streaming |
| TTS | Native audio decoder | Kokoro-82M ONNX (Apache 2.0, <300 ms warm on M2) |
| Interruption | `response.cancel` server event | Custom asyncio cancel + browser audio stop |
| Latency target | 232–800 ms ESV→FAO | **~1.0–1.4 s ESV→FAO** target on M2 |

ESV = end-of-speech-by-VAD. FAO = first-audio-out at user's ear.

The dominant non-compressible chunk in our budget is the 500–600 ms VAD silence threshold. To get below it we'd need a turn-detection model (Pipecat's smart-turn-v3) — listed as a stretch goal, not the floor.

---

## The architecture (Architecture A — recommended for hackathon)

```
  Browser                                  Server (FastAPI)
  ┌─────────────────────┐                  ┌──────────────────────────────┐
  │ getUserMedia        │                  │  /ws WebSocket               │
  │   echoCancellation  │                  │   ↓ binary frames            │
  │   noiseSuppression  │  PCM16 16 kHz    │  ring buffer (5 s)           │
  │ AudioWorklet        │ ───────────────→ │   ↓                          │
  │   downsample → 16k  │                  │  SileroVAD (ONNX, 32 ms hop) │
  │   pack int16        │                  │   ├─ on speech_start →       │
  └─────────────────────┘                  │   │    if bot_is_speaking:   │
                                           │   │      send INTERRUPT      │
                                           │   │      cancel LLM stream   │
                                           │   │      drain TTS queue     │
                                           │   └─ on speech_end →         │
                                           │        flush utterance to STT│
                                           │   ↓                          │
                                           │  faster-whisper small.en     │
                                           │   ↓ transcript               │
                                           │  RAG retrieve + form-infer   │
                                           │   ↓                          │
  ┌─────────────────────┐                  │  Ollama llama3.2:3b stream   │
  │ AudioBufferQueue    │                  │   ↓ token deltas             │
  │  - decodeAudioData  │  WAV chunks      │  Sentence buffer             │
  │  - schedule(start)  │ ←─────────────── │   ↓ flush on [.!?]           │
  │ State indicator     │  + JSON events   │  Kokoro-onnx (af_heart)      │
  │  idle/listen/think/ │                  │   ↓ chunked WAV bytes        │
  │  speak              │                  │  WebSocket send              │
  └─────────────────────┘                  │                              │
                                           │  After RESPONSE_DONE:        │
                                           │  → form_data extraction      │
                                           │  → fill_pdf                  │
                                           │  → send PDF_READY + url      │
                                           └──────────────────────────────┘
```

The form-fill + RAG citation flow runs **after** the audio reply finishes streaming. Voice answer first; PDF appears second. This preserves the refusal contract (cite-or-refuse) and the architectural-incapable-of-hallucinating demo claim — RAG threshold check happens before any tokens generate.

---

## Latency budget on M2 (target end-of-speech → first audio: ≤1.4 s)

| Stage | Time | Note |
|---|---|---|
| Audio capture + WS to server | 30–60 ms | localhost loopback |
| Silero VAD silence detection | 600 ms | tune-down to 400 ms post-demo |
| faster-whisper `small.en` int8 (3 s utterance) | 200–400 ms | RTF ~0.1 on M2 |
| Ollama TTFT for llama3.2:3b (cached system prompt) | 150–300 ms | KV cache stays warm between turns |
| First sentence assembly (5–10 tokens) | overlaps with TTFT | sentence buffer flushes on `[.!?]` |
| Kokoro first chunk (warm, resident in RAM) | 200–400 ms | model loaded at startup, not first request |
| WS transport + decode + schedule | 30 ms | |
| **Total ESV→FAO** | **~1.0–1.4 s** | |

Compressible chunks if we have time: VAD threshold (600→400 ms = -200 ms), STT model swap to Distil-Whisper (-100 ms), smart-turn-v3 (-200 ms VAD). Aspirational floor: ~700 ms.

---

## Implementation phases

Each phase has an **acceptance gate**. Don't advance until green. If a phase blows its time box, fall back to Architecture C (see Cut-list).

### Phase V0 — Pre-flight (15 min)

```bash
cd /Users/naomiivie/adjutant
source .venv/bin/activate

pip install silero-vad onnxruntime kokoro-onnx soundfile numpy
brew install espeak-ng  # Kokoro G2P fallback

# Pre-download Kokoro model + voices (~80 MB total) so we work offline
mkdir -p models/kokoro
curl -L -o models/kokoro/kokoro-v1.0.onnx \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -L -o models/kokoro/voices-v1.0.bin \
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin

# Verify Silero VAD ONNX caches
python -c "from silero_vad import load_silero_vad; m = load_silero_vad(onnx=True); print('OK')"

# Verify Kokoro
python -c "
from kokoro_onnx import Kokoro
k = Kokoro('models/kokoro/kokoro-v1.0.onnx', 'models/kokoro/voices-v1.0.bin')
samples, sr = k.create('Adjutant ready.', voice='af_heart', speed=1.0, lang='en-us')
print(f'samples={len(samples)} sr={sr}')
"
```

**Acceptance gate:** Kokoro generates audio offline (run with wifi off). Silero VAD loads.

---

### Phase V1 — Streaming LLM + sentence buffer (1 h)

Modify [adjutant/llm.py](../adjutant/llm.py):

```python
async def answer_query_stream(query: str, chunks: list[dict]):
    """Async generator yielding (text_delta, sentence_complete) tuples.
    Pure streaming version of answer_query.
    """
    if not chunks:
        # Refusal contract: stream the whole refusal in one chunk.
        yield (REFUSAL_OUT_OF_CORPUS, True)
        return

    prompt = SYSTEM_PROMPT.format(
        context=_format_context(chunks),
        query=query,
    )

    buf = ""
    stream = _client.chat(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.2, "num_predict": 400},
        stream=True,
    )
    sentence_re = re.compile(r"(.+?[.!?])(\s|$)")

    for chunk in stream:
        delta = chunk["message"]["content"]
        buf += delta
        # Flush every complete sentence as a separate yield.
        while True:
            m = sentence_re.search(buf)
            if not m:
                break
            yield (m.group(1).strip(), True)
            buf = buf[m.end():]
    # Final partial (no terminal punctuation)
    if buf.strip():
        yield (buf.strip(), True)
```

Add an async-friendly wrapper if needed. Note: ollama-python's `stream=True` returns a synchronous iterator; wrap with `asyncio.to_thread` or use an `asyncio.Queue` + thread pool.

**Acceptance gate:** Python REPL test:
```python
import asyncio
from adjutant.rag import retrieve
from adjutant.llm import answer_query_stream
async def go():
    chunks = retrieve("How does ordinary leave accrue?")
    async for s, done in answer_query_stream("...", chunks):
        print(f"[{'final' if done else 'partial'}] {s!r}")
asyncio.run(go())
```
Should print one sentence per line, in real time, not buffered to end.

---

### Phase V2 — Kokoro TTS service (45 min)

Replace [adjutant/tts.py](../adjutant/tts.py) primary path:

```python
import threading
from kokoro_onnx import Kokoro

_kokoro_lock = threading.Lock()
_kokoro: Kokoro | None = None

def _get_kokoro() -> Kokoro:
    global _kokoro
    with _kokoro_lock:
        if _kokoro is None:
            log.info("Loading Kokoro ONNX (one-time)…")
            _kokoro = Kokoro(
                os.getenv("KOKORO_MODEL", "models/kokoro/kokoro-v1.0.onnx"),
                os.getenv("KOKORO_VOICES", "models/kokoro/voices-v1.0.bin"),
            )
        return _kokoro

def synthesize_chunk(text: str, voice: str = "af_heart") -> tuple[bytes, int]:
    """Synthesize one sentence to PCM16 mono WAV bytes. Returns (wav_bytes, sample_rate).
    
    Kokoro returns float32 samples at 24 kHz. We wrap in a WAV header so the browser
    can decode each chunk independently with audioContext.decodeAudioData().
    """
    k = _get_kokoro()
    samples, sr = k.create(text, voice=voice, speed=1.0, lang="en-us")
    return _wav_bytes(samples, sr), sr
```

`_wav_bytes` writes a 44-byte WAV header onto the float32→int16 samples. Reuse `soundfile.write(io.BytesIO(), samples, sr, format='WAV', subtype='PCM_16')`.

Add startup warmup in [server.py](../adjutant/server.py):

```python
@app.on_event("startup")
async def warmup():
    from adjutant.tts import _get_kokoro
    from adjutant.stt import _get_model
    _get_kokoro()                     # ~3 s one-time
    _get_model()                      # ~2 s one-time
    # Ollama warmup ping
    try:
        from adjutant.llm import _client, MODEL
        _client.chat(model=MODEL, messages=[{"role": "user", "content": "ready?"}],
                     options={"num_predict": 5})
    except Exception as e:
        log.warning(f"Ollama warmup failed (non-fatal): {e}")
    log.info("Warmup done.")
```

Keep pyttsx3 + `say -v Samantha` as fallbacks if Kokoro raises.

**Acceptance gate:** server starts in ~10 s; first POST `/query` returns audio in <2 s (vs ~6 s before).

---

### Phase V3 — Browser AudioWorklet + WebSocket (2 h)

New file [web/audio_worklet.js](../web/audio_worklet.js):

```javascript
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.frame = new Int16Array(512); // 32ms @ 16kHz
    this.idx = 0;
  }
  process(inputs) {
    const ch = inputs[0]?.[0];
    if (!ch) return true;
    // AudioWorklet runs at sample rate of the AudioContext (usually 48000).
    // Downsample 48k → 16k by taking every 3rd sample.
    for (let i = 0; i < ch.length; i += 3) {
      const s = Math.max(-1, Math.min(1, ch[i]));
      this.frame[this.idx++] = s * 0x7fff;
      if (this.idx === 512) {
        this.port.postMessage(this.frame.slice().buffer, [this.frame.slice().buffer]);
        this.idx = 0;
      }
    }
    return true;
  }
}
registerProcessor('capture', CaptureProcessor);
```

Add to [web/app.js](../web/app.js): `startVoiceLoop()` that:
1. Opens `getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, sampleRate: 16000 }})`
2. Creates `AudioContext({ sampleRate: 16000 })` (Chrome may upmix to 48k regardless; the worklet handles the resample)
3. Loads `audio_worklet.js`, pipes mic → worklet → `ws.send(arrayBuffer)`
4. On `ws.onmessage`: branch by frame type (binary = audio, text = JSON event).
5. Schedules audio chunks:
```javascript
let nextStartTime = 0;
let activeSources = new Set();
async function playAudioChunk(arrayBuffer) {
  const buf = await audioCtx.decodeAudioData(arrayBuffer);
  const src = audioCtx.createBufferSource();
  src.buffer = buf;
  src.connect(audioCtx.destination);
  const startAt = Math.max(audioCtx.currentTime, nextStartTime);
  src.start(startAt);
  src.onended = () => activeSources.delete(src);
  activeSources.add(src);
  nextStartTime = startAt + buf.duration;
}
function stopAllAudio() {
  for (const s of activeSources) try { s.stop(); } catch(e) {}
  activeSources.clear();
  nextStartTime = audioCtx.currentTime;
}
```
6. State indicator: `idle` → green dot. `listening` → animated waveform. `thinking` → pulsing. `speaking` → a different pulse.

**Acceptance gate:** Open `http://localhost:8000/web/`, speak, see waveform animate; bot's TTS plays back through speakers as chunks arrive (don't wait for full response).

---

### Phase V4 — Server VAD loop + interruption (2 h)

New file [adjutant/voice_loop.py](../adjutant/voice_loop.py):

```python
"""Continuous voice loop. One coroutine per WebSocket connection.

Owns the per-connection state machine:
  IDLE → LISTENING → THINKING → SPEAKING → (LISTENING again, or interrupt)
"""
import asyncio
import io
import json
import logging
import numpy as np
from silero_vad import load_silero_vad
import torch

from adjutant.llm import answer_query_stream, extract_form_data
from adjutant.rag import retrieve
from adjutant.stt import transcribe
from adjutant.tts import synthesize_chunk

log = logging.getLogger("adjutant.voice")

VAD_SAMPLE_RATE = 16000
VAD_FRAME_SAMPLES = 512  # 32 ms at 16k
VAD_ACTIVATION = 0.5
SILENCE_MS_TO_END_TURN = 600

class VoiceLoop:
    def __init__(self, ws):
        self.ws = ws
        self.vad = load_silero_vad(onnx=True)
        self.audio_buffer: list[int] = []  # accumulating speech samples
        self.is_speaking = False           # bot is speaking?
        self.user_speaking = False
        self.silence_frames = 0
        self.silence_threshold_frames = SILENCE_MS_TO_END_TURN // 32
        self.in_flight_task: asyncio.Task | None = None

    async def feed_audio(self, pcm16_bytes: bytes):
        """Called from WebSocket receive loop with each 32 ms frame."""
        frame = np.frombuffer(pcm16_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        if len(frame) != VAD_FRAME_SAMPLES:
            return
        score = self.vad(torch.from_numpy(frame), VAD_SAMPLE_RATE).item()

        if score > VAD_ACTIVATION:
            if not self.user_speaking:
                self.user_speaking = True
                if self.is_speaking:
                    await self._interrupt()
                await self._send_event({"type": "USER_SPEAKING_START"})
            self.audio_buffer.extend(frame.tolist())
            self.silence_frames = 0
        else:
            if self.user_speaking:
                self.audio_buffer.extend(frame.tolist())
                self.silence_frames += 1
                if self.silence_frames >= self.silence_threshold_frames:
                    await self._end_turn()

    async def _end_turn(self):
        self.user_speaking = False
        utterance = np.array(self.audio_buffer, dtype=np.float32)
        self.audio_buffer.clear()
        self.silence_frames = 0
        await self._send_event({"type": "USER_DONE"})
        # Hand off to STT→LLM→TTS pipeline (cancellable)
        self.in_flight_task = asyncio.create_task(self._respond(utterance))

    async def _respond(self, utterance: np.ndarray):
        try:
            # Convert float32 → int16 WAV bytes for transcribe()
            int16 = (utterance * 32767).astype(np.int16).tobytes()
            wav = _wrap_wav(int16, VAD_SAMPLE_RATE)
            text = await asyncio.to_thread(transcribe, wav)
            if not text.strip():
                await self._send_event({"type": "USER_SILENT"})
                return
            await self._send_event({"type": "TRANSCRIPT", "text": text})

            chunks = await asyncio.to_thread(retrieve, text)

            self.is_speaking = True
            await self._send_event({"type": "BOT_SPEAKING_START", "citations": [
                {"source": c["source"], "section": c.get("section", "")} for c in chunks
            ]})

            full_summary = []
            async for sentence, _ in answer_query_stream(text, chunks):
                full_summary.append(sentence)
                wav_bytes, _sr = await asyncio.to_thread(synthesize_chunk, sentence)
                await self.ws.send_bytes(wav_bytes)
                await asyncio.sleep(0)  # cooperative yield for cancel

            await self._send_event({"type": "BOT_SPEAKING_END",
                                     "spoken_summary": " ".join(full_summary)})
            # Form-fill runs in parallel after audio finishes
            asyncio.create_task(self._maybe_form_fill(text, chunks))
        except asyncio.CancelledError:
            log.info("response cancelled mid-stream (user interrupted)")
        finally:
            self.is_speaking = False

    async def _interrupt(self):
        await self._send_event({"type": "INTERRUPT"})
        if self.in_flight_task:
            self.in_flight_task.cancel()
            try:
                await self.in_flight_task
            except asyncio.CancelledError:
                pass
        self.is_speaking = False

    async def _maybe_form_fill(self, query, chunks):
        # Existing form-fill flow from server.py /query route.
        # Returns PDF_READY event with url.
        ...

    async def _send_event(self, ev: dict):
        await self.ws.send_text(json.dumps(ev))


def _wrap_wav(pcm16_bytes: bytes, sr: int) -> bytes:
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm16_bytes)
    return buf.getvalue()
```

Add WebSocket route to [server.py](../adjutant/server.py):

```python
from fastapi import WebSocket, WebSocketDisconnect
from adjutant.voice_loop import VoiceLoop

@app.websocket("/ws/voice")
async def ws_voice(ws: WebSocket):
    await ws.accept()
    loop = VoiceLoop(ws)
    try:
        while True:
            msg = await ws.receive()
            if "bytes" in msg:
                await loop.feed_audio(msg["bytes"])
            elif "text" in msg:
                ev = json.loads(msg["text"])
                if ev.get("type") == "MUTE":
                    await loop._interrupt()
    except WebSocketDisconnect:
        pass
```

**Acceptance gate:** Hold mic, say *"How does ordinary leave accrue?"*, the bot's first audio plays within ~1.4 s of you stopping. Speak again while bot is talking; bot stops mid-word.

---

### Phase V5 — Form-fill side channel + UI polish (1 h)

In `_maybe_form_fill`:
- Run `_infer_forms` on the transcript (existing helper)
- For each form, call `extract_form_data` + `fill_pdf` (existing helpers, already in [server.py](../adjutant/server.py:127-162))
- Send `{"type": "PDF_READY", "form_id": ..., "pdf_url": ...}`

Browser handler appends each PDF iframe under the citation panel as it arrives. Same UX as today, just driven by WS events instead of HTTP response.

UI polish:
- Three-dot state indicator: green idle, yellow listening, blue thinking, magenta speaking
- Live transcript fading in word-by-word (subscribe to `TRANSCRIPT` event; for the demo, just render whole utterance)
- Citation panel populates on `BOT_SPEAKING_START` (don't wait for response done)

**Acceptance gate:** Full demo flow works end-to-end. SGT Chen voice query → audio reply within 1.4 s → PDF appears within 1 s of audio finishing.

---

### Phase V6 — Wifi-pulled offline test (30 min)

```bash
# Set offline mode env vars in the shell that runs uvicorn
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export NO_NETWORK_AT_INFERENCE=1   # internal flag, log if anything triggers a request

# Verify model files all live on disk
ls -lh models/kokoro/         # ~80 MB
ls -lh ~/.cache/huggingface/  # whisper, sentence-transformers
ls -lh ~/.ollama/models/      # llama3.2:3b
```

Pull wifi cable. Reload browser. Run the SGT Chen demo flow three times. Anything that fails offline must be either bundled or removed.

**Acceptance gate:** `tests/test_offline.py` still passes. Three consecutive demo runs work with airplane mode on.

---

## Cut-list — fall back to a simpler architecture if behind schedule

| Behind by | Cut |
|---|---|
| 2 h after Phase V0 | Drop Phase V3 (browser AudioWorklet). Use simple MediaRecorder + chunk POSTs every 1 s. Lose true streaming but keep VAD turn-end. |
| 4 h after Phase V0 | Drop Phase V4 (server VAD). Keep push-to-talk button. Add streaming TTS only — mic released → server streams sentence-by-sentence audio back. **This is Architecture C: 60% of the perceived improvement for 30% of the work.** |
| 6 h after Phase V0 | **Skip the entire upgrade.** Add only the streaming LLM + Kokoro TTS swap (Phases V1+V2). Keep current push-to-talk single POST. Replies feel ~3× faster than today; UI is unchanged. |
| Catastrophic | Revert to current main. The hackathon-floor demo from BUILD_PLAN.md still passes. |

---

## What we deliberately defer (post-hackathon)

| Feature | Why defer | When to revisit |
|---|---|---|
| smart-turn-v3 (Pipecat) | -200 ms VAD latency, but adds an inference dependency | Post-demo polish, week of April 27 |
| Distil-Whisper distil-large-v3 | -100 ms STT, marginally better quality | Same |
| Speculative decoding (Llama 3.2 1B drafting 8B) | -40% LLM latency | Post-demo, requires switching from Ollama to llama-server |
| Pipecat full migration | More mature framework, would let us add tool-call streaming, function-calling | Post-SCSP if we get follow-on funding |
| Moshi MLX 4-bit | True S2S, 200 ms theoretical | Research project; not production-grade for Adjutant's refusal contract |
| Voice cloning (Naomi's voice via Chatterbox) | Brand consistency w/ E4E Sabi | Post-demo; requires GPU for inference quality, doesn't help judging |

---

## Risks + mitigations

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| Kokoro ONNX fails to load on M2 | Low | High | Fallback: Piper (`pip install piper-tts`, runs on Pi 4 fine). Drop in 5 min. |
| Echo cancellation fails in browser; bot interrupts itself | Medium | Medium | Headphones for demo. Add `interrupt_min_words=2` gating on barge-in. |
| Silero VAD misses end-of-turn (long thoughtful pauses) | Medium | Medium | Add visible "Listening… (release Esc to stop)" hint. ESC key force-ends turn. |
| Ollama streaming hangs on llama3.2:3b under load | Low | High | Switch to `llama-server` direct via OpenAI-compat. ~30 min swap. |
| AudioWorklet downsample math is wrong, transcripts garbled | Medium | High | Pre-test with a known utterance through the pipe before demo rehearsal. Have ffmpeg-transcode fallback ready (current `stt.py` already does this for HTTP path). |
| First sentence audio plays before transcript renders → ugly UX | Low | Low | Send TRANSCRIPT event before BOT_SPEAKING_START; client renders transcript first. |
| Demo room has poor mic input → false speech triggers | Medium | Medium | VAD threshold up to 0.6 in noisy rooms. ESC kill-switch. |
| Ollama TTFT spikes after wifi-pull (DNS lookup retries somewhere) | Low | Medium | Set all env vars to offline before launch. `lsof -i` audit during dev. |

---

## File-by-file change list

| File | Change | Phase |
|---|---|---|
| [adjutant/llm.py](../adjutant/llm.py) | Add `answer_query_stream()` async generator | V1 |
| [adjutant/tts.py](../adjutant/tts.py) | Replace Chatterbox primary with Kokoro-onnx; add `synthesize_chunk()` | V2 |
| [adjutant/server.py](../adjutant/server.py) | Add `@app.on_event("startup")` warmup; add `/ws/voice` route | V2, V4 |
| [adjutant/voice_loop.py](../adjutant/voice_loop.py) | NEW. VoiceLoop class with VAD + cancellation | V4 |
| [web/audio_worklet.js](../web/audio_worklet.js) | NEW. Capture processor | V3 |
| [web/app.js](../web/app.js) | Replace push-to-talk with continuous loop; WS event handlers | V3, V5 |
| [web/index.html](../web/index.html) | Add state indicator dots | V5 |
| [web/styles.css](../web/styles.css) | State indicator animations | V5 |
| [requirements.txt](../requirements.txt) | + silero-vad, kokoro-onnx, soundfile (numpy already there) | V0 |
| [models/kokoro/](../models/kokoro/) | NEW. Kokoro ONNX model + voices file | V0 |
| [.env.example](../.env.example) | + `KOKORO_MODEL`, `KOKORO_VOICES`, `VAD_SILENCE_MS` | V0 |

Total new code: ~400 LOC. Modified: ~150 LOC. The existing form-fill, RAG, refusal-on-empty-chunks contract is untouched — reuses every helper from [server.py](../adjutant/server.py) and [llm.py](../adjutant/llm.py).

---

## TL;DR

| Phase | Hours | Deliverable |
|---|---|---|
| V0 | 0.25 | Kokoro + Silero installed, models cached for offline |
| V1 | 1.0 | Streaming LLM with sentence buffer |
| V2 | 0.75 | Kokoro TTS service with warmup |
| V3 | 2.0 | Browser AudioWorklet capturing 16 kHz PCM16 over WS |
| V4 | 2.0 | Server VAD loop with interruption handling |
| V5 | 1.0 | Form-fill + UI polish |
| V6 | 0.5 | Offline verification |
| **Total** | **~7.5** | **ChatGPT-AVM-feel voice flow, all offline** |

Slot into [BUILD_PLAN.md](BUILD_PLAN.md) as Phase 4 alternate, **only after Phase 3 (DD-1351-2 + DA-4856) is shipping working**. The hackathon floor (push-to-talk + filled PDF + wifi-pulled refusal demo) must be green before any of this starts. If voice upgrade breaks at hour 22, revert and demo the current flow — it already wins on the architecture-incapable-of-hallucinating beat.