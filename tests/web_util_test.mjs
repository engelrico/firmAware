// Run by tests/test_firmaware.py when Node.js is available; checks the link helpers.
import assert from "node:assert/strict";
import { hostOf, safeHttpUrl } from "../firmaware_core/web/static/util.js";

assert.equal(safeHttpUrl("http://192.168.1.20"), "http://192.168.1.20/");
assert.equal(safeHttpUrl("https://example.com/a?b=1#c"), "https://example.com/a?b=1#c");
for (const bad of ["javascript:alert(1)", "data:text/html,x", "ftp://example.com", "file:///etc/passwd", "//example.com", "example.com", "", null, undefined, 5]) {
  assert.equal(safeHttpUrl(bad), null, String(bad));
}
assert.equal(hostOf("https://support.example.com:8443/x"), "support.example.com:8443");
assert.equal(hostOf("javascript:alert(1)"), "");
console.log("link helpers ok");
