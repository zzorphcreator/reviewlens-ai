const uploadForm = document.querySelector("#upload-form");
const statusBox = document.querySelector("#status");
const statusMessage = document.querySelector("#status-message");
const statusSpinner = document.querySelector("#status-spinner");
const reviewList = document.querySelector("#review-list");
const reviewSummary = document.querySelector("#review-summary");
const refreshButton = document.querySelector("#refresh");
const sessionFilter = document.querySelector("#session-filter");
const reviewSearch = document.querySelector("#review-search");
const ratingFilter = document.querySelector("#rating-filter");
const searchReviewsButton = document.querySelector("#search-reviews");
const uploadTrigger = document.querySelector("#upload-trigger");
const importUrlButton = document.querySelector("#import-url");
const saveSessionButton = document.querySelector("#save-session");
const reviewFile = document.querySelector("#review-file");
const sourceNameInput = document.querySelector("#source-name");
const sessionNameInput = document.querySelector("#session-name");
const cancelIngestionButton = document.querySelector("#cancel-ingestion");
const chatMessages = document.querySelector("#chat-messages");
const chatInput = document.querySelector("#chat-input");
const chatSendButton = document.querySelector("#chat-send");
const chatSuggestions = document.querySelector("#chat-suggestions");
const sessionSourceList = document.querySelector("#session-source-list");
const sessionSourceCount = document.querySelector("#session-source-count");
const sessionSourceEmpty = document.querySelector("#session-source-empty");
const searchControls = document.querySelector("#search-controls");
const toggleSearchButton = document.querySelector("#toggle-search");
const SEARCH_EXPANDED_KEY = "reviewlens.searchExpanded";
const ACTIVE_JOB_KEY = "reviewlens.activeJob";
const WORKSPACE_SOURCES_KEY = "reviewlens.workspaceSources";

let lastSourceId = null;
const workspaceSourceIds = new Set();
const activeSourceDetails = new Map();
let pollTimer = null;
let activeJobId = null;
let ingestionInProgress = false;
let chatInProgress = false;

uploadTrigger?.addEventListener("click", () => {
  if (ingestionInProgress) return;
  reviewFile.click();
});

reviewFile?.addEventListener("change", async () => {
  if (!reviewFile.files.length) return;
  const selectedFileName = reviewFile.files[0].name;
  sourceNameInput.value = sessionNameInput.value || selectedFileName;
  const formData = new FormData(uploadForm);
  setIngestionInProgress(true);
  setStatus("Uploading file...", false, true);

  const response = await fetch("/api/import/file", {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    reviewFile.value = "";
    setIngestionInProgress(false);
    setStatus(`Import failed to start: ${error.detail}`, true);
    return;
  }

  const payload = await response.json();
  lastSourceId = payload.source.id;
  activeSourceDetails.set(payload.source.id, payload.source);
  reviewFile.value = "";
  setStatus(`Queued import job ${payload.job.id}.`, false, true);
  activeJobId = payload.job.id;
  persistActiveJob(payload.job.id, payload.source.id, {
    type: "file",
    fileName: selectedFileName,
  });
  pollJob(payload.job.id, payload.source.id);
});

refreshButton?.addEventListener("click", async () => {
  if (sessionFilter.value) {
    await loadSelectedSession();
    return;
  }
  loadReviews();
});

cancelIngestionButton?.addEventListener("click", () => {
  if (!activeJobId) {
    setStatus("No active ingestion job to cancel.", true);
    return;
  }
  cancelIngestionJob(activeJobId);
});

searchReviewsButton?.addEventListener("click", () => {
  lastSourceId = null;
  updateChatState();
  loadReviews();
});

sessionFilter?.addEventListener("change", async () => {
  await loadSelectedSession();
});

importUrlButton?.addEventListener("click", async (event) => {
  event.preventDefault();
  event.stopPropagation();
  if (ingestionInProgress) return;
  const urlInput = document.querySelector("#scrape-url");
  const pageCountInput = document.querySelector("#page-count");
  const url = urlInput?.value;
  const pageCount = Number(pageCountInput?.value || "1");
  const sourceName = sessionNameInput.value;
  if (!url) {
    setStatus("Enter a URL before importing reviews.", true);
    return;
  }
  setIngestionInProgress(true);
  setStatus("Starting URL scrape...", false, true);

  const response = await fetch("/api/ingest/url", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url,
      source_name: sourceName || null,
      page_count: pageCount,
    }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    setIngestionInProgress(false);
    setStatus(`Scrape failed to start: ${error.detail}`, true);
    return;
  }

  const payload = await response.json();
  lastSourceId = payload.source.id;
  activeSourceDetails.set(payload.source.id, payload.source);
  setStatus(`Queued scrape job ${payload.job.id}.`, false, true);
  activeJobId = payload.job.id;
  persistActiveJob(payload.job.id, payload.source.id, {
    type: "url",
    url,
    pageCount,
    sourceName: sourceName || "",
  });
  pollJob(payload.job.id, payload.source.id);
});

