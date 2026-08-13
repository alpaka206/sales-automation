import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { getJSON, postForm } from "../lib/api";
import { Icon } from "../ui/Icon";
import { SubmitButton, useAction } from "../ui/ActionButton";
import { kst } from "../lib/format";
import { InteractionForm, InteractionItem, type Interaction } from "../ui/InteractionForm";
import { LoadingBlock } from "../ui/Loading";

type Contract = {
  id: number; plan: string | null; status: string; amount: number | null; currency: string;
  conversation_id: number | null; sheet_client_id: number | null;
  contract_date: string | null; payment_due_at: string | null; paid_at: string | null;
  expires_at: string | null; payment_method: string | null; language_pairs: string[];
  unit_price: string | null; quote_url: string | null; invoice_url: string | null;
  payment_url: string | null; notes: string | null;
};
type Data = {
  contact: {
    id: number; full_name: string; email: string | null; company: string | null;
    domain: string | null; phone: string | null; lifecycle_stage: string | null;
    hubspot_contact_id: string | null;
  };
  profile: Record<string, string | null> | null;
  stage_options: { key: string; label: string }[];
  conversations: { id: number; created_at: string; inquiry_subject: string | null; stage: string; sheet_client_id: number | null }[];
  contracts: Contract[];
  timeline: Interaction[];
  same_company: { id: number; full_name: string; email: string | null }[];
};

const CONTRACT_STATUSES: [string, string][] = [
  ["draft", "초안"], ["sent", "계약서 발송"], ["contracted", "계약 확정"],
  ["active", "서비스 이용"], ["expired", "종료"], ["cancelled", "취소"],
];
const PAYMENT_METHODS: [string, string][] = [
  ["portone", "포트원"], ["stripe", "Stripe"], ["bank_transfer", "세금계산서·계좌이체"],
];

/** datetime-local / date inputs need the stored value trimmed to their own shape. */
const forInput = (value: string | null | undefined, length: number) =>
  value ? String(value).replace(" ", "T").slice(0, length) : "";

