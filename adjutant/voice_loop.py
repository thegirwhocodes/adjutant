"""Continuous voice loop. One coroutine per WebSocket connection.

Owns the per-connection state machine:
  IDLE → LISTENING → THINKING → SPEAKING → (LISTENING again, or interrupt)

Latency tricks (Sabi-adapted):
  - Pre-rendered "thinking cue" plays the moment Silero VAD reports end-of-speech,
    bridging the LLM-prefill gap (~2-6s on M2) with audible "Checking the regs..."
    instead of dead silence.
  - Streaming Ollama -> sentence-buffer -> async Kokoro. First real sentence
    audio queues behind the cue; subsequent sentences chain seamlessly.
  - User starts speaking while bot is speaking → cancel the in-flight LLM
    coroutine, drain TTS queue, send STOP_AUDIO to client.
"""

import asyncio
import io
import json
import logging
import random
import re
import time
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from silero_vad import load_silero_vad

from adjutant.forms import get_schema
from adjutant.llm import answer_query_stream, extract_form_data
from adjutant.pdf_fill import fill_pdf
from adjutant.rag import retrieve
from adjutant.stt import transcribe
from adjutant.tts import get_cue, synthesize_wav_bytes_async

log = logging.getLogger("adjutant.voice")

VAD_SR = 16000
VAD_FRAME_SAMPLES = 512        # 32 ms at 16 kHz — Silero ONNX expects this exactly
# Activation 0.7 (strong voice required). 0.5 default was triggering on
# ambient noise + bot's own speaker bleeding into the mic, causing the
# pipeline to fire on phantom turns.
VAD_ACTIVATION = 0.7
# When the bot is currently speaking, require an even higher VAD score
# before treating it as user barge-in. Echo cancellation isn't perfect on
# laptop speakers — without this gate, the bot interrupts itself.
VAD_BARGEIN_ACTIVATION = 0.85
SILENCE_FRAMES_TO_END = 18     # ~600 ms; tune down to 12 for snappier
# Require 400ms of sustained speech (was 160ms). Filters out coughs,
# door bangs, room AC fluctuations, and short echo-bleed bursts.
MIN_SPEECH_FRAMES = 12
# Below this confidence, treat the transcript as "didn't catch that"
# instead of feeding garbage into RAG → LLM. Silero already pre-gates,
# but Whisper still emits low-confidence guesses on near-silence.
STT_CONFIDENCE_FLOOR = 0.5
# Skip transcripts that are too short to be a real query — single-word
# noise mistakes ("the", "uh", "okay") shouldn't fire the LLM.
MIN_TRANSCRIPT_WORDS = 3

THINKING_CUES = ["thinking_0", "thinking_1", "thinking_2", "thinking_3", "thinking_4"]
FORM_ACK_CUES = {
    "DA-31":     "ack_da31",
    "DD-1351-2": "ack_dd13512",
    "DA-4856":   "ack_da4856",
}

FILLED_DIR = Path("filled_forms")

# Sabi-pattern dialog persona. Like sabi-server's SABI_CORE_PROMPT, this
# defines WHO Adjutant is and HOW it speaks — not WHAT it knows. The form
# schema, retrieved regulation chunks, currently-filled data, and the
# rolling user/assistant chat history are injected per-call.
#
# Mirrors structure of /Users/naomiivie/Education for Equality/sabi-server/llm.py
# (Sabi: Nigerian Pidgin tutor for kids; Adjutant: buddy-NCO for soldiers).
ADJUTANT_DIALOG_PROMPT = """You are Adjutant, an AI personal assistant for U.S. Army soldiers filling out their paperwork. You read the soldier's voice over a phone line and reply with voice. You have the Army's regulations on disk and you know them cold.

## YOUR PERSONALITY
- A trusted buddy NCO who's filled this exact form a hundred times.
- Calm, direct, regulation-literate. Never bureaucratic. Never robotic.
- Patient when soldiers get confused, change their minds, or ask side-questions.
- Educational only when asked — explain the WHY behind a field if the soldier wonders, then move on.
- You ARE the regulation. You don't say "the regulation requires..." — you cite the paragraph.

## CRITICAL: VOICE-FIRST, NOT CHAT
Every reply you write WILL BE READ ALOUD by a text-to-speech engine to a soldier holding a phone. This means:
- 1 to 2 sentences. No more. Brevity is professionalism.
- NEVER write markdown. No asterisks, no backticks, no code fences, no JSON in your `spoken_reply`.
- NEVER write structural labels like "Form Data:", "Regulation:", "Approval Note:", "Output:".
- Spell numbers naturally: "ten days," "two thousand twenty six," "nineteen nineteen five five five oh one four four."
- Dates as a person would say them: "June third," "July fourteenth," not "06/03" or "06-03-26."
- DO NOT START YOUR REPLY WITH FILLER. Banned openings: "Yes, Sergeant," "Roger that," "Got it," "Alright," "Okay so," "Sure thing." Just begin with the substance.

## ADAPTIVE BEHAVIOR (decide every turn)
- They named a value for a field → acknowledge in half a sentence, then ask the next missing required field.
- They named the WRONG field (one we didn't ask) → take it anyway, ask another required one.
- They corrected an earlier value ("actually 5678 not 1234") → update silently, confirm in three words, continue.
- They asked a regulation question → answer in one sentence with a paragraph citation, then nudge to the next field.
- They're confused or asked you to repeat → restate the current question DIFFERENTLY than last time.
- They say "ship it" / "good enough" / "skip" / "send it" / "I'm done" → set done=true and stop asking.
- All required fields are now filled → set done=true; acknowledge the form is ready for signature and routing.

## CITING REGULATIONS
- Format: "Per AR 600-8-10 paragraph 4-3, …" or "Per the JTR chapter two, …"
- Only cite when the soldier asks a question OR when the answer hinges on a specific paragraph.
- Never cite without a real paragraph number from the retrieved chunks below.
- Never cite a paragraph you can't find in the chunks. If unsure, paraphrase without citation.

## NEVER REPEAT
- Look at the conversation history. If you already asked a question, ask the NEXT missing field, not the same one again.
- If the soldier didn't answer last turn, REPHRASE. Use synonyms: "duty station" / "home base" / "where you're stationed."

## OUTPUT FORMAT (NON-NEGOTIABLE)
Respond with EXACTLY ONE JSON object, nothing before or after:

{
  "spoken_reply":  "<one or two sentences, plain prose, no markdown>",
  "form_updates":  {"<field_name>": "<value>", ...},
  "done":          <true or false>
}

Rules for `form_updates`:
- Keys MUST match the schema field names below exactly.
- Only include fields you EXTRACTED from this turn. Empty `{}` is valid.
- Never invent SSNs, phone numbers, addresses, dates, or names the soldier didn't say.
- For dates the soldier said in spoken form ("June third"), output ISO format ("2026-06-03").

Rules for `done`:
- true ONLY if (a) soldier explicitly said they're done, OR (b) all REQUIRED fields are now filled.
- false otherwise.
"""