saveSessionButton?.addEventListener("click", async (event) => {
  event.preventDefault();
  event.stopPropagation();
  if (ingestionInProgress) return;
  if (!workspaceSourceIds.size) {
    setStatus("Import reviews before saving a session.", true);
    return;
  }

  const name = sessionNameInput.value.trim();
  if (!name) {
    setStatus("Session name is required before saving.", true);
    sessionNameInput.focus();
    return;
  }
  const response = await fetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name,
      source_ids: [...workspaceSourceIds],
      config: { saved_from: "ui" },
    }),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    setStatus(`Session save failed: ${error.detail}`, true);
    return;
  }

  const payload = await response.json();
  setStatus(`Saved session "${payload.session.name}" with ${payload.session.review_count} reviews.`);
  await loadSessions(payload.session.id);
  updateChatState();
});

chatSendButton?.addEventListener("click", () => {
  sendChatMessage();
});

chatInput?.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendChatMessage();
  }
});

const popovers = [...document.querySelectorAll(".info-popover")];
const popoverParents = new Map();

popovers.forEach((popover) => {
  const summary = popover.querySelector("summary");
  const panel = popover.querySelector(".info-popover-panel");
  if (!summary || !panel) return;
  popover.dataset.pinned = "false";

  const positionPanel = () => {
    const rect = summary.getBoundingClientRect();
    panel.style.display = "block";
    const panelWidth = panel.offsetWidth || 320;
    const panelHeight = panel.offsetHeight || 200;
    let left = rect.left;
    let top = rect.bottom + 8;
    if (left + panelWidth > window.innerWidth - 12) {
      left = Math.max(12, window.innerWidth - panelWidth - 12);
    }
    if (top + panelHeight > window.innerHeight - 12) {
      top = Math.max(12, rect.top - panelHeight - 8);
    }
    panel.style.left = `${left}px`;
    panel.style.top = `${top}px`;
  };

  const ensurePortal = () => {
    if (!popoverParents.has(panel)) {
      popoverParents.set(panel, panel.parentElement);
    }
    if (panel.parentElement !== document.body) {
      document.body.appendChild(panel);
    }
  };

  const restorePanel = () => {
    const parent = popoverParents.get(panel);
    if (parent && panel.parentElement === document.body) {
      parent.appendChild(panel);
    }
  };

  const showPanel = () => {
    ensurePortal();
    popover.setAttribute("open", "");
    requestAnimationFrame(positionPanel);
  };

  const hidePanel = () => {
    popover.removeAttribute("open");
    panel.style.display = "";
    restorePanel();
  };

  summary.addEventListener("click", (event) => {
    event.preventDefault();
    const isPinned = popover.dataset.pinned === "true";
    if (isPinned) {
      popover.dataset.pinned = "false";
      hidePanel();
      return;
    }
    popover.dataset.pinned = "true";
    showPanel();
  });

  popover.addEventListener("mouseenter", () => {
    if (popover.dataset.pinned === "true") return;
    showPanel();
  });

  popover.addEventListener("mouseleave", () => {
    if (popover.dataset.pinned === "true") return;
    hidePanel();
  });

  panel.addEventListener("mouseleave", () => {
    if (popover.dataset.pinned === "true") return;
    hidePanel();
  });
});

