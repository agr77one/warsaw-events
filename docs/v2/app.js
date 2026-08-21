const DATA_URL = "../data/events.json";
const HEALTH_URL = "../data/source_health.json";
const PAGE_SIZE = 36;
const BLOCKED_TITLE_PATTERNS = [
  /picnic in the park\s*\+?\s*family movie night/i,
];

const state = {
  events: [],
  filtered: [],
  visibleCount: PAGE_SIZE,
  datePreset: "7",
  search: "",
  distance: "25",
  category: "all",
  admission: "all",
  confidence: "all",
  sort: "date",
  images: true,
};

const elements = {
  freshness: document.querySelector("#freshness"),
  todayCount: document.querySelector("#today-count"),
  weekendCount: document.querySelector("#weekend-count"),
  weekCount: document.querySelector("#week-count"),
  totalCount: document.querySelector("#total-count"),
  search: document.querySelector("#search"),
  distance: document.querySelector("#distance"),
  category: document.querySelector("#category"),
  admission: document.querySelector("#admission"),
  confidence: document.querySelector("#confidence"),
  sort: document.querySelector("#sort"),
  images: document.querySelector("#images"),
  resultCount: document.querySelector("#result-count"),
  rangeLabel: document.querySelector("#range-label"),
  activeFilters: document.querySelector("#active-filters"),
  groups: document.querySelector("#event-groups"),
  empty: document.querySelector("#empty-state"),
  loadMore: document.querySelector("#load-more"),
  dialog: document.querySelector("#event-dialog"),
  dialogContent: document.querySelector("#dialog-content"),
};

function parseLocalIso(value) {
  if (!value) return null;
  const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::(\d{2}))?)?/);
  if (!match) return null;
  return new Date(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
    Number(match[4] || 0),
    Number(match[5] || 0),
    Number(match[6] || 0),
  );
}

function warsawToday() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/Indiana/Indianapolis",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return new Date(Number(values.year), Number(values.month) - 1, Number(values.day));
}

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function addDays(date, count) {
  const result = new Date(date);
  result.setDate(result.getDate() + count);
  return result;
}

function dayDifference(date, base = warsawToday()) {
  return Math.round((startOfDay(date) - startOfDay(base)) / 86400000);
}

function weekendRange(base = warsawToday()) {
  const day = base.getDay();
  if (day === 6) return [base, addDays(base, 1)];
  if (day === 0) return [base, base];
  const saturday = addDays(base, 6 - day);
  return [saturday, addDays(saturday, 1)];
}

function isInPreset(event, preset) {
  const eventDate = event.startDate;
  const today = warsawToday();
  const diff = dayDifference(eventDate, today);
  if (diff < 0) return false;
  if (preset === "today") return diff === 0;
  if (preset === "tomorrow") return diff === 1;
  if (preset === "weekend") {
    const [start, end] = weekendRange(today);
    return startOfDay(eventDate) >= startOfDay(start) && startOfDay(eventDate) <= startOfDay(end);
  }
  if (preset === "7") return diff <= 6;
  if (preset === "30") return diff <= 29;
  return true;
}

function normalizeEvent(event, index) {
  const startDate = parseLocalIso(event.start);
  if (!startDate) return null;
  const distance = event.distance_miles === null || event.distance_miles === undefined
    ? null
    : Number(event.distance_miles);
  const searchText = [
    event.title,
    event.description,
    event.venue,
    event.address,
    event.city,
    event.state,
    event.category,
    event.source_name,
  ].filter(Boolean).join(" ").toLowerCase();
  return {
    ...event,
    id: event.fingerprint || `event-${index}`,
    startDate,
    endDate: parseLocalIso(event.end),
    distance,
    searchText,
  };
}

function confidenceLabel(confidence) {
  if (confidence === "A") return "Official";
  if (confidence === "B") return "Reported";
  return "Community";
}

function distanceLabel(event) {
  if (event.distance === null || Number.isNaN(event.distance)) return "Distance pending";
  if (event.distance <= 3) return "Warsaw area";
  return `${event.distance.toFixed(event.distance % 1 ? 1 : 0)} miles away`;
}

function admissionType(event) {
  const value = String(event.admission || "").trim();
  if (!value) return "unknown";
  if (/\bfree\b|no charge|no admission/i.test(value)) return "free";
  return "priced";
}

function admissionLabel(event) {
  const type = admissionType(event);
  if (type === "free") return "Free";
  if (type === "priced") return event.admission;
  return "Price not published";
}

function formatTime(date) {
  return new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: date.getMinutes() ? "2-digit" : undefined,
  }).format(date);
}

function eventTime(event) {
  const start = event.startDate;
  if (start.getHours() === 0 && start.getMinutes() === 0) return "Time to be confirmed";
  if (!event.endDate) return formatTime(start);
  return `${formatTime(start)} to ${formatTime(event.endDate)}`;
}

