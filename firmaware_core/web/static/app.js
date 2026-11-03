// firmAware web interface: status table, checks, and entry editing. No dependencies, no build step.

import { closeChecks, resumeLatest, startCheck } from "./checks.js";
import { closePanel, isOpen, openPanel, refreshPanel } from "./panel.js";
import { state } from "./state.js";
import { renderTable } from "./table.js";
import { $, api } from "./util.js";

function showMessage(text, isError = false) {
  const box = $("#message");
  box.textContent = text;
  box.className = isError ? "message error" : "message";
  box.hidden = !text;
}

function render() {
  renderTable(openPanel);
}

async function load() {
  try {
    state.entries = (await api("/api/entries")).entries;
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

document.addEventListener("entries-changed", async () => {
  await load();
  if (isOpen()) await refreshPanel();
});

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

$("#check-all").addEventListener("click", () => startCheck(null));
$("#checks-close").addEventListener("click", closeChecks);
$("#panel-close").addEventListener("click", closePanel);
$("#scrim").addEventListener("click", closePanel);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closePanel();
});

load().then(resumeLatest);