# Per-form RAG context that gets appended to the dialog system prompt. We
# inject the schema, currently-filled state, and missing-required list so
# the model knows the playing field for THIS form on THIS turn.
def _build_dialog_rag_context(pf: dict) -> str:
    schema = pf.get("schema") or {}
    form_id = pf.get("form_id", "")
    form_data = pf.get("form_data") or {}
    chunks = pf.get("chunks") or []

    # Schema description
    schema_lines = []
    for fname, spec in schema.get("fields", {}).items():
        req = "REQUIRED" if spec.get("required") else "optional"
        schema_lines.append(f"  {fname}: {spec.get('desc','')} [{req}]")
    schema_desc = "\n".join(schema_lines)

    filled = "\n".join(f"  {k}: {v}" for k, v in form_data.items() if v) or "  (nothing yet)"
    missing = [
        fname for fname, spec in schema.get("fields", {}).items()
        if spec.get("required") and not form_data.get(fname)
    ]

    ctx_lines = []
    for c in chunks[:3]:
        src = c.get("source", "")
        sec = c.get("section", "")
        quote = c.get("text", "")[:300]
        ctx_lines.append(f"[{src} {sec}] {quote}")
    rag = "\n".join(ctx_lines) if ctx_lines else "(no specific paragraph retrieved)"

    return f"""

## CURRENT FORM: {form_id}

### SCHEMA (fields you may set in form_updates):
{schema_desc}

### ALREADY FILLED (don't ask for these unless soldier corrects them):
{filled}

### STILL MISSING — REQUIRED:
{', '.join(missing) if missing else '(none — all required fields are filled, set done=true on next turn)'}

### REGULATION CONTEXT (from RAG retrieval — use to ground citations):
{rag}
"""


# Conversational form-fill: when the LLM extracts a partial form, the bot
# asks the soldier vocally for the missing required fields one at a time.
# Each entry below maps a semantic field name (the key Adjutant's LLM and
# pdf_fill use) → the natural-language question the bot speaks. Wording
# matches the "buddy NCO who's done this 100 times" tone in DEMO_SCRIPT.md.
FIELD_QUESTIONS: dict[str, str] = {
    # DA-31 Leave
    "name":              "What's your full name? Last, first, middle.",
    "ssn":               "Last four of your DoD ID or SSN.",
    "rank":              "What's your rank?",
    "unit":              "Home unit and station, please.",
    "leave_address":     "Where will you be while on leave?",
    "leave_phone":       "Best phone number to reach you on leave?",
    "emergency_contact": "Who's your emergency point of contact?",
    "start_date":        "What's the first day of leave?",
    "end_date":          "Last day of leave?",
    "days_requested":    "How many days?",
    "leave_type":        "Type of leave — ordinary, emergency, convalescent, or terminal?",
    # DD-1351-2 Travel
    "purpose":           "What's the purpose of the trip?",
    "tdy_location":      "TDY destination — city and state?",
    "depart_date":       "What's the departure date?",
    "return_date":       "When do you return?",
    "duty_station":      "Home duty station?",
    "total_days":        "How many TDY days?",
    # DA-4856 Counseling
    "date":              "What's today's date?",
    "counselor_name":    "Counselor's name?",
    "counselor_rank":    "Counselor's rank?",
    "counseling_type":   "Type of counseling — event-oriented or performance?",
    "key_points":        "What are the key points of discussion?",
    "plan_of_action":    "What's the plan of action?",
}

# Phrases the soldier can say to short-circuit the back-and-forth and
# ship whatever's filled so far. Lowercase substring match.
STOP_PHRASES = (
    "ship it", "good enough", "that's all", "thats all", "that's it",
    "thats it", "skip", "move on", "just give me", "i'm done", "im done",
    "we're done", "were done", "send it", "fill what you have",
    "leave it blank", "skip that",
)


