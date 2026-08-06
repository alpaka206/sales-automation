import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { getJSON, postForm } from "../../lib/api";
import { SubmitButton, useAction } from "../../ui/ActionButton";
import { Field } from "./WonNew";
import { type Contract, type ListData, type Row, n, num } from "./shared";

/** 계약 정보 입력 — 추가와 수정이 같은 폼입니다.
 *
 * 목업의 계약 모달 그대로이되 두 곳이 다릅니다:
 *
 * - **계약 크레딧을 입력받지 않습니다.** 공급가 ÷ 분당 단가 × 60 으로 계산해 옆에 보여 줍니다.
 *   운영자의 시트에서 그 값이 손으로 들어가다 보니 계약마다 계산 기준이 달랐습니다.
 * - **통화가 다르면 환율을 받습니다.** 원화 계약에 USD 단가를 매기는 경우가 흔한데, 그때
 *   쓴 환율이 없으면 크레딧을 계산할 수 없고 나중에 오늘 환율로 다시 계산하면 값이 바뀝니다.
 *
 * 재계약이면 직전 계약에서 플랜·단가·결제 방식을 복사해 채웁니다 — 금액·크레딧·기간만
 * 새로 씁니다.
 */
const empty = {
  deal_type: "MRR", starts_on: "", ends_on: "", ticket_id: "",
  currency: "KRW", amount_incl_vat: "", amount_excl_vat: "",
  unit_price: "", unit_currency: "USD", unit_fx_rate: "",
  payment_method: "계좌이체", payment_type: "일시불", installments: "1",
  first_payment_on: "", billing_email: "", note: "",
  plan: "Business Tier 1", plan_name: "", perso_email: "",
  invite_limit: "", queue_limit: "", concurrent_jobs: "", space_count: "", space_seq: "",
  revenue_from: "", renewal_plan: "", memo: "",
};
type Draft = typeof empty;

const addMonths = (iso: string, months: number): string => {
  if (!iso) return "";
  const [y, m, d] = iso.split("-").map(Number);
  const at = new Date(y, m - 1 + months, d);
  return `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, "0")}-${String(at.getDate()).padStart(2, "0")}`;
};

