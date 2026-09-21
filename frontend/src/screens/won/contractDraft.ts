// 계약 폼의 **규칙 한 벌** — 모달(`WonContractForm`, 새 계약)과 제자리 편집(`ContractCards`,
// 있는 계약)이 같이 씁니다. 두 화면이 각자 규칙을 들면 칸 하나 늘 때 한쪽만 늘고, 그러면
// 만든 계약과 고친 계약이 다른 모양이 됩니다 — 이 저장소가 여러 번 겪은 자리입니다.
//
// 여기 있는 것은 상태가 아니라 **계산**입니다. 어느 화면이든 `Draft` 를 자기 useState 로
// 들고, 바뀔 때마다 이 함수들을 부릅니다.
import { type Contract, n } from "./shared";

export const emptyDraft = {
  deal_type: "MRR", starts_on: "", ends_on: "", plan_starts_on: "", plan_ends_on: "", ticket_id: "",
  // 부가세가 붙는 계약인가(국내 법인이면 해당). **통화가 아니라 고객이 정합니다** — 이 값이
  // 공급가가 있는 계약인지를 정합니다(`won.supply_amount`). 폼은 문자열만 나르므로 "1" / "".
  vat_applicable: "1",
  // **계약금액은 한 칸이고 VAT 포함입니다**(2026-09-21). `vat_included`·`amount_excl_vat`
  // 가 여기 있었습니다 — 이관 0123 이 둘 다 지웠습니다.
  currency: "KRW", amount_incl_vat: "", credits: "",
  // 비워 두면 저장할 때 계약일 고시가로 채웁니다(`_fill_contract_fx`).
  fx_rate: "", terminated_on: "",
  // `credits_used` 가 여기 있었습니다 — 2026-09-21 에 「결제 · MRR」 탭의 「중도 해지 정산」
  // 박스로 옮겼습니다. **초안에서 빼는 것이 요점입니다**: 남겨 두면 계약 편집을 저장할
  // 때마다 그 사이 저 박스에서 적은 값이 열었을 때의 값으로 되돌아갑니다.
  payment_method: "직접거래", payment_type: "일시불", installments: "1",
  first_payment_on: "", billing_email: "", note: "",
  // 고객사 측 담당자·연락처. 계약마다 다를 수 있어 고객이 아니라 계약이 듭니다(0103).
  contact_name: "", contact_info: "",
  plan: "Business Tier 1", plan_name: "", perso_email: "",
  invite_limit: "", queue_limit: "", concurrent_jobs: "", space_count: "", space_seq: "",
  revenue_from: "",
};
export type Draft = typeof emptyDraft;

export const str = (value: unknown) => (value === null || value === undefined ? "" : String(value));

export function fromContract(contract: Contract): Draft {
  return {
    deal_type: contract.deal_type, starts_on: str(contract.starts_on), ends_on: str(contract.ends_on),
    plan_starts_on: contract.plan_starts_on || "", plan_ends_on: contract.plan_ends_on || "",
    ticket_id: str(contract.ticket_id), currency: contract.currency,
    vat_applicable: contract.vat_applicable ? "1" : "",
    fx_rate: str(contract.fx_rate), terminated_on: str(contract.terminated_on),
    amount_incl_vat: str(contract.amount_incl_vat),
    credits: str(contract.credits),
    payment_method: str(contract.payment_method), payment_type: str(contract.payment_type),
    installments: str(contract.installments ?? 1), first_payment_on: str(contract.first_payment_on),
    billing_email: str(contract.billing_email), note: str(contract.note),
    contact_name: str(contract.contact_name), contact_info: str(contract.contact_info),
    plan: str(contract.plan), plan_name: str(contract.plan_name), perso_email: str(contract.perso_email),
    invite_limit: str(contract.invite_limit), queue_limit: str(contract.queue_limit),
    concurrent_jobs: str(contract.concurrent_jobs), space_count: str(contract.space_count),
    space_seq: str(contract.space_seq),
    revenue_from: contract.revenue_from_set ? str(contract.revenue_from) : "",
  };
}

/** 재계약이 물려받는 것 — 플랜·단가·결제 방식·계정 한도. 금액·기간은 새로 씁니다.
 *
 * **환율은 물려받지 않습니다.** 그건 직전 계약을 맺던 날의 값이라, 새 계약에 그대로
 * 박히면 이번 계약의 크레딧이 남의 시점 환율로 계산됩니다. 쓴 사람이 직접 적습니다. */
