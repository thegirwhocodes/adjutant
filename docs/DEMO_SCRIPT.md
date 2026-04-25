# Adjutant — 5-minute demo script

**Goal:** Hit Novelty (25%), Tech Difficulty (25%), National Impact (25%), Problem-Solution Fit (25%) — all 10/10 — in five minutes flat.

**Setup before judges arrive:**
1. Laptop on stage with mic + speakers
2. Browser open at `http://localhost:8000/web/`
3. **Wifi already off. Ethernet cable already unplugged.** Browser badge already reads `OFFLINE — still working`. Judges see this from the moment they look up.
4. Filled-PDF folder empty (no leftovers from rehearsal)
5. **Second device ready** — phone or tablet logged into `https://gemini.genai.mil` in a browser, on the same question we'll ask Adjutant. (Or laptop split-screen if a second device is impossible.) This is the side-by-side weapon.
6. One printed page ready: the citation-trail screenshot in case a judge wants to inspect

---

## Beat 1 — The cold open (45 sec)

**Action:** Walk to the laptop. Don't touch the wifi — it's already disconnected. Pick up the unplugged ethernet cable, hold it up so judges can see it dangle.

> "Hi judges — I'm Naomi, team Charlie Mike. Before I say anything, I want you to know **this laptop has been offline since I walked into this room.** Wifi off. Ethernet unplugged. The little red badge on the screen says it. Everything you're about to see runs on this laptop, this CPU, this disk.
>
> RAND Corporation: Army company leaders work 12.5-hour days — longer than 96% of all American workers. Less than a third of that time is on actual unit readiness. The Modern War Institute documents that companies submit three to four dozen reports a month. The bureaucratic tail is gigantic.
>
> GenAI.mil rolled out to 1.2 million users in December. Great platform. Lives in the cloud. Requires CAC, requires connectivity, requires you to be at a desk. But the people who feel the pain hardest — the mechanics in motor pools, the platoon sergeants between formations, the soldiers on FOBs where the wifi cuts out — don't have desks.
>
> So we built **Adjutant**: voice-first, fully offline, cites the regulation by section, fills the actual DA-31 PDF. Watch."

**Why this opening lands:** the cable in your hand is the proof. By second 30 the judges have already absorbed the thing GenAI.mil and ChatGPT Voice cannot do. You've spent zero words selling "we're voice" — you're showing.

---

## Beat 2 — Leave-request flow (90 sec)

**Action:** Hold the mic button. Speak naturally:

> *"I'm Sergeant Chen at Fort Bragg. I need to file ten days of ordinary leave starting June 3, going to my sister's wedding in Atlanta. Emergency contact is Maria Chen at 919-555-0144."*

Release the mic. Watch the screen.

What judges see (~6 seconds):
1. Transcript appears: *"You said: 'I'm Sergeant Chen at Fort Bragg…'"*
2. Spoken reply (TTS audio): *"I've drafted your DA-31 for ten days of ordinary leave starting June 3 to June 12. Per AR 600-8-10, paragraph 4-3, ordinary leave accrues at 2.5 days per month. Your chain of command still has approval authority. Email it to your S1 when ready."*
3. Citations panel populates with verbatim AR 600-8-10 text, sections labeled
4. **Filled DA-31 PDF appears in the iframe** — name, dates, days_requested, leave address, emergency contact all populated

> "That's a real DA-31. AcroForm fields, fillable, signed-ready. I can print it, route it, email it to S1. ChatGPT Voice will tell you about leave forms. It cannot hand you the form."

---

## Beat 3 — TDY flow (75 sec)

**Action:** Hold mic again:

> *"I also need to attend the JRTC mission rehearsal at Fort Polk for five days starting July 14. Home station Fort Bragg."*

What judges see:
1. Transcript appears
2. Spoken reply: *"DD-1351-2 drafted. Per the Joint Travel Regulations, lodging at Fort Polk uses the Leesville, Louisiana per-diem rate — 110 dollars per day lodging, 68 dollars M&IE. Five days, 75 percent on travel days, total estimated reimbursement 822 dollars."*
3. Citations: 2 JTR chunks
4. Filled DD-1351-2 with per-diem math done, dates, locations populated

