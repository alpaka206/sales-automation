import { useState } from "react";
import { Field } from "./WonNew";
import { type Contract, type Options, addMonths, n } from "./shared";
import {
  type Draft, derive, fromContract, planPreview, toBody, withAmount,
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
 * - **계약 크레딧을 입력받지 않습니다.** 목업은 손으로 적는 칸인데, 공급가 ÷ 분당 단가 × 60
 *   으로 계산해 같은 자리에 보여 줍니다. 시트에서 손으로 들어가다 보니 계약마다 계산 기준이
 *   달랐습니다. 그래서 **공급가 (VAT 제외)** 칸이 하나 늘었습니다 — 목업에는 없습니다.
 * - **통화와 무관하게 환율을 받습니다.** 원화 계약에 USD 단가를 매기는 경우가 흔하고,
 *   예상 MRR 카드가 원화 계약을 USD 로도 보여 주기 때문입니다. 환율이 없으면 그 환산이
 *   매일 오늘 고시가로 다시 일어나 지난달 숫자가 이번 달에 달라 보입니다.
 */
export function useContractDraft(initial?: Contract) {
  const [draft, setDraft] = useState<Draft | null>(() => (initial ? fromContract(initial) : null));
  const [docTypes, setDocTypes] = useState<string[]>(() => initial?.doc_types ?? []);
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

  const set = (key: keyof Draft, value: string) =>
    setDraft((current) => (current ? { ...current, [key]: value } : current));
  const setAmount = (which: "incl" | "excl", value: string) =>
    setDraft((current) => (current ? withAmount(current, which, value) : current));
  /** 있는 계약으로 채웁니다 — 모달이 비동기로 불러온 뒤 부릅니다. 지급 일정 기준선도 같이. */
  const loadContract = (c: Contract) => {
    setDraft(fromContract(c));
    setDocTypes(c.doc_types || []);
    const [rounds, first] = schedule(c);
    setCreditRounds(rounds);
    setFirstCreditOn(first);
    setCreditBase([rounds, first]);
  };

  /** 지급 일정을 건드렸는가. 새 계약(기준선 없음)에서는 언제나 아니오 — 다시 깔 것이 없습니다. */
  const creditChanged =
    creditBase[0] !== "" && (creditRounds !== creditBase[0] || firstCreditOn !== creditBase[1]);

  /** 서버로 보낼 몸통. **경고를 띄우는 조건이 곧 다시 까는 조건입니다** — 서버는 `credit_reseed`
   *  표가 있을 때만 회차를 다시 깝니다. 폼 값과 행을 비교해 스스로 알아내게 두면, 폼이 빈
   *  첫 지급일 자리에 계약 시작일을 넣어 보내는 것을 「바뀌었다」로 읽고 경고 없이 갈아엎습니다. */
  const body = (): Record<string, string> | null => {
    if (!draft) return null;
    const out = toBody(draft, docTypes, creditRounds, firstCreditOn);
    if (creditChanged) out.credit_reseed = "1";
    return out;
  };

  return {
    draft, setDraft, set, setAmount, docTypes, setDocTypes,
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

/** 계약 · 금액 · 결제 · 크레딧 지급 · 매출 인식 · 기타. `f.draft` 가 있어야 그립니다. */
export function ContractFields({ f, options }: { f: ContractDraftState; options: Options }) {
  const { draft, set, setAmount, docTypes, setDocTypes } = f;
  if (!draft) return null;
  const { vatApplicable, unitPrice } = derive(draft);
  return (
    <>
      <div className="form-sec">계약</div>
      <div className="form-grid3">
        <Field label="수주 유형" required>
          <Sel value={draft.deal_type} onChange={(v) => set("deal_type", v)} options={options.deal_types} />
        </Field>
        <Field label="계약 시작일" required>
          <input className="inp" type="date" value={draft.starts_on}
                 onChange={(e) => { set("starts_on", e.target.value); set("ends_on", addMonths(e.target.value, 12)); }} />
        </Field>
        <Field label="계약 종료일" required>
          <input className="inp" type="date" value={draft.ends_on} onChange={(e) => set("ends_on", e.target.value)} />
        </Field>
        {/* **중도 해지일.** 플랜은 만료일과 이 날짜 중 빠른 쪽에서 끝납니다. 비어 있는
            것이 보통이고, 적히는 순간 그 계약의 매출 인식이 거기서 멈춥니다. */}
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
        {/* **수동 입력입니다.** 제품 쪽에서 사용량을 가져오는 경로가 아직 없습니다. 비어
            있으면 예상 환불 금액을 계산하지 않습니다 — 없는 값을 0 으로 두면 「하나도 안
            썼으니 전액 환불」이 되어 해지월 매출이 통째로 음수가 됩니다. */}
        <Field label="크레딧 사용량">
          <input className="inp" type="number" value={draft.credits_used}
                 onChange={(e) => set("credits_used", e.target.value)}
                 placeholder="중도 해지 시 환불 계산에 씁니다" />
        </Field>
        <div style={{ gridColumn: "span 3" }}>
          <label className="form-label">계약서 유형 <span style={{ color: "var(--faint)" }}>(복수 선택)</span></label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 14, padding: "7px 0 2px" }}>
            {options.doc_types.map((item) => (
              <label key={item} style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13, cursor: "pointer" }}>
                <input type="checkbox" checked={docTypes.includes(item)}
                       onChange={(e) => setDocTypes(
                         e.target.checked ? [...docTypes, item] : docTypes.filter((x) => x !== item))} />
                {item}
              </label>
            ))}
          </div>
        </div>
      </div>

      <div className="form-sec">금액</div>
      {/* 순서가 뜻을 갖습니다(운영자 지시): **부가세 해당 여부 → 통화 → 환율 → 금액 →
          공급가.** 앞의 것이 뒤의 것을 정하기 때문입니다 — 해당 여부가 금액 칸을 한 개로
          할지 두 개로 할지 정하고, 통화가 환율을 물어볼지 말지 정합니다. */}
      <div className="form-grid3">
        <Field label="VAT 해당 여부">
          <select className="inp" value={draft.vat_applicable}
                  onChange={(e) => set("vat_applicable", e.target.value)}>
            <option value="1">VAT 해당 (국내 법인 고객)</option>
            <option value="">VAT 미해당 (그 외 고객)</option>
          </select>
        </Field>
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
        {/* **어느 칸을 받는지는 통화와 「금액 기준」이 함께 정합니다.** 국내 계약서는
            공급가로 적히고 부가세가 따로 붙는 것이 흔하지만, 총액으로 적히는 계약도
            있습니다 — 그것을 공급가 칸에 넣으면 분당 단가가 10% 낮게 나오고 화면
            어디에도 그게 보이지 않습니다. 해외 계약에는 부가세가 없어 총액이 곧
            대금이라 고를 것이 없습니다. 어느 쪽이든 채우는 칸은 하나입니다: 둘 다
            받으면 분당 단가가 어느 쪽 기준인지 계약마다 달라집니다. */}
        {/* **해당이면 칸이 둘입니다.** 한쪽을 적으면 다른 쪽이 10% 로 따라옵니다 —
            계약서가 어느 쪽으로 적혀 있든 그 숫자를 그대로 넣을 수 있어야 합니다. 둘 다
            고칠 수 있게 두되, 저장할 때 서버가 **공급가로 고른 쪽에서 다시 계산**하므로
            두 값이 어긋난 채 저장되지는 않습니다. */}
        {vatApplicable ? (
          <>
            <Field label="총 계약금액 (VAT 포함)" required>
              <input className="inp" type="number" value={draft.amount_incl_vat}
                     onChange={(e) => setAmount("incl", e.target.value)}
                     placeholder="예: 11000000" />
            </Field>
            <Field label="공급가 (VAT 미포함)" required>
              <input className="inp" type="number" value={draft.amount_excl_vat}
                     onChange={(e) => setAmount("excl", e.target.value)}
                     placeholder="예: 10000000" />
            </Field>
            {/* 분당 단가가 어느 금액에서 나오는지. 계약서가 총액으로 적힌 건과 공급가로
                적힌 건이 둘 다 있어서, 고르지 않으면 계약마다 단가가 10% 씩 달라집니다.

                라벨에서 「공급가」를 뺐습니다 (2026-08-31 운영자 지시): 바로 위 칸이
                **공급가 (VAT 미포함)** 이라 두 칸이 같은 말로 시작했고, 이 칸은 공급가를
                입력받는 칸이 아니라 **어느 금액을 기준으로 삼을지 고르는** 칸입니다. */}
            <Field label="분당단가 기준">
              <select className="inp" value={draft.vat_included}
                      onChange={(e) => set("vat_included", e.target.value)}>
                <option value="">VAT 미포함 금액으로</option>
                <option value="1">VAT 포함 금액으로</option>
              </select>
            </Field>
          </>
        ) : (
          <div style={{ gridColumn: "span 2" }}>
            <label className="form-label">계약금액 <span className="req">*</span></label>
            <input className="inp" type="number" value={draft.amount_incl_vat}
                   onChange={(e) => set("amount_incl_vat", e.target.value)} placeholder="예: 20000" />
            <div style={{ fontSize: 11.5, color: "var(--faint)", marginTop: 4 }}>
              VAT 미해당 — 금액은 하나이고, 그 금액이 분당단가 기준입니다.
            </div>
          </div>
        )}
        {/* 계산값입니다. 계약서에 적히는 것은 금액과 크레딧이고 단가는 그 둘에서
            나옵니다 — 소수점은 남깁니다. 반올림한 단가는 되짚어 곱했을 때 금액이
            안 맞습니다. */}
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

      <div className="form-sec">매출 인식</div>
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
        플랜 기간은 계약 기간과 다릅니다 — MRR 은 이 기간으로 나누고 이 기간에 인식하며,
        「사용중」도 이 기간이 정합니다. <b>비우면 계약 기간과 같습니다.</b>
      </div>
      <div className="form-grid3">
        {/* **플랜 기간은 계약 기간과 다른 것입니다** (2026-08-31 운영자 지시). 계약은
            먼저 맺고 실제 사용은 늦게 시작하는 일이 흔한데, 한동안 이 폼이 묻지 않고
            계약 날짜를 그대로 복사했습니다 — 그래서 MRR 도 「사용중」도 계약 기간으로
            계산됐습니다.

            MRR 은 이 기간으로 나누고 이 기간에 인식합니다(`won.plan_period`), 그리고
            「사용중」도 이 기간이 정합니다. 비워 두면 계약 기간과 같습니다 — 대부분의
            계약이 그렇고, 그때는 아무것도 안 적으면 됩니다. */}
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
      {/* 「저장 후 플랜 상태」 고르개가 여기 있었습니다. 플랜 상태는 이제 계약 기간이
          정합니다 — 이 폼에 적는 시작일·종료일이 곧 그 값입니다. 고르개를 남겨 두면
          사람이 고른 값과 날짜가 말하는 값이 갈라지고, 그때 어느 쪽이 맞는지 아무도
          모릅니다. 아래 줄이 지금 무엇이 될지 미리 말해 줍니다. */}
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
