// firmAware web interface: status table, details. No dependencies, no build step.

const CATEGORIES = [
  ["firmware", "Firmware"],
  ["software", "Software"],
  ["operating_system", "Operating System"],
];

const state = {
  entries: [],
  filter: "all",
  query: "",
  selectedId: null,
  opener: null,
};

const $ = (selector) => document.querySelector(selector);

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "dataset") Object.assign(node.dataset, value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function api(path, options = {}) {
  const headers = { "X-Requested-With": "firmaware" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    ...options,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  let data = {};
  try {
    data = await response.json();
  } catch {
    // an empty or non-JSON body is handled below
  }
  if (!response.ok) throw new ApiError(response.status, data.error || response.statusText);
  return data;
}

// Same format as the command line: the stored local time without conversion.
function formatTime(iso) {
  return iso ? iso.slice(0, 19).replace("T", " ") : "never checked";
}

function dash(value) {
  return value ? value : "–";
}

function stateBadge(entry) {
  if (entry.error) return el("span", { class: "badge error", title: entry.error, text: "config error" });
  if (!entry.installed_state) return el("span", { class: "muted", text: "–" });
  return el("span", { class: `badge ${entry.installed_state}`, text: entry.installed_state });
}

function isOutdated(entry) {
  return entry.installed_state === "outdated";
}

function visibleEntries() {
  const query = state.query.trim().toLowerCase();
  return state.entries.filter((entry) => {
    if (state.filter === "outdated" && !isOutdated(entry)) return false;
    if (state.filter === "disabled" && entry.enabled) return false;
    if (!query) return true;
    return [entry.id, entry.name, entry.latest_version, entry.installed_version]
      .filter(Boolean)
      .some((value) => value.toLowerCase().includes(query));
  });
}

function renderSummary() {
  const total = state.entries.length;
  const outdated = state.entries.filter(isOutdated).length;
  const disabled = state.entries.filter((entry) => !entry.enabled).length;
  const parts = [`${total} ${total === 1 ? "entry" : "entries"}`];
  if (outdated) parts.push(`${outdated} outdated`);
  if (disabled) parts.push(`${disabled} disabled`);
  $("#summary").textContent = parts.join(" · ");
}

function entryRow(entry) {
  const nameButton = el("button", {
    type: "button",
    class: "name-btn",
    text: entry.name,
    "aria-haspopup": "dialog",
    onclick: (event) => openPanel(entry.id, event.currentTarget),
  });
  return el(
    "tr",
    { class: entry.enabled ? "" : "off", dataset: { id: entry.id } },
    el("td", { class: "cell-id mono muted", "data-label": "ID" }, entry.id, entry.enabled ? "" : " [off]"),
    el("td", { class: "cell-name", "data-label": "Entry" }, nameButton),
    el("td", { class: "nowrap", "data-label": "Last check" }, formatTime(entry.last_checked)),
    el("td", { class: "mono", "data-label": "Last version" }, dash(entry.latest_version)),
    el("td", { class: "nowrap", "data-label": "First seen" }, entry.first_seen ? formatTime(entry.first_seen) : "–"),
    el("td", { class: "mono", "data-label": "Installed" }, dash(entry.installed_version)),
    el("td", { "data-label": "State" }, stateBadge(entry)),
  );
}

function groupTable(entries) {
  const head = el(
    "tr",
    {},
    ...["ID", "Entry", "Last check", "Last version", "First seen", "Installed", "State"].map((label) =>
      el("th", { scope: "col", text: label }),
    ),
  );
  return el(
    "div",
    { class: "card" },
    el("table", {}, el("thead", {}, head), el("tbody", {}, entries.map(entryRow))),
  );
}

function render() {
  renderSummary();
  const container = $("#groups");
  container.replaceChildren();
  const shown = visibleEntries();

  if (state.entries.length === 0) {
    container.append(
      el("div", { class: "card" }, el("p", { class: "empty", text: "No entries yet. Add entries to devices.json." })),
    );
    return;
  }
  if (shown.length === 0) {
    container.append(el("div", { class: "card" }, el("p", { class: "empty", text: "No entries match the filter." })));
    return;
  }

  const known = new Set(CATEGORIES.map(([key]) => key));
  const sections = CATEGORIES.map(([key, title]) => [title, shown.filter((entry) => entry.category === key)]);
  sections.push(["Other", shown.filter((entry) => !known.has(entry.category))]);
  for (const [title, entries] of sections) {
    if (entries.length === 0) continue;
    container.append(el("section", { class: "group" }, el("h2", { text: title }), groupTable(entries)));
  }
}

// --- details panel -----------------------------------------------------

function fact(label, value) {
  return [el("dt", { text: label }), el("dd", {}, value)];
}

function renderPanel(entry) {
  $("#panel-title").textContent = entry.name;
  const facts = [
    fact("ID", el("span", { class: "mono", text: entry.id })),
    fact("Category", dash(entry.category)),
    entry.kind === "profile" ? fact("Profile", el("span", { class: "mono", text: entry.profile })) : null,
    fact("Enabled", entry.enabled ? "yes" : "no"),
    fact("Last check", formatTime(entry.last_checked)),
    fact("Last version", el("span", { class: "mono", text: dash(entry.latest_version) })),
    fact("First seen", entry.first_seen ? formatTime(entry.first_seen) : "–"),
    fact("Installed", el("span", { class: "mono", text: dash(entry.installed_version) })),
    entry.installed_state ? fact("State", stateBadge(entry)) : null,
    fact("Known versions", String(entry.known_versions)),
    entry.kind === "direct" ? fact("Source type", dash(entry.source_type)) : null,
    entry.kind === "direct" ? fact("Source URL", dash(entry.source_url)) : null,
  ].filter(Boolean);

  const body = $("#panel-body");
  body.replaceChildren();
  if (entry.error) body.append(el("div", { class: "message error", text: entry.error }));
  body.append(el("dl", { class: "facts" }, facts.flat()));
  body.append(el("h3", { text: "Version history" }));
  if (entry.history.length === 0) {
    body.append(el("p", { class: "muted", text: "No known versions yet. Run a check first." }));
  } else {
    body.append(
      el(
        "ul",
        { class: "timeline" },
        entry.history.map((item) =>
          el(
            "li",
            { class: item.newest ? "newest" : "" },
            el("span", { class: "mono", text: item.version }),
            item.newest ? " " : "",
            item.newest ? el("span", { class: "badge", text: "newest discovered" }) : "",
            el("span", { class: "when", text: `discovered ${formatTime(item.discovered_at)}` }),
          ),
        ),
      ),
    );
  }
}

async function openPanel(entryId, opener) {
  state.selectedId = entryId;
  state.opener = opener || null;
  try {
    renderPanel(await api(`/api/entries/${encodeURIComponent(entryId)}`));
  } catch (error) {
    showMessage(error.message, true);
    return;
  }
  $("#panel").hidden = false;
  $("#scrim").hidden = false;
  $("#panel-close").focus();
}

function closePanel() {
  if ($("#panel").hidden) return;
  $("#panel").hidden = true;
  $("#scrim").hidden = true;
  state.selectedId = null;
  if (state.opener && document.contains(state.opener)) state.opener.focus();
  state.opener = null;
}

// --- loading and events ------------------------------------------------

function showMessage(text, isError = false) {
  const box = $("#message");
  box.textContent = text;
  box.className = isError ? "message error" : "message";
  box.hidden = !text;
}

async function load() {
  try {
    const data = await api("/api/entries");
    state.entries = data.entries;
    showMessage("");
  } catch (error) {
    state.entries = [];
    showMessage(
      error.status === 401
        ? "Access token required. Open this address once with ?token=<your token>."
        : `Could not load entries: ${error.message}`,
      true,
    );
  }
  render();
}

$("#search").addEventListener("input", (event) => {
  state.query = event.target.value;
  render();
});

for (const button of document.querySelectorAll(".seg button")) {
  button.addEventListener("click", () => {
    state.filter = button.dataset.filter;
    for (const other of document.querySelectorAll(".seg button")) {
      other.setAttribute("aria-pressed", String(other === button));
    }
    render();
  });
}

$("#panel-close").addEventListener("click", closePanel);
$("#scrim").addEventListener("click", closePanel);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closePanel();
});

load();
