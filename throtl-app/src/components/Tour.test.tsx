import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Tour } from "./Tour";

function addTarget(name: string) {
  const el = document.createElement("div");
  el.setAttribute("data-tour", name);
  document.body.appendChild(el);
  return el;
}

beforeEach(() => {
  document.body.replaceChildren();
});

describe("Tour", () => {
  it("renders the first step title", () => {
    render(<Tour onClose={vi.fn()} />);
    expect(screen.getByText("Status & profile")).toBeInTheDocument();
  });

  it("advances to the next step when Next is clicked", async () => {
    const user = userEvent.setup();
    render(<Tour onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Your live rates")).toBeInTheDocument();
  });

  it("calls onClose when Skip is clicked", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<Tour onClose={onClose} />);
    await user.click(screen.getByRole("button", { name: "Skip" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when Done is clicked on the last step", async () => {
    // A single visible target collapses the tour to one step, so the primary
    // button reads "Done" and closing is the only way forward.
    addTarget("status");
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<Tour onClose={onClose} />);
    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
