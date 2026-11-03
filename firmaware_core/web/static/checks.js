// Check jobs: start a check, show progress and the per-entry lines as the command line prints them.

import { state } from "./state.js";
import { $, announceChange, api, el, toast } from "./util.js";

const POLL_MS = 500;
let polling = null;

function setRunning(running) {
  state.checkRunning = running;
  for (const button of document.querySelectorAll("[data-check]")) button.disabled = running;
}

function lineClass(result) {
  if (result.status === "error") return "line error";
  if (result.status === "disabled") return "line off";
  return result.new_versions.length ? "line new" : "line";
}

function render(job) {
  const box = $("#checks");
  box.hidden = false;
  const running = job.status === "running";
  const failed = job.status === "failed";
  $("#checks-title").textContent = running ? "Checking…" : "Last check";
  $("#checks-status").textContent = failed
    ? `Failed: ${job.error}`
    : `${job.done} of ${job.total} entries`;
  const percent = job.total ? Math.round((job.done / job.total) * 100) : running ? 0 : 100;
  $("#checks-bar").style.width = `${percent}%`;
  box.classList.toggle("running", running);
  box.classList.toggle("failed", failed);
  const lines = $("#checks-lines");
  lines.replaceChildren(
    ...job.results.map((result) =>
      el("div", { class: lineClass(result) }, el("span", { class: "line-name", text: result.name }), el("span", { text: result.summary })),
    ),
  );
}

function summarize(job) {
  if (job.status === "failed") return [`Check failed: ${job.error}`, true];
  const errors = job.results.filter((result) => result.status === "error").length;
  const found = job.results.reduce((sum, result) => sum + result.new_versions.length, 0);
  const parts = [found ? `${found} new ${found === 1 ? "version" : "versions"}` : "no new versions"];
  if (errors) parts.push(`${errors} ${errors === 1 ? "error" : "errors"}`);
  return [`Check finished: ${parts.join(", ")}`, errors > 0];
}

async function poll(jobId) {
  clearTimeout(polling);
  let job;
  try {
    job = (await api(`/api/checks/${encodeURIComponent(jobId)}`)).job;
  } catch (error) {
    setRunning(false);
    toast(error.message, true);
    return;
  }
  render(job);
  if (job.status === "running") {
    polling = setTimeout(() => poll(jobId), POLL_MS);
    return;
  }
  setRunning(false);
  announceChange();
  toast(...summarize(job));
}

export async function startCheck(ids) {
  if (state.checkRunning) return;
  setRunning(true);
  try {
    const { job } = await api("/api/checks", { method: "POST", body: ids ? { ids } : {} });
    render(job);
    poll(job.id);
  } catch (error) {
    setRunning(false);
    toast(error.message, true);
  }
}

// After a page reload a job may still be running, or the last result may be of interest.
export async function resumeLatest() {
  try {
    const { job } = await api("/api/checks");
    if (!job) return;
    render(job);
    if (job.status === "running") {
      setRunning(true);
      poll(job.id);
    }
  } catch {
    // the entry list reports connection problems
  }
}

export function closeChecks() {
  if (!state.checkRunning) $("#checks").hidden = true;
}
