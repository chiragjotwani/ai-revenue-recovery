import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ActionPanel } from "./action-panel";
import type { Action } from "@/lib/api";
import type { ActionActionState } from "./action-action";

const { scheduleActionMock, executeActionMock } = vi.hoisted(() => ({
  scheduleActionMock: vi.fn(),
  executeActionMock: vi.fn(),
}));

vi.mock("./action-action", () => ({
  scheduleAction: scheduleActionMock,
  executeAction: executeActionMock,
}));

function makeAction(overrides: Partial<Action> = {}): Action {
  return {
    id: "a-1",
    case_id: "c-1",
    decision_result_id: "d-1",
    action_type: "retry",
    status: "scheduled",
    created_at: "2026-03-01T00:00:00Z",
    executions: [],
    ...overrides,
  };
}

describe("ActionPanel: pre-scheduling states", () => {
  it("offers Schedule action only when the decision is approved and pending", () => {
    render(
      <ActionPanel
        caseId="c-1"
        caseState="decision_pending"
        decisionStatus="approved"
        initialAction={null}
      />,
    );
    expect(screen.getByRole("button", { name: /schedule action/i })).toBeInTheDocument();
  });

  it("does not offer Schedule action for an escalated decision", () => {
    render(
      <ActionPanel
        caseId="c-1"
        caseState="decision_pending"
        decisionStatus="escalated"
        initialAction={null}
      />,
    );
    expect(screen.queryByRole("button", { name: /schedule action/i })).not.toBeInTheDocument();
    expect(screen.getByText(/no action can be scheduled yet/i)).toBeInTheDocument();
  });
});

describe("ActionPanel: scheduled state", () => {
  it("offers Execute action once scheduled, never fabricating money movement", () => {
    render(
      <ActionPanel
        caseId="c-1"
        caseState="action_scheduled"
        decisionStatus="approved"
        initialAction={makeAction({ status: "scheduled" })}
      />,
    );
    expect(screen.getByRole("button", { name: /execute action/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /schedule action/i })).not.toBeInTheDocument();
  });

  it("renders no_action outcomes without ever implying a payment was retried", () => {
    render(
      <ActionPanel
        caseId="c-1"
        caseState="action_executed"
        decisionStatus="approved"
        initialAction={makeAction({
          action_type: "no_action",
          status: "executed",
          executions: [
            {
              id: "e-1",
              attempt_no: 1,
              idempotency_key: "arr:c-1:no_action:1",
              outcome: "no_side_effect_required",
              created_at: "2026-03-01T01:00:00Z",
            },
          ],
        })}
      />,
    );
    expect(screen.getByText(/no side effect required/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /execute action/i })).not.toBeInTheDocument();
  });

  it("honestly labels a deferred retry, not a completed one", () => {
    render(
      <ActionPanel
        caseId="c-1"
        caseState="action_executed"
        decisionStatus="approved"
        initialAction={makeAction({
          action_type: "retry",
          status: "executed",
          executions: [
            {
              id: "e-2",
              attempt_no: 1,
              idempotency_key: "arr:c-1:retry:1",
              outcome: "deferred_no_integration",
              created_at: "2026-03-01T01:00:00Z",
            },
          ],
        })}
      />,
    );
    expect(screen.getByText(/deferred no integration/i)).toBeInTheDocument();
    expect(screen.getByText(/no payment-provider or customer-messaging integration/i)).toBeInTheDocument();
  });
});

describe("ActionPanel: schedule interaction", () => {
  it("shows a loading state while scheduling is pending", async () => {
    let resolveAction!: (v: ActionActionState) => void;
    scheduleActionMock.mockReturnValue(
      new Promise<ActionActionState>((resolve) => {
        resolveAction = resolve;
      }),
    );

    render(
      <ActionPanel
        caseId="c-1"
        caseState="decision_pending"
        decisionStatus="approved"
        initialAction={null}
      />,
    );
    const button = screen.getByRole("button", { name: /schedule action/i });
    fireEvent.click(button);

    await waitFor(() => expect(screen.getByRole("button")).toBeDisabled());

    resolveAction({ status: "success", action: makeAction() });
    await waitFor(() => expect(screen.getByRole("button", { name: /execute action/i })).toBeInTheDocument());
  });

  type ErrorKind = Extract<ActionActionState, { status: "error" }>["kind"];

  it.each<[ErrorKind, string]>([
    ["not_found", "no longer exists"],
    ["conflict", "decision_pending"],
    ["unavailable", "did not respond"],
  ])("shows the %s error inline without crashing the panel", async (kind, expectedText) => {
    scheduleActionMock.mockResolvedValue({ status: "error", kind, message: `x ${expectedText} x` });

    render(
      <ActionPanel
        caseId="c-1"
        caseState="decision_pending"
        decisionStatus="approved"
        initialAction={null}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /schedule action/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(expectedText);
    expect(screen.getByRole("button", { name: /schedule action/i })).toBeInTheDocument();
  });

  it("duplicate schedule calls (double click) still resolve to one rendered action", async () => {
    scheduleActionMock.mockResolvedValue({ status: "success", action: makeAction() });

    render(
      <ActionPanel
        caseId="c-1"
        caseState="decision_pending"
        decisionStatus="approved"
        initialAction={null}
      />,
    );
    const button = screen.getByRole("button", { name: /schedule action/i });
    fireEvent.click(button);
    fireEvent.click(button); // disabled while pending; a no-op if it fires again

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /execute action/i })).toBeInTheDocument(),
    );
    expect(screen.getAllByRole("button", { name: /execute action/i })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /schedule action/i })).not.toBeInTheDocument();
  });
});
