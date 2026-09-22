import { useState } from "react";
import { Field } from "./WonNew";
import { type Contract, type Options, addMonths, n } from "./shared";
import {
  type Draft, derive, fromContract, planPreview, toBody,
} from "./contractDraft";

/** 계약 폼의 **칸 한 벌** — 모달(새 계약)과 상세의 제자리 편집(있는 계약)이 같이 그립니다.
 *
 * 상태는 `useContractDraft` 가 들고, 칸은 `ContractFields`(계약·금액·결제·크레딧 지급·매출
 * 인식·기타)와 `PlanFields`(Perso 계정 및 플랜)가 그립니다. 두 화면이 칸을 각자 들면 칸 하나
 * 늘 때 한쪽만 늘고, 만든 계약과 고친 계약이 다른 모양이 됩니다.
 *
 * 목업(`수주관리목업_0806.html` 의 `renderContractModal`)과 **다른 곳은 두 군데뿐**이고, 둘 다
 * 운영자가 그렇게 하라고 한 것입니다:
 *
 * - **계약 크레딧을 입력받습니다.** 계약서에 적히는 것이 금액과 크레딧이고 분당 단가가 그
 *   둘에서 나옵니다 — 목업은 단가를 받아 크레딧을 계산했는데, 그러면 반올림한 단가로 계산한
 *   크레딧이 계약서의 크레딧과 어긋났습니다.
 * - **통화와 무관하게 환율을 받습니다.** 원화 계약에 USD 단가를 매기는 경우가 흔하고,
 *   예상 MRR 카드가 원화 계약을 USD 로도 보여 주기 때문입니다. 환율이 없으면 그 환산이
 *   매일 오늘 고시가로 다시 일어나 지난달 숫자가 이번 달에 달라 보입니다.
 */
