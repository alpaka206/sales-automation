import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { getJSON, postForm } from "../lib/api";
import { kst } from "../lib/format";
import { Icon } from "../ui/Icon";
import { channelLabel } from "../ui/InteractionForm";
import { Modal } from "../ui/Modal";
import { ActionButton, useAction } from "../ui/ActionButton";
import { InteractionForm, InteractionItem, type Interaction } from "../ui/InteractionForm";
import { LoadingBlock } from "../ui/Loading";

type Bubble = {
  id: number;
  direction: string;
  status: string;
  subject: string | null;
  body: string;
  body_ko: string | null;
  subject_ko: string | null;
  needs_ko: boolean;
  is_auto_ack: boolean;
  language: string | null;
  created_at: string;
  sent_at: string | null;
  is_current: boolean;
};
type Detail = {
  thread: Bubble[];
  category: string | null;
  category_label: string;
  unqualified: boolean;
  progress: {
    kind: string; detail: string; created_at: string;
    channel: string | null; handler: string | null;
  }[];
  summary: string | null;
  customer_requests: string | null;
  signatures: { key: string; name: string }[];
  ticket: {
    id: number | null; ticket_id: string | null; stage: string | null;
    inquiry_subject: string | null; inquiry_language: string | null; client_id: number | null;
  };
  ticket_interactions: Interaction[];
  msg: {
    id: number; status: string; subject: string; body: string; channel: string;
    language: string | null; target_language: string | null; signature_key: string;
    to_address: string; score_snapshot: number | null; created_at: string;
    sent_at: string | null; scheduled_at: string | null; category: string | null;
  };
  contact: { id: number; name: string; email: string | null; company: string | null; domain: string | null; role_description: string | null } | null;
  customer: { has_any?: boolean; profile: Record<string, unknown> | null; interactions: Interaction[] } | null;
  stage_labels: Record<string, string>;
};