export function WonContractForm() {
  const { clientId, contractId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [params] = useSearchParams();
  const editing = Boolean(contractId);

  const { data } = useQuery({
    queryKey: ["won-customer", clientId],
    queryFn: () => getJSON<Row>(`/api/ui/won-customers/${clientId}`),
  });
  const { data: list } = useQuery({
    queryKey: ["won-customers"],
    queryFn: () => getJSON<ListData>("/api/ui/won-customers"),
  });

  const [draft, setDraft] = useState<Draft | null>(null);
  const [docTypes, setDocTypes] = useState<string[]>([]);
  const [copyPrev, setCopyPrev] = useState(true);
  const [creditRounds, setCreditRounds] = useState("1");
  const [note, setNote] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  // 첫 렌더에서 한 번만 채웁니다. 이후 다시 채우면 타이핑 중인 값이 되돌아갑니다.
  if (data && list && !loaded) {
    setLoaded(true);
    const contracts = data.contracts ?? [];
    const target = editing ? contracts.find((c) => String(c.id) === contractId) : undefined;
    const prev = contracts.length ? contracts[contracts.length - 1] : undefined;
    const pending = params.get("pending");
    const pendingTicket = list.pending.find((p) => String(p.id) === pending)?.ticket_id ?? "";
    if (target) {
      setDraft(fromContract(target));
      setDocTypes(target.doc_types || []);
    } else {
      const start = prev?.ends_on || new Date().toISOString().slice(0, 10);
      setDraft({
        ...empty,
        ...(prev && copyPrev ? carryOver(prev) : {}),
        starts_on: start,
        ends_on: addMonths(start, 12),
        first_payment_on: start,
        ticket_id: pendingTicket,
        plan_name: prev?.plan_name || data.company,
      });
      setDocTypes(prev && copyPrev ? prev.doc_types || [] : []);
    }
  }

  const set = (key: keyof Draft, value: string) =>
    setDraft((current) => (current ? { ...current, [key]: value } : current));

  // 화면에서 미리 보여 주는 계산값. 저장할 때 서버가 같은 식으로 다시 계산합니다.
  const credits = (() => {
    if (!draft) return null;
    const supply = n(draft.amount_excl_vat);
    let unit = n(draft.unit_price);
    if (!supply || !unit) return null;
    if (draft.unit_currency !== draft.currency) {
      const rate = n(draft.unit_fx_rate);
      if (!rate) return null;
      unit = draft.unit_currency === "USD" ? unit * rate : unit / rate;
    }
    return Math.floor((supply / unit) * 60);
  })();

  const [save, saving] = useAction(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!draft) return;
    setNote(null);
    if (!draft.starts_on || !draft.ends_on || draft.ends_on <= draft.starts_on) {
      setNote("계약 시작일과 종료일을 확인해 주세요."); return;
    }
    if (!n(draft.amount_incl_vat) || !n(draft.amount_excl_vat)) {
      setNote("총 계약금액과 공급가를 입력해 주세요."); return;
    }
    if (credits === null) {
      setNote("계약 크레딧을 계산할 수 없습니다 — 분당 단가와, 통화가 다르면 적용 환율을 넣어 주세요.");
      return;
    }
    const body: Record<string, string> = {
      ...draft,
      doc_types: docTypes.join("|"),
      credit_rounds: creditRounds,
    };
    try {
      if (editing) {
        await postForm(`/won-customers/contracts/${contractId}`, body);
      } else {
        await postForm(`/won-customers/${clientId}/contracts`, body);
      }
      await queryClient.invalidateQueries();
      navigate(`/won-customers/${clientId}`);
    } catch (error) {
      setNote(error instanceof Error ? error.message : String(error));
    }
  });

  if (!data || !list || !draft) return <div className="won"><div className="page">불러오는 중…</div></div>;

  const contracts = data.contracts ?? [];
  const prev = contracts.length ? contracts[contracts.length - 1] : undefined;
  const seq = editing ? contracts.find((c) => String(c.id) === contractId)?.seq : contracts.length + 1;
  const options = list.options;

  return (
    <div className="won">
      <div className="page">
        <div className="crumb">고객 관리 / 수주 고객 / {data.company}</div>
        <div className="page-head">
          <div>
            <h1 className="page-title">{editing ? "계약 수정" : prev ? "계약 추가" : "계약 정보 입력"}</h1>
            <p className="page-sub">
              {prev && !editing
                ? "Client ID는 그대로 유지되고, 계약만 별도 히스토리로 쌓입니다."
                : "이 고객의 계약 정보입니다."}
            </p>
          </div>
          <button className="btn" type="button"
                  onClick={() => navigate(`/won-customers/${clientId}`)}>← 고객 상세</button>
        </div>

        <form className="sec" onSubmit={save}>
          <div className="idbox">
            <div>
              <div className="field-label">{editing ? "수정하는 계약" : "추가되는 계약"}</div>
              <div className="big">{seq}차 계약</div>
            </div>
            <div style={{ fontSize: 12.5, color: "var(--muted)", borderLeft: "1px solid #CFE2DF", paddingLeft: 12 }}>
              {data.company} · Client ID <b>{data.client_id}</b> (유지)<br />
              기존 계약 {contracts.length}건
            </div>
          </div>

          {prev && !editing && (
            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, cursor: "pointer" }}>
              <input type="checkbox" checked={copyPrev}
                     onChange={(e) => {
                       setCopyPrev(e.target.checked);
                       setDraft((c) => c ? { ...c, ...(e.target.checked ? carryOver(prev) : emptyCarry()) } : c);
                       setDocTypes(e.target.checked ? prev.doc_types || [] : []);
                     }} />
              이전 계약({prev.label})의 플랜 · 결제 · 계정 설정 불러오기
              <span style={{ color: "var(--faint)" }}>금액 · 크레딧 · 기간은 새로 입력합니다.</span>
            </label>
          )}

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
            <Field label="Ticket ID">
              <input className="inp" value={draft.ticket_id} onChange={(e) => set("ticket_id", e.target.value)}
                     placeholder="인바운드 건만 연동" />
            </Field>
            <div style={{ gridColumn: "span 2" }}>
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
          <div className="form-grid3">
            <Field label="통화" required>
              <Sel value={draft.currency} onChange={(v) => set("currency", v)} options={options.currencies} />
            </Field>
            <Field label="총 계약금액 (VAT 포함)" required>
              <input className="inp" type="number" value={draft.amount_incl_vat}
                     onChange={(e) => set("amount_incl_vat", e.target.value)} />
            </Field>
            <Field label="공급가 (VAT 제외)" required>
              <input className="inp" type="number" value={draft.amount_excl_vat}
                     onChange={(e) => set("amount_excl_vat", e.target.value)} />
            </Field>
            <Field label="분당 단가" required>
              <input className="inp" type="number" step="0.0001" value={draft.unit_price}
                     onChange={(e) => set("unit_price", e.target.value)} />
            </Field>
            <Field label="분당 단가 통화">
              <Sel value={draft.unit_currency} onChange={(v) => set("unit_currency", v)} options={options.currencies} />
            </Field>
            {draft.unit_currency !== draft.currency && (
              <Field label="적용 환율" required>
                <input className="inp" type="number" step="0.0001" value={draft.unit_fx_rate}
                       onChange={(e) => set("unit_fx_rate", e.target.value)}
                       placeholder="계약 시점 환율" />
                <div style={{ fontSize: 11.5, color: "var(--faint)", marginTop: 4 }}>
                  계약에 저장됩니다 — 나중에 오늘 환율로 다시 계산하지 않습니다.
                </div>
              </Field>
            )}
            <div style={{ gridColumn: "span 3" }}>
              <div className="note-box">
                <b>계약 크레딧 {credits === null ? "—" : `${num(credits)} 크레딧`}</b>
                {credits !== null && ` (${num(Math.round(credits / 60))}분)`}
                {" · "}공급가 ÷ 분당 단가 × 60 으로 계산합니다. 1분 = 60크레딧.
              </div>
            </div>
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
            <Field label="크레딧 지급 회차 수">
              <input className="inp" type="number" min={1} value={creditRounds}
                     onChange={(e) => setCreditRounds(e.target.value)} disabled={editing} />
            </Field>
            <Field label="Billing Email">
              <input className="inp" value={draft.billing_email} onChange={(e) => set("billing_email", e.target.value)} />
            </Field>
          </div>

          <div className="form-sec">Perso 계정 · 플랜</div>
          <div className="form-grid3">
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

          <div className="form-sec">기타</div>
          <div className="form-grid3">
            <Field label="매출 인식 시작 월">
              <input className="inp" placeholder="YYYY-MM (비우면 계약 시작월)"
                     value={draft.revenue_from} onChange={(e) => set("revenue_from", e.target.value)} />
            </Field>
            <Field label="갱신 계획">
              <Sel value={draft.renewal_plan} onChange={(v) => set("renewal_plan", v)}
                   options={["", ...options.renewal_plans]} />
            </Field>
            <div style={{ gridColumn: "span 3" }}>
              <label className="form-label">계약 비고</label>
              <input className="inp" value={draft.note} onChange={(e) => set("note", e.target.value)} />
            </div>
          </div>

          <div className="modal-foot" style={{ marginTop: 18 }}>
            <button className="btn" type="button"
                    onClick={() => navigate(`/won-customers/${clientId}`)}>취소</button>
            <SubmitButton busy={saving} pending="저장 중">{editing ? "저장" : "계약 등록"}</SubmitButton>
          </div>
          {note && <div className="note-box" role="status">{note}</div>}
        </form>
      </div>
    </div>
  );
}

