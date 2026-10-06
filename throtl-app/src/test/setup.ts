import "@testing-library/jest-dom/vitest";

// jsdom has no layout engine, so the geometry/scroll APIs the desktop
// components rely on are either absent or throw. Provide deterministic,
// harmless stubs so they render without crashing under test.

function zeroRect(): DOMRect {
  return {
    x: 0,
    y: 0,
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    width: 0,
    height: 0,
    toJSON: () => ({}),
  } as DOMRect;
}

if (typeof Element !== "undefined") {
  Element.prototype.getBoundingClientRect = zeroRect;
  Element.prototype.scrollIntoView = () => {};
}

if (typeof HTMLElement !== "undefined") {
  HTMLElement.prototype.scrollIntoView = () => {};
}