function toggleSearchExpanded() {
  if (!searchControls || !toggleSearchButton) return;
  const isExpanded = toggleSearchButton.getAttribute("aria-expanded") === "true";
  setSearchExpanded(!isExpanded);
}

window.reviewlensToggleSearch = toggleSearchExpanded;

toggleSearchButton?.addEventListener("click", (event) => {
  event.preventDefault();
  event.stopImmediatePropagation();
  toggleSearchExpanded();
});

async function pollJob(jobId, sourceId = null) {
  clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    const response = await fetch(`/api/ingest/jobs/${jobId}`);
    if (!response.ok) {
      setStatus("Could not read import job.", true);
      clearInterval(pollTimer);
      setIngestionInProgress(false);
      return;
    }

    const { job } = await response.json();
    const stats = job.stats || {};
    const isFinished = job.status === "done" || job.status === "failed" || job.status === "cancelled";
    setStatus(jobStatusMessage(job, stats), job.status === "failed", !isFinished);

    if (isFinished) {
      clearInterval(pollTimer);
      setIngestionInProgress(false);
      activeJobId = null;
      clearActiveJob();
      if (job.status === "done" && sourceId) {
        workspaceSourceIds.add(sourceId);
        updateSaveSessionState();
        updateChatState();
        updateSourceList();
        persistWorkspaceSources();
      }
      await loadReviews();
    }
  }, 1000);
}

async function cancelIngestionJob(jobId) {
  setStatus("Cancelling ingestion...", false, true);
  const response = await fetch(`/api/ingest/jobs/${jobId}/cancel`, { method: "POST" });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    setStatus(`Cancel failed: ${error.detail}`, true);
    return;
  }
  const payload = await response.json();
  setStatus(jobStatusMessage(payload.job, payload.job.stats || {}), true);
  clearInterval(pollTimer);
  pollTimer = null;
  setIngestionInProgress(false);
  activeJobId = null;
  clearActiveJob();
}

async function loadReviews() {
  if (!sessionFilter.value && workspaceSourceIds.size === 0 && !lastSourceId) {
    reviewList.textContent = "No reviews loaded yet.";
    updateReviewSummary(0);
    return;
  }
  const params = new URLSearchParams({ limit: "25" });
  if (sessionFilter.value) {
    params.set("session_id", sessionFilter.value);
  } else if (workspaceSourceIds.size) {
    for (const sourceId of workspaceSourceIds) {
      params.append("source_ids", sourceId);
    }
  } else if (lastSourceId) {
    params.set("source_id", lastSourceId);
  }
  if (reviewSearch.value.trim()) params.set("q", reviewSearch.value.trim());
  if (ratingFilter.value) params.set("rating_min", ratingFilter.value);

  const response = await fetch(`/api/reviews?${params}`);
  if (!response.ok) {
    reviewList.textContent = "Could not load reviews.";
    updateReviewSummary(null);
    return;
  }

  const payload = await response.json();
  reviewList.replaceChildren();
  updateReviewSummary(payload.total);

  if (payload.items.length === 0) {
    reviewList.textContent = "No reviews match the current filters.";
    return;
  }

  for (const review of payload.items) {
    const item = document.createElement("article");
    item.className = "rounded-2xl border border-slate-800 bg-slate-950/70 p-4";

    const title = document.createElement("div");
    title.className = "flex items-center justify-between gap-3";
    title.innerHTML = `<strong></strong><span></span>`;
    title.querySelector("strong").textContent = review.title || review.author;
    title.querySelector("span").textContent = `${review.rating}/5`;

    const meta = document.createElement("p");
    meta.className = "mt-1 text-xs text-slate-500";
    meta.textContent = `${review.author} · ${new Date(review.reviewed_at).toLocaleDateString()}`;

    const body = document.createElement("p");
    body.className = "mt-3 text-slate-300";
    body.textContent = review.body.length > 280 ? `${review.body.slice(0, 280)}...` : review.body;

    item.append(title, meta, body);
    reviewList.append(item);
  }
}

