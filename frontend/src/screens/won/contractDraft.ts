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
  // 금액 칸을 한 개 그릴지 두 개 그릴지 정합니다. 폼은 문자열만 나르므로 "1" / "" 입니다.
  vat_applicable: "1",
  // 분당 단가의 기준이 VAT 포함 금액인가 — 「공급가」가 고르는 값입니다.
  currency: "KRW", vat_included: "", amount_incl_vat: "", amount_excl_vat: "", credits: "",
  // 비워 두면 저장할 때 계약일 고시가로 채웁니다(`_fill_contract_fx`).
  fx_rate: "", terminated_on: "", credits_used: "",
  payment_method: "계좌이체", payment_type: "일시불", installments: "1",
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
    vat_included: contract.vat_included ? "1" : "",
    fx_rate: str(contract.fx_rate), terminated_on: str(contract.terminated_on),
    credits_used: str(contract.credits_used),
    amount_incl_vat: str(contract.amount_incl_vat), amount_excl_vat: str(contract.amount_excl_vat),
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
    // 통화를 물려받으면 「VAT 포함/제외」도 물려받아야 합니다 — 같은 고객의 다음 차수
    // 계약서는 같은 방식으로 적힙니다. 통화만 따라오고 기준은 초기화되면, 총액으로 적힌
    // 계약이 공급가 칸으로 들어가 단가가 10% 낮아집니다.
    vat_applicable: prev.vat_applicable ? "1" : "",
    vat_included: prev.vat_included ? "1" : "",
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

/** 한쪽을 적으면 다른 쪽이 10% 로 따라옵니다. 반올림은 소수 둘째 자리까지 — 원화는
 *  정수로 떨어지고, 안 떨어지는 통화는 서버가 기준에서 다시 계산하므로 여기 값은
 *  운영자가 눈으로 확인하는 용도입니다. */
export function withAmount(draft: Draft, which: "incl" | "excl", value: string): Draft {
  const round2 = (x: number) => String(Math.round(x * 100) / 100);
  const typed = Number(value);
  const partner = value.trim() === "" || !Number.isFinite(typed)
    ? ""
    : which === "incl" ? round2(typed / 1.1) : round2(typed * 1.1);
  return { ...draft,
    [which === "incl" ? "amount_incl_vat" : "amount_excl_vat"]: value,
    [which === "incl" ? "amount_excl_vat" : "amount_incl_vat"]: partner };
}

/** 화면이 미리 보여 주는 계산값. 저장할 때 서버가 같은 식으로 다시 계산합니다
 *  (won.total_amount / won.unit_price) — 두 곳에 식이 있는 게 아니라, 화면은 사람이
 *  숫자를 넣는 동안 결과를 보여 줄 뿐입니다. */
export function derive(draft: Draft) {
  const krw = draft.currency === "KRW";
  // 부가세가 붙는 계약인가. **통화가 아니라 고객이 정합니다**(이관 0075).
  const vatApplicable = draft.vat_applicable === "1";
  /** 원화 계약이 **총액으로 적혔는가.** 그 외 통화는 부가세가 없어 늘 총액입니다. */
  const inclusive = vatApplicable && draft.vat_included === "1";
  /** 분당 단가가 기준으로 삼는 금액 — 계약서에 적힌 그 금액입니다. 서버의
   *  `won.billing_amount` 와 같은 갈래이고, 저장할 때 서버가 다시 계산합니다. */
  const billing = n(vatApplicable && !inclusive ? draft.amount_excl_vat : draft.amount_incl_vat);
  /** 분당 단가 = 기준 금액 ÷ (계약 크레딧 ÷ 60). 소수 둘째 자리 — 상세의 「분당 단가」와
   *  같은 자릿수여야 합니다. */
  const credits = n(draft.credits);
  const unitPrice = billing && credits ? (billing / (credits / 60)).toFixed(2) : null;
  return { krw, vatApplicable, inclusive, billing, unitPrice };
}

/** 저장하면 플랜 상태가 무엇이 될지. 서버의 won.plan_status 와 같은 규칙을 이 계약 하나에
 *  적용한 것입니다. **보는 것은 플랜 기간입니다** — 비워 두면 계약 기간과 같다는 뜻이라
 *  서버의 기본값과 같은 자리로 떨어집니다. 중도 해지일이 만료일보다 빠르면 그날 끝납니다. */
export function planPreview(draft: Draft, today = new Date().toISOString().slice(0, 10)): string {
  const start = draft.plan_starts_on || draft.starts_on;
  let end = draft.plan_ends_on || draft.ends_on;
  if (draft.terminated_on && (!end || draft.terminated_on < end)) end = draft.terminated_on;
  if (!start || !end) return "세팅중";
  if (end < today) return "사용 중단";
  if (start > today) return "세팅중";
  return "사용중";
}

/** 저장 전 검사. 통화가 정한 금액 칸과 계약 크레딧, 둘만 필수입니다 — 분당 단가는 그 둘에서
 *  나오는 계산값이라 받지 않습니다. 문제가 없으면 null. */
export function validate(draft: Draft): string | null {
  if (!draft.starts_on || !draft.ends_on || draft.ends_on <= draft.starts_on) {
    return "계약 시작일과 종료일을 확인해 주세요.";
  }
  const { krw, inclusive, billing } = derive(draft);
  if (!billing) {
    return krw && !inclusive
      ? "공급가 (VAT 제외) 를 입력해 주세요."
      : `총 계약금액을 입력해 주세요 (${draft.currency}).`;
  }
  if (!n(draft.credits)) return "계약 크레딧을 입력해 주세요 — 분당 단가가 여기서 나옵니다.";
  return null;
}

/** 서버로 보낼 몸통. 계약서 유형은 `|` 로 잇습니다(라우트가 그렇게 받습니다). */
export function toBody(draft: Draft, docTypes: string[], creditRounds: string, firstCreditOn: string) {
  const body: Record<string, string> = { ...draft, doc_types: docTypes.join("|"),
    credit_rounds: creditRounds, first_credit_on: firstCreditOn };
  return body;
}
