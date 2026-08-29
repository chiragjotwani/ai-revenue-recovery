import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  BackendUnavailable,
  EmptyState,
  NotAvailableYet,
  Panel,
  ScoreMeter,
  StatusPill,
} from "./ui";

describe("StatusPill", () => {
  it("always renders a visible text label (colour is never the only signal)", () => {
    render(<StatusPill label="high" severity="critical" />);
    expect(screen.getByText("high")).toBeInTheDocument();
  });
});

describe("ScoreMeter", () => {
  it("exposes the value to assistive tech and shows the exact number", () => {
    render(<ScoreMeter value={0.3033} severity="good" caption="risk score 0.30" />);
    const meter = screen.getByRole("meter", { name: "risk score 0.30" });
    expect(meter).toHaveAttribute("aria-valuenow", "0.3");
    expect(screen.getByText("0.30")).toBeInTheDocument();
  });
});

describe("Panel", () => {
  it("renders its title as a heading and its children", () => {
    render(
      <Panel title="Risk">
        <p>body</p>
      </Panel>,
    );
    expect(screen.getByRole("heading", { name: "Risk" })).toBeInTheDocument();
    expect(screen.getByText("body")).toBeInTheDocument();
  });
});

describe("honest-metric primitives", () => {
  it("NotAvailableYet says so explicitly and never shows a number", () => {
    render(<NotAvailableYet what="Recovered revenue" why="Phase 8" />);
    expect(screen.getByText("Not available yet")).toBeInTheDocument();
    expect(screen.getByText("Phase 8")).toBeInTheDocument();
  });

  it("EmptyState renders provided copy", () => {
    render(<EmptyState>No revenue currently at risk</EmptyState>);
    expect(screen.getByText("No revenue currently at risk")).toBeInTheDocument();
  });

  it("BackendUnavailable is distinct, explicit copy", () => {
    render(<BackendUnavailable />);
    expect(screen.getByText(/backend unavailable/i)).toBeInTheDocument();
  });
});
