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
  client_ids: number[];
  tickets: Ticket[];
  won: Won | null;
  interactions: Interaction[];
  contracts: Contract[];
  timeline: Interaction[];
  same_company: { id: number; full_name: string; email: string | null }[];
};
type Ticket = {
  conversation_id: number; ticket_id: string | null; client_id: number | null;
  subject: string | null; category: string | null; language: string | null;
  stage: string; created_at: string;
  last_incoming_at: string | null; last_outgoing_at: string | null; summary: string | null;
  messages: { id: number; direction: string; status: string; subject: string | null;
              body: string | null; happened_at: string | null }[];
  progress: { kind: string; detail: string; created_at: string }[];
};
type Won = {
  client_id: number; company: string; department: string | null; customer_type: string | null;
  plan_status: string; owner: string | null; first_won_on: string | null;
  contracts: { seq: number; state: string; deal_type: string | null;
               starts_on: string | null; ends_on: string | null; currency: string | null;
               total_amount: number | null; credits: number | null;
               plan_name: string | null; ticket_id: string | null }[];
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
          {/* 수주 DB·워크북·시트가 전부 이 번호로 엮입니다. 문의마다 붙는 값이라 한 사람에게
              여럿일 수 있고, 그때는 전부 보여 줍니다 — 어느 번호로 저쪽 화면을 찾아야 하는지가
              그 자체로 정보입니다(운영자 지시). */}
          {data.client_ids.length > 0 && (
            <div className="row wrap" style={{ gap: 6, marginTop: 8 }}>
              {data.client_ids.map((clientId) => (
                <span key={clientId} className="chip tnum">Client ID {clientId}</span>
              ))}
            </div>
          )}
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
          {/* **티켓 하나가 카드 하나.** 그 안에 그 티켓의 메일과 진행 기록이 들어갑니다.
              예전에는 모든 티켓의 메일이 한 줄로 섞여 있어서, 문의가 둘 이상인 고객에서는
              어느 메일이 어느 건인지 알 수 없었습니다. 최신 티켓만 펼쳐 둡니다 — 대개
              그것이 지금 보는 건이고, 다 펼치면 스크롤이 화면 몇 개가 됩니다. */}
          <section className="card" id="tickets">
            <div className="section-header">
              <div className="section-header__title">티켓 {data.tickets.length}건</div>
            </div>
            {data.tickets.length === 0 ? (
              <p className="t-sm t-subtle">이 고객으로 접수된 티켓이 없습니다.</p>
            ) : (
              <div className="stack" style={{ gap: 10 }}>
                {data.tickets.map((ticket, index) => (
                  <TicketBlock key={ticket.conversation_id} ticket={ticket}
                               open={index === 0} stages={data.stage_options} />
                ))}
              </div>
            )}
          </section>

          {data.won && (
            <section className="card" id="won">
              <div className="section-header">
                <div className="section-header__title">수주 고객</div>
                <Link className="chip" to={`/won-customers/${data.won.client_id}`}>
                  수주 화면에서 보기 <Icon name="chevron" size={13} />
                </Link>
              </div>
              <div className="field-grid">
                <KV k="Client ID" v={<span className="tnum">{data.won.client_id}</span>} />
                <KV k="고객사" v={data.won.company} />
                <KV k="플랜 상태" v={data.won.plan_status} />
                <KV k="담당부서" v={data.won.department || "-"} />
                <KV k="담당" v={data.won.owner || "-"} />
                <KV k="최초 수주일" v={data.won.first_won_on || "-"} />
              </div>
              <div className="history-list" style={{ marginTop: 12 }}>
                {data.won.contracts.map((contract) => (
                  <div key={contract.seq} className="row-between t-sm"
                       style={{ padding: "7px 0", borderTop: "1px solid var(--border)" }}>
                    <span>
                      <strong>{contract.seq}차</strong> · {contract.deal_type || "-"} ·{" "}
                      {contract.starts_on || "?"} ~ {contract.ends_on || "?"}
                      {contract.ticket_id && <span className="t-xs t-subtle"> · 티켓 {contract.ticket_id}</span>}
                    </span>
                    <span className="tnum">
                      {contract.total_amount != null
                        ? `${contract.currency || ""} ${Number(contract.total_amount).toLocaleString()}`
                        : "-"}
                      <span className="t-xs t-subtle"> · {contract.state}</span>
                    </span>
                  </div>
                ))}
              </div>
            </section>
          )}

          <section className="card" id="history">
            <div className="section-header"><div className="section-header__title">소통 히스토리</div></div>
            {/* No conversation_id: a record added here belongs to the customer, not to
                one inquiry. The ticket screen and the board's + button pass theirs. */}
            <InteractionForm contactId={contact.id} onSaved={refresh} />
            {/* 메일은 위의 티켓 카드 안에 있습니다. 여기는 **사람에게 달린 기록**입니다 —
                손으로 적은 메모, 통화·미팅, 허브스팟에서 가져온 것, 그리고 사라진 티켓에서
                옮겨 온 옛 메일(작성자 「지난 티켓」). */}
            <div className="history-list" style={{ marginTop: 16 }}>
              {data.interactions.length === 0 ? (
                <div className="empty"><div className="empty__text">아직 기록이 없습니다.</div></div>
              ) : (
                data.interactions.map((item, index) => <InteractionItem key={index} item={item} />)
              )}
            </div>
          </section>
        </div>

        <aside className="stack">
          {/* **읽기 전용입니다** (2026-08-19 운영자 지시). 단계·다음 액션·리드 온도의
              원본은 티켓과 수주 고객이고, 이 화면에서 또 고를 수 있으면 같은 값이 두
              곳에서 갈라집니다 — 실제로 이 폼이 저장할 때 대화 단계까지 같이 옮겨서,
              보드에서 옮긴 것과 여기서 고른 것이 서로 덮어썼습니다. 보는 자리와 정하는
              자리를 갈라 둡니다. */}
          <section className="card">
            {/* 「티켓과 수주 고객에서 정해집니다」는 뺐습니다 (2026-08-26 운영자 지시).
                고를 수 있는 것이 하나도 없는 카드라 읽기 전용인 것은 보면 알고, 어디서
                정해지는지는 그 값을 고치러 갈 때 알면 되는 이야기입니다. */}
            <div className="section-header">
              <div className="section-header__title">고객 상태</div>
            </div>
            <div className="field-grid">
              <KV k="파이프라인" v={labelFor(data.stage_options, profile?.pipeline_stage)} />
              <KV k="리드 온도" v={profile?.lead_temperature || "-"} />
              <KV k="MQL / PQL" v={profile?.qualification || "-"} />
              <KV k="산업군" v={profile?.industry || "-"} />
              <KV k="현재 플랜" v={profile?.current_plan || "-"} />
              <KV k="user-seq" v={profile?.user_seq || "-"} />
              <KV k="유입 소스" v={profile?.source || "-"} />
              <KV k="다음 액션" v={profile?.next_action
                ? `${profile.next_action}${profile.next_action_at ? ` · ${kst(profile.next_action_at)}` : ""}`
                : "-"} />
              {profile?.lost_reason && <KV k="Closed Lost 사유" v={profile.lost_reason} />}
              {profile?.notes && <KV k="운영 메모" v={profile.notes} />}
            </div>
            {/* **언제 것인지는 값이 있을 때만 적습니다.** 「아직 동기화를 하지 않았습니다」는
                할 일을 알려 주는 말처럼 보이지만, 그 버튼은 이 페이지 맨 위에 이미 서
                있습니다 — 안 누른 상태를 한 줄로 설명할 이유가 없습니다. 값이 있으면
                그때는 적어야 합니다: 이 칸들이 언제 것이냐가 곧 믿어도 되느냐입니다. */}
            {profile?.last_synced_at && (
              <div className="t-xs t-subtle" style={{ marginTop: 10 }}>
                {`마지막 HubSpot 동기화 ${kst(profile.last_synced_at)}`}
              </div>
            )}
          </section>

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

/** 읽기 전용 한 칸. 값이 정해지는 자리가 여기가 아니라는 것을 모양으로도 말합니다. */
function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="info-row">
      <dt>{k}</dt>
      <dd>{v}</dd>
    </div>
  );
}

