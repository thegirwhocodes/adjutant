# SCSP Hackathon — "DA-Form Concierge" Competitive Scan
## Does a voice-first AI assistant for Army Regulations + DA-Form auto-fill already exist?

**Compiled:** April 25, 2026 — for SCSP Hackathon Phase 1 (Apr 25–26)
**Question:** Is there prior art that would kill the concept, and where exactly is the unfilled slice?

---

## TL;DR

**Nobody has built the exact thing.** The closest prior art is text-based, online, and writing-focused — not voice-first, not offline, not form-filling. Six adjacent products exist, none of them combine the four wedges that make the SCSP-winning version novel.

**The four wedges that are unoccupied:**

1. **Voice-first I/O** (every existing tool is text/web)
2. **Offline / on-device** (one competitor exists here — Edgerunner — but it's general doctrine, not forms)
3. **Auto-fills a real DA Form PDF** (zero competitors do this; everyone stops at "writes a memo")
4. **Single-persona end-to-end flow** (all competitors are general writing copilots; none take a junior NCO from "I need leave June 3" all the way to a signed DA-31 PDF)

**Verdict for SCSP:** the concept is clean, the rubric's "Novelty (25%)" is defensible, and the "Technical Difficulty (25%)" is genuinely hard because nobody has wired voice → RAG → form-fill before. Build it.

---

## What exists today (the honest competitive map)

### Tier 1 — Government-owned platforms (the giants)

| Product | What it is | Voice? | Offline? | Form-fill? | Notes |
|---|---|---|---|---|---|
| **GenAI.mil** | DoD-wide platform serving 3M users. Hosts Google Gemini, xAI Grok, OpenAI ChatGPT, Anthropic Claude. CUI/IL5 certified. Use cases: summarizing policy, drafting correspondence, generating compliance checklists, automating routine staff processes. Used to create 100K agents (DefenseScoop, Apr 23, 2026). | No | No | Generic — no specific DA-form auto-fill | The platform you're building *for*, not *against*. Your hackathon project would presumably live here eventually. |
| **CamoGPT** (Army) | 75,000 users. Text chatbot. Started fall 2023, deployed spring 2024. Optimizes equipment maintenance, logistics, supply chain. Transitioning to a program of record + an Army-specific LLM tuned for Army acronyms and doctrine. | No | No | No | Survives alongside GenAI.mil. General chat, not form-filling. |
| **Ask Sage / Army Enterprise LLM Workspace** | $49M IDIQ contract ceiling. CUI-accredited SaaS. Powers Army drafts of press releases, personnel-description reclassification (300K updated in a week, would have been 50K manual hours). IL5. | No | No | No | Same shape as ChatGPT-for-Army. Not voice. Not form-filling. |
| **NIPRGPT** (Air Force) | AFRL-built. Phase-out announced. Army blocked it Apr 17, 2025 over data governance. | No | No | No | Effectively dying. |
| **VictorBot** (Army's "first chatbot for troops") | Reddit-style peer forum + LLM. Trained on 500+ repos including Ukraine and Iran live conflict data. Mission-specific knowledge retrieval. | No | No | No | Knowledge-retrieval only, not admin/forms. |

**Takeaway:** The DoD already has the *platforms*. None of them have the *vertical voice-first form-filling app* sitting on top.

### Tier 2 — Veteran-built writing assistants (the closest commercial competitors)

| Product | What it does | Voice? | Form-fill? | Pricing | Source |
|---|---|---|---|---|---|
| **Milnerva** | AI writing assistant for NCOERs, OERs, counseling statements, awards, memos, citations. 20K+ active users, 24K+ docs generated. Built by a former soldier. iOS app shipped, Android in dev. Uses GPT-4.1 + o3. | **No** (text only) | **No** (writes prose, doesn't populate PDFs) | Free tier; **$10/mo** paid; 7-day trial | milnerva.com |
| **WriteMyNCOER.com** | NCOER and OER writing AI. | No | No | Unknown | writemyncoer.com |
| **RapidEPR** | Air Force EPR (Enlisted Performance Report) writing assistant. | No | No | Unknown | rapidepr.com |
| **SergeantAI** (WWT demo) | RAG chatbot trained ONLY on AR 670-1 (uniforms) + DA Pam 670-1. Persona: enthusiastic sergeant. Web app, mobile + desktop responsive. | **No** (text only) | **No** | Unknown — appears to be a WWT internal/demo proj | wwt.com/blog |
| **Army Writing Assistant** (yeschat.ai) | GPT wrapper for Army writing. Free. | No | No | Free | yeschat.ai |
| **Military AI Writer** (yeschat.ai), **Simplified.com Defence Writer** | GPT wrappers. | No | No | Free / freemium | various |

**Common shape:** Web app or mobile app. Text input. Output is *prose for documents* — a memo body, an evaluation bullet, a counseling statement. **None of them open a fillable PDF and populate the actual fields.** None of them are voice. None work offline.

### Tier 3 — Offline / on-device military LLMs (the one real competitor in this lane)

**EdgeRunner AI** — Seattle, $12M raised May 2025, founded by former Army officer. April 2026: announced **EdgeRunner 20B**, GPT-5-class performance entirely on-device, air-gapped, runs on 8GB VRAM laptop/tablet/phone. Trained with Meta on 30B tokens of military doctrine, history, tactics, training manuals, field manuals, philosophy. **Live on an overseas deployment with US Special Operations.** Multimodal — listens, transcribes meetings, answers questions, processes images. Models: EdgeRunner Tactical-7B, EdgeRunner 20B.

| Dimension | EdgeRunner | Your hackathon project |
|---|---|---|
| Offline | **Yes** (its whole pitch) | Yes (your wedge too) |
| Voice | Yes — listens & transcribes | Yes — primary I/O |
| Domain | Tactical / doctrine / general military Q&A | **Junior-enlisted admin paperwork** |
| Form auto-fill | No | **Yes** — DA-31, DD-1351, DA-4856 |
| Persona | Generic warfighter | **Specific: junior NCO planning TDY** |
| Build time | 18 months + $12M | 30 hours (because Sabi stack) |

**EdgeRunner is your strongest competitor and your best validation simultaneously.** They prove (a) the offline-LLM-for-military thesis is real and venture-backed, and (b) the lane is wide open below them — they aim for tactical doctrine; the bureaucratic-tail-of-paperwork niche is uncovered. SCSP's GenAI.mil track brief literally says: *"the most immediate friction for the three million men and women in uniform often occurs in the administrative trenches"* and *"the bureaucratic tail of the military significantly drains mission readiness."* That's the open ground EdgeRunner doesn't cover.

### Tier 4 — Generic AI form-fillers (zero military specialization)

Instafill.ai, AutoFillPDF, AI Form Fill, Vaadin's AI Form Filler, PDFfiller, CocoDoc, FormSwift, PDFRun, DocHub, PDF Guru. All do generic PDF auto-population. **None are tuned to DoD forms, none cite AR/JTR, none have a voice front-end, none reason about regulation compliance.** They'd happily fill a DA-31 if you handed them the JSON, but they don't *generate* the JSON from a soldier's spoken request, and they don't validate against AR 600-8-10.

### Tier 5 — Adjacent: doctrine writing, drone voice, tactical voice

- **Army doctrine writers** are using GenAI tools to update Field Manuals (Combined Arms Doctrine Directorate, Fort Leavenworth — Feb 2026). **Authoring direction**, not soldier-facing assistance.
- **Primordial Labs Anura** — voice-controlled drones, 8,000 licenses to "transformation in contact" brigades (March 2025). Voice for *combat platforms*, not paperwork.
- **Army SBIR voice-commanded autonomous maneuver** — research-stage. Combat vehicles, not admin.

---

## Gap matrix — where is the win?

| Capability | GenAI.mil | CamoGPT | Ask Sage | Milnerva | SergeantAI | EdgeRunner | **Your build** |
|---|---|---|---|---|---|---|---|
| Voice I/O (mic + speaker) | ❌ | ❌ | ❌ | ❌ | ❌ | Listens (1-way) | **✅ Conversational** |
| Offline / air-gapped | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | **✅** |
| Cites Army Regs / FM by section | Generic | Generic | Generic | Partial | ✅ (one AR) | Generic | **✅ multi-AR + FM** |
| Auto-fills a real DA-form PDF | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **✅** |
| End-to-end persona flow (NCO → form out) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **✅** |
| Hands-free (motor-pool / FOB use case) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **✅** |
| 30-hour buildable demo | n/a | n/a | n/a | n/a | n/a | n/a | **✅** (Sabi stack) |

**Every other product is at most 2 of the 7 columns. Your build hits all 7.** That is your "Novelty" + "Technical Difficulty" score.

---

## What this means for SCSP rubric scoring

| Criterion (25% each) | Your defense |
|---|---|
| **Novelty** | "Every existing Army AI tool — CamoGPT, Milnerva, Ask Sage, SergeantAI — is a text-based web chat. EdgeRunner is offline but tactical-doctrine focused. Nobody has built voice-first, offline, multi-AR-grounded, form-output paperwork assistance. We're the first to wire all four wedges together." |
| **Technical Difficulty** | "Five hard pieces, all working offline on one laptop: Whisper STT (Nigerian-English-tuned baseline we'll re-tune), local FAISS vector store over Army Pubs Directorate PDFs, local Llama 3.1 8B via Ollama, Chatterbox TTS, and pdfplumber field-mapping for DA-31 / DD-1351 PDFs. The wifi-disconnect demo is the kill shot." |
| **National Impact** | "3M service members × 6 hrs/wk on paperwork × $25/hr loaded = $23B/yr in recovered mission-ready labor. SCSP's own brief calls this 'the bureaucratic tail [that] drains mission readiness.' We address it directly." |
| **Problem-Solution Fit** | "One persona end-to-end: a junior NCO at Fort Bragg planning TDY. Voice request → JTR per-diem lookup → DA-31 + DD-1351 auto-filled → emailed to S-1. The judges are SMEs (Liu, Mohindra at Boston) — they'll test for hallucination on regulation citations and we cite section + paragraph verbatim." |

---

## What WILL get raised by judges (and how to answer)

**Q: "Doesn't GenAI.mil already do this?"**
A: GenAI.mil is the *platform*. Our project is a *vertical app* on top — like saying "Excel exists, so why build TurboTax?" GenAI.mil hosts general-purpose Gemini/ChatGPT chat. Nobody on it has shipped a voice-first form-filling soldier-facing app. We'd run *on* GenAI.mil eventually.

**Q: "EdgeRunner already raised $12M for offline military AI."**
A: EdgeRunner does tactical doctrine for SOF. We do the bureaucratic tail for the rank-and-file. Different user, different domain. Their 30B-token training corpus is military history, tactics, philosophy — not the JTR/AR/FM admin corpus and not form-PDF schemas. We'd be a complementary layer.

**Q: "Milnerva has 20K users already."**
A: Milnerva is a writing copilot — produces prose for memos and evaluations. We generate signed PDFs from spoken intent. Different deliverable, no overlap.

**Q: "What about hallucination on regulation citations?"**
A: RAG retrieval surfaces verbatim quotes with section + paragraph (e.g., "AR 600-8-10, para 4-3"). The LLM is constrained to answer only with retrieved context. We unplug wifi during the demo to show the local FAISS + local Llama work without any external API.

**Q: "Voice-only doesn't work in a noisy motor pool."**
A: Whisper handles 60+ dB noise floor. We demo it. Also: voice is the *primary* mode but we ship a fallback typed input — same pipeline.

---

## Open angles you might want to flag in the README

- **Scope of forms in 30 hours:** pick **3–5 forms max** — DA-31 (leave), DD-1351-2 (travel voucher), DA-4856 (counseling), DA-2823 (sworn statement), DA-3349 (medical profile). Cover the daily 80%, leave the long tail.
- **Corpus to seed FAISS:** AR 600-8-10 (leave), JTR (travel/per diem), GSA per-diem rates, FM 6-22 (leadership), DA Pam 600-25 (NCO Guide). All public PDFs at armypubs.army.mil.
- **Per-diem math:** FY26 rates published — $110 lodging / $68 M&IE per day default. Hard-code the table, layer GSA city overrides via local JSON.
- **The wow moments to script in the demo:**
  1. Disconnect wifi visibly. "Watch — no internet."
  2. Ask in plain English: *"I need to file leave for ten days starting June 3 to visit family in Atlanta."*
  3. AI replies in Sabi-cloned voice, cites AR 600-8-10 para 4-3 verbatim, asks 1 clarifying question (POC during leave).
  4. Generates filled DA-31 PDF on screen.
  5. Same flow but TDY: pulls JTR per-diem for Atlanta, calculates 4-day trip, fills DD-1351-2.
- **Out-of-scope:** classified material, ITAR-controlled tech, anything requiring CAC auth. SCSP rule explicitly: only unclassified / publicly shareable corpora.

---

## Bottom line

You are not duplicating an existing product. You are **occupying the only unfilled cell in a 7-column gap matrix**, on a corpus the strongest offline competitor (EdgeRunner) deliberately doesn't address, with a build that you can finish in 30 hours because Sabi already gave you the voice + RAG + Ollama scaffolding.

The risk isn't novelty — there's plenty. The risk is **execution polish in 30 hours** and **finding a junior-enlisted or veteran teammate Saturday morning** who can pressure-test the persona flow.

Sources at the end of the dossier.

---

## Sources

**Government / DoD platforms:**
- [GenAI.mil DOW launch announcement](https://www.war.gov/News/Releases/Release/Article/4354916/the-war-department-unleashes-ai-on-new-genaimil-platform/)
- [Pentagon rolls out GenAI platform — Breaking Defense Dec 2025](https://breakingdefense.com/2025/12/pentagon-rolls-out-genai-platform-to-all-personnel-using-googles-gemini/)
- [DOD large-scale rollout — DefenseScoop](https://defensescoop.com/2025/12/09/genai-mil-platform-dod-commercial-ai-models-agentic-tools-google-gemini/)
- [Pentagon uses GenAI.mil to create 100K agents — DefenseScoop Apr 23 2026](https://defensescoop.com/2026/04/23/pentagon-uses-genai-mil-to-create-agents/)
- [Bringing ChatGPT to GenAI.mil — OpenAI](https://openai.com/index/bringing-chatgpt-to-genaimil/)
- [ChatGPT available to 3M military users — Breaking Defense Feb 2026](https://breakingdefense.com/2026/02/chatgpt-will-be-available-to-3-million-military-users-on-genai-mil/)
- [Army's CamoGPT not phased out — DefenseScoop Jan 27 2026](https://defensescoop.com/2026/01/27/army-camogpt-dod-genai-mil/)
- [CAMO + NIPR GPT integration — Army.mil](https://www.army.mil/article/283601/enhancing_military_operational_effectiveness_through_the_integration_of_camo_and_nipr_gpt)
- [Army CIO reining in AI use cases — DefenseScoop Aug 2025](https://defensescoop.com/2025/08/12/army-artificial-intelligence-camogpt-use-cases-cio-leonel-garciga/)
- [Army Enterprise LLM Workspace — Army.mil](https://www.army.mil/article/285537/army_launches_army_enterprise_llm_workspace_the_revolutionary_ai_platform_that_wrote_this_article)
- [Ask Sage Department of War](https://www.asksage.ai/who-we-serve/department-of-war/)
- [Ask Sage $10M Pentagon AI deal — Business News Today](https://business-news-today.com/ask-sage-lands-10m-pentagon-ai-deal-to-power-secure-generative-models-across-u-s-army-and-dod/)
- [VictorBot Army's first AI chatbot — IBTimes UK](https://www.ibtimes.co.uk/us-army-ai-chatbot-victorbot-1791250)
- [Army doctrine writers embrace AI — DOW News](https://www.war.gov/News/News-Stories/Article/Article/4409667/army-doctrine-writers-embrace-ai-to-speed-knowledge-to-force/)
- [Army says it's using AI for doctrine — DefenseScoop Feb 2026](https://defensescoop.com/2026/02/19/army-ai-doctrine-writing-artificial-intelligence-tools/)
- [Army AI/ML officer career path — Army.mil](https://www.army.mil/article/289843/army_establishes_new_ai_machine_learning_career_path_for_officers)

**Veteran writing tools:**
- [Milnerva](https://www.milnerva.com/)
- [WriteMyNCOER](https://writemyncoer.com/)
- [RapidEPR](https://rapidepr.com)
- [SergeantAI / WWT chatbot for AR 670-1](https://www.wwt.com/blog/can-a-chatbot-help-you-stay-within-us-army-regulations)
- [Army Writing Assistant — yeschat.ai](https://www.yeschat.ai/gpts-9t557b1Bh0n-Army-Writing-Assistant)
- [Military AI Writer — yeschat.ai](https://www.yeschat.ai/gpts-9t56Me4r8b7-Military-AI-Writer)
- [Simplified Defence Writer](https://simplified.com/ai-writer/defence)

**Offline military LLMs:**
- [EdgeRunner AI homepage](https://www.edgerunnerai.com/)
- [EdgeRunner Product page](https://www.edgerunnerai.com/product)
- [EdgeRunner GPT-5-level performance Apr 2026](https://www.edgerunnerai.com/news/edgerunner-achieves-gpt-5-level-performance-in-key-military-tasks-while-running-locally-on-device)
- [EdgeRunner Digital Adjutant — DefensePost Apr 6 2026](https://thedefensepost.com/2026/04/06/ai-offline-assistant-edgerunner/)
- [EdgeRunner $12M Series A — GeekWire May 2025](https://www.geekwire.com/2025/seattle-startup-edgerunner-ai-raises-12m-to-help-military-use-ai-without-the-internet/)
- [Madrona EdgeRunner investment](https://www.madrona.com/edgerunner-ai-air-gapped-on-device-agents/)
- [Former Army officer offline AI — Fox Business](https://www.foxbusiness.com/technology/former-army-officer-develops-offline-ai-military-use-pentagon-funds-tech-giants)

**DA Form / regulations references:**
- [AR 600-8-10 Leaves and Passes (Hawaii hosted)](https://home.army.mil/hawaii/9217/4163/6676/AR_600-8-10_Leaves_and_Passes.pdf)
- [USFK Reg 600-8-10](https://www.usfk.mil/Portals/105/Documents/Publications/Regulations/USFK-Reg-600-8-10-Leaves-and-Passes-1.pdf)
- [DA 31 fillable form (army.mil)](https://home.army.mil/riley/9415/4456/0604/DA31_Leave_Form_PDF_Fillable.pdf)
- [Joint Travel Regulations June 2025 PDF](https://api.army.mil/e2/c/downloads/2025/06/10/0da05172/jtr-june-2025.pdf)
- [Per Diem — Defense Travel Management Office](https://www.travel.dod.mil/Travel-Transportation-Rates/Per-Diem/)
- [JTR Computations and Examples](https://www.travel.dod.mil/Policy-Regulations/Joint-Travel-Regulations/Computations-and-Examples/)
- [2026 Military Per Diem Rates — Military.com](https://www.military.com/benefits/military-pay/military-travel-and-per-diem-rates.html)
- [Army Publishing Directorate](https://armypubs.army.mil/)

**Adjacent voice / SBIR / drone:**
- [Voice-controlled drones Primordial Labs Anura — Defense News Mar 2025](https://www.defensenews.com/industry/techwatch/2025/03/04/voice-controlled-drones-a-military-game-changer-primordial-labs-says/)
- [AI/ML voice-commanded autonomous maneuver — Army SBIR](https://armysbir.army.mil/topics/aiml-enabled-voice-commanded-autonomous-maneuver-ground-combat-vehicles/)

**Ecosystem / market:**
- [Meet the startups building military-specific AI — Defense One Mar 2026](https://www.defenseone.com/technology/2026/03/meet-startups-trying-build-military-specific-ai/411969/)
- [Startup debuts agentic AI assistant for war — Defense One Apr 2026](https://www.defenseone.com/technology/2026/04/startup-takes-different-approach-ai-assistants/412545/)
- [SCSP Hackathon page](https://www.scsp.ai/hackathon/)
- [Voice agent with RAG + safety — NVIDIA Technical Blog](https://developer.nvidia.com/blog/how-to-build-a-voice-agent-with-rag-and-safety-guardrails/)