export function useContractDraft(initial?: Contract) {
  const [draft, setDraft] = useState<Draft | null>(() => (initial ? fromContract(initial) : null));
  // 지급 일정 — **지금 깔려 있는 그대로**(없으면 1). 12 로 채우면 이 두 칸을 건드리지 않은
  // 저장이 서버 눈에는 「1회차 → 12회차」로 보여, 비고 한 줄 고치는 저장이 일정을 다시 깝니다.
  const schedule = (c: Contract): [string, string] => [
    String(c.credit_grants?.length || 1),
    c.credit_grants?.[0]?.grant_on || c.starts_on || "",
  ];
  const [creditRounds, setCreditRounds] = useState(() => (initial ? schedule(initial)[0] : "12"));
  const [firstCreditOn, setFirstCreditOn] = useState(() => (initial ? schedule(initial)[1] : ""));
  // 열었을 때의 지급 일정. 이 둘이 바뀌면 저장이 지급 예정 목록을 다시 깝니다 — 그래서
  // 화면이 미리 안내해야 하고, 안내하려면 「무엇이 바뀌었나」를 알아야 합니다. 매 렌더
  // 계약에서 다시 읽지 않는 이유는 초안과 같습니다: 남의 저장 하나에 SSE 로 값이 갈립니다.
  const [creditBase, setCreditBase] = useState<[string, string]>(() => (initial ? schedule(initial) : ["", ""]));
  // 계약 크레딧의 기준선 — 이것을 고친 저장도 회차 금액을 다시 나눕니다(아래 `creditChanged`).
  const [creditsBase, setCreditsBase] = useState(() => (initial ? String(initial.credits ?? "") : ""));
  // 회차가 하나도 없는 계약은 어느 저장이든 깝니다 — 없는 일정을 채우는 길이 이것뿐입니다
  // (상세 카드의 「지급 일정 다시 깔기」 폼은 2026-09-15 에 뺐습니다).
  const [noGrants, setNoGrants] = useState(() => Boolean(initial && !(initial.credit_grants?.length)));

  const set = (key: keyof Draft, value: string) =>
    setDraft((current) => (current ? { ...current, [key]: value } : current));
  /** 있는 계약으로 채웁니다 — 모달이 비동기로 불러온 뒤 부릅니다. 지급 일정 기준선도 같이. */
  const loadContract = (c: Contract) => {
    setDraft(fromContract(c));
    const [rounds, first] = schedule(c);
    setCreditRounds(rounds);
    setFirstCreditOn(first);
    setCreditBase([rounds, first]);
    setCreditsBase(String(c.credits ?? ""));
    setNoGrants(!(c.credit_grants?.length));
  };

  /** 지급 일정을 다시 깔아야 하는 저장인가 — 회차 수 · 첫 지급일 · **계약 크레딧** 중 하나를
   *  고쳤거나, 회차가 하나도 없는 계약이다. 계약 크레딧이 여기 있는 이유: 회차 금액은 그
   *  값을 회차 수로 나눈 것이라, 크레딧만 고치면 회차 합계가 계약과 어긋난 채 남고 그건
   *  카드의 「미지급 크레딧」에만 보입니다. 새 계약(기준선 없음)에서는 언제나 아니오. */
  const creditChanged =
    creditBase[0] !== "" && (
      creditRounds !== creditBase[0] || firstCreditOn !== creditBase[1]
      || (draft?.credits ?? "") !== creditsBase || noGrants);

  /** 서버로 보낼 몸통. **경고를 띄우는 조건이 곧 다시 까는 조건입니다** — 서버는 `credit_reseed`
   *  표가 있을 때만 회차를 다시 깝니다. 폼 값과 행을 비교해 스스로 알아내게 두면, 폼이 빈
   *  첫 지급일 자리에 계약 시작일을 넣어 보내는 것을 「바뀌었다」로 읽고 경고 없이 갈아엎습니다. */
  const body = (): Record<string, string> | null => {
    if (!draft) return null;
    const out = toBody(draft, creditRounds, firstCreditOn);
    if (creditChanged) out.credit_reseed = "1";
    return out;
  };

  return {
    draft, setDraft, set,
    creditRounds, setCreditRounds, firstCreditOn, setFirstCreditOn,
    creditChanged, loadContract, body,
  };
}
export type ContractDraftState = ReturnType<typeof useContractDraft>;

export function Sel({ value, onChange, options }: {
  value: string; onChange: (value: string) => void; options: string[];
}) {
  return (
    <select className="inp" value={value} onChange={(event) => onChange(event.target.value)}>
      {options.map((option) => <option key={option} value={option}>{option || "—"}</option>)}
    </select>
  );
}

/** 「매출 인식」 옆의 작은 (i) — **hover(와 키보드 포커스)로 열립니다**(2026-09-22 운영자
 *  지시: 「클릭이 아니라 hover일때 뜨도록」). 그 전날은 「눌렀을때」라 `<details>` 였습니다.
 *
 *  클래스가 `ihint` 인 이유: 처음 `hint` 로 두었더니 목업에서 옮겨 온 `.won .hint`(카드
 *  바닥의 점선 안내문 — `margin-top:22px; border-top:1px dashed`)와 **겹쳐서**, 동그라미가
 *  라벨 밑으로 밀리고 위에 점선이 그어졌습니다(운영자 보고: 「밑으로 밀려져 있어, 위에
 *  이상한 ------ 표시도 있고」). 그 규칙은 목업의 것이라 안 건드리고 이름을 비켰습니다.
 *  옷은 `.won .ihint` 한 곳에 있습니다.
 *
 *  **한 벌만 두고 두 자리가 같이 씁니다** — 폼(값을 고르는 곳)과 상세 카드(값을 읽는 곳).
 *  두 곳에 따로 적으면 같은 두 줄이 언젠가 서로 다른 말을 합니다. */
export function DealTypeHint() {
  return (
    <span className="ihint" tabIndex={0} role="note" aria-label="매출 인식이란">
      <span className="ihint__i" aria-hidden="true">i</span>
      <span className="ihint__pop">
        MRR — 계약기간 분할인식<br />
        PoC — 일괄인식
      </span>
    </span>
  );
}

