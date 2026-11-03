// The details panel: facts, version history, and all editing actions of one entry.

import { startCheck } from "./checks.js";
import { orderAfterMove, state } from "./state.js";
import { stateBadge } from "./table.js";
import { $, announceChange, api, dash, el, entryUrl, formatTime, toast } from "./util.js";

const ID_PATTERN = "[a-z0-9][a-z0-9._-]*";
let current = null; // { id, opener }

export const isOpen = () => !$("#panel").hidden;

function fact(label, value) {
  return [el("dt", { text: label }), el("dd", {}, value)];
}

async function change(request, okText) {
  try {
    await request();
    if (okText) toast(okText);
    announceChange();
  } catch (error) {
    toast(error.message, true);
    if (error.status === 404) closePanel();
  }
}

const patch = (id, changes, okText) =>
  change(() => api(entryUrl(id), { method: "PATCH", body: changes }), okText);

function textForm({ label, value, placeholder, onSave, extra, pattern }) {
  const input = el("input", { type: "text", value: value ?? "", placeholder, "aria-label": label, pattern, autocomplete: "off" });
  const form = el(
    "form",
    {
      class: "field",
      onsubmit: (event) => {
        event.preventDefault();
        onSave(input.value);
      },
    },
    el("label", { text: label }),
    el("div", { class: "row" }, input, el("button", { type: "submit", class: "btn", text: "Save" }), extra),
  );
  return form;
}

function section(title, ...children) {
  return el("section", { class: "edit" }, el("h3", { text: title }), ...children);
}

function confirmButton(label, onConfirm) {
  let timer = null;
  const button = el("button", { type: "button", class: "btn danger", text: label });
  button.addEventListener("click", () => {
    if (timer === null) {
      button.textContent = "Click again to confirm";
      timer = setTimeout(() => {
        button.textContent = label;
        timer = null;
      }, 4000);
      return;
    }
    clearTimeout(timer);
    onConfirm();
  });
  return button;
}

function move(entry, delta, label) {
  const ids = orderAfterMove(state.entries, entry.id, delta);
  return el("button", {
    type: "button",
    class: "btn",
    text: label,
    disabled: ids === null,
    onclick: () => change(() => api("/api/order", { method: "PUT", body: { ids } })),
  });
}

function renderPanel(entry) {
  $("#panel-title").textContent = entry.name;
  const body = $("#panel-body");
  body.replaceChildren();
  if (entry.error) body.append(el("div", { class: "message error", text: entry.error }));

  const facts = [
    fact("ID", el("span", { class: "mono", text: entry.id })),
    fact("Category", dash(entry.category)),
    entry.kind === "profile" ? fact("Profile", el("span", { class: "mono", text: entry.profile })) : null,
    fact("Last check", formatTime(entry.last_checked)),
    fact("Last version", el("span", { class: "mono", text: dash(entry.latest_version) })),
    fact("First seen", entry.first_seen ? formatTime(entry.first_seen) : "–"),
    fact("Installed", el("span", { class: "mono", text: dash(entry.installed_version) })),
    entry.installed_state ? fact("State", stateBadge(entry)) : null,
    fact("Known versions", String(entry.known_versions)),
    entry.kind === "direct" ? fact("Source type", dash(entry.source_type)) : null,
    entry.kind === "direct" ? fact("Source URL", dash(entry.source_url)) : null,
  ].filter(Boolean);
  body.append(el("dl", { class: "facts" }, facts.flat()));

  body.append(
    el(
      "div",
      { class: "actions" },
      el("button", {
        type: "button",
        class: "btn primary",
        text: "Check now",
        "data-check": true,
        disabled: state.checkRunning,
        onclick: () => startCheck([entry.id]),
      }),
    ),
  );

  const enabled = el("input", {
    type: "checkbox",
    checked: entry.enabled,
    onchange: (event) => patch(entry.id, { enabled: event.target.checked }, event.target.checked ? "Entry enabled" : "Entry disabled"),
  });
  enabled.checked = entry.enabled;
  const latestButton = el("button", {
    type: "button",
    class: "btn",
    text: "Mark as updated to latest",
    disabled: !entry.latest_version,
    title: entry.latest_version ? `Set to ${entry.latest_version}` : "Run a check first",
    onclick: () => change(() => api(`${entryUrl(entry.id)}/installed-latest`, { method: "POST" }), "Installed version updated"),
  });

  body.append(
    section(
      "Edit",
      textForm({
        label: "Name",
        value: entry.custom_name || "",
        placeholder: entry.name,
        onSave: (value) => patch(entry.id, { name: value }, "Name saved"),
      }),
      textForm({
        label: "Installed version",
        value: entry.installed_version || "",
        placeholder: "not set",
        onSave: (value) => patch(entry.id, { installed_version: value }, value.trim() ? "Installed version saved" : "Installed version cleared"),
        extra: latestButton,
      }),
      el("label", { class: "switch" }, enabled, el("span", { text: "Enabled (skipped by checks when off)" })),
    ),
    section(
      "Order and copies",
      el("div", { class: "row" }, move(entry, -1, "Move up"), move(entry, 1, "Move down")),
      textForm({
        label: "Duplicate as new ID",
        value: "",
        placeholder: "e.g. my-second-device",
        pattern: ID_PATTERN,
        onSave: (value) =>
          change(() => api(`${entryUrl(entry.id)}/duplicate`, { method: "POST", body: { id: value.trim() } }), `Duplicated as ${value.trim()}`),
      }),
    ),
    section(
      "Remove",
      el(
        "div",
        { class: "row" },
        confirmButton("Delete entry", () =>
          change(async () => {
            await api(entryUrl(entry.id), { method: "DELETE" });
            closePanel();
          }, "Entry deleted"),
        ),
      ),
    ),
  );

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

export async function openPanel(entryId, opener) {
  current = { id: entryId, opener: opener || null };
  try {
    renderPanel(await api(entryUrl(entryId)));
  } catch (error) {
    toast(error.message, true);
    current = null;
    return;
  }
  $("#panel").hidden = false;
  $("#scrim").hidden = false;
  $("#panel-close").focus();
}

// Re-render after a change while keeping the keyboard focus on a sensible element.
export async function refreshPanel() {
  if (!current || !isOpen()) return;
  const focused = document.activeElement;
  const label = focused && $("#panel").contains(focused) ? focused.getAttribute("aria-label") : null;
  try {
    renderPanel(await api(entryUrl(current.id)));
  } catch {
    closePanel();
    return;
  }
  if (label) $(`#panel [aria-label="${label}"]`)?.focus();
}

export function closePanel() {
  if (!isOpen()) return;
  $("#panel").hidden = true;
  $("#scrim").hidden = true;
  const opener = current?.opener;
  current = null;
  if (opener && document.contains(opener)) opener.focus();
}