class VoiceLoop:
    """Per-WebSocket state machine. Created on connect, lives for connection."""

    def __init__(self, ws):
        self.ws = ws
        self.vad = load_silero_vad(onnx=True)
        # Speech accumulation
        self.speech: list[float] = []
        self.silence_count = 0
        self.user_speaking = False
        self.bot_speaking = False
        self.pre_buffer: list[float] = []  # rolling 240ms pre-roll (catches word-onset)
        self.pre_buffer_max = 8 * VAD_FRAME_SAMPLES  # 8 frames = ~256ms
        # Cancellation
        self.in_flight: asyncio.Task | None = None
        # Sentence-stream pacing — TTS chunks queued for sequential WS send
        self.tts_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.tts_sender: asyncio.Task | None = None
        # Conversational form-fill state. None = not mid-fill. When set,
        # next user utterance is treated as a field answer rather than a
        # new query — bot asks for missing required fields one at a time
        # until the form has everything or the soldier says "ship it."
        self.pending_fill: dict | None = None

    # ------------------------------------------------------------------
    # Public API: called from the WebSocket receive loop.
    # ------------------------------------------------------------------

    async def feed_audio(self, pcm16_bytes: bytes) -> None:
        """One 32 ms PCM16 mono frame from the browser AudioWorklet."""
        if len(pcm16_bytes) != VAD_FRAME_SAMPLES * 2:
            # Browser sometimes sends fragmented frames; tolerate by re-chunking.
            return self._handle_misaligned(pcm16_bytes)

        frame_i16 = np.frombuffer(pcm16_bytes, dtype=np.int16)
        frame_f32 = frame_i16.astype(np.float32) / 32768.0
        score = self.vad(torch.from_numpy(frame_f32), VAD_SR).item()

        # Maintain rolling pre-buffer so we capture the first phoneme even
        # before VAD activates.
        self.pre_buffer.extend(frame_f32.tolist())
        if len(self.pre_buffer) > self.pre_buffer_max:
            self.pre_buffer = self.pre_buffer[-self.pre_buffer_max:]

        # When the bot is speaking, raise the bar — laptop speaker bleed +
        # imperfect browser echo cancellation can fool VAD into thinking
        # the user is barging in, which would cancel the bot mid-sentence
        # and feed Whisper its own audio.
        threshold = VAD_BARGEIN_ACTIVATION if self.bot_speaking else VAD_ACTIVATION

        if score > threshold:
            if not self.user_speaking:
                self.user_speaking = True
                # Splice pre-buffer in so the leading consonant isn't lost.
                self.speech = list(self.pre_buffer)
                if self.bot_speaking:
                    log.info(f"user barge-in detected (score={score:.2f})")
                    await self._interrupt()
                await self._send_event({"type": "USER_SPEAKING_START"})
            self.speech.extend(frame_f32.tolist())
            self.silence_count = 0
        else:
            if self.user_speaking:
                # Trailing silence — keep buffering until silence threshold.
                self.speech.extend(frame_f32.tolist())
                self.silence_count += 1
                if self.silence_count >= SILENCE_FRAMES_TO_END:
                    await self._end_turn()

    async def handle_text(self, message: str) -> None:
        """JSON control messages from the client (mute, ping, etc.)."""
        try:
            ev = json.loads(message)
        except json.JSONDecodeError:
            return
        if ev.get("type") == "MUTE":
            await self._interrupt()
        elif ev.get("type") == "PING":
            await self._send_event({"type": "PONG"})

    async def shutdown(self) -> None:
        await self._interrupt()
        await self.tts_queue.put(None)
        if self.tts_sender:
            try:
                await asyncio.wait_for(self.tts_sender, timeout=0.5)
            except asyncio.TimeoutError:
                self.tts_sender.cancel()

    # ------------------------------------------------------------------
    # Internal pipeline.
    # ------------------------------------------------------------------

    async def _end_turn(self) -> None:
        """User finished speaking. Hand off to STT → LLM → TTS pipeline."""
        utterance = np.asarray(self.speech, dtype=np.float32)
        speech_frames = len(utterance) // VAD_FRAME_SAMPLES
        self.speech = []
        self.silence_count = 0
        self.user_speaking = False

        if speech_frames < MIN_SPEECH_FRAMES:
            log.info(f"too short ({speech_frames} frames) — discarding")
            return

        await self._send_event({"type": "USER_DONE",
                                "duration_ms": int(speech_frames * 32)})

        if self.in_flight and not self.in_flight.done():
            self.in_flight.cancel()
        self.in_flight = asyncio.create_task(self._respond(utterance))

        if self.tts_sender is None or self.tts_sender.done():
            self.tts_sender = asyncio.create_task(self._tts_sender_loop())

    async def _respond(self, utterance: np.ndarray) -> None:
        """STT → RAG → streaming LLM → sentence-buffer → Kokoro → WS chunks."""
        try:
            # Latency probe: how long from end-of-speech to first audio out?
            t_speech_end = time.time()

            # === Sabi trick #1: ONE thinking cue queues IMMEDIATELY ===
            # A single cue (~1.4s of "Stand by, Sergeant") bridges the gap
            # between end-of-speech and the first real sentence. We used
            # to chain multiple cues but it sounded robotic — a stream of
            # stalling phrases is worse than brief silence. One cue is
            # the perceived-latency fix; after that, let the user wait.
            first_cue = random.choice(THINKING_CUES)
            first_cue_bytes = get_cue(first_cue)
            if first_cue_bytes:
                await self.tts_queue.put(first_cue_bytes)
                log.info(f"queued cue {first_cue}")
            real_audio_event = asyncio.Event()
            cue_chain_task: asyncio.Task | None = None  # chain disabled

            # === STT ===
            wav_bytes = _f32_to_wav_bytes(utterance, VAD_SR)
            text = await asyncio.to_thread(transcribe, wav_bytes)

            if not text.strip():
                log.info("empty transcript — skipping (no LLM call)")
                if cue_chain_task: cue_chain_task.cancel()
                await self._send_event({"type": "USER_SILENT"})
                return

            # Min-word gate. Whisper hallucinates short fragments on
            # near-silence — common false positives are "Thank you.",
            # "Bye.", "you", ".". Don't waste an LLM turn on them; they
            # also can't be real questions.
            words = [w for w in text.strip().split() if any(c.isalpha() for c in w)]
            if len(words) < MIN_TRANSCRIPT_WORDS:
                log.info(f"transcript too short ({len(words)} words: {text!r}) — skipping")
                if cue_chain_task: cue_chain_task.cancel()
                await self._send_event({"type": "USER_SILENT", "transcript": text})
                return

            await self._send_event({"type": "TRANSCRIPT", "text": text})
            log.info(f"transcript: {text!r}")

            # === Conversational fill: if we're mid-form, treat the new
            # utterance as a field answer, not a fresh query. The handler
            # asks for the next missing field and returns; we re-enter
            # this method on the next user turn.
            if self.pending_fill is not None:
                if cue_chain_task: cue_chain_task.cancel()
                await self._handle_fill_followup(text)
                return

            # === RAG ===
            # Use top_k=3 in the voice path (vs default 5 for HTTP /query).
            # Each extra chunk adds ~150-200 tokens of LLM prefill on
            # llama3.2:3b CPU; for spoken-summary use, 3 chunks is enough
            # to ground a citation and shaves ~2s off first-sentence
            # latency. The HTTP /query path stays at top_k=5 for richer
            # form-fill extraction.
            chunks = await asyncio.to_thread(retrieve, text, 3)
            citations = [
                {"source": c.get("source", "unknown"),
                 "section": c.get("section", ""),
                 "quote": _clean_citation_quote(c["text"])}
                for c in chunks
            ]
            await self._send_event({
                "type": "BOT_SPEAKING_START",
                "citations": citations,
            })
            self.bot_speaking = True

            # === L1: parallel form-fill kicks off NOW ===
            # Form-fill needs the same chunks RAG returned; it does NOT
            # need to wait for the LLM stream or audio to finish. Fire it
            # in parallel — the PDF appears in the browser while the bot
            # is still speaking the citation summary, which is the
            # demo's most visible "wow" moment vs the cloud competitors.
            # Fire-and-forget: it sends its own PDF_READY events when done.
            asyncio.create_task(self._maybe_form_fill(text, chunks))

            # === Streaming LLM → per-sentence Kokoro ===
            # Bridge ollama-python's synchronous stream into the asyncio
            # world via a thread + queue. Each sentence flushes to Kokoro
            # the moment it forms — the first sentence audio leaves the
            # server while the LLM is still generating sentence #2.
            full_summary: list[str] = []
            first_audio_at: float | None = None
            sentence_q: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def _producer():
                try:
                    for sentence, done in answer_query_stream(text, chunks):
                        asyncio.run_coroutine_threadsafe(
                            sentence_q.put(sentence), loop
                        )
                finally:
                    asyncio.run_coroutine_threadsafe(sentence_q.put(None), loop)

            import threading
            producer_thread = threading.Thread(target=_producer, daemon=True)
            producer_thread.start()

            while True:
                sentence = await sentence_q.get()
                if sentence is None:
                    break
                full_summary.append(sentence)
                wav = await synthesize_wav_bytes_async(sentence)
                if first_audio_at is None:
                    first_audio_at = time.time() - t_speech_end
                    log.info(f"first real audio at +{first_audio_at*1000:.0f}ms after end-of-speech")
                    real_audio_event.set()
                await self.tts_queue.put(wav)
            real_audio_event.set()  # belt + suspenders for empty-stream case
            if cue_chain_task: cue_chain_task.cancel()

            spoken = " ".join(full_summary)
            # Make sure every queued audio chunk has been sent over the WS
            # before we tell the client "we're done speaking" — otherwise
            # the client sees BOT_SPEAKING_END and tears down before the
            # audio bytes for the final sentence arrive.
            await self.tts_queue.join()
            await self._send_event({
                "type": "BOT_SPEAKING_END",
                "spoken_summary": spoken,
            })
            # form_fill_task may still be running — that's fine, it'll
            # send its own PDF_READY events whenever it completes.

        except asyncio.CancelledError:
            log.info("response cancelled (interruption)")
            raise
        except Exception as e:
            log.error(f"_respond error: {e}", exc_info=True)
            await self._send_event({"type": "ERROR", "message": str(e)})
        finally:
            self.bot_speaking = False

    async def _tts_sender_loop(self) -> None:
        """Drains the TTS queue and streams WAV chunks over the WebSocket
        in order. A separate task so _respond() can keep generating chunks
        in parallel with WS sends.

        Calls task_done() after each chunk so _respond() can await
        tts_queue.join() before emitting BOT_SPEAKING_END — guarantees the
        client receives all audio bytes before the "we're done" event.
        """
        try:
            while True:
                chunk = await self.tts_queue.get()
                try:
                    if chunk is None:
                        self.tts_queue.task_done()
                        return
                    try:
                        await self.ws.send_bytes(chunk)
                    except Exception as e:
                        log.warning(f"tts_sender send failed: {e}")
                        return
                finally:
                    if chunk is not None:
                        self.tts_queue.task_done()
        except asyncio.CancelledError:
            return

    async def _cue_chain(self, real_audio_event: asyncio.Event, max_cues: int = 3) -> None:
        """Top up the TTS queue with additional thinking cues if the LLM
        is taking >1.5s past the previous cue's expected end. Bounded by
        max_cues so a stuck LLM doesn't loop us forever. Sabi pattern:
        no perceived dead air during long prefills.
        """
        try:
            for _ in range(max_cues):
                # Wait until the first cue (~1.4s) would be done playing,
                # OR until the real sentence arrives — whichever first.
                try:
                    await asyncio.wait_for(real_audio_event.wait(), timeout=1.5)
                    return
                except asyncio.TimeoutError:
                    pass
                cue = random.choice(THINKING_CUES)
                cue_bytes = get_cue(cue)
                if cue_bytes:
                    await self.tts_queue.put(cue_bytes)
                    log.info(f"chained cue {cue}")
        except asyncio.CancelledError:
            return

    async def _interrupt(self) -> None:
        """Barge-in: cancel current response, drain queue, tell client to stop."""
        await self._send_event({"type": "INTERRUPT"})
        if self.in_flight and not self.in_flight.done():
            self.in_flight.cancel()
            try:
                await self.in_flight
            except (asyncio.CancelledError, Exception):
                pass
        # Drain queue so subsequent sentences from the cancelled task don't
        # leak through.
        drained = 0
        while not self.tts_queue.empty():
            try:
                self.tts_queue.get_nowait()
                drained += 1
            except asyncio.QueueEmpty:
                break
        if drained:
            log.info(f"drained {drained} queued TTS chunks on interrupt")
        self.bot_speaking = False

    async def _maybe_form_fill(self, transcript: str, chunks: list[dict]) -> None:
        """Extract whatever the soldier said and emit a PDF immediately.

        Naomi's preference (post-iteration): the original fast-fill flow
        was the demo's wow moment — soldier speaks once, PDF lands in the
        iframe, download button works. The bot then speaks an
        INFORMATIONAL summary (what got filled, what's still missing,
        what the regulation says) — but does NOT pepper the soldier
        with prescriptive field questions.

        If the soldier wants to fix or add something they just talk
        again; the next turn re-extracts (with the rolling chat
        history) and emits a new PDF. The dialog is opt-in conversation,
        not an interrogation. If the LLM genuinely cannot fill the form
        without more info, the spoken summary names the gap and the
        soldier can choose to address it.

        We still maintain `pending_fill` state so corrections route to
        `_handle_fill_followup` cleanly — but no field-by-field
        questioning loop.
        """
        from adjutant.server import _correct_leave_type, _wire_per_diem, _infer_forms

        target_forms = _infer_forms(transcript)
        if not target_forms:
            return
        log.info(f"form-fill targets: {target_forms}")
        FILLED_DIR.mkdir(exist_ok=True)

        for fid in target_forms:
            try:
                schema = get_schema(fid)
            except KeyError:
                continue
            extraction = await asyncio.to_thread(
                extract_form_data, transcript, chunks, schema
            )
            form_data = extraction.get("data") or {}
            if fid == "DA-31":
                _correct_leave_type(form_data, transcript)
            if fid == "DD-1351-2":
                _wire_per_diem(form_data)

            missing_required = [
                fname for fname, spec in schema["fields"].items()
                if spec.get("required") and not form_data.get(fname)
            ]

            # Always emit the PDF + the natural summary (handled inside
            # _emit_pdf via _compose_form_summary).
            await self._emit_pdf(fid, schema, form_data)

            # Track state so a follow-up turn can reuse the chunks +
            # accumulate corrections. NOT used to drive a question loop.
            if not self.pending_fill:
                self.pending_fill = {
                    "form_id":          fid,
                    "schema":           schema,
                    "form_data":        form_data,
                    "messages":         [{"role": "user", "content": transcript}],
                    "missing_required": missing_required,
                    "chunks":           chunks,
                }
                await self._send_event({
                    "type":             "FILL_PROGRESS",
                    "form_id":          fid,
                    "form_data":        form_data,
                    "missing_required": missing_required,
                })
            return  # one form per turn; multi-form coverage handled by re-fire

            # === legacy flow (no longer reached) ===
            # Nothing missing OR pending_fill was already set by a prior
            # form in this batch — emit the PDF now.
            await self._emit_pdf(fid, schema, form_data)


    async def _handle_fill_followup(self, transcript: str) -> None:
        """One natural conversational turn. The soldier may have answered
        the previous question, asked a side-question about the form or
        regulation, corrected a prior field, or said they're done. We hand
        the full conversation + schema + currently-filled data + RAG
        chunks to the LLM and ask it to decide what to do next.

        The LLM returns:
            {
              "spoken_reply":  "...",            # what the bot says back
              "form_updates":  {"name": "..."},  # field values to apply
              "done":          false             # true → finalize PDF
            }

        This replaces the old "rigidly walk through missing fields"
        loop. The soldier can talk to the bot like Claude with explicit
        access to the regulations — questions get answered, the form
        keeps filling in the background.
        """
        pf = self.pending_fill
        if pf is None:
            return

        # Append this user turn to the rolling chat history.
        pf.setdefault("messages", []).append(
            {"role": "user", "content": transcript}
        )

        # Hard stop-phrase short-circuit — cheaper than another LLM call
        # if the soldier explicitly says "ship it."
        low = transcript.lower()
        if any(p in low for p in STOP_PHRASES):
            log.info("fill: stop-phrase detected, shipping with what we have")
            # Brief acknowledgment so the soldier knows we're shipping.
            await self._speak_inline("Roger, shipping the form with what we have.")
            await self._finalize_pending_fill()
            return

        # === Deterministic path ===
        # Bench-tested at 1 hour-to-demo: 3B models (llama3.2 + qwen2.5)
        # are too small to reliably do extraction + natural conversational
        # reply in a single LLM call. They drop ~30% of field extractions
        # and emit chat-format dumps with markdown / JSON / forbidden
        # filler. The reliable path: use Adjutant's proven
        # `extract_form_data` for fields (~99% accurate) and the
        # hardcoded FIELD_QUESTIONS dict for the spoken reply. Adds
        # ~50ms per turn, predictable, never wrong.
        #
        # If the soldier asked a regulation question (detected below),
        # we DO call the LLM — but only to answer that question, not to
        # drive the dialog. The deterministic ack-and-ask resumes after.
        all_user_text_now = "  ".join(
            m["content"] for m in pf.get("messages", []) if m["role"] == "user"
        )
        extraction = await asyncio.to_thread(
            extract_form_data, all_user_text_now, pf["chunks"], pf["schema"]
        )
        new_data = (extraction or {}).get("data") or {}
        for k, v in new_data.items():
            flat = _flatten_field_value(v)
            if not flat:
                continue
            if k in pf["schema"]["fields"]:
                pf["form_data"][k] = flat
        # Apply Adjutant's domain-specific post-processors so e.g. the
        # leave-type override + per-diem math still kick in. We pull
        # only the soldier's turns from the rolling chat history.
        from adjutant.server import _correct_leave_type, _wire_per_diem
        all_user_text = "  ".join(
            m["content"] for m in pf.get("messages", []) if m["role"] == "user"
        )
        if pf["form_id"] == "DA-31":
            _correct_leave_type(pf["form_data"], all_user_text)
        if pf["form_id"] == "DD-1351-2":
            _wire_per_diem(pf["form_data"])

        still_missing = [
            fname for fname, spec in pf["schema"]["fields"].items()
            if spec.get("required") and not pf["form_data"].get(fname)
        ]
        pf["missing_required"] = still_missing

        # Tell the browser the form's progress so the iframe / state
        # panel can update in real time.
        await self._send_event({
            "type":             "FILL_PROGRESS",
            "form_id":          pf["form_id"],
            "form_data":        pf["form_data"],
            "missing_required": still_missing,
        })

        # === Compose spoken reply deterministically ===
        # Three branches:
        #   (a) form is complete → finalize (handled in _finalize_pending_fill,
        #       which already calls _compose_form_summary for the closing line).
        #   (b) soldier asked a regulation question → LLM-answer it once,
        #       then ask next field. ONE LLM call, scoped to "answer this".
        #   (c) normal field-answer turn → "Got that. <next FIELD_QUESTIONS>".
        any_extracted = bool(new_data)
        is_question = self._looks_like_regulation_question(transcript)

        if not still_missing:
            # Path (a) — finalize + return; _finalize_pending_fill speaks
            # its own summary via _emit_pdf → _compose_form_summary.
            log.info(f"fill: all required fields filled for {pf['form_id']}")
            await self._finalize_pending_fill()
            return

        next_field = still_missing[0]
        next_q = FIELD_QUESTIONS.get(
            next_field, f"What's your {next_field.replace('_', ' ')}?"
        )

        if is_question:
            # Path (b) — answer the regulation question with one short
            # LLM call, then chain in the next-field ask.
            answer = await asyncio.to_thread(self._answer_side_question, transcript)
            if answer:
                reply = f"{answer} {next_q}"
            else:
                reply = next_q
        else:
            # Path (c) — deterministic ack + next field.
            ack = "Got that on file. " if any_extracted else ""
            reply = f"{ack}{next_q}".strip()

        await self._speak_inline(reply, asking_field=next_field)


    def _looks_like_regulation_question(self, transcript: str) -> bool:
        """Cheap heuristic: did the soldier ask about a regulation /
        policy / what's allowed? If yes, we do one LLM call to answer.
        If no, we use the deterministic ack+ask path."""
        t = transcript.lower().strip()
        if "?" in t:
            return True
        starts = (
            "can i", "could i", "am i allowed", "is it ok", "is that ok",
            "what about", "what if", "what's the", "whats the",
            "how do i", "how does", "why do", "why does",
            "when can", "when do", "is there", "do i need",
        )
        return any(t.startswith(s) for s in starts)


    def _answer_side_question(self, question: str) -> str:
        """One short LLM call to answer a regulation/policy side-question
        with citation. Returns clean spoken prose (1-2 sentences) or ""
        on failure. Synchronous; caller wraps in asyncio.to_thread.
        """
        try:
            from adjutant.llm import _client, MODEL
        except ImportError:
            return ""
        pf = self.pending_fill or {}
        chunks = pf.get("chunks") or []
        ctx_lines = []
        for c in chunks[:2]:
            src = c.get("source", "")
            sec = c.get("section", "")
            quote = c.get("text", "")[:300]
            ctx_lines.append(f"[{src} {sec}] {quote}")
        ctx = "\n".join(ctx_lines) or "(no chunk retrieved)"
        prompt = f"""Answer the soldier's question in ONE short spoken sentence (max 25 words). Cite the regulation paragraph from the context if applicable. No markdown. No filler openings. No questions back at them.

CONTEXT:
{ctx}

QUESTION: {question}

ANSWER:"""
        try:
            resp = _client.chat(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.3, "num_predict": 80, "repeat_penalty": 1.2},
            )
            text = resp["message"]["content"].strip()
            text = _clean_spoken_text(text)
            # Cap at 2 sentences max for spoken brevity.
            sentences = re.split(r"(?<=[.!?])\s+", text)
            if len(sentences) > 2:
                text = " ".join(sentences[:2])
            return text
        except Exception as e:
            log.warning(f"_answer_side_question failed ({e!r})")
            return ""


    def _llm_dialog_step(self) -> dict | None:
        """Sabi-pattern dialog orchestrator. Built like sabi-server's
        SabiLLM.generate(): ADJUTANT_DIALOG_PROMPT as system, the rolling
        user/assistant chat history as `messages`, per-call RAG context
        appended to system. The bot's prior turns get replayed so the
        model never repeats itself or loses track.

        Synchronous; caller wraps in asyncio.to_thread. Returns None on
        failure so the caller can fall back."""
        import json
        import re as _re
        try:
            from adjutant.llm import _client, MODEL
        except ImportError:
            return None

        pf = self.pending_fill or {}
        history = pf.get("messages") or []
        if not history:
            return None

        system_prompt = ADJUTANT_DIALOG_PROMPT + _build_dialog_rag_context(pf)

        try:
            resp = _client.chat(
                model=MODEL,
                messages=[{"role": "system", "content": system_prompt}, *history],
                options={
                    # Sabi's settings — natural conversation + repetition
                    # penalty so llama3.2:3b doesn't loop on the same answer.
                    "temperature":    0.7,
                    "top_p":          0.9,
                    "repeat_penalty": 1.2,
                    "num_predict":    250,
                },
                format="json",
            )
            raw = resp["message"]["content"].strip()
            m = _re.search(r"\{.*\}", raw, _re.DOTALL)
            if m:
                raw = m.group(0)
            decision = json.loads(raw)
            log.info(f"dialog step: reply={decision.get('spoken_reply','')[:80]!r} "
                     f"updates={list((decision.get('form_updates') or {}).keys())} "
                     f"done={decision.get('done')}")
            # Persist the bot's reply into the rolling chat history so
            # the NEXT turn sees what the bot just said. Without this,
            # the 3B model loses context and repeats prior answers.
            reply_text = decision.get("spoken_reply", "").strip()
            if reply_text:
                history.append({"role": "assistant", "content": reply_text})
            return decision
        except Exception as e:
            log.warning(f"_llm_dialog_step failed ({e!r})")
            return None


    # Dead code below this line — left for reference, original prompt
    # text is preserved in ADJUTANT_DIALOG_PROMPT + _build_dialog_rag_context.
    def _llm_dialog_step_legacy(self, _latest_transcript: str) -> dict | None:
        """Pre-Sabi-refactor orchestrator. NOT CALLED — kept for diff
        review. Will be deleted once the new path is verified."""
        import json
        import re as _re
        try:
            from adjutant.llm import _client, MODEL
        except ImportError:
            return None

        pf = self.pending_fill or {}
        schema = pf.get("schema") or {}
        form_id = pf.get("form_id", "")
        form_data = pf.get("form_data") or {}
        chunks = pf.get("chunks") or []
        transcripts = pf.get("transcripts") or []

        schema_desc_lines = []
        for fname, spec in schema.get("fields", {}).items():
            req = "REQUIRED" if spec.get("required") else "optional"
            schema_desc_lines.append(f"  {fname}: {spec.get('desc','')}  [{req}]")
        schema_desc = "\n".join(schema_desc_lines)

        filled_str = "\n".join(f"  {k}: {v}" for k, v in form_data.items() if v) \
                     or "  (nothing yet)"
        missing_required = [
            fname for fname, spec in schema.get("fields", {}).items()
            if spec.get("required") and not form_data.get(fname)
        ]

        convo = "\n".join(f"  soldier: {t}" for t in transcripts)

        ctx_lines = []
        for c in chunks[:3]:
            src = c.get("source", "")
            sec = c.get("section", "")
            quote = c.get("text", "")[:300]
            ctx_lines.append(f"[{src} {sec}] {quote}")
        ctx = "\n".join(ctx_lines) if ctx_lines else "(no specific paragraph retrieved)"

        prompt = f"""You are Adjutant, a personal AI assistant helping a U.S. Army soldier fill out form {form_id}. You speak in the tone of a knowledgeable buddy NCO who's done this paperwork a hundred times — friendly, direct, regulation-literate. You have explicit access to Army Regulations and DA pamphlets.

The soldier and you are in an ongoing conversation. The soldier just said something. You decide what to do next based on what they said — ANY of:
  - they answered the field you asked about → extract it and ask the next field
  - they answered a different field than you asked about → take it anyway, ask another
  - they corrected something earlier ("actually it's 5678 not 1234") → update that field
  - they asked a question about the regulation or the form → answer briefly, then nudge them back toward the next missing field
  - they're confused / forgot the question → restate it briefly
  - they said they're done / "ship it" / "good enough" → set done=true

You always reply in plain spoken English (will be read aloud by Kokoro TTS). Keep replies SHORT — 1 to 3 sentences. No greeting. No markdown. No XML tags. End naturally.

When you cite a regulation, name the AR + paragraph number. Don't force a citation if the chunk doesn't support one.

FORM SCHEMA (every field you can fill):
{schema_desc}

ALREADY FILLED:
{filled_str}

STILL MISSING (REQUIRED ONLY):
{', '.join(missing_required) if missing_required else '(none — form is complete)'}

CONVERSATION SO FAR (oldest first; the LAST line is what you must respond to):
{convo}

RETRIEVED REGULATION CONTEXT (use to ground citations):
{ctx}

Return ONE JSON object exactly like:
{{"spoken_reply": "<what you say back, 1-3 sentences>", "form_updates": {{"<field_name>": "<value>", ...}}, "done": false}}

- "form_updates" may be empty. Include only fields you can confidently extract from this turn.
- Use field names exactly as they appear in the schema above.
- "done" is true only if the soldier said they're done OR every required field is now filled.
- Output ONLY the JSON object. No preamble. No prose around it.

JSON:
"""
        try:
            resp = _client.chat(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.3, "num_predict": 250},
                format="json",
            )
            raw = resp["message"]["content"].strip()
            m = _re.search(r"\{.*\}", raw, _re.DOTALL)
            if m:
                raw = m.group(0)
            decision = json.loads(raw)
            log.info(f"dialog step: reply={decision.get('spoken_reply','')[:80]!r} "
                     f"updates={list((decision.get('form_updates') or {}).keys())} "
                     f"done={decision.get('done')}")
            return decision
        except Exception as e:
            log.warning(f"_llm_dialog_step failed ({e!r})")
            return None


    async def _fallback_extract_and_ask(self, transcript: str) -> None:
        """If the orchestrator LLM call fails, fall back to the old
        rigid behavior: re-run extraction on combined transcripts, then
        ask the next missing field via the hardcoded FIELD_QUESTIONS."""
        pf = self.pending_fill
        if pf is None:
            return
        # Reconstruct combined user text from the message history (only
        # user roles; the messages list now interleaves user+assistant).
        user_turns = [m["content"] for m in pf.get("messages", [])
                      if m["role"] == "user"]
        # Defensive: include current transcript if not already in history.
        if not user_turns or user_turns[-1] != transcript:
            user_turns.append(transcript)
        combined = "  ".join(user_turns)
        from adjutant.server import _correct_leave_type, _wire_per_diem
        extraction = await asyncio.to_thread(
            extract_form_data, combined, pf["chunks"], pf["schema"]
        )
        new_data = extraction.get("data") or {}
        for k, v in new_data.items():
            if v not in (None, ""):
                pf["form_data"][k] = v
        if pf["form_id"] == "DA-31":
            _correct_leave_type(pf["form_data"], combined)
        if pf["form_id"] == "DD-1351-2":
            _wire_per_diem(pf["form_data"])
        still_missing = [
            f for f, s in pf["schema"]["fields"].items()
            if s.get("required") and not pf["form_data"].get(f)
        ]
        pf["missing_required"] = still_missing
        if still_missing:
            await self._ask_for_field(still_missing[0], pf["form_id"], pf["form_data"])
        else:
            await self._finalize_pending_fill()


    async def _speak_inline(self, text: str, asking_field: str | None = None) -> None:
        """Speak a single chunk of conversational reply. Mirrors
        BOT_SPEAKING_START / END events so the browser orb behaves the
        same as during a normal answer turn.

        Strips markdown / JSON code blocks / XML tags before TTS so
        Kokoro doesn't literally read 'asterisk asterisk Form Data
        colon backtick backtick backtick' aloud — which is what
        llama3.2:3b leaks when it ignores prompt instructions.
        """
        cleaned = _clean_spoken_text(text)
        await self._send_event({
            "type":      "BOT_SPEAKING_START",
            "asking":    asking_field,
            "form_id":   (self.pending_fill or {}).get("form_id"),
            "form_data": (self.pending_fill or {}).get("form_data"),
            "citations": [],
        })
        self.bot_speaking = True
        try:
            wav = await synthesize_wav_bytes_async(cleaned)
            await self.tts_queue.put(wav)
            await self.tts_queue.join()
        except Exception as e:
            log.warning(f"speak_inline synth failed: {e}")
        await self._send_event({
            "type":           "BOT_SPEAKING_END",
            "spoken_summary": cleaned,
        })
        self.bot_speaking = False


    async def _ask_for_field(self, field_name: str, form_id: str,
                             form_data: dict) -> None:
        """Speak a single follow-up question for `field_name`.

        The LLM generates the question with a personal-assistant /
        educational tone — *"Per AR 600-8-10, ordinary leave accrues at
        2.5 days a month. I just need your home unit and station to
        finish block 7. What's your unit?"* — using the RAG chunks the
        first turn pulled in. Falls back to FIELD_QUESTIONS if the LLM
        call fails or times out.
        """
        question = await self._generate_field_question(
            field_name, form_id, form_data
        ) or FIELD_QUESTIONS.get(field_name) or f"I still need {field_name.replace('_', ' ')}."
        log.info(f"fill: asking {field_name!r}: {question!r}")

        # We mirror the BOT_SPEAKING_START → audio chunks → BOT_SPEAKING_END
        # pattern so the browser's state-orb behaves the same way as a
        # normal answer turn.
        await self._send_event({
            "type":      "BOT_SPEAKING_START",
            "asking":    field_name,
            "form_id":   form_id,
            "form_data": form_data,
            "citations": [],
        })
        self.bot_speaking = True
        try:
            wav = await synthesize_wav_bytes_async(question)
            await self.tts_queue.put(wav)
            await self.tts_queue.join()
        except Exception as e:
            log.warning(f"ask_for_field synth failed: {e}")
        await self._send_event({
            "type":           "BOT_SPEAKING_END",
            "spoken_summary": question,
        })
        self.bot_speaking = False


    async def _generate_field_question(
        self, field_name: str, form_id: str, form_data: dict
    ) -> str | None:
        """Ask Adjutant's LLM to write a friendly, regulation-aware
        question for the missing field. Returns the spoken text, or
        None if the LLM failed.

        The prompt is intentionally tight — we want one question
        out, not a paragraph — and reuses the RAG chunks the first
        turn already pulled so the model can name the actual
        paragraph.
        """
        try:
            from adjutant.llm import _client, MODEL
        except ImportError:
            return None

        pf = self.pending_fill or {}
        chunks = pf.get("chunks") or []
        # Cap context — first 2 chunks is enough to ground a citation
        # without blowing up prefill.
        ctx_lines = []
        for c in chunks[:2]:
            src = c.get("source", "")
            sec = c.get("section", "")
            quote = c.get("text", "")[:300]
            ctx_lines.append(f"[{src} {sec}] {quote}")
        ctx = "\n".join(ctx_lines) if ctx_lines else "(no specific paragraph retrieved)"

        # What we already know — helps the model say "I have your dates
        # and destination, I just need…" rather than starting from zero.
        known = ", ".join(f"{k}={v}" for k, v in form_data.items() if v)

        field_hint = self._field_schema_desc(field_name, form_id)

        prompt = f"""You are Adjutant, a personal assistant helping a U.S. Army soldier fill out form {form_id}.

You speak in the tone of a knowledgeable buddy NCO who's done this paperwork a hundred times — friendly, direct, regulation-literate. You have explicit access to the soldier's regulations and DA pamphlets.

Your job RIGHT NOW: write ONE short spoken question (1–2 sentences max, will be read aloud by Kokoro TTS) asking the soldier for the value of the field "{field_name}".

When it's natural, briefly anchor your question in the governing regulation paragraph from the retrieved context. Don't force a citation if the chunk doesn't mention that field. Don't ramble.

WHAT WE ALREADY HAVE (don't ask for these again):
{known if known else "(nothing yet)"}

FIELD WE NEED NOW:
  name:        {field_name}
  description: {field_hint}

RETRIEVED REGULATION CONTEXT:
{ctx}

OUTPUT RULES:
- Plain spoken English, 1–2 short sentences.
- No markdown. No bullet points. No "<form_data>". No tags.
- No greeting (no "Hi Sergeant"). The conversation is already going.
- End with the actual question.

QUESTION:
"""
        try:
            resp = _client.chat(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.4, "num_predict": 90},
            )
            text = resp["message"]["content"].strip()
            # Defensive cleanup — strip stray markdown / tags / leading
            # "Question:" labels the model sometimes emits.
            import re as _re
            text = _re.sub(r"^(question|q|adjutant|bot)\s*:\s*", "", text, flags=_re.I)
            text = _re.sub(r"<.*?>", "", text)
            text = text.strip(' "\'\n')
            if len(text) < 3:
                return None
            return text
        except Exception as e:
            log.warning(f"_generate_field_question failed ({e!r}) — using fallback")
            return None


    def _field_schema_desc(self, field_name: str, form_id: str) -> str:
        """Pull the schema 'desc' string for a field so the question
        generator knows what kind of value the field expects (e.g.
        'Pay grade like E-5, O-3')."""
        try:
            schema = get_schema(form_id)
            spec = schema["fields"].get(field_name, {})
            return spec.get("desc", "")
        except Exception:
            return ""


    async def _finalize_pending_fill(self) -> None:
        """All required fields gathered (or soldier said 'ship it') —
        generate the PDF, emit PDF_READY, clear pending_fill."""
        pf = self.pending_fill
        if pf is None:
            return
        try:
            await self._emit_pdf(pf["form_id"], pf["schema"], pf["form_data"])
        finally:
            self.pending_fill = None


    async def _emit_pdf(self, fid: str, schema: dict, form_data: dict) -> None:
        """Render the PDF, emit PDF_READY, and speak a real LLM-generated
        summary — not the 4-word canned cue. Format mirrors a buddy NCO
        handing a filled form back to the soldier:

           "Got it, Sergeant — DA-31 drafted for ten days of ordinary
            leave starting June third, governed by AR 600-8-10 paragraph
            4-3. I have name, dates, address, phone. I still need your
            DoD ID last four — what is it?"

        Falls back to the cached cue if the LLM call fails.
        """
        if not form_data:
            log.info(f"emit_pdf: no data for {fid}, skipping")
            return
        out = FILLED_DIR / f"{fid}-{uuid.uuid4().hex[:8]}.pdf"
        try:
            await asyncio.to_thread(
                fill_pdf, schema["pdf_path"], form_data, str(out), schema
            )
        except Exception as e:
            log.warning(f"fill {fid} failed: {e}")
            return
        missing = [
            fname for fname, spec in schema["fields"].items()
            if spec.get("required") and not form_data.get(fname)
        ]
        await self._send_event({
            "type":            "PDF_READY",
            "form_id":         fid,
            "pdf_url":         f"/filled/{out.name}",
            "missing_fields":  missing,
        })

        # === Deterministic spoken summary ===
        # Earlier we composed via an LLM call (10+ seconds, sometimes
        # empty). The fast-fill demo wants the bot speaking the moment
        # the PDF lands. This is a template — no LLM, ~0ms latency,
        # never empty, always names the form + cited reg + filled
        # fields + missing.
        chunks = (self.pending_fill or {}).get("chunks") or []
        summary = self._template_form_summary(fid, form_data, missing, chunks)
        await self._speak_inline(
            summary,
            asking_field=missing[0] if missing else None,
        )


    def _template_form_summary(
        self, fid: str, form_data: dict, missing: list[str],
        chunks: list[dict],
    ) -> str:
        """Build the spoken summary deterministically. Names the form,
        the governing regulation paragraph (from the first RAG chunk
        with a section), the headline values, and what's still missing.
        Output is one to three short sentences ready for Kokoro."""

        # Pick the regulation cite — first chunk that has a section.
        cite = ""
        for c in chunks:
            sec = c.get("section", "")
            src = c.get("source", "")
            if sec and src:
                # Normalize "paragraph 4-3" → "paragraph four dash three"
                # is too aggressive; Kokoro reads "4-3" fine. Just say it.
                cite = f"Per {src}, {sec}."
                break

        # Headline values for the spoken recap. Two main flavors per form:
        # leave (DA-31), TDY (DD-1351-2), counseling (DA-4856).
        headline = ""
        if fid == "DA-31":
            days = form_data.get("days_requested")
            ltype = (form_data.get("leave_type") or "").lower() or "ordinary"
            start = _spoken_date(form_data.get("start_date"))
            end = _spoken_date(form_data.get("end_date"))
            if days and start and end:
                headline = f"{_spoken_int(days)} days of {ltype} leave from {start} to {end}."
            elif days and start:
                headline = f"{_spoken_int(days)} days of {ltype} leave starting {start}."
            elif days:
                headline = f"{_spoken_int(days)} days of {ltype} leave."
        elif fid == "DD-1351-2":
            loc = form_data.get("tdy_location")
            days = form_data.get("total_days")
            if loc and days:
                headline = f"TDY to {loc} for {_spoken_int(days)} days."
            elif loc:
                headline = f"TDY to {loc}."
        elif fid == "DA-4856":
            counselee = form_data.get("name") or "the counselee"
            purpose = (form_data.get("purpose") or "").rstrip(". ")
            if purpose:
                headline = f"Counseling for {counselee}: {purpose}."
            else:
                headline = f"Counseling form for {counselee}."

        # Pretty-print field names for spoken-aloud — "ssn" reads as
        # "ess ess en" by Kokoro otherwise, "leave_phone" as
        # "leave-underscore-phone." Map common cryptic schema names to
        # natural English here.
        FIELD_PRETTY = {
            "ssn":               "DoD ID last four",
            "leave_phone":       "phone number on leave",
            "leave_address":     "address while on leave",
            "emergency_contact": "emergency point of contact",
            "tdy_location":      "TDY destination",
            "depart_date":       "departure date",
            "return_date":       "return date",
            "duty_station":      "home duty station",
            "total_days":        "TDY day count",
            "counselor_name":    "counselor name",
            "counselor_rank":    "counselor rank",
            "counseling_type":   "counseling type",
            "key_points":        "key points of discussion",
            "plan_of_action":    "plan of action",
            "days_requested":    "number of leave days",
        }
        def _pretty(field: str) -> str:
            return FIELD_PRETTY.get(field, field.replace("_", " "))

        miss_phrase = ""
        if missing:
            pretty = [_pretty(m) for m in missing[:3]]
            if len(missing) == 1:
                miss_phrase = f" Still need {pretty[0]}."
            elif len(missing) == 2:
                miss_phrase = f" Still need {pretty[0]} and {pretty[1]}."
            elif len(missing) == 3:
                miss_phrase = f" Still need {pretty[0]}, {pretty[1]}, and {pretty[2]}."
            else:
                miss_phrase = (
                    f" Still need {pretty[0]}, {pretty[1]}, "
                    f"and {len(missing) - 2} other fields."
                )
        else:
            miss_phrase = " Form is complete and ready for signature."

        # Compose. Form name is announced; cite goes second so it lands
        # right after the form ID; headline + missing close it out.
        form_name = {
            "DA-31":     "DA Form 31",
            "DD-1351-2": "DD Form 1351 dash 2",
            "DA-4856":   "DA Form 4856",
        }.get(fid, fid)

        # Capitalize the first letter of the headline ("ten days..." → "Ten days...")
        if headline and headline[0].islower():
            headline = headline[0].upper() + headline[1:]

        parts = [f"{form_name} drafted."]
        if cite:
            parts.append(cite)
        if headline:
            parts.append(headline)
        parts.append(miss_phrase.strip())
        return " ".join(parts)


    def _compose_form_summary(
        self, fid: str, form_data: dict, missing: list[str],
        chunks: list[dict],
    ) -> str | None:
        """Single LLM call. Spoken-aloud summary of what we just filled.

        Mirrors Sabi's pattern: persona system prompt + per-call RAG
        chunks + structured user message. JSON-formatted output to keep
        the model in the lane we want.
        """
        try:
            from adjutant.llm import _client, MODEL
        except ImportError:
            return None

        # What's already on the form, in human-readable form.
        filled_lines = [f"  {k}: {v}" for k, v in form_data.items() if v]
        filled = "\n".join(filled_lines) or "  (nothing)"

        ctx_lines = []
        for c in chunks[:2]:
            src = c.get("source", "")
            sec = c.get("section", "")
            quote = c.get("text", "")[:300]
            ctx_lines.append(f"[{src} {sec}] {quote}")
        ctx = "\n".join(ctx_lines) if ctx_lines else "(no specific paragraph retrieved)"

        prompt = ADJUTANT_DIALOG_PROMPT + f"""

## RIGHT NOW
You just finished drafting form {fid} for the soldier. Speak ONE short
spoken-aloud summary (2–4 sentences max) that:

  1. Acknowledges the form is drafted.
  2. Names the governing regulation paragraph (from the chunks below).
  3. Briefly recaps the headline values (dates, days, destination).
  4. If anything required is still missing, ask for the FIRST one
     specifically. If nothing missing, tell them to sign and route.

Keep it natural. Plain spoken English. No lists. No markdown. No tags.

ALREADY FILLED:
{filled}

STILL MISSING — REQUIRED:
{', '.join(missing) if missing else '(none — form is complete)'}

REGULATION CONTEXT:
{ctx}

Return JSON: {{"spoken_reply": "<your spoken sentence(s)>"}}"""

        try:
            import json, re as _re
            resp = _client.chat(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                options={
                    "temperature":    0.6,
                    "top_p":          0.9,
                    "repeat_penalty": 1.2,
                    "num_predict":    200,
                },
                format="json",
            )
            raw = resp["message"]["content"].strip()
            m = _re.search(r"\{.*\}", raw, _re.DOTALL)
            if m:
                raw = m.group(0)
            obj = json.loads(raw)
            text = (obj.get("spoken_reply") or "").strip()
            return text or None
        except Exception as e:
            log.warning(f"_compose_form_summary failed ({e!r})")
            return None

    async def _send_event(self, ev: dict) -> None:
        try:
            await self.ws.send_text(json.dumps(ev))
        except Exception as e:
            log.warning(f"send_event failed: {e}")

    def _handle_misaligned(self, pcm16_bytes: bytes) -> None:
        # We could buffer & re-chunk, but in practice AudioWorklet aligns
        # cleanly. Log once so we notice if a browser surprises us.
        if not getattr(self, "_warned_misalign", False):
            log.warning(f"misaligned audio frame: {len(pcm16_bytes)} bytes "
                        f"(expected {VAD_FRAME_SAMPLES * 2})")
            self._warned_misalign = True