function eventLocation(event) {
  const values = [];
  [event.venue, event.address, [event.city, event.state].filter(Boolean).join(", ")]
    .filter(Boolean)
    .forEach((value) => {
      if (!values.some((existing) => existing.toLowerCase().includes(String(value).toLowerCase()))) {
        values.push(String(value));
      }
    });
  return values.join(" · ") || "Location to be confirmed";
}

function dateKey(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function dayLabel(date) {
  const diff = dayDifference(date);
  const prefix = diff === 0 ? "Today, " : diff === 1 ? "Tomorrow, " : "";
  return prefix + new Intl.DateTimeFormat("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
  }).format(date);
}

function rangeLabel(preset) {
  return {
    today: "Today",
    tomorrow: "Tomorrow",
    weekend: "This weekend",
    7: "Next 7 days",
    30: "Next 30 days",
    all: "All upcoming dates",
  }[preset] || "Upcoming events";
}

function matchesFilters(event) {
  if (!isInPreset(event, state.datePreset)) return false;
  if (state.search && !event.searchText.includes(state.search.toLowerCase())) return false;
  if (state.distance !== "all") {
    if (event.distance === null || event.distance > Number(state.distance)) return false;
  }
  if (state.category !== "all" && event.category !== state.category) return false;
  if (state.admission !== "all" && admissionType(event) !== state.admission) return false;
  if (state.confidence === "A" && event.confidence !== "A") return false;
  if (state.confidence === "AB" && !["A", "B"].includes(event.confidence)) return false;
  return true;
}