export function CustomerDetail() {
  const { id } = useParams();
  const queryClient = useQueryClient();
  const { data, isPending } = useQuery({
    queryKey: ["customer", id],
    queryFn: () => getJSON<Data>(`/api/ui/customers/${id}`),
  });
  const refresh = () => queryClient.invalidateQueries();

  // Every write goes to the route the Jinja form posts to: the stage sync, the sheet
  // mirror and the contract validation all stay server-side, in one copy.
  async function submit(event: React.FormEvent<HTMLFormElement>, path: string) {
    event.preventDefault();
    const form = event.currentTarget;
    await postForm(path, Object.fromEntries(new FormData(form) as never) as Record<string, string>);
    await refresh();
  }

  // 훅은 아래 early return 보다 **위**에서 부릅니다. 아래에 두면 로딩 렌더에서는 건너뛰고
  // 데이터가 온 렌더에서는 부르게 되어, 훅 수가 달라졌다고 React 가 터집니다(#310).
  // 그래서 경로도 data 가 없을 수 있다는 전제로 씁니다.
  const [syncHubspot, syncing] = useAction((event: React.FormEvent<HTMLFormElement>) =>
    submit(event, `/customers/${data?.contact.id}/sync`));
  const [saveProfile, savingProfile] = useAction((event: React.FormEvent<HTMLFormElement>) =>
    submit(event, `/customers/${data?.contact.id}/profile`));

  if (isPending || !data) return <LoadingBlock />;
  const { contact, profile } = data;

  const contractFields = (contract?: Contract) => (
    <>
      <label><span className="field-label">계약이 성사된 문의</span>
        <select className="select" name="conversation_id" defaultValue={contract?.conversation_id ?? ""}>
          <option value="">최근 문의 자동 선택</option>
          {data.conversations.map((conversation) => (
            <option key={conversation.id} value={conversation.id}>
              #{conversation.id} · {kst(conversation.created_at, "date")} ·{" "}
              {conversation.inquiry_subject || conversation.stage} · Client ID{" "}
              {conversation.sheet_client_id ?? "미동기화"}
            </option>
          ))}
        </select>
      </label>
      <div className="grid grid-2">
        <label><span className="field-label">플랜</span>
          <input className="input" name="plan" defaultValue={contract?.plan ?? ""} /></label>
        <label><span className="field-label">상태</span>
          <select className="select" name="status" defaultValue={contract?.status ?? "draft"}>
            {CONTRACT_STATUSES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select></label>
      </div>
      <div className="grid grid-2">
        <label><span className="field-label">금액</span>
          <input className="input" name="amount" inputMode="decimal" defaultValue={contract?.amount ?? ""} /></label>
        <label><span className="field-label">통화</span>
          <input className="input" name="currency" defaultValue={contract?.currency ?? "KRW"} /></label>
      </div>
      <label><span className="field-label">결제 채널</span>
        <select className="select" name="payment_method" defaultValue={contract?.payment_method ?? ""}>
          <option value="">미정</option>
          {PAYMENT_METHODS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <div className="grid grid-2">
        <label><span className="field-label">계약일</span>
          <input className="input" type="date" name="contract_date" defaultValue={forInput(contract?.contract_date, 10)} /></label>
        <label><span className="field-label">결제 예정일</span>
          <input className="input" type="date" name="payment_due_at" defaultValue={forInput(contract?.payment_due_at, 10)} /></label>
      </div>
      <div className="grid grid-2">
        <label><span className="field-label">입금일</span>
          <input className="input" type="date" name="paid_at" defaultValue={forInput(contract?.paid_at, 10)} /></label>
        <label><span className="field-label">만료일</span>
          <input className="input" type="date" name="expires_at" defaultValue={forInput(contract?.expires_at, 10)} /></label>
      </div>
      <label><span className="field-label">언어쌍</span>
        <input className="input" name="language_pairs" defaultValue={(contract?.language_pairs ?? []).join(", ")} /></label>
      <label><span className="field-label">계약 단가</span>
        <input className="input" name="unit_price" defaultValue={contract?.unit_price ?? ""} /></label>
      <label><span className="field-label">견적서 URL</span>
        <input className="input" name="quote_url" defaultValue={contract?.quote_url ?? ""} /></label>
      <label><span className="field-label">Invoice URL</span>
        <input className="input" name="invoice_url" defaultValue={contract?.invoice_url ?? ""} /></label>
      <label><span className="field-label">결제 링크</span>
        <input className="input" name="payment_url" defaultValue={contract?.payment_url ?? ""} /></label>
      <label><span className="field-label">계약 메모</span>
        <textarea className="textarea" name="notes" defaultValue={contract?.notes ?? ""} /></label>
    </>
  );

  return (
    <>
      <div style={{ marginBottom: 14 }}>
        <Link to="/customers" className="chip"><Icon name="chevron" size={14} /> 고객 목록</Link>
      </div>

      <div className="page-header">
        <div>
          <div className="row wrap">
            <span className="tag">{contact.lifecycle_stage || "HubSpot 단계 없음"}</span>
            {contact.domain && <span className="tag"><Icon name="building" size={13} /> {contact.domain}</span>}
          </div>
          <h1 className="page-title" style={{ marginTop: 10 }}>{contact.company || contact.full_name}</h1>
          <p className="page-sub">{contact.full_name} · {contact.email || "-"} · {contact.phone || "전화번호 없음"}</p>
        </div>
        {contact.hubspot_contact_id && (
          <form onSubmit={syncHubspot}>
            <SubmitButton busy={syncing} pending="동기화 중" className="btn btn--subtle">
              <Icon name="refresh" size={15} /> HubSpot 동기화
            </SubmitButton>
          </form>
        )}
      </div>

      <div className="customer-layout">
        <div className="stack">
          <section className="card">
            <div className="section-header"><div className="section-header__title">고객 상태 · 다음 액션</div></div>
            {/* 「고객 구분」 칸이 있었습니다(Negotiation / 서비스 이용중 / 기존 고객 Pool /
                Lost). 손으로 고르는 값인데 **바로 옆 파이프라인 단계에서 그대로 나오는
                값**이라, 둘이 어긋난 고객이 생겼습니다 — Won 인데 Negotiation 인 행.
                이제 저장할 때 서버가 단계에서 정합니다(customer_state_for), 보드에서
                카드를 옮겼을 때와 같은 규칙으로. */}
            <form className="profile-grid" onSubmit={saveProfile}>
              <label><span className="field-label">파이프라인</span>
                <select className="select" name="pipeline_stage" defaultValue={profile?.pipeline_stage ?? "new"}>
                  {data.stage_options.map((option) => (
                    <option key={option.key} value={option.key}>{option.label}</option>
                  ))}
                </select></label>
              <label><span className="field-label">리드 온도</span>
                <select className="select" name="lead_temperature" defaultValue={profile?.lead_temperature ?? ""}>
                  <option value="">미정</option>
                  {["hot", "warm", "cold"].map((value) => <option key={value} value={value}>{value}</option>)}
                </select></label>
              <label><span className="field-label">MQL / PQL</span>
                <select className="select" name="qualification" defaultValue={profile?.qualification ?? ""}>
                  <option value="">미정</option>
                  {["MQL", "PQL", "SQL"].map((value) => <option key={value} value={value}>{value}</option>)}
                </select></label>
              <label><span className="field-label">산업군</span>
                <input className="input" name="industry" defaultValue={profile?.industry ?? ""} /></label>
              <label><span className="field-label">user-seq</span>
                <input className="input" name="user_seq" defaultValue={profile?.user_seq ?? ""} /></label>
              <label><span className="field-label">현재 플랜</span>
                <input className="input" name="current_plan" defaultValue={profile?.current_plan ?? ""} /></label>
              <label><span className="field-label">유입 소스</span>
                <input className="input" name="source" defaultValue={profile?.source ?? ""} /></label>
              <label className="profile-grid__wide"><span className="field-label">다음 액션</span>
                <input className="input" name="next_action" defaultValue={profile?.next_action ?? ""}
                       placeholder="예: 견적서 확인 후 금요일 재연락" /></label>
              <label><span className="field-label">다음 액션 일시</span>
                <input className="input" type="datetime-local" name="next_action_at"
                       defaultValue={forInput(profile?.next_action_at, 16)} /></label>
              <label className="profile-grid__wide"><span className="field-label">Closed Lost 사유</span>
                <textarea className="textarea" name="lost_reason" rows={2} defaultValue={profile?.lost_reason ?? ""} /></label>
              <label className="profile-grid__wide"><span className="field-label">운영 메모</span>
                <textarea className="textarea" name="notes" rows={3} defaultValue={profile?.notes ?? ""} /></label>
              <div className="profile-grid__wide row-between">
                <span className="t-xs t-subtle">
                  {profile?.last_synced_at ? `마지막 HubSpot 동기화 ${kst(profile.last_synced_at)}` : "아직 수동 동기화하지 않았습니다."}
                </span>
                <SubmitButton busy={savingProfile}>저장</SubmitButton>
              </div>
            </form>
          </section>

          <section className="card" id="history">
            <div className="section-header"><div className="section-header__title">통합 히스토리</div></div>
            {/* No conversation_id: a record added here belongs to the customer, not to
                one inquiry. The ticket screen and the board's + button pass theirs. */}
            <InteractionForm contactId={contact.id} onSaved={refresh} />
            <div className="history-list" style={{ marginTop: 16 }}>
              {data.timeline.length === 0 ? (
                <div className="empty"><div className="empty__text">아직 히스토리가 없습니다.</div></div>
              ) : (
                data.timeline.map((item, index) => <InteractionItem key={index} item={item} />)
              )}
            </div>
          </section>
        </div>

        <aside className="stack">
          <section className="card" id="contracts">
            <div className="section-header"><div className="section-header__title">계약 · 결제</div></div>
            {data.contracts.length === 0 && <p className="t-sm t-subtle">등록된 계약이 없습니다.</p>}
            {data.contracts.map((contract) => (
              <div key={contract.id} className="contract-row">
                <div className="row-between">
                  <strong>{contract.plan || "플랜 미정"}</strong>
                  <span className="tag">{contract.status}</span>
                </div>
                <div className="tnum" style={{ fontSize: 20, fontWeight: 800, margin: "8px 0" }}>
                  {contract.amount != null ? `${Number(contract.amount).toLocaleString()} ${contract.currency}` : "-"}
                </div>
                <dl className="info-list">
                  <div className="info-row"><dt>연결 문의</dt><dd>
                    {contract.conversation_id
                      ? `#${contract.conversation_id} · Client ID ${contract.sheet_client_id ?? "미동기화"}`
                      : "미연결"}
                  </dd></div>
                  <div className="info-row"><dt>계약일</dt><dd>{kst(contract.contract_date, "date") || "-"}</dd></div>
                  <div className="info-row"><dt>결제 예정</dt><dd>{kst(contract.payment_due_at, "date") || "-"}</dd></div>
                  <div className="info-row"><dt>만료일</dt><dd>{kst(contract.expires_at, "date") || "-"}</dd></div>
                </dl>
                {/* The block sales pastes into the Flex approval form. It is a copy
                    button, not a report — the fields are exactly what that form asks
                    for, in its order. */}
                <details className="copy-block"><summary>Flex 품의용 값</summary>
                  <pre>{[
                    `고객: ${contact.company || contact.full_name}`,
                    `플랜: ${contract.plan || "-"}`,
                    `금액: ${contract.amount ?? "-"} ${contract.currency}`,
                    `결제 방식: ${contract.payment_method || "-"}`,
                    `계약일: ${kst(contract.contract_date, "date") || "-"}`,
                    `결제 예정일: ${kst(contract.payment_due_at, "date") || "-"}`,
                    `만료일: ${kst(contract.expires_at, "date") || "-"}`,
                    `언어쌍: ${(contract.language_pairs ?? []).join(", ")}`,
                    `단가: ${contract.unit_price || "-"}`,
                  ].join("\n")}</pre>
                  <button type="button" className="btn btn--subtle btn--sm"
                          onClick={(event) => {
                            const block = event.currentTarget.previousElementSibling;
                            void navigator.clipboard.writeText(block?.textContent ?? "");
                          }}>
                    복사
                  </button>
                </details>
                <details className="copy-block"><summary>계약 수정</summary>
                  <ContractForm
                    action={`/customers/${contact.id}/contracts/${contract.id}`}
                    submit={submit} label="수정 저장">{contractFields(contract)}</ContractForm>
                </details>
              </div>
            ))}
            {/* 「계약 추가」가 여기 있었습니다. 계약을 만드는 곳은 **수주 고객 화면 하나**
                입니다: 그쪽은 차수·크레딧 회차·분납·환율까지 함께 세우는데, 여기서 만든
                계약은 그것들이 전부 빈 채로 생겨서 수주 장부에서는 보이지도 않았습니다.
                이미 있는 계약을 고치는 것은 남깁니다 — 이 화면에서 보고 있던 값입니다. */}
          </section>

          {data.same_company.length > 0 && (
            <section className="card">
              <div className="section-label">같은 도메인 담당자</div>
              {data.same_company.map((person) => (
                <Link key={person.id} className="domain-hist__link" to={`/customers/${person.id}`}>
                  <strong className="t-sm">{person.full_name}</strong>
                  <div className="t-xs t-subtle">{person.email}</div>
                </Link>
              ))}
            </section>
          )}
        </aside>
      </div>
    </>
  );
}

/** 계약 하나짜리 폼. 목록 안에서 여러 번 그려지고, 저장 중 표시는 **누른 그 폼**에만
 *  떠야 하므로 상태를 각자 듭니다. */
function ContractForm({ action, submit, label, children }: {
  action: string;
  submit: (event: React.FormEvent<HTMLFormElement>, path: string) => Promise<void>;
  label: string;
  children: React.ReactNode;
}) {
  const [run, busy] = useAction((event: React.FormEvent<HTMLFormElement>) => submit(event, action));
  return (
    <form className="stack" style={{ marginTop: 12, gap: 10 }} onSubmit={run}>
      {children}
      <SubmitButton busy={busy}>{label}</SubmitButton>
    </form>
  );
}