/** 계약 · 금액 · 결제 · 크레딧 지급 · 매출 인식 · 기타. `f.draft` 가 있어야 그립니다. */
export function ContractFields({ f, options }: { f: ContractDraftState; options: Options }) {
  const { draft, set } = f;
  if (!draft) return null;
  const { unitPrice } = derive(draft);
  return (
    <>
      <div className="form-sec">계약</div>
      <div className="form-grid3">
        {/* 이름은 **매출 인식**입니다(2026-09-21 운영자 지시, 그 전에는 「수주 유형」).
            저장하는 값은 MRR·PoC 그대로입니다 — 그 두 글자가 워크북 계약 탭 E열의 값이자
            `sheet_to_db` 가 다시 읽는 값이라, 키를 따라 바꾸면 시트를 오가는 계약이 끊깁니다. */}
        <div>
          <label className="form-label">
            매출 인식<span className="req"> *</span><DealTypeHint />
          </label>
          <Sel value={draft.deal_type} onChange={(v) => set("deal_type", v)} options={options.deal_types} />
        </div>
        <Field label="계약 시작일" required>
          <input className="inp" type="date" value={draft.starts_on}
                 onChange={(e) => { set("starts_on", e.target.value); set("ends_on", addMonths(e.target.value, 12)); }} />
        </Field>
        <Field label="계약 종료일" required>
          <input className="inp" type="date" value={draft.ends_on} onChange={(e) => set("ends_on", e.target.value)} />
        </Field>
        {/* **중도 해지일.** 인식 기간은 계약 종료일과 이 날짜 중 빠른 쪽에서 끝납니다.
            비어 있는 것이 보통이고, 적히는 순간 그 계약의 매출 인식이 거기서 멈춥니다.
            환불 계산에 쓰는 크레딧 사용량은 「결제 · MRR」 탭의 「중도 해지 정산」에 있습니다. */}
        <Field label="중도 해지일">
          <input className="inp" type="date" value={draft.terminated_on}
                 onChange={(e) => set("terminated_on", e.target.value)} />
        </Field>
        {/* 수주 전환 대기에서 온 건은 티켓이 따라오고, **그 밖에는 손으로 적습니다**
            (2026-08-19, 운영자 지시 — 고객은 Client ID 로 묶이지만 계약별로 티켓을
            붙이고 싶은 건이 있습니다). 목업대로 읽기 전용이던 칸입니다. 서버는 예전부터
            받고 있었고(`_CONTRACT_FIELDS`), 막고 있던 것은 이 칸 하나였습니다.
            비우면 연동이 풀립니다 — 잘못 적은 값을 되돌릴 길이 있어야 합니다. */}
        <Field label="Ticket ID">
          <input className="inp" value={draft.ticket_id}
                 onChange={(e) => set("ticket_id", e.target.value)}
                 placeholder="인바운드 건은 자동 연동 · 그 외 직접 입력" />
        </Field>
        {/* **고객마다가 아니라 계약마다입니다**(2026-08-31 운영자 지시). 고객 기본
            정보에 한 벌만 있던 시절에는 두 번째 계약을 맺는 순간 첫 계약의 담당자가
            덮여 사라졌고, 그것이 화면에서는 「담당자가 바뀌었다」와 같아 보였습니다.
            재계약이면 직전 계약에서 물려받습니다 — 대개 같은 사람입니다. */}
        <Field label="고객 담당자">
          <input className="inp" value={draft.contact_name}
                 onChange={(e) => set("contact_name", e.target.value)}
                 placeholder="예: 박지훈 팀장" />
        </Field>
        <Field label="고객 연락처">
          <input className="inp" value={draft.contact_info}
                 onChange={(e) => set("contact_info", e.target.value)}
                 placeholder="이메일 또는 전화번호" />
        </Field>
        {/* 목업대로 손으로 적는 칸입니다. 계약서에 적히는 것이 금액과 크레딧이고,
            분당 단가가 그 둘에서 나옵니다 — 한동안 반대로 두었는데, 그러면 반올림한
            단가로 계산한 크레딧이 계약서의 크레딧과 어긋났습니다. */}
        <Field label="계약 크레딧" required>
          <input className="inp" type="number" value={draft.credits}
                 onChange={(e) => set("credits", e.target.value)} placeholder="예: 64800" />
        </Field>
        {/* 「크레딧 사용량」이 여기 있었습니다 — 2026-09-21 에 「결제 · MRR」 탭의 「중도
            해지 정산」 박스로 옮겼습니다(운영자 지시). 묻는 자리가 곧 그 값이 무엇에
            쓰이는지를 말합니다. 「계약서 유형」은 같은 날 아예 없어졌습니다(이관 0125). */}
      </div>

      <div className="form-sec">금액</div>
      {/* 「VAT 해당 여부」가 이 줄 맨 앞에 있었습니다 — 공급가가 있는 계약인지를 정하던
          칸인데, 공급가 자체를 안 쓰게 되면서(2026-09-22 운영자 지시) 같이 나갔습니다. */}
      <div className="form-grid3">
        <Field label="통화">
          <Sel value={draft.currency} onChange={(v) => set("currency", v)} options={options.currencies} />
        </Field>
        {/* **통화와 무관하게 묻습니다** (2026-08-31 운영자 지시). 예전에는 원화 계약에
            안 물었는데, 그건 한쪽 방향만 본 이야기였습니다: 예상 MRR 카드는 원화 계약을
            USD 로도 보여 주고, 계약에 환율이 없으면 그 환산이 매일 오늘 고시가로 다시
            일어납니다 — 지난달 숫자가 오늘 환율에 따라 움직입니다.

            비워 두면 저장할 때 계약일 고시가(없으면 오늘 고시가)를 조회해 계약 행에
            박아 둡니다. 이 칸은 비어 있으면 안 되는 칸입니다. */}
        <Field label="환율 (USD → KRW)">
          <input className="inp" type="number" value={draft.fx_rate}
                 onChange={(e) => set("fx_rate", e.target.value)}
                 placeholder="비우면 계약일 고시가로 자동" />
        </Field>
        {/* **칸은 하나입니다** (2026-09-21 운영자 지시: 「계약금액은 모두 VAT 포함만으로」).
            그전에는 칸이 둘이고 「분당단가 기준」 고르개가 어느 쪽을 기준으로 삼을지
            정했습니다 — 모르면 분당 단가가 계약마다 10% 씩 달라졌기 때문입니다. 그 사실은
            이제 **계약 비고**가 듭니다(이관 0123 이 옛 계약마다 한 줄 적어 두었습니다).

            라벨에 「(VAT 포함)」을 안 답니다 — 이제 모든 계약금액이 그렇습니다. */}
        <div style={{ gridColumn: "span 2" }}>
          <label className="form-label">계약금액 <span className="req">*</span></label>
          <input className="inp" type="number" value={draft.amount_incl_vat}
                 onChange={(e) => set("amount_incl_vat", e.target.value)}
                 placeholder="예: 11000000" />
        </div>
        {/* 계산값입니다 — 계약금액 ÷ (크레딧 ÷ 60). 소수점은 남깁니다: 반올림한 단가는
            되짚어 곱했을 때 금액이 안 맞습니다. 계약서가 다른 기준의 단가로 적힌 건이면
            그 숫자를 계약 비고에 적어 두십시오. */}
        <Field label="분당 단가">
          <div className="inp" aria-readonly="true"
               style={{ background: "var(--bg-soft)", fontVariantNumeric: "tabular-nums",
                        color: unitPrice === null ? "var(--faint)" : "var(--ink)" }}>
            {unitPrice === null ? "금액 · 크레딧 입력 시 계산" : `${unitPrice} ${draft.currency}`}
          </div>
        </Field>
      </div>

      <div className="form-sec">결제</div>
      <div className="form-grid3">
        <Field label="결제 수단">
          <Sel value={draft.payment_method} onChange={(v) => set("payment_method", v)} options={options.payment_methods} />
        </Field>
        <Field label="결제 방식">
          <Sel value={draft.payment_type} onChange={(v) => set("payment_type", v)} options={options.payment_types} />
        </Field>
        <Field label="총 분납 횟수">
          <input className="inp" type="number" min={1} value={draft.installments}
                 disabled={draft.payment_type !== "할부"}
                 onChange={(e) => set("installments", e.target.value)} />
        </Field>
        <Field label="최초 결제일">
          <input className="inp" type="date" value={draft.first_payment_on}
                 onChange={(e) => set("first_payment_on", e.target.value)} />
        </Field>
        <div style={{ gridColumn: "span 2" }}>
          <label className="form-label">Billing Email</label>
          <input className="inp" value={draft.billing_email} placeholder="예: ap@company.com"
                 onChange={(e) => set("billing_email", e.target.value)} />
        </div>
      </div>

      <div className="form-sec">크레딧 지급</div>
      <div className="form-grid3">
        <Field label="총 지급 회차">
          <input className="inp" type="number" min={1} value={f.creditRounds}
                 onChange={(e) => f.setCreditRounds(e.target.value)} />
        </Field>
        <Field label="첫 지급 예정일">
          <input className="inp" type="date" value={f.firstCreditOn}
                 onChange={(e) => f.setFirstCreditOn(e.target.value)} />
        </Field>
        <div style={{ display: "flex", alignItems: "flex-end", fontSize: 12,
                      color: f.creditChanged ? "var(--red-fg)" : "var(--faint)" }}>
          {f.creditChanged
            ? "저장하면 지급 예정 목록을 다시 깝니다 — 손으로 추가·수정한 회차와 지급 완료 표시가 모두 사라집니다."
            : "회차별 크레딧은 균등 분배로 자동 생성됩니다."}
        </div>
      </div>

      {/* 절 이름이 「매출 인식」이었습니다. 위 계약 절의 칸이 그 이름을 갖게 되면서
          (2026-09-21) 한 폼에 같은 말이 둘이 됐고, 그중 하나만 줄였습니다. */}
      <div className="form-sec">매출 인식 시작</div>
      <div className="form-grid3">
        <Field label="매출 인식 시작 월">
          <input className="inp" type="month" value={draft.revenue_from}
                 onChange={(e) => set("revenue_from", e.target.value)} />
          <div style={{ fontSize: 11.5, color: "var(--faint)", marginTop: 4 }}>
            비우면 계약 시작월부터 인식합니다. (MRR만 적용)
          </div>
        </Field>
      </div>

      <div className="form-sec">기타</div>
      <div>
        <label className="form-label">계약 비고</label>
        <textarea className="inp" rows={2} value={draft.note}
                  onChange={(e) => set("note", e.target.value)}
                  placeholder="갱신 조건, 협의 내용 등" />
      </div>
    </>
  );
}