export function MessageDetail() {
  const { id } = useParams();
  const queryClient = useQueryClient();
  const { data, isPending } = useQuery({
    queryKey: ["message", id],
    queryFn: () => getJSON<Detail>(`/api/ui/messages/${id}`),
    // The draft may still be being written; the Jinja page polled every 4s for that.
    refetchInterval: (query) =>
      (query.state.data as Detail | undefined)?.msg.status === "drafting" ? 4_000 : false,
  });

  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [signature, setSignature] = useState("");
  const [note, setNote] = useState("");
  const [preview, setPreview] = useState<string | null>(null);
  const [confirmSend, setConfirmSend] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [showOrig, setShowOrig] = useState<Record<number, boolean>>({});
  const [loadedId, setLoadedId] = useState<number | null>(null);

  // 훅은 아래 early return 보다 **위**에서 부릅니다. 아래에 두면 로딩 렌더에서는 건너뛰고
  // 데이터가 온 렌더에서는 부르게 되어, 훅 수가 달라졌다고 React 가 터집니다(#310) — 화면이
  // 통째로 안 뜹니다. 그래서 data 가 아직 없을 수 있다는 전제로 씁니다.
  const [saveContact, savingContact] = useAction(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    await postForm(
      `/contacts/${data?.contact?.id}/edit`,
      Object.fromEntries(new FormData(form) as never) as Record<string, string>,
    );
    setNote("연락처를 저장했습니다.");
    await queryClient.invalidateQueries({ queryKey: ["message", id] });
  });

  // Fill the editor once per message. Re-syncing on every refetch would overwrite what
  // the operator is typing while the queue revalidates underneath them.
  //
  // DURING render, not in an effect. An effect runs after the browser has painted, so the
  // first frame showed an empty 제목/본문 and the text appeared a moment later — the page
  // visibly changing after it had already loaded. Setting state while rendering makes
  // React re-render before paint instead; nothing is ever shown empty.
  if (data && loadedId !== data.msg.id) {
    setLoadedId(data.msg.id);
    setSubject(data.msg.subject);
    setBody(data.msg.body);
    setSignature(data.msg.signature_key);
  }

  if (isPending || !data) return <LoadingBlock />;

  const { msg, ticket, contact } = data;
  const isPendingApproval = msg.status === "pending_approval";
  const canTranslate = !!msg.target_language && msg.target_language !== "ko";

  // 되는 동안의 상태는 누른 버튼이 말합니다(ActionButton). 여기 남는 것은 결과뿐입니다 —
  // 진행 표시가 버튼과 다른 자리에 있으면 눌린 건지 몰라 한 번 더 누르게 됩니다.
  async function act(path: string, extra: Record<string, string> = {}) {
    setNote("");
    try {
      await postForm(path, { subject, body, signature_key: signature, ...extra });
      setNote("완료되었습니다.");
      await queryClient.invalidateQueries();
    } catch (error) {
      setNote(`실패: ${String(error)}`);
    }
  }

  async function translate() {
    setNote("");
    const response = await fetch(`/messages/${msg.id}/translate`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ body, subject, signature_key: signature }),
    });
    const result = await response.json();
    if (result.error) return setNote(result.error);
    setBody(result.body);
    if (result.subject !== undefined) setSubject(result.subject);
    setNote(result.translated ? `번역됨 → ${result.language}` : "번역할 내용이 없습니다.");
  }

  async function openPreview() {
    const response = await fetch("/messages/preview", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ body, signature_key: signature }),
    });
    // 실패 응답을 그대로 넣으면 「이메일 미리보기」 라는 제목 아래 오류 페이지가 메일처럼
    // 그려집니다 — 운영자는 그걸 고객에게 나갈 본문으로 읽습니다.
    if (!response.ok) {
      throw new Error(`미리보기를 만들지 못했습니다 (${response.status}).`);
    }
    setPreview(await response.text());
  }

  return (
    <>
      <div style={{ marginBottom: 14 }}>
        <Link to="/messages" className="chip"><Icon name="chevron" size={14} /> 회신 및 검토 목록</Link>
      </div>

      <div className="page-header">
        <div>
          <div className="row" style={{ gap: 10 }}>
            <span className="tag">
              <Icon name="messages" size={14} />{" "}
              {ticket.ticket_id ? `HubSpot 티켓 #${ticket.ticket_id}` : "연락처 기준 대화 (티켓 없음)"}
            </span>
            {ticket.inquiry_language && (
              <span className="tag"><Icon name="translate" size={13} /> 문의 언어 · {ticket.inquiry_language}</span>
            )}
          </div>
          <h1 className="page-title" style={{ marginTop: 10 }}>문의와 답변 · #{msg.id}</h1>
        </div>
      </div>

      {msg.status === "drafting" && (
        <div className="banner banner--info mb-gap" role="status">
          <span className="banner__icon"><Icon name="sparkles" size={18} /></span>
          <div><div className="banner__title">답변 작성 중</div></div>
        </div>
      )}

      <div className="split">
        <div className="stack">
          <div className="thread__meta"><Icon name="clock" size={14} /> 진행 기록 · {data.thread.length}건</div>
          <div className="thread">
            {data.thread.map((bubble) => {
              if (bubble.is_current && isPendingApproval) {
                return (
                  <div key={bubble.id} className="bubble bubble--out bubble--current">
                    <div className="bubble__head">
                      <span className="bubble__dir"><Icon name="send" size={14} /> 회신 초안 · 한국어 (검토용)</span>
                      <span className="bubble__time tnum">{kst(bubble.created_at)}</span>
                    </div>
                    <label className="field-label" htmlFor="msg-subject">제목</label>
                    <input className="input" id="msg-subject" value={subject}
                           onChange={(e) => setSubject(e.target.value)} style={{ marginBottom: 12 }} />
                    <label className="field-label" htmlFor="msg-body">본문</label>
                    <textarea className="draft-textarea" id="msg-body" value={body}
                              onChange={(e) => setBody(e.target.value)} />

                    {/* 골라야 붙습니다. 예전에는 여기에 "기본 (텍스트 서명)" 이 하나 더
                        있었는데, 그건 모델이 본문에 써 넣은 서명을 그대로 두라는 뜻이었습니다
                        — 고르지 않아도 서명이 붙던 자리입니다. 이제 없습니다. */}
                    <label className="field-label" htmlFor="msg-signature" style={{ marginTop: 12 }}>서명</label>
                    <select className="select" id="msg-signature" value={signature}
                            onChange={(e) => setSignature(e.target.value)} style={{ marginBottom: 12 }}>
                      <option value="">서명 없음</option>
                      {data.signatures.map((s) => (
                        <option key={s.key} value={s.key}>{s.name}</option>
                      ))}
                    </select>

                    <div className="action-bar">
                      {canTranslate && (
                        <ActionButton className="btn btn--subtle" pending="번역 중" onClick={translate}>
                          <Icon name="translate" size={15} /> 번역하기 ({msg.target_language})
                        </ActionButton>
                      )}
                      <button type="button" className="btn btn--ok"
                              aria-haspopup="dialog" onClick={() => setConfirmSend(true)}>
                        <Icon name="check" size={15} /> 검토 완료 · 발송
                      </button>
                      <ActionButton className="btn btn--subtle" pending="여는 중" onClick={openPreview}>
                        <Icon name="file" size={15} /> 미리보기
                      </ActionButton>
                      <ActionButton className="btn btn--subtle" pending="저장 중"
                                    onClick={() => act(`/messages/${msg.id}/edit`)}>
                        <Icon name="edit" size={15} /> 저장
                      </ActionButton>
                      <button type="button" className="btn btn--danger"
                              aria-haspopup="dialog" onClick={() => setRejecting(true)}>
                        <Icon name="x" size={15} /> 거절
                      </button>
                    </div>
                    {note && <div style={{ marginTop: 14 }} role="status" className="t-sm">{note}</div>}
                  </div>
                );
              }
              const inbound = bubble.direction === "inbound";
              const open = showOrig[bubble.id];
              return (
                <div key={bubble.id} className={`bubble bubble--${inbound ? "in" : "out"}${bubble.is_current ? " bubble--current" : ""}`}>
                  <div className="bubble__head">
                    <span className="bubble__dir">
                      <Icon name={inbound ? "inbound" : bubble.is_auto_ack ? "sparkles" : "send"} size={14} />{" "}
                      {inbound ? "고객 문의" : bubble.is_auto_ack ? "자동 접수확인 (승인 없이 발송)" : "회신"}
                    </span>
                    {bubble.needs_ko && (
                      <button type="button" className="chip chip--xs"
                              onClick={() => setShowOrig((p) => ({ ...p, [bubble.id]: !open }))}>
                        <Icon name="translate" size={13} /> {open ? "원문 닫기" : "원문 보기"}
                      </button>
                    )}
                    <span className="bubble__time tnum">{kst(bubble.sent_at || bubble.created_at)}</span>
                  </div>
                  {bubble.needs_ko ? (
                    <div className={`bubble__cols${open ? " is-split" : ""}`}>
                      <div className="bubble__col">
                        <div className="ko-block__label"><Icon name="translate" size={12} /> 한국어 번역</div>
                        {(bubble.subject_ko || bubble.subject) && (
                          <div className="bubble__subject">{bubble.subject_ko || bubble.subject}</div>
                        )}
                        <div className="msg-body">{bubble.body_ko || bubble.body}</div>
                      </div>
                      {open && (
                        <div className="bubble__col bubble__col--orig">
                          <div className="ko-block__label t-subtle">원문 ({bubble.language || "original"})</div>
                          {bubble.subject && <div className="bubble__subject">{bubble.subject}</div>}
                          <div className="msg-body">{bubble.body}</div>
                        </div>
                      )}
                    </div>
                  ) : (
                    <>
                      {bubble.subject && <div className="bubble__subject">{bubble.subject}</div>}
                      <div className="msg-body">{bubble.body}</div>
                    </>
                  )}
                </div>
              );
            })}
          </div>

          {/* This ticket's manual touchpoints — everything after the first reply happens
              off HubSpot, so the operator types it in and the ticket keeps one history. */}
          {ticket.id && contact && (
            <div className="card" id="log">
              <div className="section-header" style={{ marginBottom: 12 }}>
                <div className="section-header__l">
                  <span className="section-header__icon"><Icon name="history" size={16} /></span>
                  <div>
                    <div className="section-header__title">소통 기록</div>
                    <div className="section-header__sub">이 문의에 대해 이메일·WhatsApp·전화·문자로 오간 내용</div>
                  </div>
                </div>
              </div>
              <InteractionForm
                contactId={contact.id}
                conversationId={ticket.id}
                onSaved={() => void queryClient.invalidateQueries({ queryKey: ["message", id] })}
              />
              <div className="history-list" style={{ marginTop: 16 }}>
                {data.ticket_interactions.length === 0 ? (
                  <div className="empty"><div className="empty__text">아직 기록이 없습니다. 회신 이후의 연락은 여기에 남겨주세요.</div></div>
                ) : (
                  data.ticket_interactions.map((item) => <InteractionItem key={item.id} item={item} />)
                )}
              </div>
            </div>
          )}
        </div>

        <div className="stack" style={{ gap: "var(--gap)" }}>
          {(data.summary || data.customer_requests || data.progress.length > 0) && (
            <div className="card">
              <div className="section-label" style={{ marginBottom: 12 }}>대화 요약 · 처리경과</div>
              {data.summary && (
                <div className="msg-body--inset" style={{ marginBottom: 10 }}>
                  <div className="ko-block__label">요약</div>
                  <div className="t-sm" style={{ lineHeight: 1.6, whiteSpace: "pre-line" }}>{data.summary}</div>
                </div>
              )}
              {data.customer_requests && (
                <div className="msg-body--inset" style={{ marginBottom: 10 }}>
                  <div className="ko-block__label" style={{ color: "var(--accent)" }}>고객 요청사항</div>
                  <div className="t-sm" style={{ lineHeight: 1.6, whiteSpace: "pre-line" }}>{data.customer_requests}</div>
                </div>
              )}
              {data.progress.length > 0 && (
                <ul className="progress-log">
                  {data.progress.map((p, index) => (
                    <li key={index} className="progress-log__item">
                      <span className="progress-log__time tnum">{kst(p.created_at)}</span>
                      <span className="progress-log__detail">
                        {/* An operator's own note, in the same sequence as the sends —
                            "메일이 나갔다 → 미팅했고 요구사항은 이것" is one story, and
                            it was split across two screens. */}
                        {p.kind === "interaction" && (
                          <span className="tag" style={{ marginRight: 6 }}>
                            {channelLabel(p.channel || "manual")}
                            {p.handler ? ` · ${p.handler}` : ""}
                          </span>
                        )}
                        <span style={{ whiteSpace: "pre-line" }}>{p.detail}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <div className="card">
            <div className="section-label" style={{ marginBottom: 12 }}>티켓 / 대화</div>
            <dl className="info-list">
              <div className="info-row"><dt>티켓</dt><dd className="mono">{ticket.ticket_id ? `#${ticket.ticket_id}` : "— (없음)"}</dd></div>
              <div className="info-row"><dt>Client ID</dt><dd className="tnum">{ticket.client_id ?? "미동기화"}</dd></div>
              {ticket.stage && <div className="info-row"><dt>단계</dt><dd>{data.stage_labels[ticket.stage] ?? ticket.stage}</dd></div>}
              {ticket.inquiry_subject && <div className="info-row"><dt>문의 제목</dt><dd>{ticket.inquiry_subject}</dd></div>}
              <div className="info-row"><dt>메시지</dt><dd className="tnum">{data.thread.length}건</dd></div>
            </dl>
          </div>

          {contact && (
            <div className="card">
              <div className="section-label" style={{ marginBottom: 12 }}>연락처 정보 (편집 가능)</div>
              <dl className="info-list" style={{ marginBottom: 12 }}>
                <div className="info-row"><dt>이름</dt><dd>{contact.name}</dd></div>
                {contact.email && (
                  <div className="info-row"><dt>이메일</dt>
                    <dd className="mono truncate" style={{ maxWidth: 170 }}>{contact.email}</dd></div>
                )}
                {contact.domain && (
                  <div className="info-row"><dt>도메인</dt>
                    <dd><Link className="mono" to={`/companies/${contact.domain}`}>{contact.domain}</Link></dd></div>
                )}
              </dl>
              {/* What the operator learns mid-conversation goes here — it is the only
                  place a gmail/unverified contact gets a company name at all. */}
              <form onSubmit={saveContact}>
                <label className="field-label" htmlFor="c-company">회사</label>
                <input className="input" id="c-company" name="company"
                       defaultValue={contact.company ?? ""} style={{ marginBottom: 10 }} />
                <label className="field-label" htmlFor="c-role">하는 일 / 메모</label>
                <textarea className="textarea" id="c-role" name="role_description" rows={3}
                          defaultValue={contact.role_description ?? ""}
                          placeholder="이 고객·회사가 어떤 일을 하는지 (대화하며 알게 된 내용 포함). gmail·미확인이어도 입력해 저장됩니다." />
                <button className="btn btn--subtle btn--sm" type="submit" style={{ marginTop: 10 }}
                        disabled={savingContact} aria-busy={savingContact || undefined}>
                  {savingContact ? <><span className="spinner" role="status" /> 저장 중</>
                                 : <><Icon name="check" size={14} /> 연락처 저장</>}
                </button>
              </form>
            </div>
          )}

          <div className="card">
            <div className="section-label" style={{ marginBottom: 12 }}>발송 정보</div>
            <dl className="info-list">
              <div className="info-row"><dt>채널</dt><dd>{msg.channel}</dd></div>
              {msg.target_language && <div className="info-row"><dt>발송 언어</dt><dd>{msg.target_language}</dd></div>}
              <div className="info-row"><dt>수신자</dt><dd className="mono truncate" style={{ maxWidth: 170 }}>{msg.to_address || "—"}</dd></div>
              <div className="info-row"><dt>생성</dt><dd className="tnum">{kst(msg.created_at)}</dd></div>
              {msg.sent_at && <div className="info-row"><dt>발송</dt><dd className="tnum">{kst(msg.sent_at)}</dd></div>}
            </dl>
          </div>

          {data.customer?.interactions && data.customer.interactions.length > 0 && (
            <div className="card">
              <div className="ko-block__label" style={{ marginBottom: 6 }}>
                다른 접점 기록 (최근 {data.customer.interactions.length}건)
              </div>
              <div className="history-list">
                {data.customer.interactions.map((item, index) => <InteractionItem key={index} item={item} />)}
              </div>
            </div>
          )}
        </div>
      </div>

      {confirmSend && (
        <Modal
          title="발송하시겠습니까?"
          description={
            <>
              승인 즉시 <strong>{msg.channel}</strong>로 발송됩니다.
              {msg.to_address && <> 수신자: <span className="mono">{msg.to_address}</span>.</>}
              {canTranslate && (
                <><br />발송 본문은 문의 언어({msg.target_language})로 나갑니다 — 아직 한국어라면
                발송 직전 자동 번역됩니다.</>
              )}
              {" 이 동작은 되돌릴 수 없습니다."}
            </>
          }
          onClose={() => setConfirmSend(false)}
          actions={
            // 끝난 뒤에 닫습니다. 먼저 닫으면 "발송 중" 을 볼 자리가 사라지고, 발송은
            // 되돌릴 수 없는 동작이라 그 몇 초가 한 번 더 누르게 만드는 구간입니다.
            <ActionButton className="btn btn--ok" pending="발송 중"
                          onClick={() => act(`/messages/${msg.id}/send`).then(() => setConfirmSend(false))}>
              <Icon name="check" size={15} /> 검토 완료 · 발송
            </ActionButton>
          }
        />
      )}

      {rejecting && (
        <Modal
          title="이 초안을 거절합니다"
          description="거절하면 발송 대기에서 빠집니다. 사유는 처리경과에 남습니다."
          onClose={() => setRejecting(false)}
          actions={
            // 발송 모달과 같은 순서입니다 — 끝난 뒤에 닫습니다. 먼저 닫으면 "거절 중" 을
            // 볼 자리가 사라지고, 실패해도 목록만 그대로인 채 아무 말이 없습니다. 맨 버튼일
            // 때는 연타가 그대로 요청 두 번이기도 했습니다.
            <ActionButton className="btn btn--danger" pending="거절 중"
                          onClick={() => act(`/messages/${msg.id}/reject`, { reason })
                            .then(() => { setRejecting(false); setReason(""); })}>
              거절 확정
            </ActionButton>
          }
        >
          <label className="field-label" htmlFor="reject-reason" style={{ marginTop: 12 }}>거절 사유</label>
          <textarea className="textarea" id="reject-reason" rows={3} value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    placeholder="거절 사유를 입력하세요" />
        </Modal>
      )}

      {preview !== null && (
        <Modal title="이메일 미리보기" wide onClose={() => setPreview(null)}
               description={<>제목: <span className="mono">{subject}</span></>}>
          <iframe title="이메일 미리보기" sandbox="" srcDoc={preview}
                  style={{ width: "100%", height: "60vh", border: "1px solid var(--border)",
                           borderRadius: 8, background: "#fff" }} />
        </Modal>
      )}
    </>
  );
}