async function loadSelectedSession() {
  lastSourceId = null;

  if (!sessionFilter.value) {
    resetChatPanel("Chat activates after reviews are ingested or a saved session is selected.");
    sessionNameInput.value = "";
    updateChatState();
    await loadReviews();
    updateSourceList();
    return;
  }

  const response = await fetch(`/api/sessions/${sessionFilter.value}`);
  if (!response.ok) {
    setStatus("Could not load selected session.", true);
    return;
  }

  const payload = await response.json();
  sessionNameInput.value = payload.session.name;
  renderChatHistory(payload.messages || []);
  updateChatState();
  await loadReviews();
  updateSourceList(payload.session.sources || payload.session.source_ids || []);
  setStatus(`Loaded session "${payload.session.name}" with ${payload.session.review_count} reviews.`);
}

async function loadSessions(selectedSessionId = "") {
  const response = await fetch("/api/sessions?limit=9");
  if (!response.ok) {
    setStatus("Could not load saved sessions.", true);
    return;
  }

  const payload = await response.json();
  sessionFilter.replaceChildren();
  const allOption = document.createElement("option");
  allOption.value = "";
  allOption.textContent = "All ingested reviews";
  sessionFilter.append(allOption);

  if (payload.items.length === 0) {
    return;
  }

  for (const session of payload.items) {
    const option = document.createElement("option");
    option.value = session.id;
    option.textContent = `${session.name} (${session.review_count} reviews)`;
    sessionFilter.append(option);
  }

  if (selectedSessionId) {
    sessionFilter.value = selectedSessionId;
  } else {
    sessionFilter.value = "";
  }
}

function updateSourceList(sources = null) {
  if (!sessionSourceList || !sessionSourceCount || !sessionSourceEmpty) return;
  sessionSourceList.replaceChildren();
  const items = (sources ?? [...workspaceSourceIds]).map((source) => {
    if (typeof source === "string") {
      return activeSourceDetails.get(source) || { id: source };
    }
    return source;
  });
  sessionSourceCount.textContent = String(items.length);
  if (!items.length) {
    sessionSourceEmpty.classList.remove("hidden");
    return;
  }

  sessionSourceEmpty.classList.add("hidden");
  for (const source of items) {
    const chip = document.createElement("span");
    chip.className =
      "rounded-full border border-white/10 bg-slate-950/60 px-2.5 py-1 text-[11px] text-slate-200";
    const detail = sourceDetailLabel(source);
    chip.textContent = detail ? `${shortSourceId(source.id)} · ${detail}` : shortSourceId(source.id);
    chip.title = detail ? `${source.id} · ${detail}` : source.id;
    sessionSourceList.append(chip);
  }
  if (sources === null) {
    persistWorkspaceSources();
  }
}

function sourceDetailLabel(source) {
  if (source.url) return source.url;
  if (source.config?.filename) return source.config.filename;
  return source.name || "";
}

function shortSourceId(sourceId) {
  return sourceId ? sourceId.slice(0, 8) : "unknown";
}

function setStatus(message, isError = false, isLoading = false) {
  statusMessage.textContent = message;
  statusSpinner.classList.toggle("hidden", !isLoading);
  statusBox.classList.toggle("border-red-500", isError);
  statusBox.classList.toggle("text-red-200", isError);
}

function updateSaveSessionState() {
  saveSessionButton.disabled = ingestionInProgress || workspaceSourceIds.size === 0;
}

