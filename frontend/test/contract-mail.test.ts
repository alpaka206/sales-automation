import { describe, expect, it } from "vitest";
import { applyMailFields, emptyDraft, type Draft, type MailFields } from "../src/screens/won/contractDraft";

// 메일에서 찾은 칸은 아직 기본값인 칸에만 들어간다 — 사람이 적은 값과 이전 계약에서 물려받은 값은 그대로다.

const opened = (over: Partial<Draft> = {}): Draft => ({
  ...emptyDraft,
  starts_on: "2026-11-02",
  ends_on: "2027-11-02",
  first_payment_on: "2026-11-02",
  ...over,
});
const cell = (value: string) => ({ value, evidence: "메일 문장" });

describe("applyMailFields", () => {
  it("빈 칸과 기본값인 고르개를 채우고 무엇을 채웠는지 돌려준다", () => {
    const base = opened();
    const fields: MailFields = { amount_incl_vat: cell("12000"), plan: cell("Enterprise"), currency: cell("USD") };
    const { draft, filled } = applyMailFields(base, base, fields);
    expect(draft.amount_incl_vat).toBe("12000");
    expect(draft.plan).toBe("Enterprise");
    expect(draft.currency).toBe("USD");
    expect(Object.keys(filled).sort()).toEqual(["amount_incl_vat", "currency", "plan"]);
  });

  it("기다리는 동안 사람이 고친 칸은 건드리지 않는다", () => {
    const base = opened();
    const current = { ...base, amount_incl_vat: "5000", plan: "Business Tier 2" };
    const { draft, filled } = applyMailFields(current, base, { amount_incl_vat: cell("12000"), plan: cell("Enterprise") });
    expect(draft.amount_incl_vat).toBe("5000");
    expect(draft.plan).toBe("Business Tier 2");
    expect(filled).toEqual({});
  });

  it("재계약이 이전 계약에서 물려받은 값은 덮지 않는다", () => {
    const base = opened({ plan: "Enterprise", billing_email: "ap@old.example" });
    const { draft, filled } = applyMailFields(base, base, { plan: cell("Business Tier 1"), billing_email: cell("new@old.example") });
    expect(draft.plan).toBe("Enterprise");
    expect(draft.billing_email).toBe("ap@old.example");
    expect(filled).toEqual({});
  });

  it("시작일은 폼이 넣어 둔 기본값을 바꾸고 종료일과 최초 결제일이 따라온다", () => {
    const base = opened();
    const { draft, filled } = applyMailFields(base, base, { starts_on: cell("2026-12-01") });
    expect(draft.starts_on).toBe("2026-12-01");
    expect(draft.ends_on).toBe("2027-12-01");
    expect(draft.first_payment_on).toBe("2026-12-01");
    // 종료일은 메일에서 온 값이 아니다 — 근거 문장 없이 확인 표시만 붙는다.
    expect(filled.ends_on).toEqual({ value: "2027-12-01", evidence: "" });
    expect(filled.first_payment_on).toEqual({ value: "2026-12-01", evidence: "" });
  });

  it("사람이 고친 종료일 · 최초 결제일은 시작일을 따라가지 않는다", () => {
    const base = opened();
    const current = { ...base, ends_on: "2027-06-30", first_payment_on: "2026-12-15" };
    const { draft, filled } = applyMailFields(current, base, { starts_on: cell("2026-12-01") });
    expect(draft.ends_on).toBe("2027-06-30");
    expect(draft.first_payment_on).toBe("2026-12-15");
    expect(filled.ends_on).toBeUndefined();
    expect(filled.first_payment_on).toBeUndefined();
  });

  it("회차 수는 할부일 때만 들어간다", () => {
    const base = opened();
    const lump = applyMailFields(base, base, { installments: cell("4") });
    expect(lump.draft.installments).toBe("1");
    expect(lump.filled.installments).toBeUndefined();

    const split = applyMailFields(base, base, { payment_type: cell("할부"), installments: cell("4") });
    expect(split.draft.installments).toBe("4");
  });
});