> "Two forms, two regulations, eight seconds of speech. The math isn't done by the LLM — LLMs are bad at math. The numbers come from a deterministic GSA per-diem function. The LLM extracts the city and dates; Python does the arithmetic."

---

## Beat 4 — The GenAI.mil side-by-side (75 sec) ⭐ THE KILL SHOT

**Action:** Hold up the second device (phone/tablet) showing `https://gemini.genai.mil` open. The same question is about to go to both systems.

> "Now — the obvious question. GenAI.mil exists. 1.2 million users. Hosts Gemini, Claude, ChatGPT. Why isn't this enough? Watch."

**Action:** Type the same question into GenAI.mil's Gemini box on the second device, in front of judges, live:

> *"What does AR 27-10 say about court-martial convening authority?"*

(AR 27-10 is NOT in Adjutant's corpus. The question is also obscure enough that hosted Gemini will likely confabulate.)

**What judges see on the GenAI.mil device:**
A confident, fluent paragraph citing AR 27-10 paragraph numbers. **It will be at least partially hallucinated.** Read 2–3 sentences out loud, slowly, so judges hear the authority in the voice and the specificity of the cited paragraph numbers.

**Action:** Now ask Adjutant the same question on the laptop. Hold mic:

> *"What does AR 27-10 say about court-martial convening authority?"*

What judges see on Adjutant:
- Spoken reply: *"I don't have AR 27-10 in my regulation corpus. Check with your S1 or pull it directly from armypubs.army.mil. I won't guess on regulation language."*
- No citations
- No PDF

> "GenAI.mil's Gemini will read you a confident paragraph that mixes correct rules with hallucinated paragraph numbers. **Every voice AI does this.** When the output is read aloud in an authoritative voice with no source link to verify, that's worse than text. Soldiers have been disciplined relying on this.
>
> Adjutant is architecturally incapable of hallucinating. Every answer is constrained to retrieved regulation text. Out-of-corpus question? It refuses. That's not a chatbot wrapper — that's a different reliability contract. That's what matters when an O-3 is going to sign the form."

**If you don't want to risk a live GenAI.mil demo** (CAC issues, slow wifi on the second device, hostile network), have a pre-screenshot ready: "Here's GenAI.mil's response to the same question, captured this morning — note the cited paragraph number does not exist in AR 27-10."

---

## Beat 5 — Close (45 sec)

> "Stack: Whisper STT with military-jargon priming, FAISS over Army Pubs Directorate plus the JTR plus GSA per-diem rates, Llama 3.1 8B in Ollama, pypdf populating the actual fillable DA-31, DD-1351-2, and DA-4856 from armypubs.army.mil. Everything runs on this laptop. No cloud. No CAC. No ITAR. Public corpus only.
>
> Three million service members, six hours a week of paperwork, twenty-five dollars an hour loaded — that's twenty-three billion dollars a year of recoverable mission-readiness time. Three deployment surfaces, one backend: laptop today, mobile web mid-term, DSN voice line long-term. The DSN line is the only path that reaches every soldier regardless of OPSEC posture.
>
> Adjutant is the admin you wish your S1 had time to do.
>
> Charlie Mike. We continue the mission. Thank you."

**Action:** Plug the wifi cable back in. Browser badge flips to `Online`. Smile.

---

## If a judge asks…

**"Isn't this just a ChatGPT Voice clone?"**
> Conversationally similar — different reliability contract. ChatGPT Voice sends every word the soldier says to OpenAI's servers; Adjutant transcribes locally with Whisper, no audio leaves the device. ChatGPT Voice will hallucinate AR paragraph numbers in an authoritative tone with no link to verify; Adjutant refuses out-of-corpus. ChatGPT Voice produces text describing what your DA-31 might say; Adjutant produces the actual fillable PDF, signed-ready. Voice is the interface — the product is the offline, citation-grounded form-fill.

**"Doesn't GenAI.mil already do this?"**
> GenAI.mil is the chat platform. Adjutant is a vertical app — Excel exists, that doesn't kill TurboTax. GenAI.mil hosts general-purpose Gemini, web-based, requires CAC, requires connectivity. Nobody on it has shipped voice-first form-output for the rank-and-file. Adjutant could ship as a tenant app on GenAI.mil for connected use, AND ship as the offline standalone for motor-pool / FOB use. Same backend, different surfaces.

**"Doesn't EdgeRunner already do this?"**
> EdgeRunner targets tactical doctrine for SOF — 30 billion tokens of military history, tactics, philosophy. Different user, different corpus. Their April 2026 announcement called it a 'digital adjutant' but it doesn't fill admin forms. We're complementary — they handle field doctrine, we handle the bureaucratic tail.

**"How does SGT Chen actually use this when she's not at a laptop?"**
> Three deployment surfaces, one backend. **Today:** any government laptop on NIPRNet hits an Adjutant instance — same UX as GenAI.mil. **Mid-term:** mobile web app accessed from her personal smartphone in barracks behind CAC auth. **Long-term — the moat:** DSN voice line. She picks up any landline, dials Adjutant, talks to it, the form lands in her .mil inbox. That's the only surface that reaches every soldier regardless of OPSEC. It's a port of the Sabi voice pipeline — already shipped at scale teaching Nigerian children to read over phone calls — onto FedRAMP-compliant gov telephony.

**"What about classified data?"**
> Out of scope by design. SCSP rules require unclassified public corpora. The same architecture on a SIPR-side machine with classified ARs swaps the corpus, code unchanged. The Whisper, Llama, FAISS layer doesn't care.

**"How do you handle PDF field-name drift?"**
> `scripts/extract_form_schemas.py` re-reads the blank PDFs at install and prints the AcroForm field map. We caught the mismatch between the public DA-31 — which uses `topmostSubform[0].Page1[0].FormalName[0]`-style names — and our schema during ingestion. The download script also fell over once when armypubs.army.mil killed an eforms URL; we documented the alternate api.army.mil mirror in the commit.

**"What if the leave balance the soldier states is wrong?"**
> Adjutant doesn't write back to IPPS-A. We generate the form. The S1 still verifies balance and approves. We remove the friction, not the human in the loop. Future v2 with PKI passthrough could pull live from IPPS-A; that's a 12-24 month ATO, not this hackathon.

**"What's the latency end-to-end on this laptop?"**
> ~6 seconds from end-of-speech to filled PDF. Whisper int8 on M2 CPU is the bottleneck (~2s). Llama 3.1 8B Q4 in Ollama is ~3s for a 400-token reply. PDF fill is sub-100ms.

**"Could a UPL doing urinalysis use this?"**
> Yes — same architecture, swap the corpus to AR 600-85 plus DoDI 1010.16 and add DD-2624 to the form registry. Roughly an afternoon. Same for property accountability — drop AR 735-5 in, add the AVCATT memo template.

**"What's your business model post-hackathon?"**
> Three paths. (1) GovCon: license to a SI primary (Booz/Leidos/Accenture Federal) as a vertical AI app for their existing IL5 deployments. (2) Direct: SBIR Phase I → Phase II → III with Army CDAO as the customer. (3) Open-source the engine, sell hosted/managed. The DSN voice surface is the moat for path 2.

---

## Checklist — 10 minutes before walking on stage

- [ ] Browser cache cleared
- [ ] `filled_forms/` directory emptied
- [ ] Laptop fan quiet — close everything except Ollama and the FastAPI server
- [ ] Mic input level checked (you'll be in a noisy hackathon room)
- [ ] **Wifi already OFF. Ethernet UNPLUGGED. Badge reads OFFLINE.**
- [ ] Cable in your pocket / on the table — visible prop
- [ ] Second device (phone or tablet) on `gemini.genai.mil`, logged in, ready to type into
- [ ] Pre-captured GenAI.mil hallucination screenshot on USB stick as backup
- [ ] Backup demo video on USB stick in case the laptop melts
- [ ] One sip of water before Beat 1
- [ ] Phone face down (your demo phone, not your personal phone)