const labelFor = (options: { key: string; label: string }[], value: string | null | undefined) =>
  options.find((option) => option.key === value)?.label ?? value ?? "-";

/** 티켓 한 건 — 머리글은 늘 보이고, 안의 메일과 진행 기록은 접힙니다.
 *
 *  `details/summary` 를 쓰는 이유: 접힘 상태를 브라우저가 들고 있어서 상태 하나를 더
 *  만들지 않아도 되고, 키보드와 스크린리더가 원래부터 압니다.
 */
function TicketBlock({ ticket, open, stages }: {
  ticket: Ticket;
  open: boolean;
  stages: { key: string; label: string }[];
}) {
  return (
    <details className="card" open={open} style={{ padding: "12px 14px" }}>
      <summary style={{ cursor: "pointer", listStyle: "none" }}>
        <div className="row-between wrap" style={{ gap: 8 }}>
          <strong className="t-sm">{ticket.subject || "제목 없는 문의"}</strong>
          <span className="row wrap" style={{ gap: 6 }}>
            <span className="tag">{labelFor(stages, ticket.stage)}</span>
            {ticket.ticket_id && <span className="tag mono">#{ticket.ticket_id}</span>}
            {ticket.client_id != null && (
              <span className="tag tnum">Client ID {ticket.client_id}</span>
            )}
          </span>
        </div>
        <div className="t-xs t-subtle" style={{ marginTop: 4 }}>
          접수 {kst(ticket.created_at)}
          {ticket.last_outgoing_at && ` · 마지막 발송 ${kst(ticket.last_outgoing_at)}`}
          {` · 메일 ${ticket.messages.length}통`}
        </div>
      </summary>

      {ticket.summary && (
        <p className="t-sm" style={{ margin: "10px 0 0", color: "var(--text-muted)" }}>
          {ticket.summary}
        </p>
      )}

      <div className="history-list" style={{ marginTop: 12 }}>
        {ticket.messages.length === 0 ? (
          <p className="t-sm t-subtle">이 티켓에는 남아 있는 메일이 없습니다.</p>
        ) : (
          ticket.messages.map((message) => (
            <InteractionItem
              key={message.id}
              item={{
                channel: "email",
                direction: message.direction,
                // 상태를 작성자 자리에 적습니다 — 나간 메일과 아직 안 나간 초안은
                // 히스토리에서 반드시 구별돼야 합니다.
                handler: message.status,
                subject: message.subject,
                summary: message.body,
                context: null,
                artifact_url: null,
                happened_at: message.happened_at,
                source: "message",
              } as Interaction}
            />
          ))
        )}
      </div>

      {ticket.progress.length > 0 && (
        <div style={{ marginTop: 10, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
          {ticket.progress.map((row, index) => (
            <div key={index} className="t-xs t-subtle">
              {kst(row.created_at)} · {row.kind} · {row.detail}
            </div>
          ))}
        </div>
      )}
    </details>
  );
}
