// "Add entry" dialog: pick a device type (profile), name the device, create the entry.

import { openPanel } from "./panel.js";
import { state } from "./state.js";
import { $, announceChange, api, el, toast } from "./util.js";

let profiles = [];
let selected = null;
let suggestedName = "";

function showError(text) {
  const box = $("#add-error");
  box.textContent = text;
  box.hidden = !text;
}

function select(profile) {
  selected = profile;
  const nameField = $("#add-name");
  // Keep a name the user typed; replace only the suggestion.
  if (!nameField.value || nameField.value === suggestedName) {
    suggestedName = profile.name;
    nameField.value = suggestedName;
  }
  $("#add-submit").disabled = false;
  showError("");
}

function renderProfiles() {
  const query = $("#profile-search").value.trim().toLowerCase();
  const list = $("#profile-list");
  list.querySelectorAll("label").forEach((node) => node.remove());
  const shown = profiles.filter((profile) =>
    [profile.id, profile.name, profile.manufacturer, profile.model]
      .filter(Boolean)
      .some((value) => value.toLowerCase().includes(query)),
  );
  if (shown.length === 0) {
    list.append(el("label", { class: "profile none" }, el("span", { class: "muted", text: "No device type matches." })));
    return;
  }
  for (const profile of shown) {
    const inUse = state.entries.filter((entry) => entry.profile === profile.id).length;
    const radio = el("input", { type: "radio", name: "profile", value: profile.id, onchange: () => select(profile) });
    radio.checked = selected?.id === profile.id;
    list.append(
      el(
        "label",
        { class: "profile" },
        radio,
        el(
          "span",
          { class: "profile-text" },
          el("strong", { text: profile.name }),
          el("span", { class: "muted", text: [profile.manufacturer, profile.model].filter(Boolean).join(" · ") || profile.category }),
        ),
        inUse ? el("span", { class: "badge", text: `in use ×${inUse}` }) : null,
      ),
    );
  }
}

export async function openAddDialog() {
  selected = null;
  suggestedName = "";
  $("#add-form").reset();
  $("#add-submit").disabled = true;
  showError("");
  try {
    profiles = (await api("/api/profiles")).profiles;
  } catch (error) {
    toast(error.message, true);
    return;
  }
  renderProfiles();
  $("#add-dialog").showModal();
  $("#profile-search").focus();
}

async function submit(event) {
  event.preventDefault();
  if (!selected) return;
  const name = $("#add-name").value.trim();
  let created;
  try {
    created = await api("/api/entries", { method: "POST", body: { profile: selected.id, name } });
  } catch (error) {
    showError(error.message);
    return;
  }
  $("#add-dialog").close();
  toast(`Added ${created.name}`);
  announceChange();
  openPanel(created.id, null);
}

export function setupAddDialog() {
  $("#add-entry").addEventListener("click", openAddDialog);
  $("#add-form").addEventListener("submit", submit);
  $("#profile-search").addEventListener("input", renderProfiles);
  for (const id of ["#add-close", "#add-cancel"]) $(id).addEventListener("click", () => $("#add-dialog").close());
  $("#add-name").addEventListener("input", () => showError(""));
}
