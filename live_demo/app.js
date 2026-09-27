const transcriptEl = document.querySelector("#transcript");
const notesEl = document.querySelector("#notes");
const pinboardEl = document.querySelector("#pinboard");
const stampEl = document.querySelector("#stamp");
const penEl = document.querySelector("#pen");
const neededListEl = document.querySelector("#neededList");
const teamFeedEl = document.querySelector("#teamFeed");
const readinessEl = document.querySelector("#readiness");
const callStatus = document.querySelector("#callStatus");
const modelLabel = document.querySelector("#modelLabel");
const pageDate = document.querySelector("#pageDate");
const micButton = document.querySelector("#micButton");
const cameraButton = document.querySelector("#cameraButton");
const cameraStage = document.querySelector("#cameraStage");
const cameraPreview = document.querySelector("#cameraPreview");
const frameCanvas = document.querySelector("#frameCanvas");
const newIntakeButton = document.querySelector("#newIntakeButton");
const textForm = document.querySelector("#textForm");
const textInput = document.querySelector("#textInput");
const packetDialog = document.querySelector("#packetDialog");
const packetMarkdownEl = document.querySelector("#packetMarkdown");

const DEFAULT_API_ORIGIN = "http://127.0.0.1:4177";
const API_ORIGIN = window.location.protocol === "file:" ? DEFAULT_API_ORIGIN : window.location.origin;
const WS_ORIGIN = API_ORIGIN.replace(/^http/, "ws");
const FRAME_INTERVAL_MS = 1000;
const FRAME_WIDTH = 512;

if (window.location.protocol === "file:") {
  window.location.replace(`${API_ORIGIN}/index.html`);
}

let liveSocket = null;
let connectionPromise = null;
let generation = 0;
let processing = false;
let micPending = false;
let cameraPending = false;
let resetting = false;
const playbackSources = new Set();
let audioContext = null;
let inputProcessor = null;
let inputSource = null;
let audioStream = null;
let cameraStream = null;
let frameTimer = null;
let isRecording = false;
let nextPlaybackTime = 0;
let sessionId = null;
let state = null;
let writing = false;
const seenNotes = new Set();

const routeLabels = {
  security_review: ["Security review", "danger"],
  hardship_support: ["Hardship support", "info"],
  specialist_review: ["Specialist review", "warning"],
  needs_documents: ["Needs info", "warning"],
  ready_to_process: ["Ready to process", "success"],
};

const blockerQuestions = {
  borrower_name: "Name on the mortgage?",
  mortgage_number: "Mortgage number?",
  property_postal_code: "Property postal code?",
  contact_method: "Best contact?",
  request_summary: "What do they need?",
  payment_change_detail: "Amount, frequency, or date?",
  effective_date: "Start date?",
  prepayment_amount_cad: "Prepayment amount?",
  payout_date: "Payout date?",
  payout_reason: "Sale, switch, or paying off?",
};

const teamLabels = {
  lookup_mortgage: "Account desk",
  sync_service_request: "Request writer",
  pin_document_photo: "Documents",
  show_payment_scenario: "Calculator",
};

const emptyState = {
  route: "needs_documents",
  progress: 0,
  fields: {},
  transcript: [],
  events: [],
  tool_activity: [],
  missing_blockers: [],
  documents: [],
  evidence_photos: [],
  camera_notes: [],
  servicing_notes: [],
  scenario: null,
  mortgage: null,
  handoff: {},
  packet_markdown: "# Mortgage service request\n\nNo packet yet.",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function now() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function isFilled(field) {
  if (!field || field.status === "missing") return false;
  return !/^(missing:|not captured|not specified|unknown|none)/i.test(String(field.value || "").trim());
}

function shortBlocker(blocker) {
  if (blockerQuestions[blocker]) return blockerQuestions[blocker];
  let text = String(blocker).replace(/\s*\([^)]*\)/g, "").trim();
  if (text.length > 42) text = `${text.slice(0, 40).trim()}...`;
  return text.endsWith("?") ? text : `${text}?`;
}

function setStatus(text, tone = "") {
  callStatus.textContent = text;
  callStatus.className = `pill ${tone}`.trim();
}

