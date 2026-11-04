// The status table (start page), grouped by category.

import { CATEGORIES, categoryKey, isOutdated, orderAfterDrop, state, visibleEntries } from "./state.js";
import { $, announceChange, api, dash, el, formatTime, linkEl, toast } from "./util.js";

export function stateBadge(entry) {
  if (entry.error) return el("span", { class: "badge error", title: entry.error, text: "config error" });
  if (!entry.installed_state) return el("span", { class: "muted", text: "–" });
  return el("span", { class: `badge ${entry.installed_state}`, text: entry.installed_state });
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

// Dragging is only unambiguous without a filter or search.
const canReorder = () => state.filter === "all" && !state.query.trim();

let dragId = null;

async function drop(targetId) {
  const ids = orderAfterDrop(state.entries, dragId, targetId);
  dragId = null;
  if (!ids) return;
  try {
    await api("/api/order", { method: "PUT", body: { ids } });
    announceChange();
  } catch (error) {
    toast(error.message, true);
  }
}

function entryRow(entry, openPanel) {
  const nameButton = el("button", {
    type: "button",
    class: "name-btn",
    text: entry.name,
    "aria-haspopup": "dialog",
    onclick: (event) => openPanel(entry.id, event.currentTarget),
  });
  const row = el(
    "tr",
    { class: entry.enabled ? "" : "off", dataset: { id: entry.id } },
    el(
      "td",
      { class: "cell-name", "data-label": "Entry" },
      nameButton,
      entry.enabled ? "" : el("span", { class: "muted", text: " [off]" }),
    ),
    el("td", { class: "nowrap", "data-label": "Last check" }, formatTime(entry.last_checked)),
    el("td", { class: "mono", "data-label": "Last version" }, dash(entry.latest_version)),
    el("td", { class: "nowrap", "data-label": "First seen" }, entry.first_seen ? formatTime(entry.first_seen) : "–"),
    el("td", { class: "mono", "data-label": "Installed" }, dash(entry.installed_version)),
    el("td", { "data-label": "State" }, stateBadge(entry)),
    el(
      "td",
      { class: "cell-links nowrap", "data-label": "Links" },
      linkEl(entry.device_url, "Device", { title: `Open the device: ${entry.device_url}` }),
      entry.device_url && entry.source_page ? " · " : "",
      linkEl(entry.source_page, "Source", { title: `Firmware source: ${entry.source_page}` }),
      entry.device_url || entry.source_page ? "" : "–",
    ),
  );
  if (canReorder()) {
    row.draggable = true;
    row.addEventListener("dragstart", (event) => {
      dragId = entry.id;
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", entry.id);
      row.classList.add("dragging");
    });
    row.addEventListener("dragend", () => {
      dragId = null;
      row.classList.remove("dragging");
      for (const other of document.querySelectorAll("tr.drop-target")) other.classList.remove("drop-target");
    });
    row.addEventListener("dragover", (event) => {
      if (dragId && dragId !== entry.id) {
        event.preventDefault();
        row.classList.add("drop-target");
      }
    });
    row.addEventListener("dragleave", () => row.classList.remove("drop-target"));
    row.addEventListener("drop", (event) => {
      event.preventDefault();
      drop(entry.id);
    });
  }
  return row;
}

function groupTable(entries, openPanel) {
  const head = el(
    "tr",
    {},
    ...["Entry", "Last check", "Last version", "First seen", "Installed", "State", "Links"].map((label) =>
      el("th", { scope: "col", text: label }),
    ),
  );
  return el(
    "div",
    { class: "card" },
    el(
      "table",
      {},
      el("thead", {}, head),
      el("tbody", {}, entries.map((entry) => entryRow(entry, openPanel))),
    ),
  );
}

export function renderTable(openPanel) {
  renderSummary();
  const container = $("#groups");
  container.replaceChildren();
  const shown = visibleEntries();

  if (state.entries.length === 0) {
    container.append(
      el(
        "div",
        { class: "card" },
        el("p", { class: "empty", text: "No entries yet. Use “Add entry” to track your first device." }),
      ),
    );
    return;
  }
  if (shown.length === 0) {
    container.append(el("div", { class: "card" }, el("p", { class: "empty", text: "No entries match the filter." })));
    return;
  }

  const sections = CATEGORIES.map(([key, title]) => [title, shown.filter((entry) => categoryKey(entry) === key)]);
  sections.push(["Other", shown.filter((entry) => categoryKey(entry) === "other")]);
  for (const [title, entries] of sections) {
    if (entries.length === 0) continue;
    container.append(el("section", { class: "group" }, el("h2", { text: title }), groupTable(entries, openPanel)));
  }
}