/** Perso 계정 및 플랜 — 플랜 기간·플랜·계정 한도·space_seq, 그리고 저장하면 플랜 상태가
 *  무엇이 될지. `heading` 이 false 면 절 제목을 안 그립니다(카드 머리가 이미 그 말을 할 때). */
export function PlanFields({ f, options, heading = true }: {
  f: ContractDraftState; options: Options; heading?: boolean;
}) {
  const { draft, set } = f;
  if (!draft) return null;
  return (
    <>
      {heading && <div className="form-sec">Perso 계정 및 플랜</div>}
      <div className="note-box">
        플랜 기간은 계약 기간과 다를 수 있습니다 — 계약을 먼저 맺고 사용을 늦게 시작하는
        경우입니다. <b>MRR 과 「사용중」은 계약 기간이 정합니다</b>(2026-09-21). 이 두 날짜는
        크레딧 소진 속도(경과율·사용 전망)와 워크북에만 쓰이고, 비우면 계약 기간과 같습니다.
      </div>
      <div className="form-grid3">
        {/* **플랜 기간은 계약 기간과 다른 것입니다** (2026-08-31 운영자 지시). 계약은
            먼저 맺고 실제 사용은 늦게 시작하는 일이 흔합니다.

            **2026-09-21 부터 MRR 과 플랜 상태는 이 날짜를 안 봅니다** — 운영자 지시로
            계약 기간으로 돌아왔습니다(`won.plan_period`). 남은 독자는 크레딧 소진 속도
            (`usage.ts` 의 경과율·사용 전망)와 워크북 AE·AF 열입니다. 비워 두면 계약
            기간과 같습니다 — 대부분의 계약이 그렇고, 그때는 아무것도 안 적으면 됩니다. */}
        <Field label="플랜 시작일">
          <input className="inp" type="date" value={draft.plan_starts_on}
                 onChange={(e) => set("plan_starts_on", e.target.value)} />
        </Field>
        <Field label="플랜 만료일">
          <input className="inp" type="date" value={draft.plan_ends_on}
                 onChange={(e) => set("plan_ends_on", e.target.value)} />
        </Field>
        <Field label="플랜">
          <Sel value={draft.plan} onChange={(v) => set("plan", v)} options={options.plans} />
        </Field>
        <Field label="플랜명">
          <input className="inp" value={draft.plan_name} onChange={(e) => set("plan_name", e.target.value)} />
        </Field>
        <Field label="Perso Email">
          <input className="inp" value={draft.perso_email} onChange={(e) => set("perso_email", e.target.value)} />
        </Field>
        <Field label="Account Invitation Limit">
          <input className="inp" type="number" value={draft.invite_limit} onChange={(e) => set("invite_limit", e.target.value)} />
        </Field>
        <Field label="Queue limit">
          <input className="inp" type="number" value={draft.queue_limit} onChange={(e) => set("queue_limit", e.target.value)} />
        </Field>
        <Field label="Concurrent Jobs">
          <input className="inp" type="number" value={draft.concurrent_jobs} onChange={(e) => set("concurrent_jobs", e.target.value)} />
        </Field>
        <Field label="Space 개수">
          <input className="inp" type="number" value={draft.space_count} onChange={(e) => set("space_count", e.target.value)} />
        </Field>
        <div style={{ gridColumn: "span 2" }}>
          <label className="form-label">space_seq</label>
          <input className="inp" value={draft.space_seq} onChange={(e) => set("space_seq", e.target.value)}
                 placeholder="여러 개면 쉼표로" />
        </div>
      </div>
      {/* 「저장 후 플랜 상태」 고르개가 여기 있었습니다. 플랜 상태는 계약 기간이 정합니다 —
          **계약 절의 시작일·종료일**이 곧 그 값입니다(이 절의 플랜 날짜가 아닙니다).
          고르개를 남겨 두면 사람이 고른 값과 날짜가 말하는 값이 갈라지고, 그때 어느 쪽이
          맞는지 아무도 모릅니다. 아래 줄이 지금 무엇이 될지 미리 말해 줍니다. */}
      <div className="note-box" style={{ marginTop: 14 }}>
        플랜 상태는 계약 기간에서 정해집니다 — 이 계약은 저장하면{" "}
        <b>{planPreview(draft)}</b> 입니다.
      </div>
    </>
  );
}

/** 지급 일정 확인 창에 적을 한 줄 — 「12회차 · 첫 지급 2026-01-01」. */
export function scheduleLabel(f: ContractDraftState) {
  return `${n(f.creditRounds) || "?"}회차 · 첫 지급 ${f.firstCreditOn || "—"}`;
}
