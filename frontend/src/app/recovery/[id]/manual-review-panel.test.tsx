import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ManualReviewPanel } from "./manual-review-panel";
import type { ManualReviewResolution } from "@/lib/api";
import type { ManualReviewActionState } from "./manual-review-action";

const { resolveManualReviewMock } = vi.hoisted(() => ({ resolveManualReviewMock: vi.fn() }));

vi.mock("./manual-review-action", () => ({
  resolveManualReview: resolveManualReviewMock,
}));

function makeResolution(overrides: Partial<ManualReviewResolution> = {}): ManualReviewResolution {
  return {
    id: "mr-1",
    case_id: "c-1",
    resolution: "abandoned",
    note: "customer confirmed fraud",
    actor: "operator:resolve_manual_review",
    created_at: "2026-04-01T00:00:00Z",
    ...overrides,
  };
}

describe("ManualReviewPanel: visibility", () => {
  it("shows nothing to resolve when the case was never escalated", () => {
    render(
      <ManualReviewPanel caseId="c-1" caseState="decision_pending" initialResolution={null} />,
    );
    expect(screen.getByText(/not been escalated/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /resolve manual review/i })).not.toBeInTheDocument();
  });

  it("offers a resolution form while pending_manual_review and unresolved", () => {
    render(
      <ManualReviewPanel
        caseId="c-1"
        caseState="pending_manual_review"
        initialResolution={null}
      />,
    );
    expect(screen.getByRole("button", { name: /resolve manual review/i })).toBeInTheDocument();
    expect(screen.getByText(/abandon/i)).toBeInTheDocument();
    expect(screen.getByText(/mark failed/i)).toBeInTheDocument();
  });

  it("never offers a 'recovered' resolution option", () => {
    render(
      <ManualReviewPanel
        caseId="c-1"
        caseState="pending_manual_review"
        initialResolution={null}
      />,
    );
    expect(screen.queryByText(/^recovered$/i)).not.toBeInTheDocument();
  });

  it("shows the recorded resolution instead of the form once resolved", () => {
    render(
      <ManualReviewPanel
        caseId="c-1"
        caseState="abandoned"
        initialResolution={makeResolution()}
      />,
    );
    expect(screen.getByText(/^abandoned$/i)).toBeInTheDocument();
    expect(screen.getByText(/customer confirmed fraud/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /resolve manual review/i })).not.toBeInTheDocument();
  });
});

describe("ManualReviewPanel: resolve interaction", () => {
  it("shows a loading state while pending", async () => {
    let resolveAction!: (v: ManualReviewActionState) => void;
    resolveManualReviewMock.mockReturnValue(
      new Promise<ManualReviewActionState>((resolve) => {
        resolveAction = resolve;
      }),
    );

    render(
      <ManualReviewPanel
        caseId="c-1"
        caseState="pending_manual_review"
        initialResolution={null}
      />,
    );
    fireEvent.click(screen.getByLabelText(/abandon/i));
    fireEvent.change(screen.getByPlaceholderText(/why this case/i), {
      target: { value: "a reason" },
    });
    fireEvent.click(screen.getByRole("button", { name: /resolve manual review/i }));

    await waitFor(() => expect(screen.getByRole("button")).toBeDisabled());

    resolveAction({ status: "success", resolution: makeResolution() });
    await waitFor(() => expect(screen.getByText(/^abandoned$/i)).toBeInTheDocument());
  });

  type ErrorKind = Extract<ManualReviewActionState, { status: "error" }>["kind"];

  it.each<[ErrorKind, string]>([
    ["not_found", "no longer exists"],
    ["conflict", "already resolved"],
    ["invalid", "both required"],
    ["unavailable", "could not be resolved"],
  ])("shows the %s error inline without crashing the panel", async (kind, expectedText) => {
    resolveManualReviewMock.mockResolvedValue({
      status: "error",
      kind,
      message: `x ${expectedText} x`,
    });

    render(
      <ManualReviewPanel
        caseId="c-1"
        caseState="pending_manual_review"
        initialResolution={null}
      />,
    );
    fireEvent.click(screen.getByLabelText(/mark failed/i));
    fireEvent.change(screen.getByPlaceholderText(/why this case/i), {
      target: { value: "a reason" },
    });
    fireEvent.click(screen.getByRole("button", { name: /resolve manual review/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(expectedText);
    expect(screen.getByRole("button", { name: /resolve manual review/i })).toBeInTheDocument();
  });
});
