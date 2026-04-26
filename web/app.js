// Adjutant frontend — push-to-talk mic + reply panel.

const micBtn = document.getElementById("mic-btn");
const transcriptEl = document.getElementById("transcript");
const replyEl = document.getElementById("reply");
const spokenEl = document.getElementById("spoken-summary");
const audioEl = document.getElementById("reply-audio");
const citationsEl = document.getElementById("citations");
const formSection = document.getElementById("form-section");
const formPdf = document.getElementById("form-pdf");
const formDownload = document.getElementById("form-download");
const formMeta = document.getElementById("form-meta");
const netBadge = document.getElementById("net-status");

let mediaRecorder = null;
let audioChunks = [];

// Reflect online/offline status — the demo's "wifi disconnect" moment.
function updateNetBadge() {
  if (navigator.onLine) {
    netBadge.textContent = "Online (Adjutant doesn't need it)";
    netBadge.className = "badge online";
  } else {
    netBadge.textContent = "OFFLINE — still working";
    netBadge.className = "badge offline";
  }
}
window.addEventListener("online", updateNetBadge);
window.addEventListener("offline", updateNetBadge);
updateNetBadge();

micBtn.addEventListener("mousedown", startRecording);
micBtn.addEventListener("mouseup", stopRecording);
micBtn.addEventListener("touchstart", (e) => { e.preventDefault(); startRecording(); });
micBtn.addEventListener("touchend",   (e) => { e.preventDefault(); stopRecording(); });

async function startRecording() {
  if (mediaRecorder?.state === "recording") return;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
    audioChunks = [];
    mediaRecorder.ondataavailable = (e) => audioChunks.push(e.data);
    mediaRecorder.onstop = handleAudio;
    mediaRecorder.start();
    micBtn.classList.add("recording");
    transcriptEl.textContent = "Listening…";
  } catch (err) {
    transcriptEl.textContent = `Mic error: ${err.message}`;
  }
}

function stopRecording() {
  if (mediaRecorder?.state !== "recording") return;
  mediaRecorder.stop();
  micBtn.classList.remove("recording");
  mediaRecorder.stream.getTracks().forEach((t) => t.stop());
  transcriptEl.textContent = "Transcribing…";
}

async function handleAudio() {
  const blob = new Blob(audioChunks, { type: "audio/webm" });

  // Step 1: STT
  const fd = new FormData();
  fd.append("file", blob, "input.webm");
  let text;
  try {
    const stt = await fetch("/transcribe", { method: "POST", body: fd });
    if (!stt.ok) throw new Error(`STT ${stt.status}`);
    const sttJson = await stt.json();
    text = sttJson.text;
    transcriptEl.textContent = `You said: "${text}"`;
  } catch (err) {
    transcriptEl.textContent = `Transcription failed: ${err.message}`;
    return;
  }

  if (!text || !text.trim()) {
    transcriptEl.textContent = "Didn't catch that. Hold the mic and try again.";
    return;
  }

  // Step 2: full pipeline
  let payload;
  try {
    const resp = await fetch("/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: text }),
    });
    if (!resp.ok) throw new Error(`Query ${resp.status}`);
    payload = await resp.json();
  } catch (err) {
    transcriptEl.textContent = `Pipeline failed: ${err.message}`;
    return;
  }

  renderReply(payload);
}

function renderReply(p) {
  replyEl.hidden = false;
  spokenEl.textContent = p.spoken_summary;

  if (p.audio_url) {
    audioEl.src = p.audio_url;
    audioEl.play().catch(() => { /* user gesture missing — fine */ });
  }

  citationsEl.innerHTML = "";
  for (const c of p.citations || []) {
    const li = document.createElement("li");
    const head = c.section ? `${c.source} — ${c.section}` : c.source;
    li.innerHTML = `<strong>${head}</strong><br><span>${c.quote}</span>`;
    citationsEl.appendChild(li);
  }

  // Multi-form rendering: if /query returned forms[], render one block per form.
  // Falls back to the legacy single-form fields for backwards compat.
  const forms = (p.forms && p.forms.length) ? p.forms : (
    p.pdf_url || p.form_data
      ? [{ form_id: "Form", form_data: p.form_data || {}, missing_fields: p.missing_fields || [], pdf_url: p.pdf_url }]
      : []
  );

  if (!forms.length) {
    formSection.hidden = true;
    return;
  }

  formSection.hidden = false;
  // Replace the static iframe layout with a container we own.
  formSection.innerHTML = `<h3>${forms.length === 1 ? "Generated form" : `Generated ${forms.length} forms`}</h3>`;

  for (const f of forms) {
    const block = document.createElement("div");
    block.className = "form-block";
    const filledCount = Object.values(f.form_data || {}).filter(v => v !== "" && v !== null).length;
    const missingTxt = f.missing_fields?.length
      ? ` · need: ${f.missing_fields.join(", ")}`
      : "";

    block.innerHTML = `
      <h4>${f.form_id}</h4>
      <p class="form-meta">Filled ${filledCount} field${filledCount === 1 ? "" : "s"}${missingTxt}</p>
      ${f.pdf_url
        ? `<iframe class="form-pdf" src="${f.pdf_url}" title="${f.form_id} preview"></iframe>
           <a class="form-download" href="${f.pdf_url}" download>Download ${f.form_id}</a>`
        : `<p class="form-empty">No PDF generated — see structured data above.</p>`
      }
    `;
    formSection.appendChild(block);
  }
}
