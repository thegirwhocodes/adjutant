# Adjutant — 5-minute demo script

**Goal:** Hit Novelty (25%), Tech Difficulty (25%), National Impact (25%), Problem-Solution Fit (25%) — all 10/10 — in five minutes flat.

**Setup before judges arrive:**
1. Laptop on stage with mic + speakers
2. Browser open at `http://localhost:8000`
3. Wifi cable PHYSICALLY plugged in (we'll yank it on cue)
4. Filled-PDF folder empty (no leftovers from rehearsal)
5. Have one printed page ready: the citation-trail screenshot in case a judge wants to inspect

---

## Beat 1 — The hook (45 sec)

> "Hi judges — I'm Naomi, team Charlie Mike. We built **Adjutant**.
>
> RAND Corporation: Army company leaders work 12.5-hour days — longer than 96% of all American workers. Less than a third of that time is on actual unit readiness. Why? The Modern War Institute at West Point documents that companies submit three to four dozen reports a month. The bureaucratic tail is gigantic.
>
> GenAI.mil rolled out to 1.2 million users in December. Great platform. But the people who feel the pain hardest — the mechanics, the platoon sergeants, the soldiers on FOBs where the wifi cuts out — don't have desks. They have phones. They have laptops in motor pools. Generative AI hasn't reached them yet.
>
> Adjutant is voice-first, fully offline AI for that rank-and-file. Watch."

---

## Beat 2 — The wifi cable kill shot (15 sec)

**Action:** Yank the ethernet cable. Turn off wifi visibly in the menu bar.

> "I'm now disconnected. No GenAI.mil, no ChatGPT, no Gemini. Just the laptop."

The browser badge flips from `Online` to `OFFLINE — still working`.

---

## Beat 3 — Leave-request flow (90 sec)

**Action:** Hold the mic button. Speak naturally:

> *"I'm Sergeant Chen at Fort Bragg. I need to file ten days of ordinary leave starting June 3, going to my sister's wedding in Atlanta. Emergency contact is Maria Chen at 919-555-0144."*

Release the mic. Watch the screen.

What judges see (~6 seconds):
1. Transcript appears: *"You said: 'I'm Sergeant Chen at Fort Bragg…'"*
2. Spoken reply (TTS audio): *"I've drafted your DA-31 for ten days of ordinary leave starting June 3 to June 12. Per AR 600-8-10, paragraph 4-3, ordinary leave accrues at 2.5 days per month. Your chain of command still has approval authority. Email it to your S1 when ready."*
3. Citations panel populates with two AR 600-8-10 chunks, sections labeled
4. **Filled DA-31 PDF appears in the iframe** — name, dates, days_requested, leave address all populated

> "That's a real DA-31. I can sign it and send it to S1."

---

## Beat 4 — TDY flow (90 sec)

**Action:** Hold mic again:

> *"I also need to attend the JRTC mission rehearsal at Fort Polk for five days starting July 14. Home station Fort Bragg."*

What judges see:
1. Transcript appears
2. Spoken reply: *"DD-1351-2 drafted. Per the Joint Travel Regulations, lodging at Fort Polk uses the Leesville, Louisiana per-diem rate — 110 dollars per day lodging, 68 dollars M&IE. Five days, 75% on travel days, total estimated reimbursement 822 dollars."*
3. Citations panel: 2 JTR chunks
4. Filled DD-1351-2 with per-diem math done, dates, locations all populated

> "Two forms, two regulations, eight seconds of speech."

---

## Beat 5 — The hallucination test (60 sec)

**Action:** Hold mic:

> *"What does AR 27-10 say about court-martial convening authority?"*

(AR 27-10 is NOT in our corpus.)

What judges see:
1. Spoken reply: *"I don't have AR 27-10 in my regulation corpus. Check with your S1 or pull it directly from armypubs.army.mil. I won't guess on regulation language."*
2. No citations
3. No PDF generated

> "That's the most important part of this build. EdgeRunner has 30 billion tokens of training. Milnerva uses GPT-4. Both can hallucinate citations. Adjutant is constrained to retrieved context only — if it's not in the corpus, it refuses. That matters when you're handing a soldier a form their commander will sign."

---

## Beat 6 — Close (60 sec)

> "Stack: Whisper STT, FAISS over Army Pubs Directorate plus the JTR plus GSA per-diem rates, Llama 3.1 8B in Ollama, pdfplumber populating the actual fillable DA-31 and DD-1351-2 from armypubs.army.mil. Everything runs on this laptop. No cloud. No CAC. No ITAR. Public corpus only.
>
> 3 million service members, 6 hours a week of paperwork, $25 an hour loaded — that's $23 billion a year of recoverable mission-readiness time. Adjutant is the admin you wish your S1 had time to do.
>
> Charlie Mike. We continue the mission. Thank you."

**Action:** Plug the wifi cable back in. The browser badge flips back to `Online`. Smile.

---

## If a judge asks…

**"Doesn't GenAI.mil already do this?"**
> GenAI.mil is the chat platform. Adjutant is a vertical app on top — like saying Excel exists, why build TurboTax. GenAI.mil hosts general-purpose Gemini. It's web-based, requires CAC, requires connectivity. Nobody on it has shipped voice-first form-output for the rank-and-file. Adjutant could run *on* GenAI.mil eventually as a tenant app.

**"Doesn't EdgeRunner already do this?"**
> EdgeRunner does tactical doctrine for SOF. They're trained on 30 billion tokens of military history, tactics, philosophy. Different user, different domain. Their April 2026 announcement called it a 'digital adjutant' but it doesn't fill admin forms. We're complementary.

**"What about classified data?"**
> Out of scope by design. SCSP rules require unclassified public corpora. The same architecture deployed on a SIPR-side machine with classified ARs would just need the corpus swapped — code unchanged.

**"How do you handle PDF field-name drift?"**
> `scripts/extract_form_schemas.py` re-reads the blank PDFs at install and prints the AcroForm field map. We caught the mismatch between the public DA-31 (which uses `topmostSubform[0].Page1[0].FormalName[0]`-style names) and our schema during ingestion.

**"What if the leave balance the soldier states is wrong?"**
> Adjutant doesn't write back to IPPS-A. We generate the form. The S1 still verifies balance and approves. We remove the friction, not the human in the loop.

**"What's the latency end-to-end on this laptop?"**
> ~6 seconds from end-of-speech to filled PDF. Whisper int8 on M2 CPU is the bottleneck (~2s). Llama 3.1 8B Q4 in Ollama is ~3s for a 400-token reply. PDF fill is sub-100ms.

**"Could a UPL doing urinalysis use this?"**
> Yes — same architecture, swap the corpus to AR 600-85 + DoDI 1010.16 and add DD-2624 to the form registry. Roughly an afternoon.

---

## Checklist — 10 minutes before walking on stage

- [ ] Browser cache cleared
- [ ] `filled_forms/` directory emptied
- [ ] Laptop fan quiet — close everything except Ollama and the FastAPI server
- [ ] Mic input level checked (you'll be in a noisy hackathon room)
- [ ] Wifi cable plugged in
- [ ] Backup demo video on a USB stick in case the laptop melts
- [ ] One sip of water before Beat 1
- [ ] Phone face down