_MARKDOWN_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_MARKDOWN_ITALIC = re.compile(r"(?<!\w)[*_]([^*_]+)[*_](?!\w)")
_CODE_BLOCK = re.compile(r"```[\s\S]*?```")
_INLINE_CODE = re.compile(r"`([^`]+)`")
_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
# JSON-like object dumps the model leaks into prose.
_JSON_BLOCK = re.compile(r"\{[^{}]*\}")
# Section labels the model also leaks ("Form Data:", "Regulation:", etc.)
_LABEL_LEAKS = re.compile(
    r"\b(Form Data|Form Updates|Regulation|Approval Note|JSON|Output|Spoken Reply|Done)\s*:\s*",
    re.IGNORECASE,
)
# Filler openings that the prompt forbids but the 3B model still emits.
# Stripped post-hoc so the user never hears them.
_FILLER_OPEN = re.compile(
    r"^\s*(?:"
    r"yes\s*,?\s*sergeant\s*,?\s*"
    r"|yes\s*,?\s*sir\s*,?\s*"
    r"|yes\s*,?\s*ma'?am\s*,?\s*"
    r"|roger\s+that\s*,?\s*"
    r"|roger\s*,?\s*"
    r"|got\s+it\s*,?\s*"
    r"|alright\s*,?\s*"
    r"|okay\s*,?\s*so\s*,?\s*"
    r"|okay\s*,?\s*"
    r"|sure\s+thing\s*,?\s*"
    r"|copy\s+that\s*,?\s*"
    r"|copy\s*,?\s*"
    r"|understood\s*,?\s*"
    r"|hooah\s*,?\s*"
    r")+",
    re.IGNORECASE,
)