function setIngestionInProgress(isInProgress) {
  ingestionInProgress = isInProgress;
  importUrlButton.disabled = isInProgress;
  uploadTrigger.disabled = isInProgress;
  const urlInput = document.querySelector("#scrape-url");
  if (urlInput) urlInput.disabled = isInProgress;
  const pageCountInput = document.querySelector("#page-count");
  if (pageCountInput) pageCountInput.disabled = isInProgress;
  if (sessionNameInput) sessionNameInput.disabled = isInProgress;
  cancelIngestionButton?.classList.toggle("hidden", !isInProgress);
  updateSaveSessionState();
  updateChatState();
}

async function sendChatMessage() {
  const question = chatInput.value.trim();
  if (!question || chatInProgress || !chatScopeAvailable()) return;

  chatInput.value = "";
  appendChatMessage("user", question);
  setChatInProgress(true);
  const assistantMessage = appendChatMessage("assistant", "", { pending: true });
  const assistantText = assistantMessage.querySelector("[data-message-text='true']");

  const body = { question };
  if (sessionFilter.value) {
    body.session_id = sessionFilter.value;
  } else {
    body.source_ids = [...workspaceSourceIds];
  }

  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    assistantText.textContent = `Chat failed: ${error.detail}`;
    assistantMessage.dataset.pending = "false";
    setChatInProgress(false);
    return;
  }

  let meta = null;
  try {
    await readChatStream(response, {
      onMeta(payload) {
        meta = payload;
      },
      onToken(payload) {
        assistantText.textContent += payload.text || "";
        chatMessages.scrollTop = chatMessages.scrollHeight;
      },
      onDone(payload) {
        assistantMessage.dataset.pending = "false";
        if (payload.answer) assistantText.textContent = stripMarkdown(payload.answer);
        const model = payload.model_used || meta?.model_used;
        const reviewCount = payload.review_count ?? meta?.review_count ?? 0;
        if (model) {
          appendChatMeta(assistantMessage, `Answered by ${model} from ${reviewCount} reviews.`);
        }
      },
      onError(payload) {
        assistantMessage.dataset.pending = "false";
        if (!assistantText.textContent) assistantText.textContent = `Chat failed: ${payload.detail}`;
        appendChatMeta(assistantMessage, `Error: ${payload.detail}`);
      },
    });
  } catch (error) {
    assistantMessage.dataset.pending = "false";
    assistantText.textContent = `Chat stream failed: ${error.message}`;
  }
  setChatInProgress(false);
}

function appendChatMessage(role, content, options = {}) {
  chatMessages.querySelector("#chat-placeholder")?.remove();
  const item = document.createElement("div");
  item.className =
    role === "user"
      ? "ml-auto max-w-[65%] rounded-2xl bg-sky-300 px-4 py-3 text-slate-950"
      : "mr-auto max-w-[80%] rounded-2xl border border-white/10 bg-slate-900 px-4 py-3 text-slate-200";
  if (options.pending) item.dataset.pending = "true";

  const text = document.createElement("p");
  text.className = "whitespace-pre-wrap";
  text.dataset.messageText = "true";
  text.textContent = role === "assistant" ? stripMarkdown(content) : content;

  item.append(text);
  if (options.meta) {
    appendChatMeta(item, options.meta);
  }
  chatMessages.append(item);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return item;
}

