import { useCallback, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { getJSON, postForm } from "../../lib/api";
import { SubmitButton, useAction } from "../../ui/ActionButton";
import { Modal } from "../../ui/Modal";
import { Field } from "./WonNew";
import { type Contract, type ListData, type Row, addMonths, n } from "./shared";

/** 계약 정보 입력 — 추가와 수정이 같은 폼입니다.
 *
 * **목업(`수주관리목업_0806.html` 의 `renderContractModal`)과 절 순서·칸·문구를 맞춥니다.**
 * 머리·본문·바닥의 모양도 목업의 `.modal-head/.modal-body/.modal-foot` 을 따릅니다 — 다만
 * 그 대화상자를 다시 만들지는 않고, 콘솔의 `Modal` 에 그 옷만 입힙니다(`won.css` 끝).
 *
 * 목업과 **다른 곳은 두 군데뿐**이고, 둘 다 운영자가 그렇게 하라고 한 것입니다:
 *
 * - **계약 크레딧을 입력받지 않습니다.** 목업은 손으로 적는 칸인데, 공급가 ÷ 분당 단가 × 60
 *   으로 계산해 같은 자리에 보여 줍니다. 시트에서 손으로 들어가다 보니 계약마다 계산 기준이
 *   달랐습니다. 그래서 **공급가 (VAT 제외)** 칸이 하나 늘었습니다 — 목업에는 없습니다.
 * - **통화가 다르면 환율을 받습니다.** 원화 계약에 USD 단가를 매기는 경우가 흔한데, 그때
 *   쓴 환율이 없으면 크레딧을 계산할 수 없고 나중에 오늘 환율로 다시 계산하면 값이 바뀝니다.
 *
 * 재계약이면 직전 계약에서 플랜·단가·결제 방식을 복사해 채웁니다 — 금액·크레딧·기간만
 * 새로 씁니다.
 */
const empty = {
  deal_type: "MRR", starts_on: "", ends_on: "", ticket_id: "",
  // 부가세가 붙는 계약인가(국내 법인이면 해당). **통화가 아니라 고객이 정합니다** — 이 값이
  // 금액 칸을 한 개 그릴지 두 개 그릴지 정합니다. 폼은 문자열만 나르므로 "1" / "" 입니다.
  vat_applicable: "1",
  // 분당 단가의 기준이 VAT 포함 금액인가 — 아래 「공급가」가 고르는 값입니다.
  currency: "KRW", vat_included: "", amount_incl_vat: "", amount_excl_vat: "", credits: "",
  // 비워 두면 저장할 때 계약일 고시가로 채웁니다(`_fill_contract_fx`).
  fx_rate: "", terminated_on: "", credits_used: "",
  payment_method: "계좌이체", payment_type: "일시불", installments: "1",
  first_payment_on: "", billing_email: "", note: "",
  plan: "Business Tier 1", plan_name: "", perso_email: "",
  invite_limit: "", queue_limit: "", concurrent_jobs: "", space_count: "", space_seq: "",
  revenue_from: "",
};
type Draft = typeof empty;

// 제출 버튼이 모달 푸터에 있어서 폼을 id 로 가리킵니다.
const FORM_ID = "won-contract-form";

export function WonContractForm() {
  const { clientId, contractId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [params] = useSearchParams();
  const editing = Boolean(contractId);
  // 아직 만들어지지 않은 고객의 **첫 계약**입니다. 예전에는 이 폼을 열기 전에 고객을 먼저
  // 만들었는데, 폼을 채우지 않고 나가면 계약이 0건인 고객이 남아 워크북에 「세팅중」으로
  // 실려 나갔습니다. 이제 고객은 이 폼을 저장할 때 계약과 함께 만들어집니다.
  const creating = !clientId;

  const { data } = useQuery({
    queryKey: ["won-customer", clientId],
    queryFn: () => getJSON<Row>(`/api/ui/won-customers/${clientId}`),
    enabled: !creating,
  });
  const { data: list } = useQuery({
    queryKey: ["won-customers"],
    queryFn: () => getJSON<ListData>("/api/ui/won-customers"),
  });

  /** 만들 고객. `creating` 일 때만 값이 있습니다.
   *
   * 두 갈래로 옵니다. 「수주 고객 추가」는 1단계에서 받은 칸을 라우터 state 로 넘기고,
   * 수주 전환 대기 카드는 `?pending=` 하나만 넘깁니다 — 회사와 번호는 목록 payload 에 이미
   * 있으니 다시 나를 이유가 없고, 주소만으로 열리니 새로고침해도 살아남습니다.
   */
  const pendingId = params.get("pending");
  const pendingItem = list?.pending.find((item) => String(item.id) === pendingId);
  const handed = (location.state as { customer?: Record<string, string> } | null)?.customer;
  const contracts = (creating ? [] : data?.contracts) ?? [];
  const ready = Boolean(list) && (creating || Boolean(data));

  const [draft, setDraft] = useState<Draft | null>(null);
  // **초안과 같이 한 번만 굳힙니다.** 매 렌더 `list.pending` 에서 다시 찾으면, 폼을 채우는
  // 동안 그 대기 행이 사라졌을 때(누가 같은 티켓을 다른 계약에 적었다 — `_claim_ticket`)
  // 다 채운 폼이 「고객 정보가 없습니다」 한 줄로 바뀝니다. 아무 쓰기나 SSE 로 목록을
  // 다시 받아 오므로 남의 저장 하나에 이 화면이 통째로 날아갑니다.
  const [customer, setCustomer] = useState<Record<string, string> | null>(null);
  const [docTypes, setDocTypes] = useState<string[]>([]);
  const [copyPrev, setCopyPrev] = useState(true);
  const [creditRounds, setCreditRounds] = useState("12");
  const [firstCreditOn, setFirstCreditOn] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  // useCallback 인 이유: Modal 의 keydown 효과가 `[onClose]` 에 걸려 있어서, 매 렌더
  // 새 함수를 주면 리스너를 떼었다 붙이며 **포커스를 여는 버튼으로 되돌립니다**. 폼에
  // 글자를 칠 때마다 포커스가 튑니다.
  //
  // 닫기는 **히스토리를 되돌립니다**(push 가 아니라). 이 모달은 주소를 가진 화면이라 열 때
  // 히스토리에 한 칸이 쌓이는데, 닫을 때 상세로 다시 push 하면 그 칸이 남습니다. 그러면
  // 상세에서 뒤로가기를 눌렀을 때 목록이 아니라 **모달이 다시 열립니다** — 누르는 사람은
  // 목록으로 갈 줄 알았고, 창이 안 닫히는 것처럼 보입니다.
  //
  // `location.key === "default"` 는 이 주소가 이 세션의 첫 칸이라는 뜻입니다(모달 URL 을
  // 직접 열었거나 새로고침). 그때는 되돌릴 칸이 없으므로 상세로 바꿔 칩니다 — replace 라
  // 여기서도 모달 칸이 남지 않습니다.
  const back = useCallback(
    () => {
      // 「수주 고객 추가」에서 왔으면 **적어 온 칸을 돌려주며** 되돌립니다. 1단계는 이제
      // 아무것도 저장하지 않으므로, 그냥 뒤로 보내면 여덟 칸을 처음부터 다시 칩니다.
      // `location.state` 를 통째로 넘기는 이유는 그 안에 산업 분야의 「직접 입력」 여부처럼
      // 1단계만 아는 값이 같이 들어 있어서입니다 — 여기서 그 모양을 알 필요가 없습니다.
      if (creating && handed) {
        navigate("/won-customers/new", { state: location.state, replace: true });
        return;
      }
      if (location.key !== "default") navigate(-1);
      else navigate(creating ? "/won-customers" : `/won-customers/${clientId}`, { replace: true });
    },
    [navigate, location.key, location.state, clientId, creating, handed],
  );


  // 첫 렌더에서 한 번만 채웁니다. 이후 다시 채우면 타이핑 중인 값이 되돌아갑니다.
  if (ready && !loaded) {
    setLoaded(true);
    const made = !creating
      ? null
      : handed ?? (pendingItem
          ? {
              // 대기 건은 인바운드 문의라 1000번대입니다. 번호가 이미 있으면 그 번호를
              // 그대로 씁니다 — 문의 시점에 발급된 그 고객의 번호입니다.
              customer_type: "GTM Inbound",
              company: pendingItem.company || "고객사 미확인",
              client_id: pendingItem.client_id ? String(pendingItem.client_id) : "",
            }
          : null);
    setCustomer(made);
    const shownCompany = creating ? (made?.company ?? "") : (data?.company ?? "");
    const target = editing ? contracts.find((c) => String(c.id) === contractId) : undefined;
    const prev = contracts.length ? contracts[contracts.length - 1] : undefined;
    const pendingTicket = pendingItem?.ticket_id ?? "";
    if (target) {
      setDraft(fromContract(target));
      setDocTypes(target.doc_types || []);
      setCreditRounds(String(target.credit_grants?.length || 12));
      setFirstCreditOn(target.credit_grants?.[0]?.grant_on || target.starts_on || "");
    } else {
      const start = prev?.ends_on || new Date().toISOString().slice(0, 10);
      setDraft({
        ...empty,
        ...(prev && copyPrev ? carryOver(prev) : {}),
        starts_on: start,
        ends_on: addMonths(start, 12),
        first_payment_on: start,
        ticket_id: pendingTicket,
        plan_name: prev?.plan_name || shownCompany,
      });
      setDocTypes(prev && copyPrev ? prev.doc_types || [] : []);
      setFirstCreditOn(start);
    }
  }

  const set = (key: keyof Draft, value: string) =>
    setDraft((current) => (current ? { ...current, [key]: value } : current));

  // 저장하면 플랜 상태가 무엇이 될지. 서버의 won.plan_status 와 같은 규칙을 이 계약 하나에
  // 적용한 것입니다 — 고르는 칸이 없어진 자리에, 날짜가 무슨 뜻인지 대신 적어 줍니다.
  const planPreview = (() => {
    if (!draft) return "세팅중";
    const today = new Date().toISOString().slice(0, 10);
    if (!draft.starts_on || !draft.ends_on) return "세팅중";
    if (draft.ends_on < today) return "사용 중단";
    if (draft.starts_on > today) return "세팅중";
    return "사용중";
  })();

  // 화면에서 미리 보여 주는 계산값. 저장할 때 서버가 같은 식으로 다시 계산합니다
  // (won.total_amount / won.unit_price) — 두 곳에 식이 있는 게 아니라, 화면은 사람이
  // 숫자를 넣는 동안 결과를 보여 줄 뿐입니다.
  const krw = (draft?.currency ?? "KRW") === "KRW";
  // 부가세가 붙는 계약인가. **통화가 아니라 고객이 정합니다**(이관 0075).
  const vatApplicable = draft?.vat_applicable === "1";
  /** 원화 계약이 **총액으로 적혔는가.** 그 외 통화는 부가세가 없어 늘 총액입니다. */
  const inclusive = vatApplicable && draft?.vat_included === "1";

  /** 한쪽을 적으면 다른 쪽이 10% 로 따라옵니다. 반올림은 소수 둘째 자리까지 — 원화는
   *  정수로 떨어지고, 안 떨어지는 통화는 서버가 기준에서 다시 계산하므로 여기 값은
   *  운영자가 눈으로 확인하는 용도입니다. */
  function setAmount(which: "incl" | "excl", value: string) {
    const round2 = (n: number) => String(Math.round(n * 100) / 100);
    const typed = Number(value);
    const partner = value.trim() === "" || !Number.isFinite(typed)
      ? ""
      : which === "incl" ? round2(typed / 1.1) : round2(typed * 1.1);
    setDraft((d) => (d ? { ...d, [which === "incl" ? "amount_incl_vat" : "amount_excl_vat"]: value,
                           [which === "incl" ? "amount_excl_vat" : "amount_incl_vat"]: partner } : d));
  }

  /** 분당 단가가 기준으로 삼는 금액 — 계약서에 적힌 그 금액입니다. VAT 제외로 적힌 원화
   *  계약만 공급가 칸을 쓰고, 나머지는 총액 칸입니다. 서버의 `won.billing_amount` 와 같은
   *  갈래이고, 저장할 때 서버가 다시 계산합니다. */
  const billing = draft ? n(vatApplicable && !inclusive ? draft.amount_excl_vat : draft.amount_incl_vat) : 0;

  /** 분당 단가 = 기준 금액 ÷ (계약 크레딧 ÷ 60). 소수점은 남깁니다 — 반올림한 단가는
   *  되짚어 곱했을 때 금액이 안 맞습니다. */
  const unitPrice = (() => {
    const credits = draft ? n(draft.credits) : 0;
    if (!billing || !credits) return null;
    // 소수 둘째 자리. 상세 화면의 「분당 단가」와 같은 자릿수여야 합니다 — 만드는 화면과
    // 보는 화면이 같은 계약을 다른 숫자로 보여 주면, 어느 쪽이 저장된 값인지 알 수 없습니다.
    return (billing / (credits / 60)).toFixed(2);
  })();

  const [save, saving] = useAction(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!draft) return;
    setNote(null);
    if (!draft.starts_on || !draft.ends_on || draft.ends_on <= draft.starts_on) {
      setNote("계약 시작일과 종료일을 확인해 주세요."); return;
    }
    // 통화가 정한 금액 칸과 계약 크레딧, 둘만 필수입니다. 분당 단가는 그 둘에서
    // 나오는 계산값이라 받지 않습니다.
    if (!billing) {
      setNote(
        krw && !inclusive
          ? "공급가 (VAT 제외) 를 입력해 주세요."
          : `총 계약금액을 입력해 주세요 (${draft.currency}).`,
      );
      return;
    }
    if (!n(draft.credits)) {
      setNote("계약 크레딧을 입력해 주세요 — 분당 단가가 여기서 나옵니다."); return;
    }
    const body: Record<string, string> = {
      ...draft,
      doc_types: docTypes.join("|"),
      credit_rounds: creditRounds,
      first_credit_on: firstCreditOn,
    };
    if (!editing && pendingId) body.pending_id = pendingId;
    try {
      if (editing) {
        await postForm(`/won-customers/contracts/${contractId}`, body);
      } else if (creating) {
        // 고객과 첫 계약이 **한 요청**입니다 — 둘로 나누면 그 사이에 폼을 닫았을 때
        // 계약 없는 고객이 남습니다.
        const created = await postForm("/won-customers", { ...customer, ...body })
          .then((response) => response.json() as Promise<{ client_id: number }>);
        await queryClient.invalidateQueries();
        navigate(`/won-customers/${created.client_id}`, { replace: true });
        return;
      } else {
        await postForm(`/won-customers/${clientId}/contracts`, body);
      }
      await queryClient.invalidateQueries();
      back();
    } catch (error) {
      setNote(error instanceof Error ? error.message : String(error));
    }
  });

  // 만들 고객이 없는데 `creating` 이면 주소만 열었거나 새로고침으로 초안이 날아간 것입니다.
  // 빈 폼을 그려 두면 저장이 400 으로 떨어지고 화면에는 이유가 안 보입니다.
  // `loaded` 로 재는 이유: 한 번 굳힌 뒤의 판정이라야 목록이 다시 와도 흔들리지 않습니다.
  if (creating && loaded && !customer) {
    return <Modal key="lost" title="계약 정보" onClose={back}>
             <div className="won">
               <p className="note-box">고객 정보가 없습니다 — 「수주 고객 추가」에서 다시 시작해 주세요.</p>
             </div>
           </Modal>;
  }
  // `ready` 를 그대로 쓰지 않는 이유는 타입 하나입니다 — boolean 은 아래 `list.options` 를
  // 좁혀 주지 않습니다. 조건은 같습니다.
  if (!list || !draft || (!creating && !data)) {
    return <Modal key="loading" title="계약 정보" onClose={back}>
             <div className="won"><p className="note-box">불러오는 중…</p></div>
           </Modal>;
  }

  const company = creating ? (customer?.company ?? "") : data!.company;
  const prev = contracts.length ? contracts[contracts.length - 1] : undefined;
  const seq = editing ? contracts.find((c) => String(c.id) === contractId)?.seq : contracts.length + 1;
  const options = list.options;

  return (
    // 목업처럼 상세 위에 뜨는 대화상자입니다. 콘솔에 이미 있는 Modal 을 씁니다 — 포커스
    // 트랩·Escape·배경 스크롤 잠금이 거기 한 벌 있고, 목업의 자체 모달을 옮기면 그게 두
    // 벌이 됩니다. 제출 버튼은 푸터(본문 밖)에 있으므로 `form` 속성으로 폼을 가리킵니다.
    <Modal
      key="form"
      title={editing ? "계약 수정" : prev ? "계약 추가" : "계약 정보 입력"}
      description={
        editing
          ? "이 계약의 정보를 고칩니다."
          : prev
            ? "기존 고객에 새 계약을 추가합니다. Client ID는 그대로 유지되고, 계약만 별도 히스토리로 쌓입니다."
            : creating
              ? "저장하면 고객과 첫 계약이 함께 등록됩니다."
              : "이 고객의 첫 계약 정보를 입력합니다."
      }
      wide
      onClose={back}
      actions={
        <>
          {/* 버튼 옆입니다. 본문만 스크롤하므로 폼 끝에 두면 화면 밖에 그려지고, 누른
              사람은 아무 일도 안 일어난 줄 압니다. */}
          {note && (
            <span className="t-xs" role="status"
                  style={{ marginRight: "auto", alignSelf: "center", color: "var(--danger)" }}>
              {note}
            </span>
          )}
          <SubmitButton busy={saving} pending="저장 중" form={FORM_ID}>
            {editing ? "저장" : "계약 저장"}
          </SubmitButton>
        </>
      }
    >
      <div className="won">
        <form id={FORM_ID} onSubmit={save}>
          <div className="idbox">
            <div>
              <div className="field-label">{editing ? "수정하는 계약" : "추가되는 계약"}</div>
              <div className="big">{seq}차 계약</div>
            </div>
            <div style={{ fontSize: 12.5, color: "var(--muted)", borderLeft: "1px solid #CFE2DF", paddingLeft: 12 }}>
              {company} · Client ID{" "}
              <b>{creating ? (customer?.client_id || "저장할 때 발급") : data?.client_id}</b>
              {creating ? "" : " (유지)"}<br />
              {creating
                ? "저장하면 고객과 첫 계약이 함께 등록됩니다"
                : `기존 계약 ${contracts.length}건 · 저장 시 최신 계약으로 노출`}
            </div>
          </div>

          {prev && !editing && (
            <>
              <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13,
                              marginBottom: 4, cursor: "pointer" }}>
                <input type="checkbox" checked={copyPrev}
                       onChange={(e) => {
                         setCopyPrev(e.target.checked);
                         setDraft((c) => c ? { ...c, ...(e.target.checked ? carryOver(prev) : emptyCarry()) } : c);
                         setDocTypes(e.target.checked ? prev.doc_types || [] : []);
                       }} />
                이전 계약({prev.label})의 플랜 · 결제 · 계정 설정 불러오기
              </label>
              <div style={{ fontSize: 12, color: "var(--faint)", marginBottom: 6, paddingLeft: 22 }}>
                금액 · 크레딧 · 기간은 새로 입력합니다.
              </div>
            </>
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
            {/* 원화 계약은 환산할 것이 없어 묻지 않습니다. 비워 두면 저장할 때 계약일
                고시가를 조회해 계약 행에 박아 둡니다 — 오늘 고시가로 환산하면 같은 계약의
                지난달 매출이 이번 달에 달라 보입니다. */}
            {draft.currency !== "KRW" && (
              <Field label="환율 (선택)">
                <input className="inp" type="number" value={draft.fx_rate}
                       onChange={(e) => set("fx_rate", e.target.value)}
                       placeholder="비우면 계약일 고시가" />
              </Field>
            )}
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
                    적힌 건이 둘 다 있어서, 고르지 않으면 계약마다 단가가 10% 씩 달라집니다. */}
                <Field label="공급가 (분당단가 기준)">
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
              <input className="inp" type="number" min={1} value={creditRounds}
                     onChange={(e) => setCreditRounds(e.target.value)} disabled={editing} />
            </Field>
            <Field label="첫 지급 예정일">
              <input className="inp" type="date" value={firstCreditOn}
                     onChange={(e) => setFirstCreditOn(e.target.value)} disabled={editing} />
            </Field>
            <div style={{ display: "flex", alignItems: "flex-end", fontSize: 12, color: "var(--faint)" }}>
              {editing
                ? "회차는 크레딧 지급 섹션에서 고칩니다."
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

          <div className="form-sec">Perso 계정 및 플랜</div>
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
          <div className="note-box">플랜 시작일 · 만료일은 계약기간과 동일하게 저장됩니다.</div>

          <div className="form-sec">기타</div>
          <div>
            <label className="form-label">계약 비고</label>
            <textarea className="inp" rows={2} value={draft.note}
                      onChange={(e) => set("note", e.target.value)}
                      placeholder="갱신 조건, 협의 내용 등" />
          </div>
          {/* 「저장 후 플랜 상태」 고르개가 여기 있었습니다. 플랜 상태는 이제 계약 기간이
              정합니다 — 이 폼에 적는 시작일·종료일이 곧 그 값입니다. 고르개를 남겨 두면
              사람이 고른 값과 날짜가 말하는 값이 갈라지고, 그때 어느 쪽이 맞는지 아무도
              모릅니다. 아래 줄이 지금 무엇이 될지 미리 말해 줍니다. */}
          <div className="note-box" style={{ marginTop: 14 }}>
            플랜 상태는 계약 기간에서 정해집니다 — 이 계약은 저장하면{" "}
            <b>{planPreview}</b> 입니다.
          </div>

        </form>
      </div>
    </Modal>
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

/** 재계약이 물려받는 것 — 플랜·단가·결제 방식·계정 한도. 금액·기간은 새로 씁니다.
 *
 * **환율은 물려받지 않습니다.** 그건 직전 계약을 맺던 날의 값이라, 새 계약에 그대로
 * 박히면 이번 계약의 크레딧이 남의 시점 환율로 계산됩니다. 쓴 사람이 직접 적습니다. */
function carryOver(prev: Contract) {
  return {
    deal_type: prev.deal_type, currency: prev.currency,
    // 통화를 물려받으면 「VAT 포함/제외」도 물려받아야 합니다 — 같은 고객의 다음 차수
    // 계약서는 같은 방식으로 적힙니다. 통화만 따라오고 기준은 초기화되면, 총액으로 적힌
    // 계약이 공급가 칸으로 들어가 단가가 10% 낮아집니다.
    vat_applicable: prev.vat_applicable ? "1" : "",
    vat_included: prev.vat_included ? "1" : "",
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
    vat_applicable: contract.vat_applicable ? "1" : "",
    vat_included: contract.vat_included ? "1" : "",
    fx_rate: str(contract.fx_rate), terminated_on: str(contract.terminated_on),
    credits_used: str(contract.credits_used),
    amount_incl_vat: str(contract.amount_incl_vat), amount_excl_vat: str(contract.amount_excl_vat),
    credits: str(contract.credits),
    payment_method: str(contract.payment_method), payment_type: str(contract.payment_type),
    installments: str(contract.installments ?? 1), first_payment_on: str(contract.first_payment_on),
    billing_email: str(contract.billing_email), note: str(contract.note),
    plan: str(contract.plan), plan_name: str(contract.plan_name), perso_email: str(contract.perso_email),
    invite_limit: str(contract.invite_limit), queue_limit: str(contract.queue_limit),
    concurrent_jobs: str(contract.concurrent_jobs), space_count: str(contract.space_count),
    space_seq: str(contract.space_seq),
    revenue_from: contract.revenue_from_set ? str(contract.revenue_from) : "",
  };
}