function Sel({ value, onChange, options }: {
  value: string; onChange: (value: string) => void; options: string[];
}) {
  return (
    <select className="inp" value={value} onChange={(event) => onChange(event.target.value)}>
      {options.map((option) => <option key={option} value={option}>{option || "—"}</option>)}
    </select>
  );
}

const str = (value: unknown) => (value === null || value === undefined ? "" : String(value));

/** 재계약이 물려받는 것 — 플랜·단가·결제 방식·계정 한도. 금액·기간은 새로 씁니다. */
function carryOver(prev: Contract) {
  return {
    deal_type: prev.deal_type, currency: prev.currency,
    unit_price: str(prev.unit_price), unit_currency: str(prev.unit_currency),
    unit_fx_rate: str(prev.unit_fx_rate),
    payment_method: str(prev.payment_method), payment_type: str(prev.payment_type),
    installments: str(prev.installments ?? 1), billing_email: str(prev.billing_email),
    plan: str(prev.plan), plan_name: str(prev.plan_name), perso_email: str(prev.perso_email),
    invite_limit: str(prev.invite_limit), queue_limit: str(prev.queue_limit),
    concurrent_jobs: str(prev.concurrent_jobs), space_count: str(prev.space_count),
    space_seq: str(prev.space_seq),
  };
}
const emptyCarry = () => carryOverKeys.reduce((acc, key) => ({ ...acc, [key]: "" }), {});
const carryOverKeys = Object.keys(carryOver({} as Contract)) as (keyof Draft)[];

function fromContract(contract: Contract): Draft {
  return {
    deal_type: contract.deal_type, starts_on: str(contract.starts_on), ends_on: str(contract.ends_on),
    ticket_id: str(contract.ticket_id), currency: contract.currency,
    amount_incl_vat: str(contract.amount_incl_vat), amount_excl_vat: str(contract.amount_excl_vat),
    unit_price: str(contract.unit_price), unit_currency: str(contract.unit_currency) || "USD",
    unit_fx_rate: str(contract.unit_fx_rate),
    payment_method: str(contract.payment_method), payment_type: str(contract.payment_type),
    installments: str(contract.installments ?? 1), first_payment_on: str(contract.first_payment_on),
    billing_email: str(contract.billing_email), note: str(contract.note),
    plan: str(contract.plan), plan_name: str(contract.plan_name), perso_email: str(contract.perso_email),
    invite_limit: str(contract.invite_limit), queue_limit: str(contract.queue_limit),
    concurrent_jobs: str(contract.concurrent_jobs), space_count: str(contract.space_count),
    space_seq: str(contract.space_seq),
    revenue_from: contract.revenue_from_set ? str(contract.revenue_from) : "",
    renewal_plan: str(contract.renewal_plan), memo: str(contract.memo),
  };
}