export function carryOver(prev: Contract) {
  return {
    deal_type: prev.deal_type, currency: prev.currency,
    // 부가세 해당 여부는 **고객의 성질**이라 다음 차수도 같습니다. 「VAT 포함/제외」도
    // 여기 있었는데, 금액 칸이 하나가 되면서 물려받을 것이 없어졌습니다(이관 0123).
    vat_applicable: prev.vat_applicable ? "1" : "",
    payment_method: str(prev.payment_method), payment_type: str(prev.payment_type),
    installments: str(prev.installments ?? 1), billing_email: str(prev.billing_email),
    contact_name: str(prev.contact_name), contact_info: str(prev.contact_info),
    plan: str(prev.plan), plan_name: str(prev.plan_name), perso_email: str(prev.perso_email),
    invite_limit: str(prev.invite_limit), queue_limit: str(prev.queue_limit),
    concurrent_jobs: str(prev.concurrent_jobs), space_count: str(prev.space_count),
    space_seq: str(prev.space_seq),
  };
}
export const carryOverKeys = Object.keys(carryOver({} as Contract)) as (keyof Draft)[];
export const emptyCarry = () => carryOverKeys.reduce((acc, key) => ({ ...acc, [key]: "" }), {});

// `withAmount` 가 여기 있었습니다 — 금액 칸이 둘이던 시절 한쪽을 적으면 다른 쪽을 10% 로
// 채우던 함수입니다. 칸이 하나가 되면서(이관 0123) 채울 상대가 없어졌습니다.

/** 화면이 미리 보여 주는 계산값. 저장할 때 서버가 같은 식으로 다시 계산합니다
 *  (won.total_amount / won.unit_price) — 두 곳에 식이 있는 게 아니라, 화면은 사람이
 *  숫자를 넣는 동안 결과를 보여 줄 뿐입니다. */
export function derive(draft: Draft) {
  // 부가세가 붙는 계약인가. **통화가 아니라 고객이 정합니다**(이관 0075). 금액 칸을 가르지는
  // 않고, 공급가가 있는 계약인지를 정합니다.
  const vatApplicable = draft.vat_applicable === "1";
  /** 분당 단가의 기준 = **계약금액**. 한 칸이라 고를 것이 없습니다(`won.total_amount`). */
  const billing = n(draft.amount_incl_vat);
  /** 분당 단가 = 계약금액 ÷ (계약 크레딧 ÷ 60). 소수 둘째 자리 — 상세의 「분당 단가」와
   *  같은 자릿수여야 합니다. */
  const credits = n(draft.credits);
  const unitPrice = billing && credits ? (billing / (credits / 60)).toFixed(2) : null;
  return { vatApplicable, billing, unitPrice };
}

/** 저장하면 플랜 상태가 무엇이 될지. 서버의 won.plan_status 와 같은 규칙을 이 계약 하나에
 *  적용한 것입니다. **보는 것은 계약 기간입니다**(2026-09-21) — 플랜 날짜는 더 이상 이
 *  판정에 안 들어갑니다. 중도 해지일이 종료일보다 빠르면 그날 끝납니다. */
export function planPreview(draft: Draft, today = new Date().toISOString().slice(0, 10)): string {
  const start = draft.starts_on;
  let end = draft.ends_on;
  if (draft.terminated_on && (!end || draft.terminated_on < end)) end = draft.terminated_on;
  if (!start || !end) return "세팅중";
  if (end < today) return "사용 중단";
  if (start > today) return "세팅중";
  return "사용중";
}

/** 저장 전 검사. 계약금액과 계약 크레딧, 둘만 필수입니다 — 분당 단가는 그 둘에서 나오는
 *  계산값이라 받지 않습니다. 문제가 없으면 null. */
export function validate(draft: Draft): string | null {
  if (!draft.starts_on || !draft.ends_on || draft.ends_on <= draft.starts_on) {
    return "계약 시작일과 종료일을 확인해 주세요.";
  }
  if (!derive(draft).billing) return `계약금액을 입력해 주세요 (${draft.currency}).`;
  if (!n(draft.credits)) return "계약 크레딧을 입력해 주세요 — 분당 단가가 여기서 나옵니다.";
  return null;
}

/** 서버로 보낼 몸통. */
export function toBody(draft: Draft, creditRounds: string, firstCreditOn: string) {
  const body: Record<string, string> = { ...draft,
    credit_rounds: creditRounds, first_credit_on: firstCreditOn };
  return body;
}