function setWriting(next) {
  writing = next;
  penEl.hidden = !next;
}

function setState(nextState) {
  const previous = state || emptyState;
  state = {
    ...emptyState,
    ...nextState,
    tool_activity: nextState.tool_activity ?? previous.tool_activity,
  };
  render();
}

function render() {
  renderTranscript();
  renderNotes();
  renderPinboard();
  renderNeeded();
  renderTeam();
  renderStamp();
  document.querySelector("#downloadPacket").href = sessionId ? `${API_ORIGIN}/api/sessions/${sessionId}/packet` : "#";
  packetMarkdownEl.textContent = state.packet_markdown || emptyState.packet_markdown;
}

function renderTranscript() {
  transcriptEl.innerHTML = (state.transcript || [])
    .map((turn) => {
      const cls = turn.speaker === "Agent" ? "agent" : turn.speaker === "System" ? "system" : "caller";
      const who = turn.speaker === "Agent" ? "AI" : turn.speaker === "System" ? "!" : "You";
      return `<article class="turn ${cls} ${turn.streaming ? "streaming" : ""}"><span class="who">${who}</span><p>${escapeHtml(turn.text)}</p></article>`;
    })
    .join("");
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

function buildNotes() {
  const f = state.fields || {};
  const notes = [];
  const name = isFilled(f.caller) ? f.caller.value : "";
  const mortgage = isFilled(f.mortgage) ? f.mortgage.value : "";
  if (name || mortgage) {
    notes.push({ key: `title:${name}|${mortgage}`, cls: "title", text: [name, mortgage].filter(Boolean).join("  ·  ") });
  }
  const record = state.mortgage;
  if (record) {
    if (!record.found) {
      notes.push({ key: `acct:notfound:${record.mortgage_number}`, cls: "flag urgent", text: `Mortgage ${record.mortgage_number || ""} not found, confirm the number.` });
    } else if (!record.verified) {
      notes.push({ key: `acct:unverified:${record.mortgage_number}:${record.verification_failed}`, cls: "flag urgent", text: record.verification_failed ? "Details don't match the record. No account info shared." : "Found. Verify name and postal code before sharing details." });
    } else {
      const active = record.status === "active";
      notes.push({ key: `acct:${record.mortgage_number}:${record.status}`, cls: active ? "check" : "flag urgent", text: `Verified. ${record.product} at ${(record.rate * 100).toFixed(2)}%${active ? "" : `, ${record.status.replaceAll("_", " ")}`}` });
      notes.push({ key: `acct-pay:${record.payment_amount}`, cls: "aside", text: `Pays ${f.payment?.value || money(record.payment_amount)}, ${money(record.balance, 0)} left, matures ${record.maturity_date}` });
    }
  }
  if (isFilled(f.request)) notes.push({ key: `req:${f.request.value}`, cls: "", text: f.request.value });
  if (isFilled(f.changes)) notes.push({ key: `chg:${f.changes.value}`, cls: "", text: `Wants: ${f.changes.value}` });
  if (isFilled(f.hardship)) notes.push({ key: `hard:${f.hardship.value}`, cls: "flag urgent", text: `Hardship: ${f.hardship.value}` });
  for (const note of state.servicing_notes || []) notes.push({ key: `rule:${note}`, cls: "aside", text: note });
  if (isFilled(f.contact)) notes.push({ key: `contact:${f.contact.value}`, cls: "aside", text: `Reach at ${f.contact.value}` });
  if (callerHasSpoken()) {
    const seenQuestions = new Set();
    for (const blocker of state.missing_blockers || []) {
      const question = shortBlocker(blocker);
      const dedupe = question.toLowerCase().replace(/[^a-z]/g, "");
      if (seenQuestions.has(dedupe) || seenQuestions.size >= 3) continue;
      seenQuestions.add(dedupe);
      notes.push({ key: `blank:${blocker}`, cls: "blank", text: question, blank: true });
    }
  }
  if (!notes.length) {
    notes.push({ key: "empty", cls: "aside", text: "Waiting for the caller. Tap Talk, show a document, or type below." });
  }
  return notes;
}

function money(value, digits = 2) {
  return `$${Number(value || 0).toLocaleString("en-CA", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

function callerHasSpoken() {
  return (state.transcript || []).some((turn) => turn.speaker === "Caller" && String(turn.text || "").trim());
}

function renderNotes() {
  const notes = buildNotes();
  notesEl.innerHTML = notes
    .map((note) => {
      const fresh = !seenNotes.has(note.key);
      seenNotes.add(note.key);
      return `<div class="note ${note.cls} ${fresh ? "ink-in" : ""}">${escapeHtml(note.text)}${note.blank ? '<span class="blank-line"></span>' : ""}</div>`;
    })
    .join("");
}

function renderPinboard() {
  const photos = state.evidence_photos || [];
  const scenario = state.scenario;
  const cards = photos.map(
    (photo, index) => `
      <figure class="polaroid" style="--tilt: ${index % 2 ? 2 : -2.5}deg" data-key="${escapeHtml(photo.id)}">
        <img src="${photo.data_url}" alt="Document captured on camera" />
        <figcaption>${escapeHtml(photo.caption)}</figcaption>
        <span class="tag ${photo.confirmed ? "" : "unconfirmed"}">${photo.caller_description ? `Caller says: ${escapeHtml(photo.caller_description)} · ` : ""}${photo.confirmed ? "Matches what the caller described" : "Not confirmed by this image"}</span>
        <span class="tag">Captured ${escapeHtml(photo.captured_at || "")}</span>
      </figure>`
  );
  if (scenario) cards.unshift(scenarioCard(scenario));
  const currentKeys = [...pinboardEl.querySelectorAll("[data-key]")].map((el) => el.dataset.key).join("|");
  const nextKeys = [scenario ? `scenario-${scenario.version}` : "", ...photos.map((p) => p.id)].filter(Boolean).join("|");
  if (currentKeys !== nextKeys) pinboardEl.innerHTML = cards.join("");
}

const CHART = { width: 360, height: 190, left: 44, right: 12, top: 12, bottom: 26 };

function chartPoints(balances, maxYears, maxBalance) {
  const w = CHART.width - CHART.left - CHART.right;
  const h = CHART.height - CHART.top - CHART.bottom;
  return balances.map((balance, year) => [CHART.left + (year / maxYears) * w, CHART.top + h - (balance / maxBalance) * h]);
}

function scenarioCard(s) {
  const current = s.current.yearly_balances;
  const proposed = s.proposed.yearly_balances;
  const maxYears = Math.max(current.length, proposed.length) - 1 || 1;
  const maxBalance = Math.max(current[0], proposed[0]) || 1;
  const path = (points) => points.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const cur = chartPoints(current, maxYears, maxBalance);
  const pro = chartPoints(proposed, maxYears, maxBalance);
  const h = CHART.height - CHART.top - CHART.bottom;
  const ticks = [0, 0.5, 1].map((t) => {
    const y = CHART.top + h - t * h;
    return `<line class="grid" x1="${CHART.left}" x2="${CHART.width - CHART.right}" y1="${y}" y2="${y}"></line><text class="axis" x="${CHART.left - 6}" y="${y + 4}" text-anchor="end">${money((maxBalance * t) / 1000, 0)}k</text>`;
  }).join("");
  const step = maxYears > 20 ? 10 : 5;
  const xTicks = [];
  for (let year = 0; year <= maxYears; year += step) {
    const x = CHART.left + (year / maxYears) * (CHART.width - CHART.left - CHART.right);
    xTicks.push(`<text class="axis" x="${x}" y="${CHART.height - 8}" text-anchor="middle">${year}y</text>`);
  }
  const saved = s.interest_saved != null && s.interest_saved > 0;
  const sooner = s.years_saved != null && s.years_saved > 0;
  return `
    <figure class="polaroid chart" style="--tilt: -1deg" data-key="scenario-${s.version}">
      <figcaption>${escapeHtml(s.title)}</figcaption>
      <div class="chart-stats">
        <div><span class="stat-value">${money(s.proposed.payment)}</span><span class="stat-label">${escapeHtml(s.proposed.frequency.toLowerCase())}, was ${money(s.current.payment)} ${escapeHtml(s.current.frequency.toLowerCase())}</span></div>
        <div><span class="stat-value">${sooner ? `${s.years_saved.toFixed(1)} yrs` : "—"}</span><span class="stat-label">${sooner ? "sooner" : "no change to payoff"}</span></div>
        <div><span class="stat-value">${saved ? money(s.interest_saved, 0) : "—"}</span><span class="stat-label">${saved ? "less interest" : "no interest saved"}</span></div>
      </div>
      <div class="chart-legend"><span class="key current"></span>Current, ${s.current.years} yrs <span class="key proposed"></span>Proposed, ${s.proposed.years} yrs</div>
      <div class="chart-wrap">
        <svg class="balance-chart" viewBox="0 0 ${CHART.width} ${CHART.height}" role="img" aria-label="Mortgage balance over time, current versus proposed">
          ${ticks}${xTicks.join("")}
          <path class="line current" d="${path(cur)}"></path>
          <path class="line proposed" d="${path(pro)}"></path>
          <line class="crosshair" y1="${CHART.top}" y2="${CHART.height - CHART.bottom}" hidden></line>
          <rect class="hit" x="${CHART.left}" y="${CHART.top}" width="${CHART.width - CHART.left - CHART.right}" height="${h}" data-years="${maxYears}"></rect>
        </svg>
        <div class="chart-tip" hidden></div>
      </div>
      <table class="visually-hidden"><caption>Balance at each year</caption><tr><th>Year</th><th>Current</th><th>Proposed</th></tr>${Array.from({ length: maxYears + 1 }, (_, y) => `<tr><td>${y}</td><td>${current[y] != null ? money(current[y], 0) : "paid off"}</td><td>${proposed[y] != null ? money(proposed[y], 0) : "paid off"}</td></tr>`).join("")}</table>
      <span class="tag">${escapeHtml(s.assumptions)}</span>
    </figure>`;
}

function onChartHover(event) {
  if (event.type === "mouseleave") {
    pinboardEl.querySelectorAll(".chart-tip").forEach((tip) => { tip.hidden = true; });
    pinboardEl.querySelectorAll(".crosshair").forEach((line) => line.setAttribute("hidden", ""));
    return;
  }
  const hit = event.target.closest?.(".balance-chart .hit");
  const figure = event.target.closest?.(".polaroid.chart");
  if (!figure || !state.scenario) return;
  const tip = figure.querySelector(".chart-tip");
  const cross = figure.querySelector(".crosshair");
  if (!hit) { tip.hidden = true; cross.setAttribute("hidden", ""); return; }
  const svg = hit.ownerSVGElement;
  const box = svg.getBoundingClientRect();
  const x = ((event.clientX - box.left) / box.width) * CHART.width;
  const maxYears = Number(hit.dataset.years);
  const w = CHART.width - CHART.left - CHART.right;
  const year = Math.max(0, Math.min(maxYears, Math.round(((x - CHART.left) / w) * maxYears)));
  const cx = CHART.left + (year / maxYears) * w;
  cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.removeAttribute("hidden");
  const value = (list) => (list[year] != null ? money(list[year], 0) : "paid off");
  tip.innerHTML = `<strong>Year ${year}</strong><br>Current ${value(state.scenario.current.yearly_balances)}<br>Proposed ${value(state.scenario.proposed.yearly_balances)}`;
  tip.style.left = `${(cx / CHART.width) * 100}%`;
  tip.hidden = false;
}

function renderStamp() {
  const route = state.route;
  const [label, tone] = routeLabels[route] || [route, "warning"];
  stampEl.hidden = !callerHasSpoken() || writing;
  stampEl.textContent = label;
  if (stampEl.dataset.route !== route) {
    stampEl.dataset.route = route;
    stampEl.className = `stamp ${tone}`;
    stampEl.style.animation = "none";
    void stampEl.offsetWidth;
    stampEl.style.animation = "";
  }
}

function renderNeeded() {
  const items = [];
  for (const blocker of state.missing_blockers || []) {
    items.push({ text: shortBlocker(blocker).replace(/\?$/, ""), cls: "blocker" });
  }
  for (const doc of state.documents || []) {
    items.push({ text: `${doc.item} — ${doc.status || "unknown"}`, cls: doc.already_provided ? "done" : "" });
  }
  neededListEl.innerHTML = items.length
    ? items.map((item) => `<li class="${item.cls}"><span class="tick-box"></span><span>${escapeHtml(item.text)}</span></li>`).join("")
    : `<li class="empty">${callerHasSpoken() ? "Everything needed so far is collected." : "Nothing yet. The list fills in as the servicing team reads the call."}</li>`;
  const progress = Number(state.progress || 0);
  readinessEl.textContent = `${progress}% collected`;
  readinessEl.className = `pill ${progress >= 80 ? "" : progress >= 40 ? "warning" : "neutral"}`;
}

function renderTeam() {
  const activity = [...(state.tool_activity || [])].slice(-6).reverse();
  teamFeedEl.innerHTML = activity.length
    ? activity
        .map((item) => {
          const phase = item.phase || "running";
          let headline = item.headline || "";
          if (item.name === "sync_service_request" && phase === "done" && item.result) {
            const facts = (item.result.open_items || []).length;
            const docs = (item.result.open_documents || []).length;
            headline = `${String(item.result.routing_decision || "").replaceAll("_", " ")}: ${facts} open fact${facts === 1 ? "" : "s"}${docs ? ", documents still needed" : ""}`;
          }
          const meta = phase === "running" ? "working" : item.scheduling === "INTERRUPT" ? "interrupted the agent" : item.duration_ms != null ? `${item.duration_ms} ms` : "";
          return `<li><span class="team-dot ${phase}"></span><span><span class="team-name">${escapeHtml(teamLabels[item.name] || item.name)}</span> · ${escapeHtml(headline)}</span><span class="team-meta ${item.scheduling === "INTERRUPT" ? "interrupt" : ""}">${escapeHtml(meta)}</span></li>`;
        })
        .join("")
    : `<li class="empty">Account desk, request writer, documents, and calculator will show up here as the agent calls them.</li>`;
  setWriting(processing || (state.tool_activity || []).some((item) => item.phase === "running"));
}

function appendSystem(text) {
  setState({ ...state, transcript: [...(state.transcript || []), { id: crypto.randomUUID(), speaker: "System", text }] });
}

function mergeTranscript(authoritative, local) {
  const byId = new Map((authoritative || []).map(turn => [turn.id, turn]));
  for (const turn of local || []) {
    if (!byId.has(turn.id)) byId.set(turn.id, turn);
  }
  return [...byId.values()];
}

function applyServerState(nextState) {
  if (nextState.session_id !== sessionId) return;
  setState({
    ...nextState,
    transcript: mergeTranscript(nextState.transcript, state?.transcript),
    tool_activity: mergeToolActivity(nextState.tool_activity, state?.tool_activity),
  });
}

function mergeToolActivity(authoritative, local) {
  const finished = new Set(["done", "error", "cancelled"]);
  const byId = new Map();
  for (const item of authoritative || []) byId.set(item.id, item);
  for (const item of local || []) {
    const current = byId.get(item.id);
    if (!current || (finished.has(item.phase) && !finished.has(current.phase))) byId.set(item.id, item);
  }
  return [...byId.values()];
}

function applyToolEvent(message) {
  const { type, ...entry } = message;
  const others = (state.tool_activity || []).filter((item) => item.id !== entry.id);
  setState({ ...state, tool_activity: [...others, entry] });
}

function upsertStreamingTurn(speaker, text, final = false, id = crypto.randomUUID()) {
  if (!String(text || "").trim()) return;
  const transcript = [...(state.transcript || [])];
  const index = transcript.findIndex(turn => turn.id === id);
  const turn = { id, speaker, text, streaming: !final };
  if (index >= 0) transcript[index] = turn;
  else transcript.push(turn);
  setState({ ...state, transcript });
}

async function api(path, options = {}) {
  const response = await fetch(`${API_ORIGIN}${path}`, { headers: { "Content-Type": "application/json" }, ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `Request failed with status ${response.status}`);
  return payload;
}

function stopPlayback() {
  window.claimAvatar?.interrupt();
  for (const source of playbackSources) { try { source.stop(); } catch (_) {} }
  playbackSources.clear();
  nextPlaybackTime = audioContext?.currentTime || 0;
}

function clearBusy() {
  processing = false;
  state.tool_activity = (state.tool_activity || []).map(item => item.phase === "running" ? { ...item, phase: "cancelled", headline: "Connection ended" } : item);
  render();
}

function disconnectLive() {
  generation += 1;
  const socket = liveSocket;
  liveSocket = null;
  connectionPromise = null;
  stopLiveVoice(false);
  stopCamera();
  stopPlayback();
  window.claimAvatar?.reset();
  if (socket) socket.close();
  if (state) clearBusy();
}

async function createSession(resume = false) {
  if (resetting) return;
  resetting = true;
  newIntakeButton.disabled = true;
  const previous = sessionId || sessionStorage.getItem("intakeSession");
  disconnectLive();
  const epoch = generation;
  seenNotes.clear();
  setStatus("Connecting", "neutral");
  sessionId = null;
  setState(emptyState);
  pageDate.textContent = `Mortgage call notes · ${new Date().toLocaleDateString([], { month: "short", day: "numeric" })}`;
  try {
    let payload;
    if (resume && previous) {
      try { payload = await api(`/api/sessions/${previous}`); } catch (_) { sessionStorage.removeItem("intakeSession"); }
    } else if (previous) {
      await api(`/api/sessions/${previous}`, { method: "DELETE" }).catch(() => {});
    }
    payload = payload || await api("/api/sessions", { method: "POST" });
    if (epoch !== generation) return;
    sessionId = payload.session_id;
    sessionStorage.setItem("intakeSession", sessionId);
    setState(payload.state);
    const health = await api("/api/health");
    window.claimAvatar?.configure(health.avatar);
    modelLabel.textContent = `${health.live_model} · request team on ${health.model}`;
    setStatus(payload.has_api_key ? "Ready" : "API key required", payload.has_api_key ? "" : "danger");
    textInput.focus();
  } catch (error) {
    setStatus("Backend unavailable", "danger");
    appendSystem(error.message);
  } finally {
    resetting = false;
    newIntakeButton.disabled = false;
  }
}

function connectLive() {
  if (connectionPromise) return connectionPromise;
  if (!sessionId || resetting) return Promise.reject(new Error("Wait for the intake to be ready."));
  const epoch = generation;
  const avatarMode = window.claimAvatar?.supported === false ? "&avatar=off" : "";
  window.claimAvatar?.connecting();
  const socket = new WebSocket(`${WS_ORIGIN}/ws/live?session_id=${encodeURIComponent(sessionId)}${avatarMode}`);
  liveSocket = socket;
  connectionPromise = new Promise((resolve, reject) => {
    let ready = false;
    const timeout = setTimeout(() => { reject(new Error("Live connection timed out.")); socket.close(); }, 20000);
    const current = () => epoch === generation && liveSocket === socket;
    socket.onmessage = (event) => {
      if (!current()) return;
      const message = JSON.parse(event.data);
      if (message.session_id !== sessionId) return;
      if (message.type === "ready") {
        ready = true;
        clearTimeout(timeout);
        setStatus("Live", "");
        resolve();
      } else if (message.type === "session") {
        window.claimAvatar?.configure(message.avatar);
        modelLabel.textContent = `${message.model} · live call`;
      } else if (message.type === "processing") {
        processing = message.active;
        render();
      } else if (message.type === "transcript") {
        upsertStreamingTurn(message.speaker, message.text, message.final, message.id);
      } else if (message.type === "tool") {
        applyToolEvent(message);
      } else if (message.type === "audio") {
        playPcm24(message.data);
      } else if (message.type === "avatar_video") {
        window.claimAvatar?.append(message.data);
      } else if (message.type === "turn_complete") {
        window.claimAvatar?.finishTurn();
      } else if (message.type === "state") {
        applyServerState(message.state);
      } else if (message.type === "interrupted") {
        stopPlayback();
      } else if (message.type === "error") {
        processing = false;
        appendSystem(message.message);
        if (!ready) { reject(new Error(message.message)); socket.close(); }
      }
    };
    socket.onerror = () => { clearTimeout(timeout); reject(new Error("Live connection failed.")); socket.close(); };
    socket.onclose = () => {
      clearTimeout(timeout);
      if (!ready) reject(new Error("Live connection ended before it was ready."));
      if (!current()) return;
      liveSocket = null;
      connectionPromise = null;
      stopLiveVoice(false);
      stopCamera();
      stopPlayback();
      window.claimAvatar?.reset();
      clearBusy();
      setStatus("Disconnected — reconnect to continue", "warning");
    };
  });
  return connectionPromise;
}

async function unlockAudio() {
  if (window.AudioContext) {
    audioContext = audioContext || new AudioContext();
    await audioContext.resume();
  }
}

async function sendCallerTurn(text) {
  const epoch = generation;
  try {
    stopPlayback();
    await unlockAudio();
    await connectLive();
    if (epoch !== generation) return;
    const id = crypto.randomUUID();
    liveSocket.send(JSON.stringify({ type: "text", text, id }));
    upsertStreamingTurn("Caller", text, true, id);
    processing = true;
    render();
  } catch (error) {
    if (epoch !== generation) return;
    textInput.value = text;
    appendSystem(`${error.message} Your message is back in the input; send it again to retry.`);
  }
}

async function startLiveVoice() {
  if (micPending || isRecording) return;
  const epoch = generation;
  if (!navigator.mediaDevices?.getUserMedia || !window.AudioContext) {
    appendSystem("This browser cannot capture microphone audio. Type instead.");
    return;
  }
  try {
    micPending = true;
    await unlockAudio();
    await connectLive();
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    if (epoch !== generation || !liveSocket || liveSocket.readyState !== WebSocket.OPEN) { stream.getTracks().forEach(track => track.stop()); return; }
    audioStream = stream;
    audioContext = audioContext || new AudioContext();
    await audioContext.resume();
    inputSource = audioContext.createMediaStreamSource(audioStream);
    inputProcessor = audioContext.createScriptProcessor(4096, 1, 1);
    inputProcessor.onaudioprocess = (event) => {
      event.outputBuffer.getChannelData(0).fill(0);
      if (!liveSocket || liveSocket.readyState !== WebSocket.OPEN) return;
      const pcm16 = resampleToPcm16(event.inputBuffer.getChannelData(0), audioContext.sampleRate, 16000);
      liveSocket.send(JSON.stringify({ type: "audio", data: arrayBufferToBase64(pcm16.buffer) }));
    };
    inputSource.connect(inputProcessor);
    inputProcessor.connect(audioContext.destination);
    isRecording = true;
    micButton.classList.add("active");
    micButton.querySelector(".round-label").textContent = "Listening";
    setStatus("Live, listening", "");
  } catch (error) {
    const denied = error.name === "NotAllowedError" || /denied|permission/i.test(error.message);
    appendSystem(denied ? "Microphone access was denied. Allow it for this site or type instead." : `Voice failed: ${error.message}`);
    stopLiveVoice(false);
  } finally { micPending = false; }
}

function stopLiveVoice(closeSocket = true) {
  isRecording = false;
  micButton.classList.remove("active");
  micButton.querySelector(".round-label").textContent = "Talk";
  inputProcessor?.disconnect();
  inputSource?.disconnect();
  audioStream?.getTracks().forEach((track) => track.stop());
  inputProcessor = null;
  inputSource = null;
  audioStream = null;
  if (closeSocket) {
    disconnectLive();
    setStatus("Call ended — notes kept for reconnect", "neutral");
  }
}

async function startCamera() {
  if (cameraPending || cameraStream) return;
  const epoch = generation;
  if (!navigator.mediaDevices?.getUserMedia) {
    appendSystem("This browser cannot access a camera.");
    return;
  }
  try {
    cameraPending = true;
    await unlockAudio();
    await connectLive();
    const stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "environment" } });
    if (epoch !== generation || !liveSocket || liveSocket.readyState !== WebSocket.OPEN) { stream.getTracks().forEach(track => track.stop()); return; }
    cameraStream = stream;
    stream.getVideoTracks().forEach(track => track.addEventListener("ended", stopCamera, { once: true }));
    liveSocket.send(JSON.stringify({ type: "camera_state", enabled: true }));
    cameraPreview.srcObject = cameraStream;
    cameraStage.hidden = false;
    cameraButton.classList.add("active");
    cameraButton.querySelector(".round-label").textContent = "Stop camera";
    frameTimer = window.setInterval(sendFrame, FRAME_INTERVAL_MS);
    if (!isRecording) setStatus("Camera on, hold up a document", "");
  } catch (error) {
    const denied = error.name === "NotAllowedError" || /denied|permission/i.test(error.message);
    appendSystem(denied ? "Camera access was denied. Allow it for this site to show a document." : `Camera failed: ${error.message}`);
    stopCamera();
  } finally { cameraPending = false; }
}

function stopCamera() {
  const wasOn = Boolean(cameraStream);
  if (frameTimer) window.clearInterval(frameTimer);
  frameTimer = null;
  cameraStream?.getTracks().forEach((track) => track.stop());
  cameraStream = null;
  cameraPreview.srcObject = null;
  cameraStage.hidden = true;
  cameraButton.classList.remove("active");
  cameraButton.querySelector(".round-label").textContent = "Show document";
  if (wasOn && liveSocket?.readyState === WebSocket.OPEN) {
    liveSocket.send(JSON.stringify({ type: "camera_state", enabled: false }));
    if (!isRecording) setStatus("Camera off", "");
  }
}

function sendFrame() {
  if (!cameraStream || !liveSocket || liveSocket.readyState !== WebSocket.OPEN) return;
  if (!cameraPreview.videoWidth) return;
  const scale = FRAME_WIDTH / cameraPreview.videoWidth;
  frameCanvas.width = FRAME_WIDTH;
  frameCanvas.height = Math.round(cameraPreview.videoHeight * scale);
  const ctx = frameCanvas.getContext("2d");
  ctx.drawImage(cameraPreview, 0, 0, frameCanvas.width, frameCanvas.height);
  const dataUrl = frameCanvas.toDataURL("image/jpeg", 0.6);
  liveSocket.send(JSON.stringify({ type: "video", data: dataUrl.split(",")[1] }));
}

function resampleToPcm16(input, inputRate, outputRate) {
  const ratio = inputRate / outputRate;
  const outputLength = Math.floor(input.length / ratio);
  const pcm = new Int16Array(outputLength);
  for (let i = 0; i < outputLength; i += 1) {
    const index = i * ratio;
    const before = Math.floor(index);
    const after = Math.min(before + 1, input.length - 1);
    const weight = index - before;
    const sample = Math.max(-1, Math.min(1, input[before] * (1 - weight) + input[after] * weight));
    pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  return pcm;
}

function arrayBufferToBase64(buffer) {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.byteLength; i += 1) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

function playPcm24(base64) {
  audioContext = audioContext || new AudioContext();
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  const pcm = new Int16Array(bytes.buffer);
  const audioBuffer = audioContext.createBuffer(1, pcm.length, 24000);
  const channel = audioBuffer.getChannelData(0);
  for (let i = 0; i < pcm.length; i += 1) channel[i] = pcm[i] / 32768;
  const source = audioContext.createBufferSource();
  source.buffer = audioBuffer;
  source.connect(audioContext.destination);
  const startAt = Math.max(audioContext.currentTime, nextPlaybackTime);
  playbackSources.add(source);
  source.onended = () => playbackSources.delete(source);
  source.start(startAt);
  nextPlaybackTime = startAt + audioBuffer.duration;
}

micButton.addEventListener("click", () => (isRecording ? stopLiveVoice() : startLiveVoice()));
cameraButton.addEventListener("click", () => (cameraStream ? stopCamera() : startCamera()));
newIntakeButton.addEventListener("click", () => createSession(false));
window.addEventListener("pagehide", disconnectLive);
textForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const value = textInput.value.trim();
  if (!value) return;
  textInput.value = "";
  sendCallerTurn(value);
});
pinboardEl.addEventListener("mousemove", onChartHover);
pinboardEl.addEventListener("mouseleave", onChartHover);
document.querySelector("#openPacket").addEventListener("click", () => packetDialog.showModal());
document.querySelector("#closePacket").addEventListener("click", () => packetDialog.close());
if (window.claimAvatar) {
  window.claimAvatar.onFallback = () => {
    disconnectLive();
    setStatus("Voice mode — tap Talk to reconnect", "neutral");
  };
}

createSession(true);
