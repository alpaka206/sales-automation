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

  it("sends a known customer straight to the contract form", () => {
    const data = { rows: [{ client_id: 1323 }] } as ListData;

    expect(pendingContractPath(data, pending())).toBe(
      "/won-customers/1323/contracts/new?pending=7",
    );
  });

  it("opens the new-customer contract form without creating anything", () => {
    // 고객을 미리 만들면, 계약을 채우지 않고 나갔을 때 계약 0건짜리 고객이 남고 그
    // 고객이 워크북에 「세팅중」으로 실려 나갑니다. 요청은 저장할 때 한 번만 나갑니다.
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const data = { rows: [] } as unknown as ListData;

    expect(pendingContractPath(data, pending({ client_id: null, known: false }))).toBe(
      "/won-customers/new/contract?pending=7",
    );
    expect(fetch).not.toHaveBeenCalled();
  });

  it("also defers when the pending row carries a number the ledger has never seen", () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const data = { rows: [{ client_id: 1108 }] } as ListData;

    expect(pendingContractPath(data, pending({ client_id: 1160, known: false }))).toBe(
      "/won-customers/new/contract?pending=7",
    );
    expect(fetch).not.toHaveBeenCalled();
  });
});
