# SCSP Hackathon — How the Army Actually Operates, Where Paperwork Hurts Most, and Where GenAI.mil Falls Short

**Compiled:** April 25, 2026
**Purpose:** Strategic brief to pick the right persona + pain point + demo wedge for the SCSP GenAI.mil track
**Bottom line up front:** Pick **the company commander or junior NCO doing TDY + leave + counseling**. Don't pick "regulation Q&A" — that's already saturated. The unfilled lane is **voice-first, offline, end-to-end form output that survives a wifi disconnect on stage.**

---

## 1. How a soldier actually fills out paperwork (the workflow, not the form)

The friction isn't the form itself — it's the system around it. Six things have to happen for one DA-31 leave request:

1. **Soldier opens DA-31 PDF** from Army Publishing Directorate (armypubs.army.mil) — fillable but often gets re-flattened by mail clients and has to be re-typed.
2. **Soldier fills it out manually** — name, SSN, rank, unit, dates, leave address, emergency contact, accrued leave balance from LES.
3. **Routes it through the chain** — squad leader → platoon sergeant → 1SG → company commander → S1.
4. **S1 verifies leave balance** in IPPS-A (which has a known-issues list maintained at ipps-a.army.mil and replaced eMILPO + the old pay system).
5. **Common rejections:** missing signatures, wrong dates, incomplete leave address, balance mismatch — kicked back to soldier.
6. **If approved**, soldier gets it back signed, has to keep the printed copy (still). And when leave ends, has to sign in.

**Recommended advance notice:** 30 days for routine leave (per most unit SOPs). For a system that should take 5 minutes, this consumes hours per request across the chain — and it's one of the simpler forms.

For TDY (training trips), it gets dramatically worse — that's where DTS lives, and DTS is universally hated.

---

## 2. The systems soldiers fight every day

