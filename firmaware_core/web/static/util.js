// Shared helpers: DOM creation, API calls, formatting, toasts.

export const $ = (selector) => document.querySelector(selector);

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "value") node.value = value;
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

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

export async function api(path, { method = "GET", body } = {}) {
  const headers = { "X-Requested-With": "firmaware" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
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

export const entryUrl = (id) => `/api/entries/${encodeURIComponent(id)}`;

// Same format as the command line: the stored local time without conversion.
export function formatTime(iso) {
  return iso ? iso.slice(0, 19).replace("T", " ") : "never checked";
}

export function dash(value) {
  return value ? value : "–";
}

export function toast(text, isError = false) {
  const item = el("div", { class: isError ? "toast error" : "toast", text });
  $("#toasts").append(item);
  setTimeout(() => item.remove(), isError ? 7000 : 3500);
}

// Components talk through events so they do not import each other.
export function announceChange() {
  document.dispatchEvent(new CustomEvent("entries-changed"));
}
