import { useLayoutEffect, useState, type ReactNode } from "react";
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
/** 한 줄 = 운영자 표의 「필드」 하나. 값은 자유 입력이라 `truncate` 로 감쌉니다 —
 *  `.info-row` 는 flex 라 `plan-2026-kr-renewal` 같은 한 덩어리 글자가 320px 카드를
 *  뚫고 나갑니다. 옆의 이메일·수신자 줄이 같은 이유로 이미 그렇게 하고 있습니다. */
function CompanyRow({ row }: { row: { label: string; value: string | null; found: boolean } }) {
  return (
    <div className="info-row">
      <dt>{row.label}</dt>
      <dd className="truncate">
        {!row.found ? <span className="t-subtle">필드를 찾지 못했습니다</span>
         : row.value ?? <span className="t-subtle">—</span>}
      </dd>
    </div>
  );
}

/** 허브스팟 연락처 레코드의 「기본 그룹」. 카드를 나누는 것도 줄 이름도 서버가 정합니다 — 필드가 늘 때
 *  고칠 곳이 한 곳이어야 합니다(`src/integrations/hubspot_record.py`). */
type HubSpotRecord = {
  groups: {
    key: string;
    title: string;
    /** `found: false` 는 「그 회사에 값이 없다」가 아니라 「허브스팟에서 그 속성을 못 찾았다」
     *  입니다. 값이 빈 것은 `—` 로 서고(허브스팟 사이드바가 `--` 를 그리는 그 자리),
     *  못 찾은 것은 그렇다고 적습니다 — 앞엣것은 이 고객 이야기이고 뒤엣것은 설정
     *  이야기라, 화면에서 같아 보이면 안 됩니다. */
    rows: { label: string; value: string | null; found: boolean }[];
  }[];
  error: string | null;
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
    /** Won Type / Lost Reason. 보드 카드와 같은 값 — 지금 단계의 목록에 있을 때만 옵니다. */
    deal_detail: string | null;
    inquiry_subject: string | null; inquiry_language: string | null; client_id: number | null;
  };
  ticket_interactions: Interaction[];
  /** 메일이 하나도 없는 티켓은 `null` 입니다 — HubSpot 에서 들여온 티켓이 그렇습니다. */
  msg: {
    id: number; status: string; subject: string; body: string; channel: string;
    language: string | null; target_language: string | null; signature_key: string;
    to_address: string; score_snapshot: number | null; created_at: string;
    sent_at: string | null; scheduled_at: string | null; category: string | null;
  } | null;
  contact: { id: number; name: string; email: string | null; company: string | null; domain: string | null; role_description: string | null } | null;
  customer: { profile: Record<string, unknown> | null; interactions: Interaction[] } | null;
  stage_labels: Record<string, string>;
  /** 소통 기록을 남길 수 있는 단계 — 보드의 + 버튼과 같은 목록, 같은 출처. */
  manual_log_stages: string[];
  /** 단계 → 고를 수 있는 Deal Detail. Won 과 Lost 만 있습니다 — 보드와 같은 출처. */
  deal_details: Record<string, string[]>;
};

/** 편집기 도구 → 본문 표기. `email_html._inline` 이 읽는 것과 **같은 넷**입니다.
 *  여기에 하나 더하면 그쪽 렌더러에도 더해야 합니다 — 안 그러면 화면에만 있는 표기가 되고,
 *  고객은 별표를 그대로 받습니다.
 *
 *  버튼에 글자 대신 그 서식이 걸린 **표시**를 씁니다(B·I·U·고리). 메일 편집기에서 늘 보던
 *  자리·모양이라 읽지 않아도 무엇인지 압니다. */
const MARKS: { key: string; mark: ReactNode; wrap: [string, string]; title: string }[] = [
  { key: "b", mark: <b>B</b>, wrap: ["**", "**"], title: "굵게 (**글자**)" },
  { key: "i", mark: <i style={{ fontFamily: "Georgia,serif" }}>I</i>, wrap: ["*", "*"], title: "기울임 (*글자*)" },
  { key: "u", mark: <u>U</u>, wrap: ["__", "__"], title: "밑줄 (__글자__)" },
  { key: "a", mark: <Icon name="link" size={14} />, wrap: ["[", "](https://)"],
    title: "링크 ([글자](주소)) — 고른 글자가 링크 글자가 됩니다" },
];

