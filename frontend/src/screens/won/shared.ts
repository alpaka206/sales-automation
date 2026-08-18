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
export type Contract = {
  id: number; seq: number; label: string; state: string;
  ticket_id: string | null; deal_type: string;
  starts_on: string | null; ends_on: string | null; months: number;
  doc_types: string[]; credits: number | null; currency: string;
  amount_incl_vat: Money; amount_excl_vat: Money;
  /** **분당 단가의 기준이 VAT 포함 금액인가.** 화면의 「공급가 선택」이 고른 값입니다.
   *  부가세가 없는 계약에는 고를 것이 없어 늘 false 입니다. */
  vat_included: boolean;
  /** 부가세가 붙는 계약인가. **통화가 아니라 고객이 정합니다**(국내 법인이면 해당).
   *  이 값이 폼의 금액 칸을 한 개로 할지 두 개로 할지 정합니다. */
  vat_applicable: boolean;
  /** 그 계약에 적용할 환율과 기준 날짜. 비어 있으면 저장할 때 계약일 고시가로 채웁니다. */
  fx_rate: Money; fx_on: string | null;
  /** 중도 해지일 — 플랜은 만료일과 이 날짜 중 빠른 쪽에서 끝납니다. */
  terminated_on: string | null;
  /** 크레딧 사용량. 수동 입력이라 비어 있는 것이 정상입니다. */
  credits_used: number | null;
  /** 계산값 — 금액 ÷ (계약 크레딧 ÷ 60). 통화는 계약 통화입니다. */
  unit_price: Money;
  payment_method: string | null; payment_type: string | null; installments: number | null;
  first_payment_on: string | null; billing_email: string | null; note: string | null;
  revenue_from: string | null; revenue_from_set: boolean; monthly_revenue: Money;
  plan: string | null; plan_name: string | null; perso_email: string | null;
  plan_starts_on: string | null; plan_ends_on: string | null; plan_days_left: number | null;
  invite_limit: number | null; queue_limit: number | null; concurrent_jobs: number | null;
  space_count: number | null; space_seq: string | null;
  granted_credits: number; collected: Money;
  next_credit_on: string | null; next_credit_amount: number | null;
  next_credit_no: number | null; next_credit_total: number | null;
  next_pay_on: string | null; next_pay_amount: Money;
  next_pay_no: number | null; next_pay_total: number | null;
  credit_grants: Grant[]; payments: Payment[];
};
export type Row = {
  client_id: number; company: string; customer_type: string;
  industry: string | null; country: string | null;
  /** 적어 둔 값이 없으면 Client ID 번호대에서 되짚은 값 — 서버가 정합니다(won.department). */
  department: string | null;
  contact_name: string | null; contact_info: string | null;
  /** 연결된 인바운드 연락처의 것. 목록 검색이 씁니다. 아웃바운드 고객은 비어 있습니다. */
  email: string | null; phone: string | null;
  first_won_on: string | null; plan_status: string; owner: string | null;
  contact_id: number | null; setup_count: number;
  /** 이번 달에 이 고객이 얹은 금액, 통화별. 계약 **전부**를 더한 값입니다. */
  month_revenue: Record<string, number>;
  active: Contract | null; contract_count?: number; contracts?: Contract[];
  comms?: Comm[];
};
export type Comm = {
  id: number; channel: string; handler: string | null; subject: string | null;
  summary: string; happened_at: string; contract_seq: number | null;
};
export type Options = {
  industries: string[]; plans: string[]; plan_statuses: string[]; deal_types: string[];
  doc_types: string[];
  payment_methods: string[]; payment_types: string[]; currencies: string[];
  customer_types: string[]; departments: string[];
  /** 「전체」의 이름 — 부서가 아니라 셋을 합친 묶음이고, 서버가 보낸 키와 같아야 합니다. */
  all_departments: string;
};
export type ListData = {
  today: string;
  rows: Row[];
  pending: { id: number; ticket_id: string; company: string | null; client_id: number | null;
             won_type: string; next_seq: number; known: boolean; won_on: string | null }[];
  boards: {
    credit: { client_id: number; company: string; on: string; amount: number | null;
              no: number | null; total: number | null }[];
    payment: { client_id: number; company: string; on: string; amount: Money; currency: string;
               no: number | null; total: number | null }[];
  };
  fx_rate: number;
  fx_on: string | null;
  fx_source: string;
  /** 못 가져왔을 때 왜 — 「설정값」 옆 툴팁에 그대로 적습니다. */
  fx_error: string | null;
  /** 이번 달(YYYY-MM). 카드가 「이번달」이라고 말하는 그 달입니다. */
  month: string;
  /** 이번 달로 끝나는 최근 12개월, 오래된 것부터. */
  months: string[];
  /** 담당부서 → 달 → 통화 → 금액.
   *
   *  `mrr_months` 는 **플랜 기간에 균등 배분한 인식 매출**이고, `cash_months` 는 결제 회차가
   *  잡힌 달에 통째로 얹는 **현금흐름**입니다. 둘이 갈릴 때가 그 계약을 봐야 할 때라 한
   *  화면에 같이 둡니다.
   *
   *  두 통화가 다 채워져 있습니다 — 환산은 서버가 **계약마다 그 계약의 환율로** 한 번만
   *  합니다. 화면이 다시 환산하면 같은 숫자가 화면마다 달라집니다. 「전체」 묶음도 서버가
   *  같이 만듭니다: 화면이 부서별 값을 다시 더하면 그 덧셈이 두 곳에 생깁니다. */
  mrr_months: Record<string, Record<string, Record<string, number>>>;
  cash_months: Record<string, Record<string, Record<string, number>>>;
  options: Options;
};

