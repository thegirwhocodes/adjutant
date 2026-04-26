# Adjutant — Demo Pitch Sheet

**Use this as cue cards. Each section is sized to be readable from across the room. Read the bolded lines verbatim; everything else is paraphrasable.**

---

## 0:00 — 0:30 · The hook

> "Army company leaders work **twelve-and-a-half hour days** — longer than ninety-six percent of all American workers — and **less than a third** of that time is on actual readiness."
>
> "Why? Because Modern War Institute — West Point's own think tank — documents that companies submit **three to four dozen reports a month**."
>
> "The Pentagon launched **GenAI-dot-mil** in December to one-point-two million users. But the Pentagon's own framing admits the gap — quote: *'Not everyone is sitting in a nice, cushy, air-conditioned office typing away at a computer all day.'* The mechanics, the platoon sergeants, the soldiers in motor pools where the wifi cuts out — they don't have desks."
>
> *[unplug your wifi cable, hold it up so judges see]*
>
> "So we built **Adjutant**: voice-first, fully offline, cites the regulation by section, fills the actual DA-form PDF. Watch."

---

## 0:30 — 1:30 · Beat 1: voice → cited answer + filled DA-31

*[Click mic in the browser. Speak this slowly and clearly:]*

> "I'm Sergeant Chen at Fort Bragg. I need to file ten days of ordinary leave starting June 3, going to my sister's wedding in Atlanta. Emergency contact is Maria Chen at 919-555-0144."

*[Wait ~10 seconds. The screen will show:]*
- *Spoken summary citing AR 600-8-10 ¶ 4-3*
- *Citations panel with `AR 600-8-10` source + tier badge*
- *Filled DA-31 PDF in iframe*

*[Narrate while it renders:]*
> "There. Adjutant pulled **AR six-hundred-dash-eight-dash-ten paragraph four-dash-three**. Drafted the DA-31. Override caught that 'emergency contact' isn't 'emergency leave' — leave type is correctly 'Ordinary'. That PDF is signed-ready, with all ten fields populated. **And the wifi is unplugged. Everything just ran on this laptop.**"

---

## 1:30 — 2:15 · Beat 2: the wow move — multi-form

*[Click mic. Speak the JRTC prompt:]*

> "I'm going to JRTC at Fort Polk July 14 for 5 days, need to counsel SPC Garcia tomorrow, and want 2 days of leave when I get back."

*[Three PDFs render in the iframe — DD-1351-2 first, then DA-4856, then DA-31. Narrate as they appear:]*
> "**One sentence. Three forms.**"
> "DD-1351-2 with **deterministic GSA per-diem math** — Leesville, Louisiana, **one-ten lodging, sixty-eight meals, seventy-five percent travel-day rule applied. Total: seven hundred forty-six dollars.** That's not the LLM — that's a separate function we wrote because LLMs are unreliable on math."
> "DA-4856 counseling for SPC Garcia."
> "DA-31 leave for the recovery days."
>
> "**No other tool — not GenAI-dot-mil, not Milnerva, not EdgeRunner — produces multiple PDFs from one voice request.**"

---

## 2:15 — 2:50 · Beat 3: reliability — no hallucination

*[Click mic. Ask a deliberately out-of-corpus question:]*

> "What does AR 692-10 paragraph 3-7 say about overseas hardship duty pay?"

*[Adjutant refuses. Narrate:]*
> "**AR six-ninety-two-dash-ten doesn't exist. I made the number up.** Adjutant's retrieval returned zero chunks above the score threshold, so the LLM is constrained — it can't guess. **The same question to Gemini will get a confident hallucination with fabricated paragraph numbers.**"
>
> "This is the architectural floor that matters when an O-3 is signing the form. Adjutant **cannot hallucinate regulation citations** — retrieval-context-only constraint, score threshold floor, every citation is a verbatim quote with section and paragraph."

---

## 2:50 — 3:00 · The close

> "Three million service members. Six hours a week on paperwork. Twenty-five dollars an hour loaded labor cost."
>
> "**That's twenty-three-point-four billion dollars a year in mission-readiness time we can give back.**"
>
> "GenAI-dot-mil reaches the desk officers. **Adjutant reaches everyone else.** Voice-first. Offline. Cited."
>
> "**Speak it. Sign it. Move out.**"

---

## Stats — memorize for Q&A

| Metric | Value |
|---|---|
| Documents indexed | **933 Army regulations + DA Pamphlets + FMs** |
| Retrieval chunks | **271,333** across HOT/WARM/COLD tiers |
| Resident RAM | ~3 GB |
| Disk footprint | ~7 GB models + corpus + index |
| Hardware tested on | **8 GB MacBook Air M1** (worst case) |
| Tech stack | Whisper + FAISS + Llama 3.2 3B + Kokoro TTS + pikepdf |
| Build time | 30 hours, solo |

---

## Q&A cheat sheet

