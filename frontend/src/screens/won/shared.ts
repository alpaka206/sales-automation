/** 수주 고객 화면이 함께 쓰는 타입과 표시 규칙.
 *
 * 계산은 서버가 합니다(`src/common/won.py`). 여기 있는 것은 **표시**뿐입니다 — 숫자를
 * 어떻게 찍을지, 날짜를 어떻게 줄일지. 계산을 양쪽에 두면 화면과 API 가 다른 답을 냅니다.
 */

export type Money = string | number | null;

export type Grant = {
  id: number; no: number; total: number; grant_on: string | null;
  amount: number | null; granted_by: string | null; done: boolean; memo: string | null;
};
export type Payment = {
  id: number; no: number; total: number; paid_on: string | null;
  amount: Money; done: boolean; fx_rate: Money; fx_on: string | null;
};
export type Claim = {
  id: number; kind: string; happened_on: string | null; compensation: string | null;
  progress: string; action_on: string | null;
};
export type Contract = {
  id: number; seq: number; label: string; state: string;
  ticket_id: string | null; deal_type: string;
  starts_on: string | null; ends_on: string | null; months: number;
  doc_types: string[]; credits: number | null; currency: string;
  amount_incl_vat: Money; amount_excl_vat: Money;
  unit_price: Money; unit_currency: string | null; unit_fx_rate: Money;
  payment_method: string | null; payment_type: string | null; installments: number | null;
  first_payment_on: string | null; billing_email: string | null; note: string | null;
  renewal_plan: string | null; stop_reason: string | null; memo: string | null;
  revenue_from: string | null; revenue_from_set: boolean; monthly_revenue: Money;
  plan: string | null; plan_name: string | null; perso_email: string | null;
  plan_starts_on: string | null; plan_ends_on: string | null; plan_days_left: number | null;
  invite_limit: number | null; queue_limit: number | null; concurrent_jobs: number | null;
  space_count: number | null; space_seq: string | null;
  granted_credits: number; collected: Money;
  next_credit_on: string | null; next_credit_amount: number | null;
  next_pay_on: string | null; next_pay_amount: Money;
  credit_grants: Grant[]; payments: Payment[]; claims: Claim[];
};
export type Row = {
  client_id: number; company: string; customer_type: string;
  industry: string | null; country: string | null; department: string | null;
  contact_name: string | null; contact_info: string | null;
  first_won_on: string | null; plan_status: string; owner: string | null;
  contact_id: number | null; setup_count: number; open_claims: number;
  active: Contract | null; contract_count?: number; contracts?: Contract[];
  comms?: Comm[];
};
export type Comm = {
  id: number; channel: string; handler: string | null; subject: string | null;
  summary: string; happened_at: string; contract_seq: number | null;
};
export type Options = {
  industries: string[]; plans: string[]; plan_statuses: string[]; deal_types: string[];
  doc_types: string[]; renewal_plans: string[]; claim_progress: string[];
  payment_methods: string[]; payment_types: string[]; currencies: string[];
  customer_types: string[]; departments: string[];
};
export type ListData = {
  today: string;
  rows: Row[];
  pending: { id: number; ticket_id: string; company: string | null; client_id: number | null;
             won_type: string | null; won_on: string | null }[];
  boards: {
    credit: { client_id: number; company: string; on: string; amount: number | null }[];
    payment: { client_id: number; company: string; on: string; amount: Money; currency: string }[];
    claim: { client_id: number; company: string; kind: string; on: string | null; progress: string }[];
  };
  fx_rate: number;
  options: Options;
};

export const n = (value: Money | number | null | undefined): number =>
  value === null || value === undefined || value === "" ? 0 : Number(value);

/** 목업과 같은 표기: 통화 기호 + 천 단위 쉼표, 소수점 없음. */
export const money = (value: Money, currency = "KRW"): string =>
  (currency === "USD" ? "$" : "₩") + Math.round(n(value)).toLocaleString("en-US");

export const num = (value: number | null | undefined): string =>
  Number(value ?? 0).toLocaleString("en-US");

/** `2026-08-06` → `26.08.06`. 목업의 날짜 표기입니다. */
export const fmt = (value: string | null | undefined): string =>
  value ? value.replaceAll("-", ".").slice(2) : "—";

export const daysUntil = (value: string | null | undefined, today: string): number | null => {
  if (!value) return null;
  return Math.round((Date.parse(value) - Date.parse(today)) / 86400000);
};

/** 며칠 남았나 — 지났으면 빨강, 7일 이내면 주황. 목업의 `dueClass`. */
export const dueClass = (value: string | null | undefined, today: string): string => {
  const left = daysUntil(value, today);
  if (left === null) return "";
  if (left < 0) return "over";
  return left <= 7 ? "due" : "";
};

export const dueText = (value: string | null | undefined, today: string): string => {
  const left = daysUntil(value, today);
  if (left === null) return "—";
  if (left < 0) return `${fmt(value)} · ${-left}일 지남`;
  if (left === 0) return `${fmt(value)} · 오늘`;
  return `${fmt(value)} · ${left}일 뒤`;
};

/** 목록 정렬: 손이 가야 하는 것이 위로. 세팅중 → 사용중 → 사용 중단. */
export const STATUS_ORDER: Record<string, number> = { "세팅중": 0, "사용중": 1, "사용 중단": 2 };

export const initials = (name: string): string =>
  (name.replace(/[^A-Za-z가-힣 ]/g, "").split(" ")[0] || name).slice(0, 2).toUpperCase();

/** 목업의 뱃지 색 규칙. */
export const statusTone = (status: string): string =>
  status === "사용중" ? "ok" : status === "세팅중" ? "warn" : "off";

export const planTone = (plan: string | null): string =>
  plan === "Enterprise" ? "ent" : "biz";
