import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CommandPalette } from "./CommandPalette";
import { getProfiles } from "../lib/api";

vi.mock("../lib/api", () => ({
  getProfiles: vi.fn(),
}));

const mockedGetProfiles = vi.mocked(getProfiles);

function renderPalette() {
  const props = {
    enabled: true,
    onClose: vi.fn(),
    onToggleShaping: vi.fn(),
    onFocusFilter: vi.fn(),
    onAddRule: vi.fn(),
    onOpenSheet: vi.fn(),
    onTour: vi.fn(),
    onResetStats: vi.fn(),
    onToggleTheme: vi.fn(),
    onSwitchProfile: vi.fn(),
  };
  const user = userEvent.setup();
  render(<CommandPalette {...props} />);
  return { ...props, user };
}

beforeEach(() => {
  mockedGetProfiles.mockResolvedValue({
    profiles: ["Standard", "University"],
    active: "Standard",
  });
});

describe("CommandPalette", () => {
  it("lists commands and loads profiles", async () => {
    renderPalette();
    expect(screen.getByText("Focus application filter")).toBeInTheDocument();
    expect(await screen.findByText("Switch profile: Standard")).toBeInTheDocument();
    expect(screen.getByText("Switch profile: University")).toBeInTheDocument();
  });

  it("filters the list as you type", async () => {
    const { user } = renderPalette();
    await user.type(screen.getByRole("textbox", { name: "Search commands" }), "theme");
    expect(screen.getByText("Toggle light/dark theme")).toBeInTheDocument();
    expect(screen.queryByText("Focus application filter")).not.toBeInTheDocument();
  });

  it("runs the selected command on Enter", async () => {
    const { user, onFocusFilter, onClose } = renderPalette();
    await user.keyboard("{Enter}");
    expect(onFocusFilter).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on Escape", async () => {
    const { user, onClose } = renderPalette();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("switches profile when a profile command is clicked", async () => {
    const { user, onSwitchProfile } = renderPalette();
    await user.click(await screen.findByRole("option", { name: "Switch profile: University" }));
    expect(onSwitchProfile).toHaveBeenCalledWith("University");
  });
});