export function MessageDetail() {
  // 같은 화면에 문이 둘입니다. `/messages/:id` 는 회신 및 검토 목록에서 **그 초안**을 열 때,
  // `/tickets/:conversationId` 는 보드 카드에서 **티켓**을 열 때 — 뒤엣것은 메일이 하나도
  // 없는 티켓(HubSpot 에서 들여온 것)도 엽니다. 질의 키가 둘을 가릅니다.
  const { id, conversationId } = useParams();
  const key = conversationId ? ["ticket", conversationId] : ["message", id];
  const path = conversationId ? `/api/ui/tickets/${conversationId}` : `/api/ui/messages/${id}`;
  const queryClient = useQueryClient();
  const { data, isPending } = useQuery({
    queryKey: key,
    queryFn: () => getJSON<Detail>(path),
    // The draft may still be being written; the Jinja page polled every 4s for that.
    refetchInterval: (query) =>
      (query.state.data as Detail | undefined)?.msg?.status === "drafting" ? 4_000 : false,
  });

  // 허브스팟에 물어야 나오는 값이라 본문과 따로 받습니다. 같이 받으면 답을 읽는 일이
  // 허브스팟 응답을 기다리게 됩니다 — 패널만 늦게 채워지는 편이 낫습니다.
  const contactId = data?.contact?.id;
  const { data: hubspot, isPending: hubspotPending } = useQuery({
    queryKey: ["hubspot-record", contactId],
    queryFn: () => getJSON<HubSpotRecord>(`/api/ui/contacts/${contactId}/hubspot-record`),
    enabled: !!contactId,
    // 플랜은 티켓 하나 읽는 동안 바뀌지 않습니다.
    staleTime: 5 * 60_000,
  });

  const [editingContact, setEditingContact] = useState(false);
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
  const [logging, setLogging] = useState(false);
  /** 이 화면의 확인 창 하나. 오른쪽 칸의 저장은 **검토 중인 초안 밖**에서 일어나는데,
   *  결과를 적던 `note` 는 그 초안 안에서만 그려집니다 — 이미 답이 나간 티켓에서는 눌러도
   *  화면이 아무 말도 하지 않았습니다. 성공도 실패도 여기로 옵니다. */
  const [notice, setNotice] = useState<{ title: string; body: ReactNode } | null>(null);
  // 서식을 씌운 뒤 되돌려 놓을 선택 범위. 상태로 두는 이유는 아래 효과 참고.
  const [pendingSel, setPendingSel] = useState<[number, number] | null>(null);

  // 훅은 아래 early return 보다 **위**에서 부릅니다. 아래에 두면 로딩 렌더에서는 건너뛰고
  // 데이터가 온 렌더에서는 부르게 되어, 훅 수가 달라졌다고 React 가 터집니다(#310) — 화면이
  // 통째로 안 뜹니다. 그래서 data 가 아직 없을 수 있다는 전제로 씁니다.
  const [saveContact, savingContact] = useAction(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = Object.fromEntries(new FormData(form) as never) as Record<string, string>;
    await postForm(`/contacts/${data?.contact?.id}/edit`, fields);
    setNotice({
      title: "티켓 정보를 저장했습니다",
      body: (
        <dl className="info-list" style={{ marginTop: 12 }}>
          <div className="info-row"><dt>회사</dt>
            <dd>{fields.company?.trim() || "—"}</dd></div>
          <div className="info-row"><dt>하는 일 / 메모</dt>
            <dd style={{ whiteSpace: "pre-line" }}>{fields.role_description?.trim() || "—"}</dd></div>
        </dl>
      ),
    });
    setEditingContact(false);
    await queryClient.invalidateQueries({ queryKey: key });
  });

  // Fill the editor once per message. Re-syncing on every refetch would overwrite what
  // the operator is typing while the queue revalidates underneath them.
  //
  // DURING render, not in an effect. An effect runs after the browser has painted, so the
  // first frame showed an empty 제목/본문 and the text appeared a moment later — the page
  // visibly changing after it had already loaded. Setting state while rendering makes
  // React re-render before paint instead; nothing is ever shown empty.
  if (data?.msg && loadedId !== data.msg.id) {
    setLoadedId(data.msg.id);
    setSubject(data.msg.subject);
    setBody(data.msg.body);
    setSignature(data.msg.signature_key);
  }

  /** 본문에서 고른 글자를 표기로 감쌉니다. 아무것도 안 골랐으면 커서 자리에 껍데기만
   *  넣고 그 안에 커서를 둡니다 — 표기를 외우지 않아도 쓸 수 있게. */
  function wrapSelection([before, after]: [string, string]) {
    const field = document.getElementById("msg-body") as HTMLTextAreaElement | null;
    if (!field) return;
    const { selectionStart: from, selectionEnd: to } = field;
    const picked = body.slice(from, to);
    setBody(body.slice(0, from) + before + picked + after + body.slice(to));
    setPendingSel([from + before.length, from + before.length + picked.length]);
  }

  // 선택 복원은 **커밋 뒤**에 해야 합니다. 제어된 textarea 는 React 가 value 를 다시 넣을
  // 때 선택이 끝으로 풀리는데, requestAnimationFrame 으로 미루면 그 둘의 순서가 React 의
  // 스케줄링에 달립니다 — 실제로 풀린 채 남았습니다. useLayoutEffect 는 커밋 직후·그리기
  // 직전이라 순서가 보장됩니다. 씌운 글자가 그대로 골라져 있어야 굵게 → 기울임처럼 잇습니다.
  useLayoutEffect(() => {
    if (!pendingSel) return;
    const field = document.getElementById("msg-body") as HTMLTextAreaElement | null;
    field?.focus();
    field?.setSelectionRange(pendingSel[0], pendingSel[1]);
    setPendingSel(null);
  }, [pendingSel]);

  if (isPending || !data) return <LoadingBlock />;

  const { msg, ticket, contact } = data;
  const isPendingApproval = msg?.status === "pending_approval";
  // 보드가 어느 열에 + 를 그릴지 정하는 것과 **같은 목록**입니다. 서버가 주므로
  // 단계 이름이 바뀌어도 두 화면이 어긋나지 않습니다.
  const canLog = !!ticket.stage && data.manual_log_stages.includes(ticket.stage);
  const canTranslate = !!msg?.target_language && msg.target_language !== "ko";
  // Won 과 Lost 에만 있습니다. 목록도 「이 단계에 고르개가 붙는가」도 서버가 정합니다 —
  // 보드 카드와 같은 출처라 두 화면이 다른 값을 내놓을 수 없습니다.
  const dealOptions = ticket.stage ? data.deal_details[ticket.stage] : undefined;

  /** 고른 값을 그 자리에서 저장합니다. 저장 버튼을 따로 두지 않는 이유는 보드 카드와
   *  같습니다 — 값 하나짜리 고르개에 저장 버튼이 붙으면, 고르고 안 누른 상태가 생깁니다. */
  async function saveDealDetail(detail: string) {
    try {
      await postForm(`/pipeline/conversations/${ticket.id}/deal-detail`, { detail });
      await queryClient.invalidateQueries({ queryKey: key });
    } catch (error) {
      // 실패하면 말합니다. 조용히 두면 고른 값이 화면에만 남아 저장된 것으로 읽힙니다 —
      // 다시 받아 오므로 고르개는 저장된 값으로 되돌아갑니다.
      setNotice({ title: "Deal Detail 을 저장하지 못했습니다", body: String(error) });
      await queryClient.invalidateQueries({ queryKey: key });
    }
  }

  // 되는 동안의 상태는 누른 버튼이 말합니다(ActionButton). 여기 남는 것은 결과뿐입니다 —
  // 진행 표시가 버튼과 다른 자리에 있으면 눌린 건지 몰라 한 번 더 누르게 됩니다.
  async function act(action: string, extra: Record<string, string> = {}) {
    if (!msg) return;
    setNote("");
    try {
      await postForm(`/messages/${msg.id}/${action}`, { subject, body, signature_key: signature, ...extra });
      setNote("완료되었습니다.");
      // 허브스팟 패널은 빼고 무효화합니다 — 우리가 저장한다고 저쪽 값이 바뀌지 않는데,
      // 같이 걸면 콘솔의 모든 저장이 열려 있는 티켓 탭마다 외부 왕복을 한 번씩 냅니다.
      await queryClient.invalidateQueries({
        predicate: (query) => query.queryKey[0] !== "hubspot-record",
      });
    } catch (error) {
      setNote(`실패: ${String(error)}`);
    }
  }

  async function translate() {
    if (!msg) return;
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
      {/* 나가는 문 둘. 왼쪽은 온 곳으로, 오른쪽은 **이 고객의 히스토리**입니다 —
          보드에서 티켓으로 들어오게 바뀌었으니, 고객 단위로 보고 싶을 때 갈 곳이
          여기 있어야 합니다. Deal Detail·소통 기록은 티켓의 값이라 이 화면이 먼저입니다. */}
      <div className="row-between" style={{ marginBottom: 14 }}>
        <Link to={conversationId ? "/" : "/messages"} className="chip">
          <Icon name="chevron" size={14} /> {conversationId ? "문의 대시보드" : "회신 및 검토 목록"}
        </Link>
        {contact && (
          <Link to={`/customers/${contact.id}`} className="chip">
            <Icon name="users" size={14} /> 이 고객 히스토리
          </Link>
        )}
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
          <h1 className="page-title" style={{ marginTop: 10 }}>
            문의와 답변{ticket.inquiry_subject ? ` · ${ticket.inquiry_subject}` : ""}
          </h1>
        </div>
      </div>

      {msg?.status === "drafting" && (
        <div className="banner banner--info mb-gap" role="status">
          <span className="banner__icon"><Icon name="sparkles" size={18} /></span>
          <div><div className="banner__title">답변 작성 중</div></div>
        </div>
      )}

      <div className="split">
        <div className="stack">
          <div className="thread__meta"><Icon name="clock" size={14} /> 진행 기록 · {data.thread.length}건</div>
          {data.thread.length === 0 && (
            <div className="empty">
              <div className="empty__text">
                이 티켓에는 이 콘솔이 주고받은 메일이 없습니다 — HubSpot 에서 들여온
                티켓입니다. 오간 연락은 아래 소통 기록에 남겨주세요.
              </div>
            </div>
          )}
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
                    {/* 도구는 메일 편집기처럼 **본문 상자 안쪽 아래**입니다 — 글자를 고른
                        손이 곧바로 닿는 자리. 상자 테두리는 이 wrapper 가 그리고 textarea 는
                        테두리를 벗습니다(안에 든 것처럼 보이도록).

                        WYSIWYG 이 아닌 이유: 이 칸의 글자가 그대로 메일이 되는 것이 이 화면의
                        전제입니다(모델이 쓰고, 번역이 지나가고, 사람이 고칩니다). 숨은 서식을
                        들고 있으면 그 셋이 서로 모르는 상태가 되고, 화면과 나간 메일이 갈립니다. */}
                    <div className="draft-editor">
                      <textarea className="draft-textarea" id="msg-body" value={body}
                                onChange={(e) => setBody(e.target.value)} />
                      <div className="draft-tools">
                        {MARKS.map(({ key, mark, wrap, title }) => (
                          <button key={key} type="button" className="draft-tool"
                                  title={title} aria-label={title}
                                  /* 누르는 순간 본문의 선택이 풀리면 감쌀 것이 없어집니다. */
                                  onMouseDown={(e) => e.preventDefault()}
                                  onClick={() => wrapSelection(wrap)}>
                            {mark}
                          </button>
                        ))}
                      </div>
                    </div>

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
                                    onClick={() => act("edit")}>
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
              off HubSpot, so the operator types it in and the ticket keeps one history.

              폼은 펼쳐 두지 않고 `추가하기` → 모달입니다. 이 화면의 일은 초안을 읽고
              보내는 것이고, 서른 줄짜리 입력 폼이 그 사이에 늘 끼어 있을 이유가 없습니다.
              문의 대시보드 카드의 + 버튼이 띄우는 것과 같은 모달·같은 폼입니다.

              단계가 아직 New 면 버튼도 없습니다. 검토할 초안이 있다는 것 자체가 아직
              아무 답도 안 나갔다는 뜻이라 적을 소통이 없습니다 — 보드에서 New 열에만
              + 버튼이 없는 것과 같은 규칙이고, 목록도 서버가 주는 같은 것을 씁니다. */}
          {ticket.id && contact && (canLog || data.ticket_interactions.length > 0) && (
            <div className="card" id="log">
              <div className="section-header" style={{ marginBottom: 12 }}>
                <div className="section-header__l">
                  <span className="section-header__icon"><Icon name="history" size={16} /></span>
                  <div>
                    <div className="section-header__title">소통 기록</div>
                    <div className="section-header__sub">이 문의에 대해 이메일·WhatsApp·전화·문자로 오간 내용</div>
                  </div>
                </div>
                {canLog && (
                  <button
                    type="button"
                    className="btn btn--subtle btn--sm"
                    aria-haspopup="dialog"
                    onClick={() => setLogging(true)}
                  >
                    <Icon name="plus" size={14} /> 추가하기
                  </button>
                )}
              </div>
              <div className="history-list">
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
            <div className="section-label" style={{ marginBottom: 12 }}>티켓 정보</div>
            <dl className="info-list">
              <div className="info-row"><dt>티켓</dt><dd className="mono">{ticket.ticket_id ? `#${ticket.ticket_id}` : "— (없음)"}</dd></div>
              <div className="info-row"><dt>Client ID</dt><dd className="tnum">{ticket.client_id ?? "미동기화"}</dd></div>
              {ticket.stage && <div className="info-row"><dt>Stage</dt><dd>{data.stage_labels[ticket.stage] ?? ticket.stage}</dd></div>}
              {/* Won 과 Lost 일 때만 나옵니다 — 왜 이겼나 / 왜 졌나는 결말이 난 건에만
                  있는 정보입니다. 보드 카드에도 같은 고르개가 있고, 값 목록과 「지금
                  단계의 값인가」 판단은 둘 다 서버에서 옵니다. 여기 둔 이유: 이 화면에서
                  대화를 다 읽고 결론을 내리는데, 그걸 적으려고 대시보드로 나가 카드를
                  찾아야 했습니다. */}
              {dealOptions && (
                <div className="info-row"><dt>Deal Detail</dt>
                  <dd>
                    <select className="select select--inline" value={ticket.deal_detail ?? ""}
                            aria-label={ticket.stage === "won" ? "Won Type" : "Lost Reason"}
                            onChange={(event) => void saveDealDetail(event.target.value)}>
                      <option value="">선택 안 함</option>
                      {dealOptions.map((option) => (
                        <option key={option} value={option}>{option}</option>
                      ))}
                    </select>
                  </dd></div>
              )}
            </dl>
          </div>

          {/* 허브스팟 Company 레코드. 카드가 곧 「레코드」, 줄이 곧 「필드」입니다.
              `contact` 로 표시된 묶음은 제 카드를 만들지 않고 아래 연락처 정보 카드에
              얹힙니다 — 같은 제목의 카드가 둘 서지 않도록. 그 키의 출처는 서버의
              `GROUPS`(`src/integrations/hubspot_record.py`)이고, 이름을 바꾸면 여기도
              같이 바뀌어야 합니다. */}
          {contact && hubspotPending && (
            <div className="card">
              <div className="section-label">플랜 정보</div>
              <div className="t-xs t-subtle" style={{ marginTop: 10 }}>
                <span className="spinner" role="status" /> 허브스팟에서 읽는 중
              </div>
            </div>
          )}

          {hubspot?.error && (
            <div className="card">
              <div className="section-label" style={{ marginBottom: 10 }}>플랜 정보</div>
              <p className="t-xs t-subtle" style={{ margin: 0 }}>{hubspot.error}</p>
            </div>
          )}

          {hubspot?.groups
            ?.filter((group) => group.key !== "contact")
            .map((group) => (
              <div className="card" key={group.key}>
                <div className="section-label" style={{ marginBottom: 12 }}>{group.title}</div>
                <dl className="info-list">
                  {group.rows.map((row) => <CompanyRow key={row.label} row={row} />)}
                </dl>
              </div>
            ))}

          {contact && (
            <div className="card">
              {/* 평소에는 읽기만 하는 카드입니다. 늘 펼쳐 둔 폼이 있으면 사이드바 절반이
                  입력칸이고, 저장 버튼은 누를 일이 없는 날에도 자리를 차지합니다. 연필을
                  누른 동안만 폼이 되고, 저장 버튼도 그때만 섭니다. */}
              <div className="row-between" style={{ marginBottom: 12 }}>
                <div className="section-label">연락처 정보</div>
                <button type="button" className="btn btn--subtle btn--sm"
                        onClick={() => setEditingContact((on) => !on)}
                        aria-pressed={editingContact}
                        aria-label={editingContact ? "연락처 수정 취소" : "연락처 수정"}
                        title={editingContact ? "수정 취소" : "수정"}>
                  <Icon name={editingContact ? "x" : "edit"} size={14} />
                </button>
              </div>
              <dl className="info-list">
                <div className="info-row"><dt>이름</dt><dd>{contact.name}</dd></div>
                {contact.email && (
                  <div className="info-row"><dt>이메일</dt>
                    <dd className="mono truncate" style={{ maxWidth: 170 }}>{contact.email}</dd></div>
                )}
                {contact.domain && (
                  <div className="info-row"><dt>도메인</dt>
                    <dd><Link className="mono" to={`/companies/${contact.domain}`}>{contact.domain}</Link></dd></div>
                )}
                {hubspot?.groups
                  ?.find((group) => group.key === "contact")
                  ?.rows.map((row) => <CompanyRow key={row.label} row={row} />)}
                {!editingContact && (
                  <div className="info-row"><dt>회사</dt>
                    <dd className="truncate">{contact.company || "—"}</dd></div>
                )}
              </dl>

              {!editingContact && contact.role_description && (
                <div style={{ marginTop: 12 }}>
                  <div className="field-label">하는 일 / 메모</div>
                  <p className="t-xs" style={{ margin: 0, whiteSpace: "pre-line" }}>
                    {contact.role_description}
                  </p>
                </div>
              )}

              {/* What the operator learns mid-conversation goes here — it is the only
                  place a gmail/unverified contact gets a company name at all. */}
              {editingContact && (
                <form onSubmit={saveContact} style={{ marginTop: 12 }}>
                  <label className="field-label" htmlFor="c-company">회사</label>
                  <input className="input" id="c-company" name="company"
                         defaultValue={contact.company ?? ""} style={{ marginBottom: 10 }} />
                  <label className="field-label" htmlFor="c-role">하는 일 / 메모</label>
                  <textarea className="textarea" id="c-role" name="role_description" rows={3}
                            defaultValue={contact.role_description ?? ""}
                            placeholder="이 고객·회사가 어떤 일을 하는지 (대화하며 알게 된 내용 포함). gmail·미확인이어도 입력해 저장됩니다." />
                  <button className="btn btn--subtle btn--sm" type="submit"
                          style={{ marginTop: 10, width: "100%" }}
                          disabled={savingContact} aria-busy={savingContact || undefined}>
                    {savingContact ? <><span className="spinner" role="status" /> 저장 중</>
                                   : <><Icon name="check" size={14} /> 연락처 저장</>}
                  </button>
                </form>
              )}
            </div>
          )}

          {msg && (
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
          )}

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

      {confirmSend && msg && (
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
                          onClick={() => act("send").then(() => setConfirmSend(false))}>
              <Icon name="check" size={15} /> 검토 완료 · 발송
            </ActionButton>
          }
        />
      )}

      {rejecting && msg && (
        <Modal
          title="이 초안을 거절합니다"
          description="거절하면 발송 대기에서 빠집니다. 사유는 처리경과에 남습니다."
          onClose={() => setRejecting(false)}
          actions={
            // 발송 모달과 같은 순서입니다 — 끝난 뒤에 닫습니다. 먼저 닫으면 "거절 중" 을
            // 볼 자리가 사라지고, 실패해도 목록만 그대로인 채 아무 말이 없습니다. 맨 버튼일
            // 때는 연타가 그대로 요청 두 번이기도 했습니다.
            <ActionButton className="btn btn--danger" pending="거절 중"
                          onClick={() => act("reject", { reason })
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

      {notice && (
        <Modal
          title={notice.title}
          onClose={() => setNotice(null)}
          actions={
            <button type="button" className="btn btn--ok" onClick={() => setNotice(null)}>
              확인
            </button>
          }
        >
          {notice.body}
        </Modal>
      )}

      {/* 보드 카드의 + 가 띄우는 것과 같은 모달, 같은 폼입니다. */}
      {logging && ticket.id && contact && (
        <Modal
          title="소통 기록 추가"
          description={`${contact.company || contact.name} · 이 문의에 대해 오간 연락을 남깁니다.`}
          wide
          onClose={() => setLogging(false)}
        >
          <div style={{ marginTop: 16 }}>
            <InteractionForm
              contactId={contact.id}
              conversationId={ticket.id}
              onSaved={() => {
                setLogging(false);
                void queryClient.invalidateQueries({ queryKey: key });
              }}
            />
          </div>
        </Modal>
      )}
    </>
  );
}
