import { afterEach, describe, expect, it, vi } from "vitest";
import { pendingContractPath } from "../src/screens/won/pending";
import type { ListData } from "../src/screens/won/shared";

const pending = (overrides: Partial<ListData["pending"][number]> = {}) => ({
  id: 7,
  ticket_id: "T-7",
  company: "JUPITER AND MERCURY",
  client_id: 1323,
  won_type: "Renewal",
  next_seq: 2,
  known: true,
  won_on: "2026-08-24",
  ...overrides,
});

describe("pendingContractPath", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends a known customer straight to the contract form", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const data = {
      rows: [{ client_id: 1323 }],
    } as ListData;

    await expect(pendingContractPath(data, pending())).resolves.toBe(
      "/won-customers/1323/contracts/new?pending=7",
    );
    expect(fetch).not.toHaveBeenCalled();
  });

  it("creates a new customer before opening the same contract form", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ client_id: 1365 }),
    });
    vi.stubGlobal("fetch", fetch);
    const data = { rows: [] } as unknown as ListData;

    await expect(
      pendingContractPath(data, pending({ client_id: null, known: false })),
    ).resolves.toBe("/won-customers/1365/contracts/new?pending=7");
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch.mock.calls[0][0]).toBe("/won-customers");
  });
});
