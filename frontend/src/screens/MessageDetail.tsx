import { useLayoutEffect, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { getJSON, postForm } from "../lib/api";
import { kst } from "../lib/format";
import { Icon } from "../ui/Icon";
import { directionMark, interactionMark } from "../ui/InteractionForm";
import { Modal } from "../ui/Modal";
import { ConfirmModal } from "../ui/ConfirmModal";
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
  summary_line: string | null;
  language: string | null;
  created_at: string;
  sent_at: string | null;
  is_current: boolean;
};
/** 한 줄 = 운영자 표의 「필드」 하나. 값은 자유 입력이라 `truncate` 로 감쌉니다 —
 *  `.info-row` 는 flex 라 `plan-2026-kr-renewal` 같은 한 덩어리 글자가 320px 카드를
 *  뚫고 나갑니다. 옆의 이메일·수신자 줄이 같은 이유로 이미 그렇게 하고 있습니다. */
type RecordRow = {
  key: string; label: string; value: string | null; found: boolean; editable: boolean;
};

function CompanyRow({ row, editing }: { row: RecordRow; editing?: boolean }) {
  // 고칠 수 없는 줄은 수정 중에도 그냥 글자입니다 — 「국가」는 허브스팟이 접속 IP 로 뽑는
  // 값이고, 못 찾은 필드는 쓸 대상 자체가 없습니다.
  if (editing && row.editable) {
    return (
      <div className="info-row">
        <dt><label htmlFor={`hs-${row.key}`}>{row.label}</label></dt>
        <dd style={{ maxWidth: 168 }}>
          <input className="input" id={`hs-${row.key}`} name={row.key}
                 defaultValue={row.value ?? ""} style={{ height: 30, fontSize: 13 }} />
        </dd>
      </div>
    );
  }
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

/** 플랜·연락처 칸. **값은 우리 DB 에서 옵니다** (0094) — 티켓을 열 때마다 허브스팟을 읽던
 *  것을 그만뒀습니다. 저쪽에서 값이 들어오는 문은 셋입니다: 웹훅 · 10분 스윕 · 고객 상세의
 *  「HubSpot 동기화」(`src/agents/contact_sync.py`).
 *
 *  카드를 나누는 것도 줄 이름도 서버가 정합니다 — 필드가 늘 때 고칠 곳이 한 곳이어야
 *  합니다(`src/integrations/hubspot_record.py`). */
type HubSpotRecord = {
  /** 마지막으로 허브스팟에서 받아온 시각. 저쪽을 그때그때 읽던 시절에는 물어볼 필요가
   *  없던 질문이고, 지금은 화면이 답할 수 있어야 합니다 — 언제 것이냐가 곧 믿어도
   *  되느냐입니다. 한 번도 못 받아왔으면 `null`. */
  synced_at?: string | null;
  groups: {
    key: string;
    title: string;
    /** `found: false` 는 「그 회사에 값이 없다」가 아니라 「허브스팟에서 그 속성을 못 찾았다」
     *  입니다. 값이 빈 것은 `—` 로 서고(허브스팟 사이드바가 `--` 를 그리는 그 자리),
     *  못 찾은 것은 그렇다고 적습니다 — 앞엣것은 이 고객 이야기이고 뒤엣것은 설정
     *  이야기라, 화면에서 같아 보이면 안 됩니다. */
    rows: { key: string; label: string; value: string | null; found: boolean; editable: boolean }[];
    /** 이 카드에 연필을 달까. 못 찾은 필드는 쓸 수도 없으므로 서버가 그것까지 빼고 셉니다 —
     *  연필만 달아 두면 저장이 아무 일도 안 하고 성공한 척합니다. */
    editable: boolean;
  }[];
  error: string | null;
};

type Detail = {
  thread: Bubble[];
  category: string | null;
  category_label: string;
  unqualified: boolean;
  progress: { kind: string; detail: string; created_at: string }[];
  customer_requests: string | null;
  other_tickets: {
    conversation_id: number; ticket_id: string | null; subject: string | null;
    stage: string; created_at: string;
    /** 그 티켓에 오간 것마다 쌓인 요약(`conversations.summary`). 접수만 되고 아무 일도
     *  없었던 티켓에서는 접수 때 뽑은 `customer_requests` 로 떨어집니다. */
    summary: string | null;
  }[];
  won: {
    client_id: number; company: string; plan_status: string; department: string | null;
    contracts: { seq: number; state: string; deal_type: string | null;
                 starts_on: string | null; ends_on: string | null; currency: string | null;
                 total_amount: number | null; next_pay_on: string | null;
                 next_pay_amount: number | null }[];
  } | null;
  signatures: { key: string; name: string }[];
  ticket: {
    id: number | null; ticket_id: string | null; stage: string | null;
    /** Won Type / Lost Reason. 보드 카드와 같은 값 — 지금 단계의 목록에 있을 때만 옵니다. */
    deal_detail: string | null;
    inquiry_subject: string | null; inquiry_language: string | null; client_id: number | null;
    /** 티켓이 만들어진 날. 백필분은 허브스팟의 생성일 그대로입니다. */
    created_at: string | null;
  };
  ticket_interactions: Interaction[];
  /** 메일이 하나도 없는 티켓은 `null` 입니다 — HubSpot 에서 들여온 티켓이 그렇습니다. */
  msg: {
    id: number; status: string; send_error: string | null; subject: string; body: string; channel: string;
    /** 번역이 덮어쓰기 전의 한국어 초안. 번역 전에는 `null`. */
    body_ko: string | null;
    language: string | null; target_language: string | null; signature_key: string;
    /** 운영자가 고른 발신 계정 id. 빈 문자열이면 「안 고름」 = 스레드가 정합니다. */
    channel_account_id: string;
    to_address: string; score_snapshot: number | null; created_at: string;
    sent_at: string | null; scheduled_at: string | null; category: string | null;
  } | null;
  contact: { id: number; name: string; email: string | null; company: string | null; domain: string | null; role_description: string | null;
    /** MQL / PQL — 구독 플랜이 정합니다(플랜 없음·Free·N/A → MQL, 그 외 → PQL). 서버가
     *  계산해서 내려주므로 언제나 값이 있습니다. */
    qualification: string } | null;
  customer: {
    profile: Record<string, unknown> | null;
    /** 티켓에서 나왔는데 그 티켓이 지워진 기록 — 제목으로 다시 묶은 것. 살아 있는
     *  티켓과 같은 모양(제목 + 요약)으로 섭니다. */
    past_tickets: { subject: string; summary: string | null; count: number; last_at: string | null }[];
    /** **진짜 티켓이 없던** 접점의 개수 — 허브스팟 딜·노트, 고객 단위 메모, 수주 기록. */
    loose_count: number;
  } | null;
  stage_labels: Record<string, string>;
  /** 소통 히스토리를 남길 수 있는 단계 — 보드의 + 버튼과 같은 목록, 같은 출처. */
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

function isMostlyKoreanText(text: string): boolean {
  const letters = text.match(/\p{L}/gu) ?? [];
  if (letters.length === 0) return false;
  const hangul = letters.filter((ch) => /[가-힣ᄀ-ᇿ㄰-㆏]/u.test(ch)).length;
  return hangul / letters.length >= 0.5;
}

export function MessageDetail() {
  // 같은 화면에 문이 둘입니다. `/messages/:id` 는 회신 및 검토 목록에서 **그 초안**을 열 때,
  // `/tickets/:conversationId` 는 보드 카드에서 **티켓**을 열 때 — 뒤엣것은 메일이 하나도
  // 없는 티켓(HubSpot 에서 들여온 것)도 엽니다. 질의 키가 둘을 가릅니다.
  const { id, conversationId } = useParams();
  const navigate = useNavigate();
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
  // 고를 수 있는 발신 주소. 허브스팟에 물어야 나오므로 본문과 따로 받습니다 — 같이
  // 받으면 답을 읽는 일이 이 조회를 기다립니다. 못 가져오면 고르개가 안 뜰 뿐, 발송은
  // 예전대로 됩니다(스레드가 정합니다).
  const msgId = data?.msg?.id;
  const { data: senders } = useQuery({
    queryKey: ["reply-senders", msgId],
    queryFn: () => getJSON<{ senders: { id: string; address: string; is_default: boolean }[];
                             default_address: string;
                             fallback_address: string;
                             error: string | null }>(`/api/ui/messages/${msgId}/senders`),
    enabled: !!msgId,
    staleTime: 5 * 60_000,
  });

  const contactId = data?.contact?.id;
  const { data: hubspot, isPending: hubspotPending } = useQuery({
    queryKey: ["hubspot-record", contactId],
    queryFn: () => getJSON<HubSpotRecord>(`/api/ui/contacts/${contactId}/hubspot-record`),
    enabled: !!contactId,
    // 플랜은 티켓 하나 읽는 동안 바뀌지 않습니다.
    staleTime: 5 * 60_000,
  });

  const [editingContact, setEditingContact] = useState(false);
  // 저장 **전에** 묻습니다. 예전에는 끝난 뒤 「저장했습니다」를 띄웠는데, 그건 이미 벌어진
  // 일을 알려 줄 뿐이라 잘못 누른 사람에게는 쓸모가 없습니다 — 게다가 이 값들은 우리 DB 에서
  // 끝나지 않고 허브스팟까지 갑니다.
  const [confirm, setConfirm] = useState<
    { description: React.ReactNode; run: () => Promise<void> } | null
  >(null);
  const [editingRecord, setEditingRecord] = useState<string | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [signature, setSignature] = useState("");
  // 어느 주소에서 나갈까. 빈 문자열은 「고르지 않음」이고, 그때는 그 스레드에 이미 있던
  // 계정이 정합니다 — 예전 동작 그대로입니다.
  const [sender, setSender] = useState("");
  const [draftLanguage, setDraftLanguage] = useState("");
  // 번역 전의 한국어 초안. 번역은 되돌릴 수 없는 한 번의 누름이라, 무엇을 승인했는지
  // 다시 읽을 자리가 있어야 합니다.
  const [koreanDraft, setKoreanDraft] = useState<string | null>(null);
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
  async function saveContactFields(fields: Record<string, string>) {
    try {
      await postForm(`/contacts/${data?.contact?.id}/edit`, fields);
    } catch (error) {
      // 실패하면 말합니다. 확인 창을 지나온 뒤의 침묵은 「눌렀는데 아무 일도 안 일어난다」로
      // 읽혀서 한 번 더 누르게 만듭니다. 옆의 saveDealDetailNow 와 같은 모양입니다.
      setNotice({ title: "연락처를 저장하지 못했습니다", body: String(error) });
      return;
    }
    setEditingContact(false);
    await queryClient.invalidateQueries({ queryKey: key });
  }

  /** 플랜 값을 허브스팟에 되씁니다.
   *
   *  저장한 뒤 이 질의만 따로 무효화합니다 — 다른 저장들이 쓰는 일괄 무효화에서 허브스팟
   *  패널은 일부러 빼 두었기 때문입니다(우리가 저장한다고 저쪽 값이 바뀌지 않는데, 같이
   *  걸면 콘솔의 모든 저장이 열려 있는 티켓 탭마다 외부 왕복을 냅니다). 여기서는 저쪽 값이
   *  **정말로** 바뀌었으므로 다시 읽는 것이 맞습니다. */
  const [saveRecord, savingRecord] = useAction(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const fields = Object.fromEntries(new FormData(event.currentTarget) as never) as Record<string, string>;
    await postForm(`/contacts/${data?.contact?.id}/hubspot-record`, fields);
    setEditingRecord(null);
    // 「어디에」는 버튼이 아니라 결과가 말합니다 — 누를 때 알아야 할 것은 「저장한다」
    // 하나뿐이고, 어디에 갔는지는 누른 뒤에 알면 됩니다.
    setNotice({ title: "저장했습니다 (콘솔 · 허브스팟)", body: null });
    await queryClient.invalidateQueries({ queryKey: ["hubspot-record", data?.contact?.id] });
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
    setSender(data.msg.channel_account_id);
    setDraftLanguage(data.msg.language || "");
    setKoreanDraft(data.msg.body_ko);
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
  // **발송이 실패한 초안도 검토 중인 초안과 같은 화면입니다.** 실패는 「고객에게 아무것도
  // 안 갔다」는 뜻이라 아직 보낼 것이 남아 있고, 그러면 고칠 수 있어야 하고 보낼 수 있어야
  // 합니다. 예전에는 편집기 자체가 안 그려져서, 실패한 초안은 읽기 전용 말풍선으로 기록
  // 줄기에 섞여 들어가고 다시 보낼 길이 복구 화면밖에 없었습니다(2026-08-26 운영자 지시).
  const isDraftOpen =
    msg?.status === "pending_approval" || msg?.status === "send_failed";
  const sendFailed = msg?.status === "send_failed";
  // 보드가 어느 열에 + 를 그릴지 정하는 것과 **같은 목록**입니다. 서버가 주므로
  // 단계 이름이 바뀌어도 두 화면이 어긋나지 않습니다.
  const canLog = !!ticket.stage && data.manual_log_stages.includes(ticket.stage);
  // **New 는 다른 화면입니다.** 아직 아무 일도 안 일어난 티켓이라 요약도 처리 경과도
  // 보여 줄 것이 없고(있어야 「문의 접수」한 줄), 그 자리에 고객 요청사항만 둡니다.
  // `initial` 은 모델 기본값 — 단계가 아직 안 정해진 것이라 New 와 같이 봅니다.
  const afterNew = !!ticket.stage && !["new", "initial"].includes(ticket.stage);
  // **「문의 접수」는 처리 경과가 아닙니다.** 무엇을 요청했는지는 바로 위 요청사항 카드가,
  // 언제 왔는지는 왼쪽 문의 말풍선이 이미 말합니다. 그 줄 하나만 남는 티켓(=New)에서는
  // 카드 자체가 안 뜹니다 — 온 것밖에 없는데 「처리 경과」라는 이름의 카드를 하나 더
  // 세우는 셈이었습니다(2026-08-19 운영자 지시).
  //
  // 거르는 일은 이제 서버가 합니다(`ROUTINE_PROGRESS_KINDS`). 여기서 하던 시절에는 같은
  // 규칙이 두 화면에 각자 있었고, 고객 상세 쪽에만 빠져 있었습니다.
  const happened = data.progress;
  // 티켓 기록 한 줄기: 사람이 적은 기록 + 시스템이 남긴 사실. 시간순입니다 — 기록은
  // 읽는 순서가 곧 일어난 순서여야 합니다. **처리 경과가 여기 사는 이유**: 그것은 이
  // 티켓에 일어난 일이라(2026-08-20 운영자 지시) 오른쪽 참고 칸이 아니라 기록 줄기에
  // 속합니다 — 「메일이 나갔다 → 미팅했고 요구사항은 이것」이 한 이야기입니다.
  //
  // **New 를 지나면 문의·회신도 이 줄기에 들어옵니다** (2026-08-20 운영자 지시). 그때부터
  // 이 화면의 일은 초안을 고치는 것이 아니라 「무슨 이야기가 오갔나」를 읽는 것이고,
  // 우리가 주고받은 메일만 위에 따로 세워 두면 같은 시간축이 두 동강 납니다 — 허브스팟에서
  // 온 답과 우리 메일 중 무엇이 먼저였는지가 화면에서 안 보입니다. New 에서는 그대로
  // 위에 남습니다: 거기서는 원문을 읽고 초안을 쓰는 것이 본론입니다.
  // **첫 번째로 나간 답변**이 어느 줄인지. 그 한 줄만 「문의 회신」이고, 그 뒤의 우리
  // 메일은 「이메일 발송」입니다 — 「문의 회신」은 이 티켓에서 한 번 일어나는 사건이라
  // 두 번 세 번 적히면 어느 것이 그 사건인지 알 수 없습니다 (2026-08-26 운영자 지시).
  const firstReplyId = data.thread.find((b) => SENT.has(b.direction))?.id;
  // New 를 지나면 말풍선은 「이 티켓의 기록」 줄기로 내려가고, 위에는 검토 중인 초안만
  // 남습니다. 그 초안마저 없으면 그릴 것이 없습니다.
  const visibleBubbles = afterNew
    ? data.thread.filter((b) => b.is_current && isDraftOpen)
    : data.thread;
  // **이 티켓의 접점 기록은 여기 섭니다** (2026-09-04 운영자 지시: 「둘을 기존처럼 따로」).
  //
  // 하루 동안 반대로 돌려 봤습니다 — 이 목록에서 빼고 「리드 히스토리」가 티켓별로 묶어
  // 전부 보여 주게. 그건 답을 쓰는 화면에 메일함을 하나 더 세우는 일이었습니다. 지금은
  // 둘이 **세는 것 자체가 다릅니다**: 여기는 이 티켓에서 실제로 오간 것(메일 · 접점 기록 ·
  // 진행 기록), 「리드 히스토리」는 **다른 티켓들의 요약 한 문단씩**.
  const ticketLog = [
    ...data.ticket_interactions.map((item, index) => ({
      key: `i${item.id ?? index}`,
      at: item.happened_at || "",
      item,
      progress: null as string | null,
      bubble: null as Bubble | null,
    })),
    ...happened.map((entry, index) => ({
      key: `p${index}`,
      at: entry.created_at,
      item: null,
      progress: entry.detail,
      bubble: null as Bubble | null,
    })),
    ...(afterNew
      ? data.thread
          .filter((b) => !(b.is_current && isDraftOpen))
          .map((b) => ({
            key: `m${b.id}`,
            at: b.sent_at || b.created_at,
            item: null,
            progress: null as string | null,
            bubble: b,
          }))
      : []),
    // **최신순입니다** (2026-08-26 운영자 지시). 오래된 순이던 시절에는 기록이 쌓일수록
    // 방금 일어난 일이 스크롤 밑바닥으로 내려갔습니다 — 이 목록을 여는 이유는 대개
    // 「마지막으로 무슨 일이 있었나」이지 「처음에 무슨 일이 있었나」가 아닙니다.
  ].sort((a, b) => (b.at || "").localeCompare(a.at || ""));
  const canTranslate = !!msg?.target_language && msg.target_language !== "ko";
  const translationRequired = canTranslate && (
    draftLanguage.toLowerCase() !== msg?.target_language?.toLowerCase()
    || isMostlyKoreanText(body)
  );
  // Won 과 Lost 에만 있습니다. 목록도 「이 단계에 고르개가 붙는가」도 서버가 정합니다 —
  // 보드 카드와 같은 출처라 두 화면이 다른 값을 내놓을 수 없습니다.
  const dealOptions = ticket.stage ? data.deal_details[ticket.stage] : undefined;

  /** 고른 값을 그 자리에서 저장합니다. 저장 버튼을 따로 두지 않는 이유는 보드 카드와
   *  같습니다 — 값 하나짜리 고르개에 저장 버튼이 붙으면, 고르고 안 누른 상태가 생깁니다. */
  async function saveDealDetailNow(detail: string) {
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

  /** New 를 지난 티켓에 **직접 쓰는** 후속 회신. 모델을 부르지 않습니다.
   *
   *  자동 초안은 New 티켓에만 생기고 한 번 나가면 다시 안 생깁니다. 그 뒤의 대화는 지금까지
   *  전부 허브스팟에서 이뤄졌고, 그래서 이 화면에는 무엇이 오갔는지가 남지 않았습니다.
   *  서버가 빈 초안을 세우고(`POST /tickets/{id}/reply`), 그 글로 옮겨 가면 이미 있는
   *  편집기가 그대로 열립니다 — 편집·번역·승인·발송은 자동 초안과 같은 길입니다. */
  async function startReply() {
    if (!ticket.id) return;
    setNote("");
    try {
      const created = await postForm(`/tickets/${ticket.id}/reply`, {}).then((r) => r.json());
      await queryClient.invalidateQueries({ queryKey: key });
      navigate(`/messages/${created.message_id}`);
    } catch (error) {
      setNote(`실패: ${String(error)}`);
    }
  }

  // 되는 동안의 상태는 누른 버튼이 말합니다(ActionButton). 여기 남는 것은 결과뿐입니다 —
  // 진행 표시가 버튼과 다른 자리에 있으면 눌린 건지 몰라 한 번 더 누르게 됩니다.
  async function act(action: string, extra: Record<string, string> = {}) {
    if (!msg) return;
    setNote("");
    try {
      await postForm(`/messages/${msg.id}/${action}`,
                     { subject, body, signature_key: signature, channel_account_id: sender, ...extra });
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
      body: new URLSearchParams({ body, subject, signature_key: signature, channel_account_id: sender }),
    });
    const result = await response.json();
    if (result.error) return setNote(result.error);
    setBody(result.body);
    if (result.subject !== undefined) setSubject(result.subject);
    if (result.language) setDraftLanguage(result.language);
    setKoreanDraft(result.body_ko ?? null);
    setNote(result.translated ? `번역됨 → ${result.language}` : "번역할 내용이 없습니다.");
  }

  async function openPreview() {
    const response = await fetch("/messages/preview", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ body, signature_key: signature, channel_account_id: sender }),
    });
    // 실패 응답을 그대로 넣으면 「이메일 미리보기」 라는 제목 아래 오류 페이지가 메일처럼
    // 그려집니다 — 운영자는 그걸 고객에게 나갈 본문으로 읽습니다.
    if (!response.ok) {
      throw new Error(`미리보기를 만들지 못했습니다 (${response.status}).`);
    }
    setPreview(await response.text());
  }

  // **리드 히스토리는 한 벌이고 자리가 둘입니다** (2026-09-04 운영자 지시).
  // New 에서는 오른쪽 — 그때 본론은 초안이고 이건 그것을 쓰기 위한 참고입니다. New 를
  // 지나면 본론이 「이 사람과 무슨 이야기가 오갔나」로 바뀌므로 「이 티켓의 기록」 **아래**,
  // 본문 칼럼에 섭니다. 같은 것을 두 벌 적으면 한쪽만 고치는 날이 옵니다.
  // **리드 히스토리 — 이전 티켓의 요약만** (2026-09-04 운영자 지시).
  //        「세부 이메일 내용 아예 x」. 답을 쓰는 자리에서 필요한 것은 「이 사람과 전에
  //        무슨 이야기가 있었나」 한 문단이지 그때 오간 메일의 본문이 아닙니다. 본문은
  //        「전체보기」가 가는 고객 상세에 있고, 그 화면은 **같은 값을 같은 모양으로**
  //        그립니다(`CustomerDetail` 의 `ticket.summary`).
  //
  //        **지금 보고 있는 티켓은 안 넣습니다.** 그 요약 불릿은 바로 왼쪽 「이 티켓의
  //        기록」 각 줄의 둘째 줄과 **같은 문자열**이라(한 줄을 만들어 `messages.
  //        summary_line` 과 `conversations.summary` 에 같이 씁니다), 넣으면 2026-08-25 에
  //        지운 요약 카드가 그대로 부활합니다. 「**이전** 히스토리」라는 말도 그 뜻입니다.
  //
  //        머리글 오른쪽의 「전체보기」가 예전의 떠 있던 칩을 대신합니다 — 같은 곳으로
  //        가는데, 카드 안에 있으면 「무엇의 전체인가」가 붙습니다.
  const leadHistoryCard = contact && (
      <div className="card">
        <div className="section-header" style={{ marginBottom: 12 }}>
          <div className="section-header__l">
            <span className="section-header__icon"><Icon name="history" size={16} /></span>
            <div className="section-header__title">리드 히스토리</div>
          </div>
          <Link className="btn btn--subtle btn--sm" to={`/customers/${contact.id}`}>
            전체보기
          </Link>
        </div>
        {data.other_tickets.length === 0
       && !data.customer?.past_tickets?.length
       && !data.customer?.loose_count ? (
          /* **눈에 띄어야 합니다** (2026-09-04 운영자 지시). 「없다」는 이 화면에서
             판단에 쓰는 사실입니다 — 처음 연락하는 사람인지, 오래 이야기해 온
             사람인지가 답의 톤을 바꿉니다. 흐린 작은 글씨로 적으면 「아직 안
             불러왔다」로 읽힙니다. */
          <div className="empty">
            <div className="empty__text empty__text--lead">
              이전 히스토리가 존재하지 않습니다.
            </div>
          </div>
        ) : (
          <div className="stack" style={{ gap: 12 }}>
            {data.other_tickets.map((other) => (
              <Link key={other.conversation_id} className="link--plain"
                    to={`/tickets/${other.conversation_id}`}>
                <div className="row-between" style={{ gap: 8 }}>
                  <strong className="t-sm">{other.subject || "제목 없는 문의"}</strong>
                  <span className="tag">{data.stage_labels[other.stage] ?? other.stage}</span>
                </div>
                <div className="t-xs t-subtle">
                  {kst(other.created_at)}{other.ticket_id ? ` · #${other.ticket_id}` : ""}
                </div>
                {/* **자르지 않습니다** (2026-09-04 운영자 지시: 「전부 보여줌」).
                    오간 것마다 한 줄씩 쌓인 값이라 오래된 티켓은 길어질 수 있는데,
                    그게 그 티켓의 이야기 전부입니다. */}
                {other.summary && (
                  <div className="t-sm" style={{ marginTop: 4, whiteSpace: "pre-line" }}>
                    {other.summary}
                  </div>
                )}
              </Link>
            ))}
            {/* **지워진 티켓도 그 티켓끼리 섭니다** (2026-09-04 운영자 지시).
                허브스팟에서 티켓을 지우면 그 메일이 연락처 기록으로 옮겨지는데, 한
                덩어리로 쓸어 담으면 한 문의였던 메일 세 통이 출처 없는 세 건이 됩니다.
                티켓 행이 없어 묶는 열쇠는 제목이고, 그래서 눌러 갈 곳도 없습니다 —
                내용은 「전체보기」에 있습니다. 모양은 살아 있는 티켓과 같습니다. */}
            {data.customer?.past_tickets?.map((past) => (
              <div key={past.subject}>
                <div className="row-between" style={{ gap: 8 }}>
                  <strong className="t-sm">{past.subject}</strong>
                  <span className="tag">지난 티켓</span>
                </div>
                <div className="t-xs t-subtle">
                  {past.last_at ? kst(past.last_at) : ""} · {past.count}건
                </div>
                {past.summary && (
                  <div className="t-sm" style={{ marginTop: 4, whiteSpace: "pre-line" }}>
                    {past.summary}
                  </div>
                )}
              </div>
            ))}
            {/* **진짜 티켓이 없던 것만** 여기 셉니다 — 허브스팟 딜·노트, 손으로 적은
                고객 단위 메모, 수주 화면에서 적은 소통 기록. 줄로 설 자리는 없지만
                없는 척하면 고객 상세와 건수가 안 맞습니다. */}
            {!!data.customer?.loose_count && (
              <div className="t-xs t-subtle">
                티켓 외 {data.customer.loose_count}건
              </div>
            )}
          </div>
        )}
      </div>
  );

  return (
    <>
      {/* 나가는 문 둘. 왼쪽은 온 곳으로, 오른쪽은 **이 고객의 히스토리**입니다 —
          보드에서 티켓으로 들어오게 바뀌었으니, 고객 단위로 보고 싶을 때 갈 곳이
          여기 있어야 합니다. Deal Detail·소통 히스토리는 티켓의 값이라 이 화면이 먼저입니다. */}
      <div className="row-between" style={{ marginBottom: 14 }}>
        <Link to={conversationId ? "/" : "/messages"} className="chip">
          <Icon name="chevron" size={14} /> {conversationId ? "문의 대시보드" : "회신 및 검토 목록"}
        </Link>
      </div>

      {/* 큰 글씨는 **누구인가** 입니다. 오래 「문의와 답변 · 제목」이었고 티켓 번호가 맨 위
          태그였는데, 티켓 번호는 이 화면에서 답을 쓰는 데 한 번도 쓰이지 않습니다 — 허브스팟에
          가서 같은 티켓을 찾을 때나 씁니다. 그래서 회사 이름을 올렸고, 나머지는 전부
          내려갔습니다. 제목·티켓 번호·Client ID 는 오른쪽 티켓 정보 카드에 있습니다. */}
      <div className="page-header">
        <div>
          <div className="row wrap" style={{ gap: 10 }}>
            <h1 className="page-title page-title--lead">
              {contact?.company || contact?.name || ticket.inquiry_subject || "이름 없는 문의"}
            </h1>
            {/* **머리글은 이름 하나입니다** (2026-08-26 운영자 지시). 여기 있던 넷 —
                문의 언어 · 진행 기록 건수 · 상태 · 티켓 번호 · Client ID — 는 전부 이
                화면의 다른 자리가 이미 말합니다:

                  문의 언어    초안 편집기의 번역하기·발송 언어
                  진행 기록 n건 바로 아래 목록을 세면 나온다
                  상태         작성 중·발송 실패는 배너, 발송 대기는 편집기와 발송 버튼,
                               발송 완료는 기록의 그 줄
                  티켓 번호     오른쪽 티켓 정보 카드
                  Client ID    오른쪽 티켓 정보 카드

                머리글이 그것을 한 번 더 적으면 화면을 여는 사람이 같은 사실을 두 번 읽고,
                정작 「누구인가」가 그 틈에 묻힙니다. */}
          </div>
        </div>
      </div>

      {msg?.status === "drafting" && (
        <div className="banner banner--info mb-gap" role="status">
          <span className="banner__icon"><Icon name="sparkles" size={18} /></span>
          <div><div className="banner__title">답변 작성 중</div></div>
        </div>
      )}

      {/* 발송을 누른 자리가 여기이므로 실패한 이유도 여기에 섭니다. 배지만 「발송 실패」로
          바뀌던 시절에는 왜인지 알 방법이 화면에 없었습니다(2026-08-26). */}
      {(msg?.status === "send_failed" || msg?.status === "delivery_unknown") && msg.send_error && (
        <div className="banner banner--danger mb-gap" role="alert">
          <span className="banner__icon"><Icon name="warn" size={18} /></span>
          <div>
            <div className="banner__title">
              {msg.status === "send_failed" ? "발송 실패" : "발송 확인 필요"}
            </div>
            <div className="t-sm">{msg.send_error}</div>
            {/* 실패한 뒤에 할 수 있는 일은 둘입니다 — 글부터 다시 쓰거나(여기), 같은 글을
                그대로 다시 보내거나(아래 편집기의 「검토 완료 · 발송」). 실패가 배달 사고만은
                아니라서 둘 다 있어야 합니다: 초안 자체가 틀렸으면 다시 보내도 같은 것이
                나갑니다. */}
            {sendFailed && (
              <ActionButton className="btn btn--subtle btn--sm" style={{ marginTop: 10 }}
                            pending="다시 쓰는 중" onClick={() => act("redraft")}>
                <Icon name="refresh" size={14} /> 초안 다시 쓰기
              </ActionButton>
            )}
          </div>
        </div>
      )}

      <div className="split">
        <div className="stack">
          {/* 「회신 작성」 안내 상자가 여기 있었습니다 (2026-09-03 운영자 지시로 옮김).
              **자리를 늘 차지했습니다** — New 를 지난 티켓이면 초안이 없을 때마다 본문
              칼럼 맨 위에 네 줄짜리 상자가 서 있었고, 운영자 화면은 세로 640px 입니다.
              버튼은 아래 「이 티켓의 기록」 머리로 갔습니다: 「추가하기」 바로 옆이라
              **이 티켓에 무언가를 남기는 두 가지가 한자리**에 섭니다. */}
          {!afterNew && data.thread.length === 0 && (
            <div className="empty">
              <div className="empty__text">
                이 티켓에는 이 콘솔이 주고받은 메일이 없습니다 — HubSpot 에서 들여온
                티켓입니다. 오간 연락은 아래 「이 티켓의 기록」에 남겨주세요.
              </div>
            </div>
          )}
          {/* **그릴 것이 없으면 이 상자 자체를 안 그립니다.** `.stack` 은 flex 라 빈 자식도
              간격 하나를 차지합니다 — 초안이 닫힌 티켓에서 「이 티켓의 기록」이 오른쪽
              카드보다 딱 그만큼 내려가 있던 이유입니다 (2026-08-26 운영자 지적). */}
          {visibleBubbles.length > 0 && (
          <div className="thread">
            {visibleBubbles.map((bubble) => {
              if (bubble.is_current && isDraftOpen) {
                return (
                  <div key={bubble.id} className="bubble bubble--out bubble--current">
                    <div className="bubble__head">
                      {/* 이 칸의 글이 곧 고객이 받는 글입니다 — 예전처럼 「검토용 한국어」가
                          아닙니다. 한국어 대역은 아래 접힌 줄에 저장돼 있습니다. */}
                      <span className="bubble__dir">
                        <Icon name="send" size={14} /> 문의 회신 초안
                        {/* **원어를 같이 적습니다** (2026-09-03 운영자 지시). 초안은 이제
                            나갈 언어로 쓰이므로(0045 이후) 「이 글이 무슨 말로 쓰여 있나」가
                            제목 옆에 있어야 합니다. 값은 문의가 들어온 언어입니다 —
                            `msg.language` 는 「번역하기」를 누르면 바뀌는 값이라, 제목 옆에
                            두면 같은 티켓이 누를 때마다 다른 말을 합니다.
                            `.chip--xs` 를 씁니다 — 누를 수 없는 짧은 값이고, 옆의
                            아이콘·시각과 크기가 맞습니다. */}
                        {ticket.inquiry_language && (
                          <span className="chip chip--xs" style={{ marginLeft: 6 }}>
                            {languageLabel(ticket.inquiry_language)}
                          </span>
                        )}
                      </span>
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
                    {/* 한국어 대역. **초안 때 한 번 만들어 행에 저장한 것**이라 여기를
                        펼쳐도 모델을 부르지 않습니다. 접어 두는 이유는 지금 고치는 것이
                        나갈 본문이고 이것은 대조용이기 때문입니다. */}
                    {koreanDraft && !isMostlyKoreanText(body) && (
                      <details style={{ marginTop: 10 }}>
                        <summary className="t-xs t-subtle" style={{ cursor: "pointer" }}>
                          <Icon name="translate" size={12} /> 한국어로 보기
                        </summary>
                        <div className="msg-body msg-body--inset" style={{ marginTop: 6 }}>
                          {koreanDraft}
                        </div>
                      </details>
                    )}

                    {/* 골라야 붙습니다. 예전에는 여기에 "기본 (텍스트 서명)" 이 하나 더
                        있었는데, 그건 모델이 본문에 써 넣은 서명을 그대로 두라는 뜻이었습니다
                        — 고르지 않아도 서명이 붙던 자리입니다. 이제 없습니다. */}
                    {/* **어느 주소에서 나가나.** 예전에는 고를 수 없었습니다 — 그 스레드에
                        이미 있던 계정이 정했고, 화면에는 그게 무엇인지도 안 보였습니다.
                        목록은 서버가 만듭니다(`/senders`): 그 스레드의 인박스에 연결된
                        살아 있는 주소만 들어갑니다. 화면이 스스로 목록을 지으면 고를 수는
                        있는데 발송이 거절하는 값이 생깁니다. */}
                    {/* **고르개가 안 뜨는 이유는 화면에 적습니다** (2026-09-03).
                        예전에는 조회가 실패하면 라우트가 `{senders: [], error}` 로 200 을
                        돌려주는데 화면이 그 `error` 를 아무 데도 안 그려서, 고르개가 이유
                        없이 사라졌습니다 — 운영자는 「왜 안 뜨지」밖에 알 수 없었습니다.
                        고를 것이 없는 것과 못 가져온 것은 다른 이야기입니다. */}
                    {(senders?.senders?.length ?? 0) === 0 && senders?.error && (
                      <div className="t-xs t-subtle" style={{ marginTop: 12 }}>
                        발신 주소를 고를 수 없습니다 — {senders.error}
                      </div>
                    )}
                    {(senders?.senders?.length ?? 0) > 0 && (
                      <>
                        <label className="field-label" htmlFor="msg-sender"
                               style={{ marginTop: 12 }}>발신 주소</label>
                        <select className="select" id="msg-sender" value={sender}
                                onChange={(e) => setSender(e.target.value)}>
                          {/* **「자동」이 무엇인지 서버가 말해 줍니다.** 목록에서 찾지
                              않는 이유: 기본값이 고르개에 없는 주소일 때가 있습니다(허브스팟
                              기계 주소, 또는 운영자가 고르개에서 뺀 주소) — 목록에서만
                              찾으면 그 티켓은 어느 주소로 나갈지가 화면에 안 적힙니다.

                              **「이 대화의 주소」 같은 두루뭉술한 말은 안 씁니다**
                              (2026-09-03 운영자 지시). 주소를 못 가져왔으면 못 가져왔다고
                              적습니다 — 그건 조회가 실패했다는 뜻이라 다른 이야기입니다. */}
                          <option value="">
                            {senders?.default_address
                              ? `자동 — ${senders.default_address}`
                              : "자동 (발신 주소를 확인하지 못했습니다)"}
                            {senders?.default_address && senders?.fallback_address
                              ? ` (거절되면 ${senders.fallback_address})` : ""}
                          </option>
                          {senders?.senders?.map((x) => (
                            <option key={x.id} value={x.id}>{x.address}</option>
                          ))}
                        </select>
                      </>
                    )}

                    <label className="field-label" htmlFor="msg-signature" style={{ marginTop: 12 }}>서명</label>
                    <select className="select" id="msg-signature" value={signature}
                            onChange={(e) => setSignature(e.target.value)} style={{ marginBottom: 12 }}>
                      <option value="">서명 없음</option>
                      {data.signatures.map((s) => (
                        <option key={s.key} value={s.key}>{s.name}</option>
                      ))}
                    </select>

                    <div className="action-bar">
                      {translationRequired ? (
                        <ActionButton className="btn btn--subtle" pending="번역 중" onClick={translate}>
                          <Icon name="translate" size={15} /> 번역하기 ({msg.target_language})
                        </ActionButton>
                      ) : (
                        <button type="button" className="btn btn--ok"
                                aria-haspopup="dialog" onClick={() => setConfirmSend(true)}>
                          <Icon name="check" size={15} /> 검토 완료 · 발송
                        </button>
                      )}
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
              const label = inbound
                ? "고객 문의"
                : bubble.is_auto_ack
                  ? "자동 접수확인 (승인 없이 발송)"
                  : "회신";
              return (
                <div key={bubble.id} className={`bubble bubble--${inbound ? "in" : "out"}${bubble.is_current ? " bubble--current" : ""}`}>
                  <div className="bubble__head">
                    <span className="bubble__dir">
                      <Icon name={inbound ? "inbound" : bubble.is_auto_ack ? "sparkles" : "send"} size={14} />{" "}
                      {label}
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
          )}

          {/* This ticket's manual touchpoints — everything after the first reply happens
              off HubSpot, so the operator types it in and the ticket keeps one history.

              폼은 펼쳐 두지 않고 `추가하기` → 모달입니다. 이 화면의 일은 초안을 읽고
              보내는 것이고, 서른 줄짜리 입력 폼이 그 사이에 늘 끼어 있을 이유가 없습니다.
              문의 대시보드 카드의 + 버튼이 띄우는 것과 같은 모달·같은 폼입니다.

              단계가 아직 New 면 버튼도 없습니다. 검토할 초안이 있다는 것 자체가 아직
              아무 답도 안 나갔다는 뜻이라 적을 소통이 없습니다 — 보드에서 New 열에만
              + 버튼이 없는 것과 같은 규칙이고, 목록도 서버가 주는 같은 것을 씁니다. */}
          {/* **New 는 다른 화면입니다** (2026-08-20 운영자 지시). 아직 아무 일도 안 일어난
              티켓이라 「이 티켓의 기록」이 있을 수 없습니다 — 한동안 허브스팟에서 가져온
              그 사람의 **옛** 메일이 시각만 보고 여기 붙었는데, 몇 달 전 다른 이야기가
              방금 온 문의의 기록으로 그려졌습니다(0084 가 그 연결을 풀었습니다).
              New 에서 이 화면의 일은 초안을 읽고 보내는 것 하나입니다. */}
          {afterNew && ticket.id && contact && (
            <div className="card" id="log">
              <div className="section-header" style={{ marginBottom: 12 }}>
                <div className="section-header__l">
                  <span className="section-header__icon"><Icon name="history" size={16} /></span>
                  <div className="section-header__title">이 티켓의 기록</div>
                </div>
                <div className="row" style={{ gap: 6 }}>
                  {/* **메일과 기록이 한자리에 섭니다** (2026-09-03 운영자 지시).
                      이 티켓에 무언가를 남기는 길이 둘인데 화면의 양 끝에 떨어져
                      있었습니다 — 메일은 본문 위 안내 상자, 기록은 여기.

                      초안이 열려 있으면 안 그립니다: 그때는 위에 편집기가 이미 있고,
                      티켓 하나에 초안이 둘이면 어느 것이 나갈지 화면만 봐서는 모릅니다. */}
                  {!isDraftOpen && ticket.ticket_id && (
                    <ActionButton className="btn btn--subtle btn--sm" pending="여는 중"
                                  onClick={startReply}>
                      <Icon name="mail" size={14} /> 메일 발송
                    </ActionButton>
                  )}
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
              </div>
              <div className="history-list">
                {ticketLog.length === 0 ? (
                  <div className="empty"><div className="empty__text">아직 기록이 없습니다. 회신 이후의 연락은 여기에 남겨주세요.</div></div>
                ) : (
                  ticketLog.map((entry) =>
                    entry.progress ? (
                      <div key={entry.key} className="t-xs t-subtle"
                           style={{ padding: "6px 0", borderTop: "1px solid var(--border)" }}>
                        <span className="tnum">{kst(entry.at)}</span>{" · "}{entry.progress}
                      </div>
                    ) : entry.bubble ? (
                      <MessageRow key={entry.key} bubble={entry.bubble}
                                  isFirstReply={entry.bubble.id === firstReplyId} />
                    ) : (
                      <InteractionItem key={entry.key} item={entry.item as Interaction}
                                       hideSubject hideHandler />
                    ),
                  )
                )}
              </div>
            </div>
          )}
          {/* New 를 지나면 본론이 「이 사람과 무슨 이야기가 오갔나」로 바뀝니다 —
              그래서 「이 티켓의 기록」 바로 아래, 본문 칼럼에 섭니다 (2026-09-04 운영자 지시). */}
          {afterNew && leadHistoryCard}
        </div>

        <div className="stack" style={{ gap: "var(--gap)" }}>
          {/* **고객 요청사항은 늘 맨 위, 시각 없이.** New 티켓에는 이것 말고 보여 줄
              것이 없습니다 — 처리 경과라고 해 봐야 「문의 접수」한 줄이고, 그 줄의 시각은
              바로 위 문의 말풍선에 이미 있습니다. 그래서 New 는 이 카드 하나로 끝나고,
              단계가 넘어가면 그 아래로 실제로 일어난 일(발송·가드)과 요약이 붙습니다
              (2026-08-19 운영자 지시). */}
          {data.customer_requests && (
            <div className="card">
              <div className="section-label" style={{ marginBottom: 8 }}>고객 요청사항</div>
              <div className="t-sm" style={{ lineHeight: 1.7, whiteSpace: "pre-line" }}>
                {data.customer_requests}
              </div>
            </div>
          )}

          {/* 여기 있던 요약 카드는 뺐습니다 (2026-08-25 운영자 지시). 그 불릿들은
              「이 티켓의 기록」 각 줄의 **둘째 줄과 같은 문자열**입니다 — 한 줄을 만들어
              메시지 행(`messages.summary_line`)과 `conversations.summary` 에 **같이**
              쓰기 때문입니다(`summaries.append_line`). 한 화면이 같은 말을 두 번 했고,
              한쪽에는 시각도 방향도 없었습니다.
              **값은 그대로 쌓입니다** — 초안 프롬프트가 「기존 대화 요약」으로 읽습니다
              (`inbound.py`). 그래서 지운 것은 카드뿐이고, 이 화면은 그 값을 이제 안
              받습니다. */}

          {/* New 에서는 오른쪽입니다 — 그때 본론은 초안이고, 이건 그 초안을 쓰기 위한
              참고입니다 (2026-09-04 운영자 지시). */}
          {!afterNew && leadHistoryCard}

          <div className="card">
            <div className="section-label" style={{ marginBottom: 12 }}>티켓 정보</div>
            <dl className="info-list">
              <div className="info-row"><dt>티켓</dt><dd className="mono">{ticket.ticket_id ? `#${ticket.ticket_id}` : "— (없음)"}</dd></div>
              <div className="info-row"><dt>Client ID</dt><dd className="tnum">{ticket.client_id ?? "미동기화"}</dd></div>
              {/* 티켓이 만들어진 날 (2026-09-03 운영자 요청). 백필이 허브스팟의 생성일을
                  그대로 복사해 두므로 우리 값이 곧 허브스팟 값입니다 — 이 화면을 열 때마다
                  허브스팟에 물으러 가지 않습니다. */}
              {ticket.created_at && (
                <div className="info-row"><dt>생성</dt><dd className="tnum">{kst(ticket.created_at)}</dd></div>
              )}
              {/* 「발송 정보」 카드를 지우면서(2026-09-03 운영자 지시) **수신자 한 줄만**
                  여기로 옮겼습니다. 나머지(채널·발송 언어·생성)는 다른 데서도 볼 수 있는데
                  수신 주소는 이 콘솔에서 볼 곳이 여기와 발송 확인 창뿐이었습니다 — 확인
                  창은 초안이 열려 있을 때만 잠깐 뜨므로, 이미 나간 메일의 수신 주소를 볼
                  자리가 통째로 사라질 뻔했습니다. */}
              {msg?.to_address && (
                <div className="info-row"><dt>수신자</dt>
                  <dd className="mono truncate" style={{ maxWidth: 170 }}>{msg.to_address}</dd>
                </div>
              )}
              {msg?.sent_at && (
                <div className="info-row"><dt>발송</dt><dd className="tnum">{kst(msg.sent_at)}</dd></div>
              )}
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
                            onChange={(event) => {
                              const detail = event.target.value;
                              setConfirm({
                                description: (
                                  <>
                                    Deal Detail 을 <strong>{detail || "선택 안 함"}</strong> 로
                                    바꿉니다.
                                  </>
                                ),
                                run: () => saveDealDetailNow(detail),
                              });
                            }}>
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
            .map((group) => {
              const editing = editingRecord === group.key;
              return (
                <div className="card" key={group.key}>
                  <div className="row-between" style={{ marginBottom: 12 }}>
                    <div className="section-label">{group.title}</div>
                    {group.editable && (
                      <button type="button" className="btn btn--subtle btn--sm"
                              onClick={() => setEditingRecord(editing ? null : group.key)}
                              aria-pressed={editing}
                              aria-label={editing ? `${group.title} 수정 취소` : `${group.title} 수정`}
                              title={editing ? "수정 취소" : "수정"}>
                        <Icon name={editing ? "x" : "edit"} size={14} />
                      </button>
                    )}
                  </div>
                  {/* 저장은 허브스팟 연락처로 갑니다 — 이 화면에서 유일하게 바깥으로 쓰는
                      폼입니다. 제품 쪽 연동이 100% 가 아니라 사람이 채워야 할 때가 있어
                      열었습니다(운영자 판단). 안전 모드는 서버가 봅니다. */}
                  <form onSubmit={(event) => void saveRecord(event)}>
                    <dl className="info-list">
                      {group.rows.map((row) => (
                        <CompanyRow key={row.key} row={row} editing={editing} />
                      ))}
                    </dl>
                    {/* **언제 것인지가 곧 믿어도 되느냐입니다.** 값이 있을 때만 적습니다 —
                        한 번도 못 받아온 상태를 한 줄로 설명할 이유는 없습니다. */}
                    {hubspot?.synced_at && (
                      <div className="t-xs t-subtle" style={{ marginTop: 10 }}>
                        마지막 HubSpot 수신 {kst(hubspot.synced_at)}
                      </div>
                    )}
                    {editing && (
                      <button className="btn btn--subtle btn--sm" type="submit"
                              style={{ marginTop: 12, width: "100%" }}
                              disabled={savingRecord} aria-busy={savingRecord || undefined}>
                        {savingRecord ? <><span className="spinner" role="status" /> 저장 중</>
                                      : <><Icon name="check" size={14} /> 저장</>}
                      </button>
                    )}
                  </form>
                </div>
              );
            })}

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
                {/* 이 사람이 리드인지 제품을 쓰는 고객인지 — 구독 플랜이 정합니다
                    (2026-09-02 운영자 지시). 옆 「플랜 정보」 카드가 그 플랜을 들고
                    있으므로 두 카드가 같은 사실의 두 면입니다. 허브스팟에는 대응 속성이
                    없어 그 카드의 행으로 넣지 않았습니다 — 저기 서면 「허브스팟이 아는
                    값」으로 읽히고, 고칠 수 있는 칸처럼 보입니다. */}
                <div className="info-row"><dt>MQL / PQL</dt><dd>{contact.qualification}</dd></div>
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
                <form
                  style={{ marginTop: 12 }}
                  onSubmit={(event) => {
                    event.preventDefault();
                    // FormData 는 이 시점의 스냅숏입니다 — 확인 창을 지나면
                    // event.currentTarget 은 이미 없습니다.
                    const fields = Object.fromEntries(
                      new FormData(event.currentTarget) as never,
                    ) as Record<string, string>;
                    setConfirm({
                      description: (
                        <>
                          이 고객의 회사와 메모를 저장합니다. 회사:{" "}
                          <strong>{fields.company?.trim() || "—"}</strong>
                        </>
                      ),
                      run: () => saveContactFields(fields),
                    });
                  }}
                >
                  <label className="field-label" htmlFor="c-company">회사</label>
                  <input className="input" id="c-company" name="company"
                         defaultValue={contact.company ?? ""} style={{ marginBottom: 10 }} />
                  <label className="field-label" htmlFor="c-role">하는 일 / 메모</label>
                  <textarea className="textarea" id="c-role" name="role_description" rows={3}
                            defaultValue={contact.role_description ?? ""}
                            placeholder="이 고객·회사가 어떤 일을 하는지 (대화하며 알게 된 내용 포함). gmail·미확인이어도 입력해 저장됩니다." />
                  <button className="btn btn--subtle btn--sm" type="submit"
                          style={{ marginTop: 10, width: "100%" }}>
                    <Icon name="check" size={14} /> 저장
                  </button>
                </form>
              )}
            </div>
          )}

          {/* 「발송 정보」 카드는 지웠습니다 (2026-09-03 운영자 지시). 채널은 행마다
              `email` 한 값이고, 발송 언어는 「번역하기」 버튼이 이미 적으며, 생성 시각은
              초안 말풍선에 붙어 있습니다. 살아남을 이유가 있던 **수신자와 발송 시각은 위
              「티켓 정보」로 옮겼습니다.** 발송 실패 사유는 애초에 이 카드에 없었습니다 —
              빨간 배너가 따로 그립니다. */}

          {/* **이 티켓 밖의 접점은 한 곳입니다.** 한동안 「다른 접점 기록」과 「고객
              히스토리」 두 카드가 거의 같은 것을 나눠 들고 있었습니다 — 앞엣것은 이 티켓을
              뺀 모든 기록, 뒤엣것은 그중 어느 티켓에도 안 달린 것. 읽는 사람에게는 둘 다
              「이 사람과 전에 오간 것」이라 가를 이유가 없습니다. 앞엣것 하나로 남깁니다.
              허브스팟에서 끌어온 옛 메일도 여기 들어옵니다. */}
        </div>
      </div>

      {confirm && (
        <ConfirmModal
          description={confirm.description}
          onConfirm={confirm.run}
          onClose={() => setConfirm(null)}
        />
      )}

      {confirmSend && msg && (
        <Modal
          title="발송하시겠습니까?"
          description={
            <>
              승인 즉시 <strong>{msg.channel}</strong>로 발송됩니다.
              {msg.to_address && <> 수신자: <span className="mono">{msg.to_address}</span>.</>}
              {msg.target_language && <><br />확인한 {msg.target_language} 번역문이 그대로 발송됩니다.</>}
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
          title="히스토리 추가"
          hideCancel
          wide
          onClose={() => setLogging(false)}
        >
          <div style={{ marginTop: 16 }}>
            <InteractionForm
              onCancel={() => setLogging(false)}
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

/** 우리가 주고받은 메일 한 줄 — 소통 기록과 **같은 모양**으로 그립니다.
 *
 *  New 를 지난 티켓에서 이 줄은 「이 티켓의 기록」 안에 삽니다. 우리 메일만 다른 모양이면
 *  같은 시간축에 있어도 두 목록으로 읽히고, 「허브스팟에서 온 답과 우리 메일 중 무엇이
 *  먼저였나」가 안 보입니다. 방향 색도 같은 것을 씁니다(문의 접수 파랑 · 문의 회신 청록).
 *
 *  줄이 아직 없는 옛 메일은 본문 앞머리를 대신 씁니다 — 접히기는 해야 목록이 됩니다.
 *
 *  **제목 줄은 없습니다** (2026-08-25 운영자 지시). 이 목록은 스레드 하나라 줄마다 같은
 *  제목이 반복되고, 우리 메일 줄은 그 제목을 번역해서 쓰기 때문에 같은 제목이 원문·국문
 *  으로 나란히 놓여 다른 두 건처럼 보였습니다. 소통 기록 줄도 같이 껐습니다
 *  (`InteractionItem hideSubject`). */
/** 메일 한 줄. **말이 셋으로 갈립니다** (2026-08-26 운영자 지시):
 *
 *   고객이 보낸 것        → 「문의 접수」  + 허브스팟 마크
 *   우리가 보낸 **첫** 답 → 「문의 회신」
 *   그 뒤의 우리 메일     → 「이메일 발송」
 *
 *  앞의 둘은 이 티켓에서 한 번씩 일어나는 사건이라 이름이 있고, 그 뒤로는 그냥 오가는
 *  것이라 사람이 적는 기록과 같은 말(채널 + 방향)을 씁니다. 셋을 다 「문의 접수/회신」로
 *  적으면 그 두 사건이 어느 줄인지 목록에서 사라집니다.
 */
function MessageRow({ bubble, isFirstReply = false }: {
  bubble: Bubble;
  isFirstReply?: boolean;
}) {
  const sent = SENT.has(bubble.direction);
  const dir = sent && !isFirstReply
    ? interactionMark("email", "outgoing")
    : directionMark(bubble.direction);
  // **이름 붙은 두 사건이 허브스팟 마크를 답니다** — 문의 접수와 문의 회신. 둘 다 저쪽
  // 스레드에서 일어난 일이라서입니다: 문의가 거기로 왔고, 첫 답이 거기로 나갔습니다.
  // 그 뒤로 오간 것은 운영자가 고른 채널의 아이콘을 답니다 (2026-08-26 운영자 지시).
  const fromHubspot = !sent || isFirstReply;
  // 본문 없이 제목만 있는 메일이 있습니다(제목이 곧 문의 전부인 경우). 제목 줄을 안
  // 그리므로 그때는 제목이 본문 자리에 옵니다 — 아니면 빈 줄이 됩니다.
  const title = bubble.subject_ko || bubble.subject || "";
  // `body_ko` 는 **번역해서 보여 주는 고객 문의**의 자리입니다(`needs_ko`). 우리 회신에도
  // 같은 칸이 차는데(번역 전 한국어 초안), 거기서 그 값을 쓰면 이 줄이 고객에게 실제로 나간
  // 글 대신 그 전 판본을 보여 줍니다.
  const body = ((bubble.needs_ko ? bubble.body_ko : "") || bubble.body || "").trim() || title;
  const preview = (bubble.summary_line || body).replace(/\s+/g, " ");
  return (
    <article className={`history-item history-item--${dir.tone}`}>
      <div className="history-item__rail"><span /></div>
      <div>
        <details>
          <summary style={{ cursor: "pointer", listStyle: "none" }}>
            <div className="row wrap" style={{ gap: 6 }}>
              <span className="t-xs history-item__dir">
                <span style={fromHubspot ? { color: "#ff7a59" } : undefined}>
                  <Icon name={bubble.is_auto_ack ? "sparkles" : fromHubspot ? "hubspot" : dir.icon}
                        size={13} />
                </span>
                {dir.label}
              </span>
              {bubble.is_auto_ack && <span className="tag">자동 접수확인</span>}
              <time className="t-xs t-subtle tnum">
                {kst(bubble.sent_at || bubble.created_at)}
              </time>
            </div>
            <div className="t-sm t-subtle" style={{ lineHeight: 1.6 }}>
              {preview.slice(0, 120)}
              {preview.length > 120 ? "… " : " "}
              <span style={{ color: "var(--accent)" }}>전체보기</span>
            </div>
          </summary>
          <div className="history-item__body" style={{ marginTop: 8 }}>{body}</div>
          {/* 번역해서 보여 준 메일은 원문도 같이 둡니다 — 숫자와 고유명사는 원문이 기준입니다. */}
          {bubble.needs_ko && bubble.body_ko && (
            <div className="msg-body--inset" style={{ marginTop: 8 }}>
              <div className="ko-block__label t-subtle">원문 ({bubble.language || "original"})</div>
              <div className="history-item__body">{bubble.body}</div>
            </div>
          )}
        </details>
      </div>
    </article>
  );
}

/** 우리가 보낸 메일의 방향 값들. 옛 행이 `outbound` 를 들고 있습니다. */
const SENT = new Set(["outgoing", "outbound"]);

/** 언어 코드를 사람이 읽는 말로. 값은 `ko`·`en` 같은 두 글자 코드라 그대로 두면 칩에
 *  소문자 두 글자가 뜹니다. 표를 크게 만들지 않는 이유: 여기 뜰 수 있는 언어는 문의가
 *  실제로 들어온 언어이고, 목록을 늘려 봐야 안 오는 말이 대부분입니다 — 모르는 코드는
 *  대문자로 적으면 그 자체로 읽힙니다(`PT`·`ES`). */
const LANGUAGE_LABELS: Record<string, string> = {
  ko: "한국어", en: "English", ja: "日本語", zh: "中文",
};
function languageLabel(code: string) {
  const key = code.trim().toLowerCase();
  return LANGUAGE_LABELS[key] ?? code.trim().toUpperCase();
}

