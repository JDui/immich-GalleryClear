const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

// Load functions without booting the page or making network requests.
const source = fs.readFileSync(path.join(__dirname, "../app/static/js/main.js"), "utf8");
const grid = { clientWidth: 780 };
const context = vm.createContext({
  window: { innerWidth: 800, innerHeight: 800 },
  document: { getElementById: (id) => id === "grid" ? grid : null },
  getComputedStyle: () => ({ gap: "10px" }),
  setTimeout: (fn) => { timers.push(fn); return timers.length; },
  clearTimeout: () => {},
});
const timers = [];
vm.runInContext(source.slice(0, source.indexOf('document.getElementById("btn-refresh").addEventListener')), context);

for (const [width, height] of [[720, 720], [850, 650], [1200, 1100]]) {
  context.window.innerWidth = width;
  context.window.innerHeight = height;
  assert.equal(context.computeGridColumns(6), 2);
  assert.equal(context.computeGridColumns(1), 1);
}

const listeners = {};
const classes = new Set();
const card = {
  contains: (target) => target === img,
  dataset: {},
  classList: {
    add: (...items) => items.forEach((item) => classes.add(item)),
    remove: (...items) => items.forEach((item) => classes.delete(item)),
  },
  addEventListener: (type, fn) => { listeners[type] = fn; },
};
const img = { style: {}, getBoundingClientRect: () => ({ left: 0, top: 0, width: 300, height: 300 }) };
const touch = (x) => ({ clientX: x, clientY: 100, target: img });
const event = (type, touches) => ({ type, touches, preventDefault() { this.prevented = true; } });
context.attachPinchZoom(card, img);
listeners.touchstart(event("touchstart", [touch(50), touch(150)]));
assert.equal(classes.has("pinching"), true);
listeners.touchmove(event("touchmove", [touch(0), touch(200)]));
assert.equal(img.style.transform, "scale(2)");
listeners.touchend(event("touchend", [touch(0)]));
assert.equal(img.style.transform, "");
assert.equal(classes.has("pinching"), false);
let blocked = false;
listeners.click({ preventDefault() {}, stopImmediatePropagation() { blocked = true; } });
assert.equal(blocked, true, "Pinch must not change the card's mark");
listeners.touchend(event("touchend", []));
timers.splice(0).forEach((fn) => fn());
blocked = false;
listeners.click({ preventDefault() {}, stopImmediatePropagation() { blocked = true; } });
assert.equal(blocked, false, "Ordinary taps must remain usable after release");
listeners.touchstart(event("touchstart", [touch(50), touch(150)]));
listeners.touchmove(event("touchmove", [touch(0), touch(250)]));
listeners.touchcancel(event("touchcancel", []));
assert.equal(img.style.transform, "", "Cancelled gestures must also restore the image");
console.log("Square layout and touch zoom checks passed");