export const n = (value: Money | number | null | undefined): number =>
  value === null || value === undefined || value === "" ? 0 : Number(value);

/** 목업과 같은 표기: 통화 기호 + 천 단위 쉼표, 기본은 소수점 없음.
 *
 * `decimals` 는 **분당 단가**를 위한 것입니다. 계약 금액은 원 단위까지가 전부라 소수점이
 * 군더더기지만, 단가는 금액 ÷ (크레딧 ÷ 60) 이라 딱 떨어지는 쪽이 드뭅니다 — 2,630.89 를
 * 2,631 로 반올림해 보여 주면, 그 값을 다시 곱해 본 사람이 계약서의 금액과 안 맞는다고
 * 생각합니다. 실제 계산은 늘 온전한 값으로 하고 여기서는 표기만 자릅니다. */
export const money = (value: Money, currency = "KRW", decimals = 0): string =>
  (currency === "USD" ? "$" : "₩") +
  n(value).toLocaleString("en-US", {
    minimumFractionDigits: decimals, maximumFractionDigits: decimals,
  });

/** 금액을 읽히는 단위로. **단위는 한 번 정해 그 화면이 같이 씁니다.**
 *
 *  `man()` 한 줄이 하던 일이었는데(`₩{value/10000}만`), 세 군데서 어긋났습니다:
 *  0 이 `₩0만` 으로, 음수가 `₩-940만` 으로(부호가 통화 기호 뒤), 그리고 같은 축에서 어떤
 *  눈금은 50만·어떤 눈금은 1,000만이라 자릿수를 세어야 비교가 됐습니다.
 *
 *  기준을 **최댓값 하나**로 잡고 축 전체가 같은 단위를 쓰면 눈금이 서로 비교됩니다.
 *  원화만 억·만으로 접습니다 — 달러에는 그런 단위가 없습니다. */
export type Scale = { div: number; suffix: string };

export const scaleFor = (peak: number, currency: string): Scale => {
  if (currency !== "KRW") return { div: 1, suffix: "" };
  const size = Math.abs(peak);
  if (size >= 100_000_000) return { div: 100_000_000, suffix: "억" };
  if (size >= 10_000) return { div: 10_000, suffix: "만" };
  return { div: 1, suffix: "" };
};