def _clean_spoken_text(text: str) -> str:
    """Strip everything Kokoro would mispronounce or that the prompt
    forbids: markdown, code fences, JSON blobs, HTML tags, leaked
    section labels, and the canned filler openings the 3B model insists
    on emitting despite explicit instructions ("Roger that", "Got it",
    "Yes, Sergeant", etc.)."""
    if not text:
        return ""
    text = _CODE_BLOCK.sub(" ", text)
    text = _JSON_BLOCK.sub(" ", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _HTML_TAG.sub("", text)
    text = _MARKDOWN_BOLD.sub(r"\1", text)
    text = _MARKDOWN_ITALIC.sub(r"\1", text)
    text = _LABEL_LEAKS.sub("", text)
    # Strip filler openings (loop in case multiple stack: "Yes Sergeant, Roger,...")
    for _ in range(3):
        new_text = _FILLER_OPEN.sub("", text).lstrip()
        if new_text == text:
            break
        text = new_text
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" -•:\n\t\"'")
    # Capitalize first letter if we stripped lowercase filler (e.g.
    # "got it, your address is now on file" → "your address..." → "Your address...")
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    return text


_MONTHS = ["January","February","March","April","May","June",
           "July","August","September","October","November","December"]
_ORDINALS = {1:"first",2:"second",3:"third",4:"fourth",5:"fifth",
             6:"sixth",7:"seventh",8:"eighth",9:"ninth",10:"tenth",
             11:"eleventh",12:"twelfth",13:"thirteenth",14:"fourteenth",
             15:"fifteenth",16:"sixteenth",17:"seventeenth",18:"eighteenth",
             19:"nineteenth",20:"twentieth",21:"twenty-first",22:"twenty-second",
             23:"twenty-third",24:"twenty-fourth",25:"twenty-fifth",
             26:"twenty-sixth",27:"twenty-seventh",28:"twenty-eighth",
             29:"twenty-ninth",30:"thirtieth",31:"thirty-first"}