| System | What it does | Why it hurts |
|---|---|---|
| **DTS (Defense Travel System)** | Books TDY travel, files vouchers, calculates per diem | Former Army Col. Arnold Strong calls it the **"Don't Travel Service."** ~18% of vouchers were rejected and resubmitted (FY 2004 GAO data — and the architecture hasn't fundamentally changed). Soldiers' personal credit scores get damaged from late government-card payments. One sergeant went 10 months without per diem because DTS set the wrong payout date. Replacement "MyTravel" with Concur was promised by 2025, still not fully in. |
| **IPPS-A (Integrated Personnel and Pay System – Army)** | The unified personnel + pay + talent system replacing eMILPO + the old pay system | Multi-year delayed rollout (2021 delays after "year of tech headaches"). Maintains a published **"Known Issues" PDF** (updated 24 Dec 2025). User pain points: inaccurate retirement dates, missing training records hurting post-service career assistance, system-upgrade-induced glitches that the team "is working through resolution as quickly as possible." |
| **GCSS-Army (Global Combat Support System – Army)** | Logistics, supply, maintenance, property | Property accountability is the #1 fear at company command (see §4) |
| **DTMS (Digital Training Management System)** | Tracks individual + collective training | Pulls leaders into computers daily |
| **MEDPROS** | Medical readiness | Required readiness reports, periodic re-verifications |
| **ARIMS (Army Records Information Management System)** | Records | Used as part of admin filing duties |
| **eMILPO (legacy, now mostly replaced)** | Personnel accounting / strength reporting | Migration to IPPS-A still incomplete in some units |
| **Army Publishing Directorate (armypubs.army.mil)** | All ARs, ADPs, FMs, DA forms | Public source of every regulation — but search is keyword-based, not semantic |
| **DD-1351-2** | Travel Voucher | The form that DTS generates / soldiers fight to get reimbursed |
| **DA-31** | Request and Authority for Leave | The most common touched form |
| **DA-4856** | Developmental Counseling | Required for almost every personnel action |
| **DA-2823** | Sworn Statement | Required after almost any incident |
| **DA-3349** | Physical Profile | Required from medical, controls duty assignments |
| **DA Form 705** | Army Combat Fitness Test scorecard | Constant input/update churn |
| **DA-5500** | Body fat worksheet | When tape-test required |

---

## 3. The headline statistics (memorize these for the pitch)

- **RAND Corporation:** Army company leaders work an average **12.5-hour workday — more than 96% of US workers.** Less than **one-third** of that time is actually spent on unit training or readiness. ([Army Times](https://www.armytimes.com/news/your-army/2019/12/22/army-company-leader-workload-is-unsustainable-here-are-some-ways-to-fix-it/))
- **Modern War Institute (West Point):** Companies submit **"three dozen to four dozen monthly reports"** — completion process consumes "**one week every month** for company command teams." Key leaders are "stuck behind computers for most of their workdays and on many of their off days just trying to keep up." ([MWI West Point](https://mwi.westpoint.edu/the-mission-and-the-bureaucracy-how-administrative-requirements-hinder-warfighting/))
- **Modern War Institute, named systems pulling time:** DTS, GCSS-Army, ARIMS, MEDPROS, DTMS — *"troops-to-task reports rarely feed actual decisions, processes, or systems, but instead pull platoon sergeants and operations sergeants away from warfighting operations."*
- **Pentagon's own framing on GenAI.mil:** Use cases include "automating routine staff processes that currently consume **thousands of man-hours**." ([War.gov](https://www.war.gov/News/Releases/Release/Article/4354916/the-war-department-unleashes-ai-on-new-genaimil-platform/))
- **Ask Sage / Army Enterprise LLM Workspace:** 300,000 personnel descriptions updated in a week vs **~50,000 hours manual** — proves AI can absorb this work category. ([AFCEA](https://www.afcea.org/signal-media/armys-new-ai/llm-tools-boost-productivity))
- **GenAI.mil:** **1.2M+ users** as of Apr 2026, but **culture change "will take years, not weeks"** (Small Wars Journal). Senior leaders use it more than the rank-and-file.

The hackathon's own brief tells you the framing the judges want to hear: *"the most immediate friction for the three million men and women in uniform often occurs in the **administrative trenches**."*

---

## 4. The actual pain points — ranked by what soldiers complain about

### Captain-rank rant ([Task & Purpose Reddit feature](https://taskandpurpose.com/culture/army-officer-reddit-rant/)) — verbatim:

The disgruntled Army company commander on r/army:

- **Property accountability is the #1 nightmare.** "Your platoon leadership will join hands and dance on your grave" over property issues. Success "envied and minimized," mistakes "amplified and scorned." The PBO (Property Book Officer) is the "Finger of Death."
- "Your Supply Sergeant will burn your BOMs in a pagan equinox revel."
- ESR (Equipment Status Report), PMCS (Preventive Maintenance Checks and Services), CMDP (Computerized Maintenance Management) are weaponized against commanders.

### Sergeant Major of the Army's Reddit feedback thread (recurring complaints):
- Barracks conditions
- Leaders' time management
- Inconsistent standards across units
- Encouraging behavioral health resources without retaliation
- **Stopping work at reasonable hours** so people can be with families
- Excessive admin: "useless requirements like tracking training status waste valuable leadership time"

### NCOER / OER pain (from armywriter.com, asktop.net, RallyPoint discussions):
- Senior raters writing dozens of NCOERs/OERs per cycle
- Fear of "Not Qualified" block ending careers — high stakes per form
- Errors get kicked back from HRC, restart entire approval chain
- Bullet writing requires precise voice/format that soldiers don't have time to learn

### DTS-specific pain:
- "Chaotic routing with long layovers, poor connections" because system optimizes for cheapest source
- Vouchers get stuck for weeks; soldiers' personal credit takes the hit
- Approval bottlenecks: "units with only one, two, or three people authorized to approve vouchers" create huge delays
- Some soldiers pay thousands out of pocket waiting for reimbursement

### IPPS-A:
- Public Known Issues list (24 Dec 2025) maintained on the website itself — admission that bugs are persistent
- Inaccurate retirement dates, missing training records
- ATAP marketplace migration introduced new errors

### Substance abuse / urinalysis paperwork (AR 600-85, DoDI 1010.16):
- 10% of unit must be tested monthly, 25% quarterly, max 40% per collection
- Unit Prevention Leader (UPL) job is paperwork-heavy: DD-2624 chain of custody, collection scheduling, lab coordination
- A "flawless" UPL collection is one without paperwork errors — high churn on chain of custody

### SHARP / EO / behavioral health reporting:
- Required cycle reports across multiple systems
- Each incident generates DA-2823 sworn statements, counseling forms, and routing to specialized officers

---

## 5. What GenAI.mil actually is — and where it leaves a gap

**GenAI.mil status (Apr 2026):**
- 1.2M+ users
- Hosts: Google Gemini (Dec 2025), xAI Grok (added later), OpenAI ChatGPT (Feb 2026), Anthropic Claude
- All certified IL5 / CUI
- Used to create 100K+ agents (DefenseScoop, Apr 23 2026)
- Web-based, browser access via CAC
- **Unclassified only** — no classified data, no ITAR

**What people actually do on it (per Lexington Institute, Small Wars Journal, war.gov):**
- Write performance evaluations (NCOERs, OERs)
- Draft awards, counseling statements, memos
- Summarize policy documents
- Generate compliance checklists
- S2 intel-estimate updates
- Fires-cell airspace deconfliction briefs
- Training scenario generation

**Critical limitations the Small Wars Journal author flags:**
- **Hallucinations** — fabricated citations
- **Sycophancy** — agreeable but wrong advice
- **No persistent context** — chatbot only knows what user pastes in
- **Senior leaders adopt faster than juniors** — exactly inverse of where the pain is biggest
- **"AI-induced psychosis"** for isolated decision-makers (real concern in the literature)
- **Privacy** — communications aren't legally privileged

**The DefenseScoop quote that should drive your design:**
> "We have a lot of mechanics, we got a lot of people turning wrenches. **Not everyone is sitting in a nice, cushy, air-conditioned office typing away at a computer all day.**"

This is your wedge. **GenAI.mil is built for desk workers. The pain is at the rank-and-file who don't have a desk.** They need voice. They need offline (when on a FOB, in a motor pool, on a field exercise). They need it on a phone or laptop they're already carrying.

---

## 6. Competitive landscape — where each existing tool lives

| Tool | What it does | Voice? | Offline? | Forms? | Persona |
|---|---|---|---|---|---|
| **GenAI.mil** | Generic chat / multi-LLM | ❌ | ❌ | partial | desk worker |
| **CamoGPT (Army)** | 75K-user chat, transitioning to Army-specific LLM | ❌ | ❌ | ❌ | desk worker |
| **Ask Sage / Army Enterprise LLM Workspace** | $49M IDIQ; CUI SaaS for personnel desc, press releases | ❌ | ❌ | ❌ | HR specialist |
| **Milnerva** | $10/mo writing copilot for NCOERs/OERs/memos | ❌ | ❌ | ❌ (writes prose) | NCO at desk |
| **WriteMyNCOER, RapidEPR** | Eval writing assistants | ❌ | ❌ | ❌ | NCO at desk |
| **SergeantAI (WWT)** | Demo: text RAG over AR 670-1 (uniforms) only | ❌ | ❌ | ❌ | demo |
| **EdgeRunner AI** | $12M Series A, GPT-5-class on-device, deployed with US SOF | listens 1-way | ✅ | ❌ | tactical SOF |
| **VictorBot (Army)** | Reddit + LLM over conflict-zone repos | ❌ | ❌ | ❌ | mission knowledge |
| **Generic AI form-fillers (Instafill etc.)** | Auto-populate any PDF | ❌ | ❌ | ✅ generic | civilian |

**Nobody combines:** voice-first + offline + cites AR/JTR by section + auto-fills the actual DA-form PDF + end-to-end persona flow. **That's the unfilled cell.**

---

## 7. The four highest-pain personas — pick ONE for the demo

| Persona | Daily pain | What they need | Demo flow |
|---|---|---|---|
| **A. Junior NCO planning TDY** | DTS hell, JTR per-diem math, DD-1351-2 voucher rejected | Voice request → JTR/GSA lookup → DD-1351-2 + DA-31 generated → emailed to S1 | "I need to attend the JRTC mission rehearsal at Fort Polk for 5 days starting June 10, my home station is Fort Bragg" → outputs filled DD-1351-2 with correct per diem, DA-31 if leave-adjacent |
| **B. Company XO / 1LT doing property** | PBO terror, BOM tracking, ESR / PMCS, ~50% of waking hours | Voice query into property regs → cited section → PCC change-of-command memo template | "What does AR 735-5 require when I sign for Class IX bench stock?" |
| **C. Squad/team leader writing NCOERs** | 60-hour/week burst at rating period close-out; bullet-format paranoia | Voice dictation of accomplishments → NCOER bullets in correct format with cited reg references | "Sergeant Diaz led the platoon's M4 qual range, qualified 100% first time, identified two safety issues" → produces compliant Part IV-V bullets |
| **D. UPL / S1 admin clerk** | Urinalysis chain of custody, 10/25/40% targets, DD-2624 paperwork | Voice query into AR 600-85 + DoDI 1010.16 → checklist + filled DD-2624 | Niche, but judges who've been admin would love it |

**My recommendation: Persona A (junior NCO planning TDY).** Why:
- Most universally felt pain (every soldier travels)
- Most quotable demo ("Don't Travel Service")
- Cleanest 5-minute demo flow (one voice request → two forms out)
- JTR + GSA APIs are public and fully documented (per the SCSP brief's dataset list)
- Hits "Problem-Solution Fit" rubric hardest because the persona is so specific
- Hits "National Impact" because every TDY across DoD touches DTS

**Backup recommendation: Persona C (NCOER writer)** — slightly more crowded (Milnerva exists) but still nobody does it offline + voice. Could run it as a sub-feature of Persona A's demo.

---

## 8. The unique angle no one else will pitch

Every team will build a chat-over-Field-Manuals RAG. That's table stakes — even the SCSP brief's first example ("Regulation navigator") sounds like it. **You'll lose Novelty points if that's all you build.**

The differentiation is in this layer cake:

1. **Voice in.** Soldier hits a button on laptop/phone, speaks naturally.
2. **Whisper STT (offline).** Local model handles motor-pool noise, military jargon.
3. **Local RAG over a curated corpus** — not all of armypubs, but the specific ARs/JTR/GSA/DA-form schemas that matter for the persona.
4. **Local Llama (Ollama).** Constrained system prompt: must cite section + paragraph; refuse if not in retrieved context.
5. **Form-schema reasoning.** Output structured JSON matching DA-31 / DD-1351-2 PDF field names (pdfplumber extracts the schema once).
6. **PDF auto-fill.** Populate actual fields, generate signed-ready PDF.
7. **Voice out (Chatterbox).** "I've drafted your DA-31 for 10 days starting June 3. Per AR 600-8-10 paragraph 4-3, you have 22.5 days accrued, so this is approved. Email it to your S1?"
8. **Demo: pull the wifi cable.** Repeat the exact same flow. Watch judges sit up.

**That's a 5-minute demo and it scores high on every rubric line:**

- Novelty (25%): no one else combines voice + offline + form output
- Technical Difficulty (25%): 6 hard pieces wired together
- National Impact (25%): RAND 12.5-hr workday × 3M users × 6 hrs/wk on paperwork = $23B/yr in recovered labor
- Problem-Solution Fit (25%): one persona, end-to-end, regulation citations verbatim

---

## 9. What the judges care about (Boston specifically — Liu + Mohindra)

**Dr. Sanjeev Mohindra** leads MIT Lincoln Lab's AI Technology Group, ISR & Tactical Systems Division. PhD Cornell, BTech IIT Delhi. His current research is **AI test & evaluation for defense applications** — funded by the CDAO (the same office that runs GenAI.mil). He's developing libraries for AI T&E workflows. ([MIT Lincoln Lab bio](https://www.ll.mit.edu/biographies/sanjeev-mohindra))

**What this means for your demo:**
- He'll test your system for **hallucinations and sycophancy** — the exact failure modes Small Wars Journal flags
- He'll respect rigorous evaluation — show your retrieval scores, show how you constrain the LLM, show what happens when you ask it something out-of-corpus (and it correctly refuses)
- He won't be impressed by demo polish — he'll be impressed by **technical honesty**
- Cite "AR 600-8-10, paragraph 4-3" verbatim and show the source quote on screen — that's reliability theater that wins MIT-trained AI judges

**Dr. Ho-Chit Liu** (Boston co-judge) — likely also Lincoln Lab background based on co-pairing. Same playbook applies.

**Stuart Wagner** (DC, also co-judge across tracks) — Air Force/Space Force CDTO, founded BRAVO Hackathon. He **built NIPRGPT** at AFRL before it got phased out / Army-blocked. Master's in CS from UPenn + LSE public policy. ([SAFCN bio](https://www.safcn.af.mil/About-Us/Biographies/Display/Article/2593412/stuart-wagner/))

**What this means:**
- He's seen every flavor of "let's wrap GPT in a military skin." He'll be looking for the thing that's actually different.
- He believes in operational prototypes "10x-100x lower cost than any other DoD prototyping pathway" — your hackathon-speed-and-cost story aligns.
- The fact that you'd be voice-first + offline directly addresses the gap he saw in NIPRGPT (which was browser-only).

---

## 10. The pitch script (rough — to refine Saturday)

> **"Hi, I'm Naomi from Charlie Mike. Our team built Adjutant — a voice-first, fully offline AI assistant for the rank-and-file Army.**
>
> **The problem: a RAND study found Army company leaders work 12.5-hour days — longer than 96% of all American workers — and less than a third of that time is on actual readiness. Why? Because the bureaucratic tail is gigantic. Modern War Institute documents companies submit three to four dozen reports a month. Most of it is paperwork.**
>
> **GenAI.mil rolled out in December to 1.2 million users. But the Pentagon's own framing admits it: the people who need it most — the mechanics, the platoon sergeants, the soldiers in the field — don't have desks. They have phones. They have laptops in motor pools where the wifi cuts out. Generative AI hasn't reached them yet.**
>
> **So we built Adjutant. Watch this. [Pull wifi cable.] Now I'll request 10 days of leave starting June 3. [Speak request.] Adjutant cites AR 600-8-10 paragraph 4-3, confirms my balance, drafts the DA-31 with all fields populated, and asks one clarifying question. [PDF appears on screen.] I sign it, it's done. Same flow for TDY — JTR per-diem auto-calculated, DD-1351-2 voucher pre-filled.**
>
> **Stack: Whisper STT, local FAISS RAG over Army Pubs Directorate + JTR + GSA per-diem, Llama 3.1 8B in Ollama, pdfplumber for form-field schema, Chatterbox TTS — all on this laptop, no network. We built it in 30 hours because we already had the voice + RAG plumbing from Sabi, our voice AI tutor for Nigerian children.**
>
> **3 million service members, 6 hours of paperwork per week, $25 an hour loaded — that's $23 billion a year of mission-readiness time we can give back. Adjutant: the admin you wish your S1 had time to do."**

---

## 11. Risks the judges will probe

- **"Hallucination on regulation citations"** → mitigation: retrieved-context-only constraint, show on screen the verbatim AR section the answer was sourced from. Have a deliberate out-of-corpus question ready that shows the system correctly refusing.
- **"What about classified data?"** → "Out of scope. The hackathon brief explicitly limits to unclassified public corpora. Adjutant runs locally so the next step is loading classified ARs into a SIPR-deployed instance."
- **"Doesn't EdgeRunner already do this?"** → "EdgeRunner is for tactical doctrine and SOF. We're for the bureaucratic tail of the rank-and-file. Different user, different corpus, complementary."
- **"Doesn't GenAI.mil already do this?"** → "GenAI.mil is the chat platform. Adjutant is a vertical app — like saying Excel exists so why TurboTax."
- **"What if the soldier's leave balance is wrong?"** → "We don't write back to IPPS-A. We generate the form and surface the regulation. The S1 still has authority. We remove the friction, not the human."
- **"3M users × 6hrs/wk — show the math."** → 3,000,000 × 6 × 52 × $25 = **$23.4B/yr.** Conservative — RAND data suggests the real number is higher.

---

## 12. Sources

**Admin burden:**
- [Modern War Institute — The Mission and the Bureaucracy](https://mwi.westpoint.edu/the-mission-and-the-bureaucracy-how-administrative-requirements-hinder-warfighting/)
- [Army Times — Army company leader workload is unsustainable (RAND study)](https://www.armytimes.com/news/your-army/2019/12/22/army-company-leader-workload-is-unsustainable-here-are-some-ways-to-fix-it/)
- [Task & Purpose — Army officer's epic rant about company command](https://taskandpurpose.com/culture/army-officer-reddit-rant/)
- [Task & Purpose — Sgt. Maj. of the Army Reddit feedback](https://taskandpurpose.com/news/army-grinston-reddit-soldier-feedback/)

**DTS:**
- [Task & Purpose — DTS days are numbered, here's why it sucks](https://taskandpurpose.com/news/defense-travel-system-replacement/)
- [Task & Purpose — DTS being upgraded by 2025](https://taskandpurpose.com/news/defense-travel-system-upgrade/)
- [GAO — Army National Guard inefficient travel reimbursement](https://www.govinfo.gov/content/pkg/GAOREPORTS-GAO-05-79/html/GAOREPORTS-GAO-05-79.htm)
- [DTS Voucher Guide 3](https://media.defense.gov/2022/May/11/2002995240/-1/-1/0/DTS_GUIDE_3_VOUCHER.PDF)

**IPPS-A:**
- [IPPS-A homepage](https://ipps-a.army.mil/)
- [IPPS-A Known Issues PDF (24 Dec 2025)](https://ipps-a.army.mil/Portals/129/Documents/IPPSA%20Known%20Issues_20251224.pdf)
- [DOT&E IPPS-A Increment II FY2025 report](https://www.dote.osd.mil/Portals/97/pub/reports/FY2025/army/2025ipps-a.pdf)
- [Military.com — Army delays IPPS-A after year of tech headaches](https://www.military.com/daily-news/2021/10/08/army-delays-new-one-stop-personnel-and-pay-system-after-year-of-tech-headaches.html)
- [Federal News Network — Army personnel leaders modernize](https://federalnewsnetwork.com/army/2025/11/army-personnel-leaders-are-pushing-hard-to-modernize-how-the-service-manages-its-people/)

**ATAP:**
- [Army.mil — Officers Guide to ATAP](https://www.army.mil/article/280742/officers_your_guide_to_the_talent_alignment_marketplace)
- [Modern War Institute — Winning in the Marketplace ATAP](https://mwi.westpoint.edu/winning-in-the-marketplace-how-officers-and-units-can-get-the-most-out-of-the-army-talent-alignment-process/)

**Forms & regulations:**
- [AR 600-8-10 Leaves and Passes (full PDF)](https://armypubs.army.mil/epubs/DR_pubs/DR_a/ARN30018-AR_600-8-10-000-WEB-1.pdf)
- [JTR June 2025 PDF](https://api.army.mil/e2/c/downloads/2025/06/10/0da05172/jtr-june-2025.pdf)
- [Per Diem DTMO](https://www.travel.dod.mil/Travel-Transportation-Rates/Per-Diem/)
- [Army Publishing Directorate](https://armypubs.army.mil/)
- [DoDI 1010.16 Urinalysis Procedures](https://www.esd.whs.mil/Portals/54/Documents/DD/issuances/dodi/101016p.pdf)
- [AR 600-85 Substance Abuse Program](https://www.mcmilitarylaw.com/documents/ar_60085_army_substance_abuse_program.pdf)

**GenAI.mil:**
- [War.gov — GenAI.mil launch announcement](https://www.war.gov/News/Releases/Release/Article/4354916/the-war-department-unleashes-ai-on-new-genaimil-platform/)
- [Small Wars Journal — GenAI.mil critical analysis (Jan 2026)](https://smallwarsjournal.com/2026/01/19/genai/)
- [DefenseScoop — Pentagon uses GenAI.mil to create 100K agents (Apr 23 2026)](https://defensescoop.com/2026/04/23/pentagon-uses-genai-mil-to-create-agents/)
- [DefenseScoop — DoD large-scale rollout Dec 2025](https://defensescoop.com/2025/12/09/genai-mil-platform-dod-commercial-ai-models-agentic-tools-google-gemini/)
- [Lexington Institute — Google Gemini first out of the gate](https://lexingtoninstitute.org/google-gemini-first-out-of-the-gate-for-genai-in-the-military/)
- [Breaking Defense — ChatGPT to 3M military users on GenAI.mil (Feb 2026)](https://breakingdefense.com/2026/02/chatgpt-will-be-available-to-3-million-military-users-on-genai-mil/)
- [OpenAI — Bringing ChatGPT to GenAI.mil](https://openai.com/index/bringing-chatgpt-to-genaimil/)

**Existing tools / competitive:**
- [Army.mil — Army Enterprise LLM Workspace](https://www.army.mil/article/285537/army_launches_army_enterprise_llm_workspace_the_revolutionary_ai_platform_that_wrote_this_article)
- [Ask Sage — Department of War](https://www.asksage.ai/who-we-serve/department-of-war/)
- [DefenseScoop — Army's CamoGPT not phased out (Jan 2026)](https://defensescoop.com/2026/01/27/army-camogpt-dod-genai-mil/)
- [Milnerva](https://www.milnerva.com/)
- [WWT — SergeantAI chatbot for AR 670-1](https://www.wwt.com/blog/can-a-chatbot-help-you-stay-within-us-army-regulations)
- [EdgeRunner AI](https://www.edgerunnerai.com/)
- [EdgeRunner GPT-5-class on-device (Apr 2026)](https://www.edgerunnerai.com/news/edgerunner-achieves-gpt-5-level-performance-in-key-military-tasks-while-running-locally-on-device)
- [DefensePost — EdgeRunner Digital Adjutant](https://thedefensepost.com/2026/04/06/ai-offline-assistant-edgerunner/)

**Judges:**
- [MIT Lincoln Lab — Sanjeev Mohindra bio](https://www.ll.mit.edu/biographies/sanjeev-mohindra)
- [SAF/CN — Stuart Wagner bio](https://www.safcn.af.mil/About-Us/Biographies/Display/Article/2593412/stuart-wagner/)
- [Executive Gov — Wagner stepping down](https://www.executivegov.com/articles/air-force-chief-digital-transformation-officer-stuart-wagner-stepping-down)

---

## TL;DR for Naomi

1. **Pick the junior NCO TDY persona.** Most universal pain, cleanest 5-min demo, hits all four rubric criteria.
2. **The quotable RAND stat is your opener:** *"Army company leaders work longer days than 96% of all American workers, and less than a third of that time is on readiness."*
3. **GenAI.mil is your context, not your competition.** Frame your build as the "voice-first, offline vertical app the platform doesn't have yet."
4. **The wifi-disconnect demo is the kill shot.** Practice it 10 times.
5. **Mohindra will probe for hallucinations.** Constrain the LLM to retrieved context. Show your sources on screen with section/paragraph numbers. Have one deliberately-out-of-corpus question ready that the system refuses.
6. **EdgeRunner is your peer, not your competitor.** They do tactical doctrine for SOF. You do the admin tail for the rank-and-file.
7. **Charlie Mike + Adjutant** as team and product names are well-aligned with the audience.
8. **Build forms in scope:** DA-31 (leave) + DD-1351-2 (TDY voucher) + DA-4856 (counseling). Three forms, one persona, end-to-end.