/** 부호는 **통화 기호 앞**입니다. `₩-940만` 은 원화가 음수인 것처럼 읽힙니다. */
const scaled = (value: number, scale: Scale): string => {
  const size = Math.abs(value);
  // **접을 수 없는 값은 접지 않습니다.** 5,000원을 `0.5만` 이라고 쓰면 접은 것이 아니라
  // 읽기 어렵게 만든 것입니다 — 자릿수가 적어 원 단위가 이미 짧습니다.
  if (size < scale.div) return size.toLocaleString("en-US");
  const folded = size / scale.div;
  // 한 자리 수만 소수 한 자리까지: `2.5억` 은 `250,000,000` 보다 읽히고 `3억` 보다 정확합니다.
  const digits = folded < 10 ? 1 : 0;
  return folded.toLocaleString("en-US", {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  }) + scale.suffix;
};

/** 통화 기호가 붙은 금액. 0 에는 단위를 안 붙입니다 — `₩0만` 은 금액이 아닙니다. */
export const amount = (value: Money, currency = "KRW", scale?: Scale): string => {
  const raw = n(value);
  const symbol = currency === "USD" ? "$" : "₩";
  if (!raw) return `${symbol}0`;
  return `${raw < 0 ? "-" : ""}${symbol}${scaled(raw, scale ?? scaleFor(raw, currency))}`;
};

/** 축 눈금 — 통화 기호 없이. 기호는 큰 숫자와 고르개가 이미 말하고, 눈금마다 반복하면
 *  격자보다 시끄러워집니다. */
export const tickLabel = (value: number, scale: Scale): string =>
  value === 0 ? "0" : `${value < 0 ? "-" : ""}${scaled(value, scale)}`;

export const num = (value: number | null | undefined): string =>
  Number(value ?? 0).toLocaleString("en-US");

/** `2026-08-06` → `26.08.06`. 목업의 날짜 표기입니다. */
export const fmt = (value: string | null | undefined): string =>
  value ? value.replaceAll("-", ".").slice(2) : "—";

export const daysUntil = (value: string | null | undefined, today: string): number | null => {
  if (!value) return null;
  return Math.round((Date.parse(value) - Date.parse(today)) / 86400000);
};

/** 며칠 남았나 — 지났으면 빨강, **14일** 이내면 주황. 목업의 `dueClass` 그대로입니다.
 *  7일로 좁히면 다음 주에 할 일이 회색으로 묻혀, 월요일에 몰아서 보는 화면이 못 됩니다. */
export const dueClass = (value: string | null | undefined, today: string): string => {
  const left = daysUntil(value, today);
  if (left === null) return "";
  if (left < 0) return "over";
  return left <= 14 ? "due" : "";
};

/** 며칠 남았나만. `D-6` / `오늘` / `3일 지연`. 목업의 `dueText` 그대로입니다.
 *
 * 날짜가 이미 옆에 있는 자리에서 씁니다 — `26.08.12` 를 적어 놓고 다시 `26.08.12 · 6일 뒤`
 * 라고 쓰면 같은 날짜가 두 번입니다. 훑는 화면에서 두 번 읽히는 글자는 그만큼 느립니다.
 *
 * 지난 것을 `D+3` 이 아니라 `3일 지연` 이라 쓰는 이유: `D-3` 과 `D+3` 은 부호 하나 차이라
 * 훑을 때 뒤집혀 읽힙니다. 늦은 건은 글자가 달라야 눈에 걸립니다.
 */
export const dday = (value: string | null | undefined, today: string): string => {
  const left = daysUntil(value, today);
  if (left === null) return "—";
  if (left === 0) return "오늘";
  return left < 0 ? `${-left}일 지연` : `D-${left}`;
};

/** 날짜와 D-day 를 함께. 날짜가 그 자리에만 있는 곳(액션 보드)에서 씁니다. */
export const dueText = (value: string | null | undefined, today: string): string => {
  const left = daysUntil(value, today);
  if (left === null) return "—";
  return `${fmt(value)} · ${dday(value, today)}`;
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

/** n개월 뒤 같은 날. 그 달에 그 날이 없으면 말일 — 서버의 `_add_months` 와 같은 규칙입니다.
 *  매출 인식 막대가 쓰고, 계약 폼의 기본 종료일이 씁니다. */
export const addMonths = (iso: string, months: number): string => {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  const last = new Date(y, m - 1 + months + 1, 0).getDate();
  const at = new Date(y, m - 1 + months, Math.min(d, last));
  return `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, "0")}-${String(at.getDate()).padStart(2, "0")}`;
};
