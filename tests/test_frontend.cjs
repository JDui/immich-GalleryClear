const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

// Load functions without booting the page or making network requests.
const source = fs.readFileSync(path.join(__dirname, "../app/static/js/main.js"), "utf8");
const gridClasses = new Set();
const grid = {
  clientWidth: 780,
  clientHeight: 620,
  classList: {
    add: (name) => gridClasses.add(name),
    remove: (name) => gridClasses.delete(name),
  },
};
const toastNodes = [];
const context = vm.createContext({
  window: { innerWidth: 800, innerHeight: 800 },
  document: {
    getElementById: (id) => id === "grid" ? grid :
      id === "toast-container" ? { appendChild: (node) => toastNodes.push(node) } : null,
    createElement: () => ({ style: {}, remove() {} }),
  },
  getComputedStyle: () => ({ gap: "10px" }),
  setTimeout: (fn) => { timers.push(fn); return timers.length; },
  clearTimeout: () => {},
});
const timers = [];
vm.runInContext(source.slice(0, source.indexOf('document.getElementById("btn-refresh").addEventListener')), context);

grid.clientWidth = 360;
grid.clientHeight = 480;
assert.equal(context.computeGridColumns(6), 2, "Portrait phones should fit six cards in two columns");
grid.clientWidth = 1360;
grid.clientHeight = 580;
assert.equal(context.computeGridColumns(6), 3, "Wide galleries should use the available width");
grid.clientWidth = 780;
grid.clientHeight = 620;
assert.equal(context.computeGridColumns(1), 1);
assert.ok(context.computeGridColumns(25) > context.computeGridColumns(6), "Dense batches need more columns");

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

context.toast("Background processing");
context.toast("Done", "success");
assert.equal(toastNodes.length, 0, "Normal operations must stay quiet");
context.toast("Failed", "error");
assert.equal(toastNodes.length, 1, "Errors must still be shown");

const frames = [];
context.requestAnimationFrame = (fn) => frames.push(fn);
classes.add("delete-out");
classes.delete("img-loaded");
card.isConnected = true;
card.appendChild = (node) => { node.isConnected = true; };
context.revealCardImage(card, img, { isConnected: false });
assert.equal(classes.has("img-loaded"), false);
frames.shift()();
assert.equal(classes.has("img-loaded"), false, "Cached images need a transparent paint first");
frames.shift()();
assert.equal(classes.has("img-loaded"), true);
assert.equal(classes.has("delete-out"), true, "Late loads must never restart an exiting card");
gridClasses.add("round-leaving");
context.revealGrid();
frames.shift()();
assert.equal(gridClasses.has("round-leaving"), true);
frames.shift()();
assert.equal(gridClasses.has("round-leaving"), false);
console.log("Layout, touch zoom, error-only notifications and fade handoff checks passed");
