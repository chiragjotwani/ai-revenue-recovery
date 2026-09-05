import { afterEach, describe, expect, it, vi } from "vitest";
import {
  apiGet,
  caseSeverity,
  formatCurrencyMap,
  provenanceLabel,
  riskSeverity,
} from "./api";

function mockFetch(status: number, body: unknown = {}): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    })) as unknown as typeof fetch,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("apiGet result discrimination (BUG-004)", () => {
  it("200 -> ok with data", async () => {
    mockFetch(200, { a: 1 });
    const r = await apiGet<{ a: number }>("/x");
    expect(r).toEqual({ ok: true, data: { a: 1 } });
  });

  it("404 -> not_found (unknown record)", async () => {
    mockFetch(404);
    expect(await apiGet("/x")).toEqual({ ok: false, kind: "not_found" });
  });

  it("422 -> not_found, NOT unavailable (a malformed id is not a dead backend)", async () => {
    mockFetch(422);
    expect(await apiGet("/x")).toEqual({ ok: false, kind: "not_found" });
  });

  it("400 -> not_found", async () => {
    mockFetch(400);
    expect(await apiGet("/x")).toEqual({ ok: false, kind: "not_found" });
  });

  it("500 -> unavailable", async () => {
    mockFetch(500);
    expect(await apiGet("/x")).toEqual({ ok: false, kind: "unavailable" });
  });

  it("network throw -> unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("ECONNREFUSED");
      }) as unknown as typeof fetch,
    );
    expect(await apiGet("/x")).toEqual({ ok: false, kind: "unavailable" });
  });
});

describe("domain helpers", () => {
  it("riskSeverity maps levels", () => {
    expect(riskSeverity("high")).toBe("critical");
    expect(riskSeverity("medium")).toBe("warn");
    expect(riskSeverity("low")).toBe("good");
  });

  it("caseSeverity maps lifecycle states", () => {
    expect(caseSeverity("recovered")).toBe("good");
    expect(caseSeverity("failed")).toBe("critical");
    expect(caseSeverity("abandoned")).toBe("critical");
    expect(caseSeverity("diagnosing")).toBe("warn");
  });

  it("provenanceLabel distinguishes mock from a real model", () => {
    const mock = provenanceLabel("mock");
    expect(mock.isReal).toBe(false);
    expect(mock.text.toLowerCase()).toContain("mock");

    const real = provenanceLabel("qwen");
    expect(real.isReal).toBe(true);
    expect(real.text).toContain("qwen");
  });

  it("formatCurrencyMap joins per-currency amounts and never fabricates a total", () => {
    expect(formatCurrencyMap({})).toBe("0");
    expect(formatCurrencyMap({ INR: "4999.00" })).toBe("4999.00 INR");
    expect(formatCurrencyMap({ INR: "1000.00", USD: "50.00" })).toContain("INR");
    expect(formatCurrencyMap({ INR: "1000.00", USD: "50.00" })).toContain("USD");
  });
});