_INT_WORDS = {0:"zero",1:"one",2:"two",3:"three",4:"four",5:"five",6:"six",
              7:"seven",8:"eight",9:"nine",10:"ten",11:"eleven",12:"twelve",
              13:"thirteen",14:"fourteen",15:"fifteen",16:"sixteen",
              17:"seventeen",18:"eighteen",19:"nineteen",20:"twenty",
              21:"twenty-one",22:"twenty-two",23:"twenty-three",
              24:"twenty-four",25:"twenty-five",26:"twenty-six",
              27:"twenty-seven",28:"twenty-eight",29:"twenty-nine",
              30:"thirty"}


def _spoken_date(iso: str | None) -> str:
    """ISO 'YYYY-MM-DD' → 'June third'. Returns '' if unparseable."""
    if not iso or not isinstance(iso, str):
        return ""
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", iso.strip())
    if not m:
        return ""
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return ""
    return f"{_MONTHS[mo-1]} {_ORDINALS.get(d, str(d))}"


def _spoken_int(n) -> str:
    """Small integer → spoken word ('ten', 'thirty'). Falls back to digits."""
    try:
        i = int(n)
    except (TypeError, ValueError):
        return str(n)
    return _INT_WORDS.get(i, str(i))


# Citation cleaner lives in adjutant.text_utils so adjutant.llm can also
# import it without creating a circular dependency back to voice_loop.
from adjutant.text_utils import clean_citation_quote as _clean_citation_quote


def _flatten_field_value(v):
    """LLM extractors sometimes return a structured dict for a field
    that should be a single string ({'name': 'Maria Chen', 'relation':
    'Mother'}). Flatten to a single readable string so PDF fill and
    Aspose form-fill don't choke.
    """
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        # Common shapes: {name, relation}, {first, last}, {city, state}
        parts = []
        for key in ("name", "first_name", "first", "last_name", "last",
                    "rank", "relation", "relationship", "phone", "city",
                    "state", "address", "purpose"):
            if key in v and v[key]:
                parts.append(str(v[key]))
        if not parts:
            parts = [str(x) for x in v.values() if x]
        return " · ".join(parts)
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v if x)
    return str(v)


def _f32_to_wav_bytes(samples: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, samples, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()