function sortEvents(events) {
  const confidenceScore = { A: 0, B: 1, C: 2 };
  return [...events].sort((a, b) => {
    if (state.sort === "distance") {
      const distanceA = a.distance ?? 999;
      const distanceB = b.distance ?? 999;
      return distanceA - distanceB || a.startDate - b.startDate || a.title.localeCompare(b.title);
    }
    if (state.sort === "quality") {
      return confidenceScore[a.confidence] - confidenceScore[b.confidence]
        || a.startDate - b.startDate
        || (b.importance || 0) - (a.importance || 0);
    }
    return a.startDate - b.startDate
      || (b.importance || 0) - (a.importance || 0)
      || a.title.localeCompare(b.title);
  });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function safeUrl(value) {
  try {
    const url = new URL(value, window.location.href);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "#";
  } catch {
    return "#";
  }
}

function cardHtml(event) {
  const image = state.images && event.image_url
    ? `<img class="event-image" src="${escapeHtml(safeUrl(event.image_url))}" alt="" loading="lazy" referrerpolicy="no-referrer">`
    : "";
  const noImageClass = image ? "" : " no-image";
  const admission = admissionType(event);
  const freePill = admission === "free" ? '<span class="meta-pill free">Free</span>' : "";
  return `
    <article class="event-card${noImageClass}" data-event-id="${escapeHtml(event.id)}">
      ${image}
      <div class="event-main">
        <div class="meta-row">
          <span class="meta-pill">${escapeHtml(event.category || "Community")}</span>
          <span class="meta-pill distance">${escapeHtml(distanceLabel(event))}</span>
          ${freePill}
        </div>
        <h4>${escapeHtml(event.title)}</h4>
        <p class="event-time">${escapeHtml(eventTime(event))}</p>
        <p class="event-location">${escapeHtml(eventLocation(event))}</p>
        <p class="source-line"><strong>${escapeHtml(confidenceLabel(event.confidence))}</strong> · ${escapeHtml(event.source_name || "Public source")}</p>
      </div>
      <div class="event-actions">
        <button class="details-button" type="button" data-open-event="${escapeHtml(event.id)}">Details</button>
        <a class="source-link" href="${escapeHtml(safeUrl(event.event_url || event.source_url))}" target="_blank" rel="noopener noreferrer">Source</a>
      </div>
    </article>`;
}

function renderGroups() {
  const visible = state.filtered.slice(0, state.visibleCount);
  const grouped = new Map();
  visible.forEach((event) => {
    const key = dateKey(event.startDate);
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(event);
  });

  elements.groups.innerHTML = [...grouped.entries()].map(([, events]) => {
    const date = events[0].startDate;
    return `
      <section class="day-group">
        <div class="day-heading">
          <h3>${escapeHtml(dayLabel(date))}</h3>
          <span>${events.length} shown${state.filtered.filter((event) => dateKey(event.startDate) === dateKey(date)).length > events.length ? " in this batch" : ""}</span>
        </div>
        <div class="day-events">${events.map(cardHtml).join("")}</div>
      </section>`;
  }).join("");

  elements.loadMore.hidden = state.visibleCount >= state.filtered.length;
  elements.empty.hidden = state.filtered.length > 0;
  elements.groups.hidden = state.filtered.length === 0;
}

function renderActiveFilters() {
  const chips = [];
  chips.push(rangeLabel(state.datePreset));
  if (state.distance !== "all") chips.push(`Within ${state.distance} miles`);
  if (state.category !== "all") chips.push(state.category);
  if (state.admission !== "all") chips.push({ free: "Free", priced: "Price listed", unknown: "Price not published" }[state.admission]);
  if (state.confidence === "A") chips.push("Official sources only");
  if (state.confidence === "AB") chips.push("Official and reported sources");
  if (state.search) chips.push(`Search: ${state.search}`);
  elements.activeFilters.innerHTML = chips.map((chip) => `<span class="filter-chip">${escapeHtml(chip)}</span>`).join("");
}

function updateUrl() {
  const params = new URLSearchParams();
  if (state.datePreset !== "7") params.set("when", state.datePreset);
  if (state.distance !== "25") params.set("distance", state.distance);
  if (state.category !== "all") params.set("category", state.category);
  if (state.admission !== "all") params.set("admission", state.admission);
  if (state.confidence !== "all") params.set("source", state.confidence);
  if (state.search) params.set("q", state.search);
  if (state.sort !== "date") params.set("sort", state.sort);
  const query = params.toString();
  history.replaceState(null, "", query ? `?${query}` : window.location.pathname);
}

function applyFilters({ resetVisible = true } = {}) {
  if (resetVisible) state.visibleCount = PAGE_SIZE;
  state.filtered = sortEvents(state.events.filter(matchesFilters));
  elements.resultCount.textContent = state.filtered.length.toLocaleString();
  elements.rangeLabel.textContent = rangeLabel(state.datePreset);
  renderActiveFilters();
  renderGroups();
  updateUrl();
}

function setPreset(preset) {
  state.datePreset = preset;
  document.querySelectorAll("[data-date-preset]").forEach((button) => {
    button.classList.toggle("active", button.dataset.datePreset === preset);
  });
  applyFilters();
}

function resetFilters() {
  state.search = "";
  state.distance = "25";
  state.category = "all";
  state.admission = "all";
  state.confidence = "all";
  state.sort = "date";
  state.images = true;
  elements.search.value = "";
  elements.distance.value = "25";
  elements.category.value = "all";
  elements.admission.value = "all";
  elements.confidence.value = "all";
  elements.sort.value = "date";
  elements.images.checked = true;
  setPreset("7");
}

function renderSummary() {
  const upcoming = state.events.filter((event) => dayDifference(event.startDate) >= 0);
  elements.todayCount.textContent = upcoming.filter((event) => isInPreset(event, "today")).length.toLocaleString();
  elements.weekendCount.textContent = upcoming.filter((event) => isInPreset(event, "weekend")).length.toLocaleString();
  elements.weekCount.textContent = upcoming.filter((event) => isInPreset(event, "7")).length.toLocaleString();
  elements.totalCount.textContent = upcoming.length.toLocaleString();
}

function populateCategories() {
  const categories = [...new Set(state.events.map((event) => event.category || "Community"))].sort();
  elements.category.insertAdjacentHTML("beforeend", categories.map((category) =>
    `<option value="${escapeHtml(category)}">${escapeHtml(category)}</option>`
  ).join(""));
}

function openDialog(eventId) {
  const event = state.events.find((item) => item.id === eventId);
  if (!event) return;
  const image = event.image_url
    ? `<img class="dialog-hero" src="${escapeHtml(safeUrl(event.image_url))}" alt="" referrerpolicy="no-referrer">`
    : "";
  const bodyClass = image ? "dialog-body" : "dialog-body no-hero";
  const description = event.description
    ? `<p class="dialog-description">${escapeHtml(event.description)}</p>`
    : '<p class="dialog-description">A description was not published. Use the source link for registration and the latest details.</p>';
  elements.dialogContent.innerHTML = `
    ${image}
    <div class="${bodyClass}">
      <p class="eyebrow">${escapeHtml(event.category || "Community")}</p>
      <h2>${escapeHtml(event.title)}</h2>
      <div class="dialog-facts">
        <div><span>Date</span><strong>${escapeHtml(dayLabel(event.startDate))}</strong></div>
        <div><span>Time</span><strong>${escapeHtml(eventTime(event))}</strong></div>
        <div><span>Location</span><strong>${escapeHtml(eventLocation(event))}</strong></div>
        <div><span>Admission</span><strong>${escapeHtml(admissionLabel(event))}</strong></div>
      </div>
      ${description}
      <p class="dialog-source">${escapeHtml(confidenceLabel(event.confidence))} source: ${escapeHtml(event.source_name || "Public listing")}. ${escapeHtml(distanceLabel(event))}.</p>
      <a class="dialog-primary" href="${escapeHtml(safeUrl(event.event_url || event.source_url))}" target="_blank" rel="noopener noreferrer">Open event source</a>
    </div>`;
  elements.dialog.showModal();
}

function readUrlState() {
  const params = new URLSearchParams(window.location.search);
  const preset = params.get("when");
  if (["today", "tomorrow", "weekend", "7", "30", "all"].includes(preset)) state.datePreset = preset;
  const distance = params.get("distance");
  if (["10", "25", "50", "75", "all"].includes(distance)) state.distance = distance;
  state.search = params.get("q") || "";
  state.category = params.get("category") || "all";
  state.admission = ["free", "priced", "unknown"].includes(params.get("admission")) ? params.get("admission") : "all";
  state.confidence = ["A", "AB"].includes(params.get("source")) ? params.get("source") : "all";
  state.sort = ["date", "distance", "quality"].includes(params.get("sort")) ? params.get("sort") : "date";
}

function syncControls() {
  elements.search.value = state.search;
  elements.distance.value = state.distance;
  elements.category.value = [...elements.category.options].some((option) => option.value === state.category) ? state.category : "all";
  state.category = elements.category.value;
  elements.admission.value = state.admission;
  elements.confidence.value = state.confidence;
  elements.sort.value = state.sort;
  elements.images.checked = state.images;
  document.querySelectorAll("[data-date-preset]").forEach((button) => {
    button.classList.toggle("active", button.dataset.datePreset === state.datePreset);
  });
}

function bindEvents() {
  document.querySelectorAll("[data-date-preset]").forEach((button) => {
    button.addEventListener("click", () => setPreset(button.dataset.datePreset));
  });
  elements.search.addEventListener("input", (event) => {
    state.search = event.target.value.trim();
    applyFilters();
  });
  elements.distance.addEventListener("change", (event) => { state.distance = event.target.value; applyFilters(); });
  elements.category.addEventListener("change", (event) => { state.category = event.target.value; applyFilters(); });
  elements.admission.addEventListener("change", (event) => { state.admission = event.target.value; applyFilters(); });
  elements.confidence.addEventListener("change", (event) => { state.confidence = event.target.value; applyFilters(); });
  elements.sort.addEventListener("change", (event) => { state.sort = event.target.value; applyFilters(); });
  elements.images.addEventListener("change", (event) => { state.images = event.target.checked; renderGroups(); });
  document.querySelector("#reset-filters").addEventListener("click", resetFilters);
  document.querySelector("#empty-reset").addEventListener("click", resetFilters);
  elements.loadMore.addEventListener("click", () => {
    state.visibleCount += PAGE_SIZE;
    renderGroups();
  });
  elements.groups.addEventListener("click", (event) => {
    const button = event.target.closest("[data-open-event]");
    if (button) openDialog(button.dataset.openEvent);
  });
  document.querySelector(".dialog-close").addEventListener("click", () => elements.dialog.close());
  elements.dialog.addEventListener("click", (event) => {
    if (event.target === elements.dialog) elements.dialog.close();
  });
}

async function loadData() {
  try {
    const [eventsResponse, healthResponse] = await Promise.all([
      fetch(DATA_URL, { cache: "no-store" }),
      fetch(HEALTH_URL, { cache: "no-store" }),
    ]);
    if (!eventsResponse.ok) throw new Error(`Event data returned ${eventsResponse.status}`);
    const rawEvents = await eventsResponse.json();
    const health = healthResponse.ok ? await healthResponse.json() : [];
    state.events = rawEvents
      .map(normalizeEvent)
      .filter(Boolean)
      .filter((event) => !BLOCKED_TITLE_PATTERNS.some((pattern) => pattern.test(event.title || "")));
    const contributing = health.filter((source) => Number(source.event_count || 0) > 0).length;
    elements.freshness.textContent = `${state.events.length.toLocaleString()} upcoming events loaded${contributing ? ` from ${contributing} contributing sources` : ""}. Data refreshes through GitHub Actions.`;
    populateCategories();
    readUrlState();
    syncControls();
    renderSummary();
    bindEvents();
    applyFilters();
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
  } catch (error) {
    console.error(error);
    elements.freshness.textContent = "The live event data could not be loaded.";
    elements.groups.innerHTML = `
      <div class="empty-state">
        <h3>Event data is temporarily unavailable.</h3>
        <p>Open the current dashboard while the V2 data connection is checked.</p>
        <a class="source-link" href="../">Open current dashboard</a>
      </div>`;
    elements.resultCount.textContent = "0";
  }
}

loadData();
