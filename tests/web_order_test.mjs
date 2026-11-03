// Run by tests/test_firmaware.py when Node.js is available; checks the pure ordering helpers.
import assert from "node:assert/strict";
import { orderAfterDrop, orderAfterMove } from "../firmaware_core/web/static/state.js";

const e = (id, category) => ({ id, category });
const entries = [e("a", "firmware"), e("x", "software"), e("b", "firmware"), e("c", "firmware"), e("y", "software")];

// move within a category keeps the other category's slots
assert.deepEqual(orderAfterMove(entries, "b", -1), ["b", "x", "a", "c", "y"]);
assert.deepEqual(orderAfterMove(entries, "b", 1), ["a", "x", "c", "b", "y"]);
assert.equal(orderAfterMove(entries, "a", -1), null);
assert.equal(orderAfterMove(entries, "c", 1), null);
assert.equal(orderAfterMove(entries, "nope", 1), null);
// unknown categories form the "other" group
const other = [e("p", "weird"), e("q", ""), e("r", "firmware")];
assert.deepEqual(orderAfterMove(other, "q", -1), ["q", "p", "r"]);
// drag: downwards lands after the target, upwards before it
assert.deepEqual(orderAfterDrop(entries, "a", "c"), ["b", "x", "c", "a", "y"]);
assert.deepEqual(orderAfterDrop(entries, "c", "a"), ["c", "x", "a", "b", "y"]);
assert.deepEqual(orderAfterDrop(entries, "a", "b"), ["b", "x", "a", "c", "y"]);
assert.equal(orderAfterDrop(entries, "a", "x"), null); // different category
assert.equal(orderAfterDrop(entries, "a", "a"), null);
console.log("ordering helpers ok");