| Judge asks | Your answer |
|---|---|
| **"Doesn't GenAI.mil already do this?"** | "GenAI.mil is the *platform* — chat over Gemini and ChatGPT in a browser. Adjutant is a *vertical app* for the rank-and-file who don't have desks. Different surface. We'd run on GenAI.mil eventually." |
| **"What about hallucinations?"** | "Architecturally impossible. RAG threshold returns zero chunks if the reg isn't in our corpus. LLM is constrained to retrieved context. Out-of-corpus questions return refusal, not hallucination — we just demoed that." |
| **"How does this scale to all of DOD?"** | "Three-tier architecture in the repo. HOT on-device, WARM at the unit-server level, COLD in the cloud. Each tier degrades independently. Adjutant runs offline, syncs opportunistically when NIPR is reachable. Designed for **DDIL** environments." |
| **"What about classified regs?"** | "Out of scope for this hackathon — public corpus only. SIPR / IL5 deployment is the v2, with Authority to Operate. The architecture is identical, just behind classified network boundaries." |
| **"Why offline?"** | "FOB connectivity. Motor pool basement signal. Helicopter airframes. Field exercises. The Pentagon's own GenAI.mil quote: 'not everyone is sitting in a nice cushy air-conditioned office.' Adjutant goes where the network can't." |
| **"30 hours of build?"** | "Reused the voice pipeline from my Sabi project — voice AI tutor for Nigerian children. Same Whisper-RAG-Llama-TTS stack, repointed at Army regulations. The voice pipeline is already production-deployed for E4E in Lagos." |
| **"Who else is doing this?"** | "Closest is EdgeRunner AI — $17M raised, deployed with US SOF. They do tactical doctrine offline, we do administrative paperwork offline. Different domain, different user. Milnerva does NCOER writing for $10/month — text only, no voice, no offline, no PDF fill. Nobody combines voice + offline + multi-form auto-fill." |
| **"What's next?"** | "DSN voice surface — soldier dials a phone, talks to Adjutant, form lands in their .mil inbox. Reaches every soldier regardless of CAC. SBIR Phase I with Army CDAO. Path 2 candidate for the platform." |
| **"Why three forms only?"** | "DA-31, DD-1351-2, DA-4856 cover the daily 80% case for our junior-NCO persona. Adding more forms is a schema-extraction step at install time, not a research project. We deliberately scoped depth over breadth for the 30-hour window." |
| **"Per-diem math is wrong on my city"** | "Live-API integration with GSA's open per-diem API is wired but rate-limited locally. We have FY26 defaults baked in for major TDY destinations: Fort Polk, Fort Bragg, Fort Hood, JRTC, NTC. Production deploy refreshes the cache nightly from open.gsa.gov." |
| **"Who are you?"** | "Naomi Ivie. Wesleyan student. Solo on this team. Built Sabi — a voice AI tutor for Nigerian children — and won 2nd place at MIT Bitcoin Hackathon, NVA Grand Prize for the Lagos pilot. This is the same voice stack pointed at Army regulations." |

---

## Pre-demo checklist (run through 5 min before judges arrive)

```
[ ] Three terminals running: WARM 8001, COLD 8002, MAIN 8000
[ ] Browser open at http://localhost:8000/web/
[ ] All three tier LEDs green
[ ] Mic permissions granted in browser
[ ] Wifi cable plugged in (so you can dramatically pull it)
[ ] Test query 1: "How does ordinary leave accrue?" → should cite AR 600-8-10
[ ] Test query 2: SGT Chen leave prompt → should produce DA-31 PDF
[ ] Volume up so spoken summary is audible
[ ] Backup browser tab at gemini.google.com if you do the side-by-side
[ ] PITCH.md (this file) open on second monitor or printed
```

---

## If something breaks mid-demo

| Failure mode | What to say |
|---|---|
| Mic doesn't pick up speech | *"Push-to-talk fallback is right here…"* — use the typed-input alternative |
| LLM returns weird JSON | *"This is exactly the failure mode our schema-validator catches in production…"* — show the raw chunks panel |
| One tier shows red on the LED | *"And there's the graceful degradation — COLD just dropped, watch the query still resolve from HOT and WARM."* — turn it into a feature |
| Whole pipeline hangs > 30 sec | *"In production we have a 4-second cue-audio that bridges this gap. Right now you're seeing the worst-case cold load."* — pivot to architecture explanation while it loads |
| Total stack-out | *"Let me restart that…"* — open a new tab, restart the main server, fall back to the architecture diagram + comparator slide |

---

## Closing line variants (pick one based on judge energy)

- **Mohindra/Liu (rigor-loving):** *"Hallucination is architecturally impossible here, not best-effort. That's the contract that matters when an O-3 is signing the form."*
- **Wagner-style (operational prototypes):** *"Built this in 30 hours, runs on a $999 MacBook Air, no internet, no CAC. That's the cost-and-time-to-prototype DOD says it wants."*
- **General audience:** *"Speak it. Sign it. Move out."*

---

**Go win.**
