// The details panel: facts, version history, and all editing actions of one entry.

import { startCheck } from "./checks.js";
import { orderAfterMove, state } from "./state.js";
import { stateBadge } from "./table.js";
import { $, announceChange, api, dash, el, entryUrl, formatTime, hostOf, linkEl, toast } from "./util.js";

let current = null; // { id, opener }

export const isOpen = () => !$("#panel").hidden;

function fact(label, value) {
  return [el("dt", { text: label }), el("dd", {}, value)];
}

// Returns whether the change was saved, so a control can restore its old value after an error.
async function change(request, okText) {
  try {
    await request();
    if (okText) toast(okText);
    announceChange();
    return true;
  } catch (error) {
    toast(error.message, true);
    if (error.status === 404) closePanel();
    return false;
  }
}

const patch = (id, changes, okText) =>
  change(() => api(entryUrl(id), { method: "PATCH", body: changes }), okText);

function textForm({ label, value, placeholder, onSave, extra, hint, buttonText = "Save" }) {
  const input = el("input", { type: "text", value: value ?? "", placeholder, "aria-label": label, autocomplete: "off" });
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
    el("div", { class: "row" }, input, el("button", { type: "submit", class: "btn", text: buttonText }), extra),
    hint ? el("p", { class: "hint", text: hint }) : null,
  );
  return form;
}

// The installed version can only be picked from the versions known for the device type.
function installedField(entry, extra) {
  const known = entry.history.map((item) => item.version);
  const current = entry.installed_version || "";
  const options = [el("option", { value: "", text: "Not set" })];
  if (current && !known.includes(current)) {
    options.push(el("option", { value: current, text: `${current} (not in the list)` }));
  }
  known.forEach((version, index) => {
    options.push(el("option", { value: version, text: index === 0 ? `${version} – latest` : version }));
  });
  const select = el(
    "select",
    {
      "aria-label": "Installed version",
      disabled: known.length === 0 && !current,
      onchange: (event) =>
        patch(
          entry.id,
          { installed_version: event.target.value },
          event.target.value ? "Installed version saved" : "Installed version cleared",
        ),
    },
    options,
  );
  select.value = current;
  return el(
    "div",
    { class: "field" },
    el("label", { text: "Installed version" }),
    el("div", { class: "row" }, select, extra),
    known.length === 0 ? el("p", { class: "hint", text: "Run a check first to see the available versions." }) : null,
  );
}

let profiles = [];

async function loadProfiles() {
  try {
    profiles = (await api("/api/profiles")).profiles;
  } catch {
    profiles = []; // the rest of the panel still works
  }
}

// Which device type (profile) the entry follows; changing it changes source and version list.
function deviceTypeField(entry) {
  if (entry.kind !== "profile") return null;
  const options = profiles
    .slice()
    .sort((a, b) => a.name.localeCompare(b.name))
    .map((profile) => el("option", { value: profile.id, text: profile.name }));
  if (!profiles.some((profile) => profile.id === entry.profile)) {
    options.unshift(el("option", { value: entry.profile, text: `${entry.profile} (unknown)` }));
  }
  const select = el(
    "select",
    {
      "aria-label": "Device type",
      onchange: async (event) => {
        const saved = await patch(
          entry.id,
          { profile: event.target.value },
          "Device type changed; run a check to load its versions",
        );
        if (!saved) select.value = entry.profile;
      },
    },
    options,
  );
  select.value = entry.profile;
  return el(
    "div",
    { class: "field" },
    el("label", { text: "Device type" }),
    el("div", { class: "row" }, select),
    el("p", { class: "hint", text: "The source and version list this device follows. Its installed version is kept." }),
  );
}

function section(title, ...children) {
  return el("section", { class: "edit" }, el("h3", { text: title }), ...children);
}

function confirmButton(label, onConfirm, className = "btn danger", confirmText = "Click again to confirm") {
  let timer = null;
  const button = el("button", { type: "button", class: className, text: label });
  button.addEventListener("click", () => {
    if (timer === null) {
      button.textContent = confirmText;
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

// For a version a profile detected by mistake: removes it from the history of the device type.
function forgetButton(entry, version) {
  const button = confirmButton(
    "Remove",
    () =>
      change(
        () => api(`${entryUrl(entry.id)}/forget-version`, { method: "POST", body: { version } }),
        `Removed ${version} from the history`,
      ),
    "link-btn",
    "Click again to remove",
  );
  button.title = "Remove this version from the history (for versions that were detected by mistake)";
  button.setAttribute("aria-label", `Remove version ${version} from the history`);
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
    fact("Category", dash(entry.category)),
    entry.kind === "profile" ? fact("Profile", el("span", { class: "mono", text: entry.profile })) : null,
    fact("Last check", formatTime(entry.last_checked)),
    fact("Last version", el("span", { class: "mono", text: dash(entry.latest_version) })),
    fact("First seen", entry.first_seen ? formatTime(entry.first_seen) : "–"),
    fact("Installed", el("span", { class: "mono", text: dash(entry.installed_version) })),
    entry.installed_state ? fact("State", stateBadge(entry)) : null,
    fact("Known versions", String(entry.known_versions)),
    entry.kind === "profile" && entry.type_devices > 1 ? fact("Devices of this type", String(entry.type_devices)) : null,
    entry.kind === "direct" ? fact("Source type", dash(entry.source_type)) : null,
    entry.device_url ? fact("Device page", linkEl(entry.device_url, hostOf(entry.device_url) || entry.device_url)) : null,
    entry.source_page ? fact("Firmware source", linkEl(entry.source_page, hostOf(entry.source_page) || entry.source_page)) : null,
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
      deviceTypeField(entry),
      installedField(entry, latestButton),
      textForm({
        label: "Device URL",
        value: entry.device_url || "",
        placeholder: "e.g. http://192.168.1.20",
        hint: "Where the device itself can be reached in your network; it becomes a link.",
        onSave: (value) =>
          patch(entry.id, { device_url: value.trim() }, value.trim() ? "Device URL saved" : "Device URL cleared"),
      }),
      el("label", { class: "switch" }, enabled, el("span", { text: "Enabled (skipped by checks when off)" })),
    ),
    section(
      "Order and copies",
      el("div", { class: "row" }, move(entry, -1, "Move up"), move(entry, 1, "Move down")),
      textForm({
        label: "Name for the copy",
        value: `${entry.name} (copy)`,
        placeholder: "Name of the new device",
        buttonText: "Duplicate",
        onSave: (value) =>
          change(() => api(`${entryUrl(entry.id)}/duplicate`, { method: "POST", body: { name: value.trim() } }), `Duplicated as ${value.trim()}`),
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
  if (entry.history.length > 0) {
    body.append(
      el("p", { class: "muted", text: "A wrongly detected version can be removed; it comes back if the source still lists it." }),
    );
  }
  if (entry.kind === "profile" && entry.type_devices > 1) {
    body.append(
      el("p", { class: "muted", text: `Shared by all ${entry.type_devices} devices of this type; only the installed version is per device.` }),
    );
  }
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
            el("span", { class: "when" }, `discovered ${formatTime(item.discovered_at)} `, forgetButton(entry, item.version)),
          ),
        ),
      ),
    );
  }
}

export async function openPanel(entryId, opener) {
  current = { id: entryId, opener: opener || null };
  try {
    await loadProfiles();
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
