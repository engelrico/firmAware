// Shared state and pure helpers for filtering and ordering.

export const CATEGORIES = [
  ["firmware", "Firmware"],
  ["software", "Software"],
  ["operating_system", "Operating System"],
];
const KNOWN = new Set(CATEGORIES.map(([key]) => key));

export const state = {
  entries: [],
  filter: "all",
  query: "",
  checkRunning: false,
};

export const categoryKey = (entry) => (KNOWN.has(entry.category) ? entry.category : "other");
export const isOutdated = (entry) => entry.installed_state === "outdated";

export function visibleEntries() {
  const query = state.query.trim().toLowerCase();
  return state.entries.filter((entry) => {
    if (state.filter === "outdated" && !isOutdated(entry)) return false;
    if (state.filter === "disabled" && entry.enabled) return false;
    if (!query) return true;
    return [entry.name, entry.latest_version, entry.installed_version]
      .filter(Boolean)
      .some((value) => value.toLowerCase().includes(query));
  });
}

// Entries are listed per category, but the file order is global: a move only
// permutes the slots the category already occupies.
function placeGroup(entries, key, reordered) {
  let next = 0;
  return entries.map((entry) => (categoryKey(entry) === key ? reordered[next++].id : entry.id));
}

export function orderAfterMove(entries, id, delta) {
  const entry = entries.find((item) => item.id === id);
  if (!entry) return null;
  const key = categoryKey(entry);
  const group = entries.filter((item) => categoryKey(item) === key);
  const from = group.findIndex((item) => item.id === id);
  const to = from + delta;
  if (to < 0 || to >= group.length) return null;
  const reordered = group.slice();
  [reordered[from], reordered[to]] = [reordered[to], reordered[from]];
  return placeGroup(entries, key, reordered);
}

export function orderAfterDrop(entries, dragId, targetId) {
  const dragged = entries.find((item) => item.id === dragId);
  const target = entries.find((item) => item.id === targetId);
  if (!dragged || !target || dragId === targetId) return null;
  const key = categoryKey(dragged);
  if (categoryKey(target) !== key) return null;
  const group = entries.filter((item) => categoryKey(item) === key);
  const from = group.findIndex((item) => item.id === dragId);
  const reordered = group.filter((item) => item.id !== dragId);
  const at = reordered.findIndex((item) => item.id === targetId);
  reordered.splice(from < group.findIndex((item) => item.id === targetId) ? at + 1 : at, 0, dragged);
  return placeGroup(entries, key, reordered);
}
