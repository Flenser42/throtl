import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { HistoryPoint } from "../lib/model";
import { Sparkline } from "./Sparkline";

const pt = (down: number): HistoryPoint => ({ t: 0, down, up: 0 });

function pointsOf(history: HistoryPoint[]): string | null {
  const { container } = render(<Sparkline history={history} />);
  return container.querySelector("polyline")?.getAttribute("points") ?? null;
}

describe("Sparkline", () => {
  it("renders an <svg>", () => {
    const { container } = render(<Sparkline history={[]} />);
    expect(container.querySelector("svg")).toBeInTheDocument();
  });

  it("renders no polyline for empty history", () => {
    const { container } = render(<Sparkline history={[]} />);
    expect(container.querySelector("polyline")).toBeNull();
  });

  it("renders no polyline for a single point", () => {
    const { container } = render(<Sparkline history={[pt(5)]} />);
    expect(container.querySelector("polyline")).toBeNull();
  });

  it("renders a polyline whose points change with input", () => {
    const flat = pointsOf([pt(0), pt(50), pt(100)]);
    const spike = pointsOf([pt(0), pt(10), pt(100)]);
    expect(flat).toBeTruthy();
    expect(spike).toBeTruthy();
    expect(flat).not.toBe(spike);
  });
});
