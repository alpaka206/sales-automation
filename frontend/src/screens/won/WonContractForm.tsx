import { useCallback, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { getJSON, postForm } from "../../lib/api";
import { SubmitButton, useAction } from "../../ui/ActionButton";
import { Modal } from "../../ui/Modal";
import { ContractFields, PlanFields, useContractDraft } from "./ContractFields";
import { carryOver, emptyCarry, emptyDraft, validate } from "./contractDraft";
import { type ListData, type Row, addMonths } from "./shared";

/** 계약 정보 입력 — 새 계약은 이 모달, 있는 계약은 상세의 제자리 편집(`ContractCards`).
 *
 * **칸과 규칙은 `ContractFields` · `contractDraft` 한 벌**이고 여기는 그 옷(콘솔의 `Modal`,
 * `won.css` 끝의 `.modal-*`)과 「누구의 몇 차 계약인가」만 듭니다. 머리·본문·바닥의 모양은
 * 목업(`수주관리목업_0806.html` 의 `renderContractModal`)을 따릅니다.
 *
 * 재계약이면 직전 계약에서 플랜·단가·결제 방식을 복사해 채웁니다 — 금액·크레딧·기간만
 * 새로 씁니다. 수정 주소(`/contracts/{id}`)는 남아 있습니다 — 옛 링크가 열리게.
 */
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

  const f = useContractDraft();
  const { draft, setDraft, setDocTypes } = f;
  // **초안과 같이 한 번만 굳힙니다.** 매 렌더 `list.pending` 에서 다시 찾으면, 폼을 채우는
  // 동안 그 대기 행이 사라졌을 때(누가 같은 티켓을 다른 계약에 적었다 — `_claim_ticket`)
  // 다 채운 폼이 「고객 정보가 없습니다」 한 줄로 바뀝니다. 아무 쓰기나 SSE 로 목록을
  // 다시 받아 오므로 남의 저장 하나에 이 화면이 통째로 날아갑니다.
  const [customer, setCustomer] = useState<Record<string, string> | null>(null);
  const [copyPrev, setCopyPrev] = useState(true);
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
      f.loadContract(target);
    } else {
      const start = prev?.ends_on || new Date().toISOString().slice(0, 10);
      setDraft({
        ...emptyDraft,
        ...(prev && copyPrev ? carryOver(prev) : {}),
        starts_on: start,
        ends_on: addMonths(start, 12),
        first_payment_on: start,
        ticket_id: pendingTicket,
        plan_name: prev?.plan_name || shownCompany,
      });
      setDocTypes(prev && copyPrev ? prev.doc_types || [] : []);
      f.setFirstCreditOn(start);
    }
  }

  const [save, saving] = useAction(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!draft) return;
    setNote(null);
    const problem = validate(draft);
    if (problem) { setNote(problem); return; }
    const body = f.body()!;
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

          <ContractFields f={f} options={options} />
          <PlanFields f={f} options={options} />
        </form>
      </div>
    </Modal>
  );
}
