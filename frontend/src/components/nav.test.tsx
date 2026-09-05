import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const nav = vi.hoisted(() => ({ pathname: "/" }));
vi.mock("next/navigation", () => ({
  usePathname: () => nav.pathname,
}));

import { Nav } from "./nav";

beforeEach(() => {
  nav.pathname = "/";
});

describe("Nav", () => {
  it("marks the matching route as the current page", () => {
    nav.pathname = "/risk";
    render(<Nav />);
    expect(screen.getByRole("link", { name: "Risk queue" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "Overview" })).not.toHaveAttribute("aria-current");
  });

  it("treats a nested recovery route as active for the Recovery tab", () => {
    nav.pathname = "/recovery/abc-123";
    render(<Nav />);
    expect(screen.getByRole("link", { name: "Recovery" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("renders all primary tabs inside a labelled nav", () => {
    render(<Nav />);
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
    for (const label of ["Overview", "Risk queue", "Recovery"]) {
      expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
    }
  });
});