function stripMarkdown(value) {
  return (value || "")
    .replace(/```(?:\w+)?\s*/g, "")
    .replace(/```/g, "")
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/^\s{0,3}#{1,6}\s*/gm, "")
    .replace(/^\s{0,3}>\s?/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+\.\s+/gm, "")
    .replace(/(\*\*|__)(.*?)\1/g, "$2")
    .replace(/(\*|_)(.*?)\1/g, "$2")
    .replace(/`/g, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function renderChatHistory(messages) {
  resetChatPanel("No saved chat history for this session yet.");
  if (!messages.length) return;

  chatMessages.replaceChildren();
  for (const message of messages) {
    const meta =
      message.role === "assistant" && message.model_used
        ? `Answered by ${message.model_used}${message.latency_ms ? ` in ${message.latency_ms} ms` : ""}.`
        : "";
    appendChatMessage(message.role, message.content, { meta });
  }
}

function resetChatPanel(message) {
  chatMessages.replaceChildren();
  const placeholder = document.createElement("p");
  placeholder.id = "chat-placeholder";
  placeholder.textContent = message;
  chatMessages.append(placeholder);
}

function appendChatMeta(item, content) {
  const meta = document.createElement("p");
  meta.className = "mt-2 text-xs text-slate-500";
  meta.textContent = content;
  item.append(meta);
}

function updateReviewSummary(total) {
  const totalText = total === null ? "unknown" : total;
  reviewSummary.textContent = `Preview the normalized reviews currently loaded. Total = ${totalText}`;
}

async function readChatStream(response, handlers) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const eventText of events) {
      const event = parseSseEvent(eventText);
      if (!event) continue;
      if (event.event === "meta") handlers.onMeta?.(event.data);
      if (event.event === "token") handlers.onToken?.(event.data);
      if (event.event === "done") handlers.onDone?.(event.data);
      if (event.event === "error") handlers.onError?.(event.data);
    }
  }
}

function parseSseEvent(eventText) {
  const lines = eventText.split("\n");
  const event = lines.find((line) => line.startsWith("event: "))?.slice(7);
  const data = lines
    .filter((line) => line.startsWith("data: "))
    .map((line) => line.slice(6))
    .join("\n");
  if (!event || !data) return null;
  return { event, data: JSON.parse(data) };
}

function setChatInProgress(isInProgress) {
  chatInProgress = isInProgress;
  updateChatState();
}

function updateChatState() {
  const enabled = chatScopeAvailable() && !chatInProgress;
  chatInput.disabled = !enabled;
  chatSendButton.disabled = !enabled;
}

function chatScopeAvailable() {
  return Boolean(sessionFilter.value || workspaceSourceIds.size);
}

function jobStatusMessage(job, stats) {
  const base = `Job ${job.status}. Inserted: ${stats.inserted ?? 0}, duplicates: ${
    stats.duplicates ?? 0
  }, rejected: ${stats.rejected ?? 0}.`;

  if (job.status === "running" && stats.total_pages) {
    if (stats.progress_stage === "embedding") {
      return `Embedding reviews for RAG. ${base}`;
    }
    const currentPage = Math.max(Number(stats.current_page || 1), 1);
    const totalPages = Number(stats.total_pages);
    const provider = stats.current_provider ? ` via ${formatProvider(stats.current_provider)}` : "";
    const pageMessage = `Processing page ${currentPage} of ${totalPages}${provider}.`;
    return `${pageMessage} ${base}`;
  }

  if (job.status === "running" && stats.progress_stage === "embedding") {
    return `Embedding reviews for RAG. ${base}`;
  }

  if (job.status === "failed" && job.error) {
    return `${base} ${friendlyJobError(job.error)}`;
  }

  if (stats.provider) {
    return `${base} Provider: ${stats.provider}.`;
  }

  return base;
}

function friendlyJobError(error) {
  if (!error) return "The job failed.";
  if (
    error.includes("All scraper providers failed") ||
    error.includes("Bright Data") ||
    error.includes("Zyte API") ||
    error.includes("403 Forbidden")
  ) {
    return "We could not fetch reviews from this URL right now. The site may be blocking automated access or returning a page format we cannot parse yet. Try another URL or upload CSV/JSON instead.";
  }
  return `Error: ${error}`;
}

function formatProvider(provider) {
  return provider
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => {
      if (item === "brightdata") return "Bright Data";
      if (item === "zyte") return "Zyte";
      if (item === "http") return "direct HTTP";
      return item;
    })
    .join(", ");
}

function setSearchExpanded(isExpanded) {
  if (!searchControls || !toggleSearchButton) return;
  searchControls.hidden = !isExpanded;
  searchControls.classList.toggle("hidden", !isExpanded);
  toggleSearchButton.textContent = isExpanded ? "Hide" : "Show";
  toggleSearchButton.setAttribute("aria-expanded", String(isExpanded));
  localStorage.setItem(SEARCH_EXPANDED_KEY, String(isExpanded));
}

function restoreSearchExpandedState() {
  setSearchExpanded(localStorage.getItem(SEARCH_EXPANDED_KEY) === "true");
}

function persistActiveJob(jobId, sourceId, meta = {}) {
  if (!jobId) return;
  localStorage.setItem(
    ACTIVE_JOB_KEY,
    JSON.stringify({
      jobId,
      sourceId: sourceId || null,
      ...meta,
    })
  );
}

function clearActiveJob() {
  localStorage.removeItem(ACTIVE_JOB_KEY);
}

function restoreActiveJob() {
  const raw = localStorage.getItem(ACTIVE_JOB_KEY);
  if (!raw) return;
  let payload = null;
  try {
    payload = JSON.parse(raw);
  } catch (error) {
    clearActiveJob();
    return;
  }
  if (!payload?.jobId) {
    clearActiveJob();
    return;
  }
  activeJobId = payload.jobId;
  if (payload.type === "url" && payload.url) {
    const urlInput = document.querySelector("#scrape-url");
    const pageCountInput = document.querySelector("#page-count");
    if (urlInput) urlInput.value = payload.url;
    if (pageCountInput && payload.pageCount) {
      pageCountInput.value = String(payload.pageCount);
    }
    if (sessionNameInput && payload.sourceName) {
      sessionNameInput.value = payload.sourceName;
    }
  }
  if (payload.type === "file" && payload.fileName) {
    if (sessionNameInput && !sessionNameInput.value) {
      sessionNameInput.value = payload.fileName;
    }
    setStatus(`Resuming ingestion job ${payload.jobId} (file: ${payload.fileName})...`, false, true);
  }
  setIngestionInProgress(true);
  setStatus(`Resuming ingestion job ${payload.jobId}...`, false, true);
  pollJob(payload.jobId, payload.sourceId || null);
}

function persistWorkspaceSources() {
  const items = [...workspaceSourceIds].map((sourceId) => {
    const source = activeSourceDetails.get(sourceId) || { id: sourceId };
    return {
      id: source.id,
      name: source.name,
      platform: source.platform,
      url: source.url,
      config: source.config,
    };
  });
  if (!items.length) {
    localStorage.removeItem(WORKSPACE_SOURCES_KEY);
    return;
  }
  localStorage.setItem(WORKSPACE_SOURCES_KEY, JSON.stringify(items));
}

function restoreWorkspaceSources() {
  const raw = localStorage.getItem(WORKSPACE_SOURCES_KEY);
  if (!raw) return;
  let payload = null;
  try {
    payload = JSON.parse(raw);
  } catch (error) {
    localStorage.removeItem(WORKSPACE_SOURCES_KEY);
    return;
  }
  if (!Array.isArray(payload)) {
    localStorage.removeItem(WORKSPACE_SOURCES_KEY);
    return;
  }
  workspaceSourceIds.clear();
  for (const source of payload) {
    if (!source?.id) continue;
    workspaceSourceIds.add(source.id);
    activeSourceDetails.set(source.id, source);
  }
  updateSaveSessionState();
  updateChatState();
  updateSourceList();
}

reviewList.textContent = "No reviews loaded yet. Import reviews or use Search to load saved data.";
restoreSearchExpandedState();
restoreActiveJob();
restoreWorkspaceSources();
loadSessions("");
updateSaveSessionState();
updateChatState();
updateSourceList();
