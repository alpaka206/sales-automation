import { useState } from "react";
import { Icon } from "./Icon";
import { postForm } from "../lib/api";
import { kst } from "../lib/format";
import { SubmitButton, useAction } from "./ActionButton";

// The 소통·미팅 기록 form, defined ONCE — the port of partials/interaction_form.html.
// After the first reply the thread leaves HubSpot and the customer answers wherever they
// like, so the operator types what happened. Three surfaces offer it and they must ask
// for the same fields or the records cannot be read as one history.
// **고를 수 있는 것과 이미 저장된 것은 다른 목록입니다** (2026-09-03 운영자 지시로 갈랐습니다).
//
// `CHANNELS` 는 폼의 고르개에 뜨는 「타입」이고, `CHANNEL_LABELS` 는 **줄에 글자를 찍는
// 사전**입니다. 하나로 두면 고르개에서 뺀 값이 그 순간 화면에서 영어 키로 뒤집힙니다 —
// `channelLabel` 이 못 찾은 값을 그대로 돌려주기 때문입니다. 그리고 뺀 값들은 지금도
// 계속 쌓입니다: `hubspot` 은 `customer_ops` 가, 「채팅」·「폼」은 허브스팟 수집기
// (`agents/ticket_history`)가 넣습니다. 사람이 못 고를 뿐 화면에는 계속 떠야 합니다.
//
// **저장되는 키는 절대 바꾸지 마십시오.** `meeting` 은 글자가 아니라 규칙입니다 —
// `customer_ops.py` 의 `if channel == "meeting"` 이 「미팅을 적으면 티켓이 협상 중으로
// 넘어간다」이고, 키를 바꾸면 그 규칙이 **아무 표시 없이** 죽습니다.
export const CHANNELS: [string, string][] = [
  ["email", "이메일"], ["whatsapp", "WhatsApp"], ["phone", "전화"], ["sms", "문자"],
  ["meeting", "미팅 진행"], ["invoice", "Invoice 발송"], ["manual", "메모"],
];
const CHANNEL_LABELS: Record<string, string> = {
  ...Object.fromEntries(CHANNELS),
  // 고르개에서 내렸지만 행에는 남아 있는 값들.
  kakao: "카카오톡", hubspot: "HubSpot", contract: "계약",
};
function channelLabel(value: string) {
  return CHANNEL_LABELS[value] ?? value;
}

/** 방향을 묻는 타입 — **이메일뿐입니다** (2026-09-03 운영자 지시). 전화·미팅·메모는 한 건을
 *  통째로 적는 기록이라 발송·수신으로 갈리지 않습니다.
 *
 *  **한글 철자도 같이 넣습니다.** 이 표의 가장 큰 기록자는 폼이 아니라 허브스팟 스레드
 *  수집기이고, 그것은 채널을 **한글 라벨로** 저장합니다(`agents/ticket_history._channel_label`:
 *  채팅 · 이메일 · 폼 · 기타). `"email"` 만 보면 수입된 메일 수백 줄이 「이메일 발송」에서
 *  그냥 「이메일」로 주저앉고, 방향은 화살표 색으로만 남습니다. */
export const DIRECTIONAL_CHANNELS = new Set(["email", "이메일"]);

/** 기록을 **티켓별로** 묶습니다. 티켓 순서는 최신이 위이고, 어느 티켓에도 안 달린 기록은
 *  맨 끝에 「티켓과 무관」으로 모읍니다.
 *
 *  묶는 일을 화면이 하는 이유: 서버는 기록을 시간순으로 주고, 그 순서가 티켓 안에서도
 *  맞아야 합니다. 서버에서 미리 묶어 보내면 같은 목록을 두 모양으로 유지하게 됩니다.
 *
 *  **`ui/` 에 있는 이유**(2026-09-03): 고객 상세와 티켓 상세가 같은 목록을 그립니다. 화면
 *  하나에 두고 다른 화면이 가져다 쓰면 「화면 A 가 화면 B 를 import 한다」가 되고, 그때부터
 *  둘 중 하나를 고칠 때마다 다른 하나를 같이 봐야 합니다. */
export function groupByTicket(
  items: Interaction[],
  tickets: { conversation_id: number; subject: string | null; ticket_id: string | null }[],
) {
  const label = new Map(
    tickets.map((t) => [
      t.conversation_id,
      t.subject || (t.ticket_id ? `티켓 ${t.ticket_id}` : `문의 ${t.conversation_id}`),
    ]),
  );
  const groups = new Map<string, { key: string; label: string; items: Interaction[] }>();
  for (const item of items) {
    const id = item.conversation_id ?? null;
    const key = id === null ? "none" : String(id);
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        label: id === null ? "티켓과 무관한 기록" : (label.get(id) ?? `문의 ${id}`),
        items: [],
      });
    }
    groups.get(key)!.items.push(item);
  }
  // 티켓이 없는 묶음은 언제나 맨 끝입니다 — 티켓 이야기를 먼저 읽게.
  return [...groups.values()].sort((a, b) =>
    a.key === "none" ? 1 : b.key === "none" ? -1 : Number(b.key) - Number(a.key),
  );
}

// 방향 — **고르는 말과 줄에 찍히는 말이 다릅니다** (2026-08-25 운영자 지시). 폼에서는
// 「무엇을 적는가」라 발송·수신이고, 목록에서는 「이 티켓에 무슨 일이 있었나」라 문의
// 접수·문의 회신입니다. 값 셋은 이미 쓰이던 것 그대로입니다 — `inbound`/`outgoing` 은
// 허브스팟에서 가져온 줄이 쓰고, `note` 는 한 건에 양쪽이 다 들어 있는 기록입니다.
// 새 값을 만들면 옛 줄과 새 줄이 다른 말로 같은 것을 가리킵니다.
export const DIRECTIONS: [string, string][] = [
  ["outgoing", "발송"], ["inbound", "수신"], ["note", "주고받음"],
];

/** 채널마다 앞에 서는 아이콘. 「이 줄이 무엇으로 오갔나」가 글자를 읽기 전에 보여야
 *  합니다 — 스무 줄을 훑을 때 눈이 먼저 잡는 것은 모양이지 말이 아닙니다. */
const CHANNEL_ICON: Record<string, string> = {
  email: "mail",
  whatsapp: "messages",
  sms: "messages",
  kakao: "messages",
  phone: "phone",
  meeting: "users",
  hubspot: "hubspot",
  invoice: "file",
  contract: "file",
  manual: "edit",
  // 허브스팟 스레드 수집기가 넣는 한글 철자(`ticket_history._channel_label`). 이것이 없으면
  // 수입된 줄만 모양이 달라집니다 — 같은 메일인데 폼으로 적은 줄에는 봉투가, 수집된 줄에는
  // 화살표가 섭니다.
  이메일: "mail",
  채팅: "messages",
  폼: "file",
  기타: "edit",
};

/** 사람이 적은 기록 한 줄의 **말과 아이콘** — 「이메일 발송」 · 「왓츠앱 수신」 · 「통화 주고받음」.
 *
 *  메일 줄과 다른 말을 쓰는 이유(2026-08-26 운영자 지시): 이 티켓에서 「문의 접수」와
 *  「문의 회신」은 **한 번씩만 일어나는 사건**입니다 — 허브스팟으로 문의가 왔고, 우리가
 *  첫 답을 보냈다. 그 뒤로 오가는 것은 종류가 다양해서 같은 두 말로는 못 적습니다.
 *  왓츠앱으로 받은 것과 전화로 통화한 것이 화면에서 똑같이 「문의 접수」였습니다.
 *
 *  그래서 여기서는 **고를 때 쓴 말을 그대로** 씁니다 — 채널 이름 + 방향(발송·수신·주고받음).
 *  운영자가 폼에서 고른 두 값이 곧 줄에 찍히는 말이라, 무엇을 고르면 무엇이 보이는지가
 *  화면만 봐도 이어집니다.
 */
export function interactionMark(channel: string, direction: string) {
  const base = directionMark(direction);
  const tail = DIRECTIONS.find(([key]) => key === direction)?.[1]
    ?? (direction === "incoming" ? "수신" : direction === "outbound" ? "발송" : "주고받음");
  // **방향을 안 묻는 타입에는 꼬리말을 안 붙입니다** (2026-09-03). 폼이 이메일에만 방향을
  // 묻게 되면서 나머지는 전부 기본값 `note` 가 되는데, 그대로 이으면 「미팅 진행 주고받음」
  // ·「Invoice 발송 주고받음」이 됩니다 — 타입 이름에 이미 동사가 들어 있어서입니다.
  // 화면이 폼과 같은 말을 하게 두는 편이 맞습니다: 방향을 안 물었으면 안 적습니다.
  const label = DIRECTIONAL_CHANNELS.has(channel)
    ? `${channelLabel(channel)} ${tail}`
    : channelLabel(channel);
  return {
    label,
    icon: CHANNEL_ICON[channel] ?? base.icon,
    tone: base.tone,
  };
}

/** 방향 한 줄에 필요한 것 — 말·아이콘·색을 **한 곳에서** 정합니다. 티켓 기록·고객
 *  기록·리드 히스토리가 같은 목록을 그리므로 여기가 갈리면 같은 줄이 화면마다 다른
 *  말을 합니다. 별칭(`incoming`·`outbound`)은 옛 행이 들고 있는 값입니다. */
export function directionMark(value: string) {
  if (value === "inbound" || value === "incoming")
    return { label: "문의 접수", icon: "inbound", tone: "in" };
  if (value === "outgoing" || value === "outbound")
    return { label: "문의 회신", icon: "send", tone: "out" };
  return { label: "주고받음", icon: "messages", tone: "both" };
}

export type Interaction = {
  id?: number;
  /** 어느 티켓의 대화인가. HubSpot 에서 받아온 기록은 티켓이 달려 있고, 손으로 적은
   *  고객 단위 메모는 비어 있습니다 — 화면이 그것으로 묶습니다. */
  conversation_id?: number | null;
  channel: string;
  direction: string;
  handler: string | null;
  subject: string | null;
  summary: string;
  context: string | null;
  happened_at: string;
};

export function InteractionForm({
  contactId,
  conversationId,
  onSaved,
  onCancel,
}: {
  contactId: number;
  conversationId?: number | null;
  onSaved: () => void;
  /** 창을 닫는 길. **선택이 아닙니다** — 이 폼이 사는 자리는 셋 다 모달입니다(티켓 세부
   *  내역 · 보드 카드의 + · 고객 상세). 한동안 고객 상세만 카드 안에 펼쳐 둬서 여기가
   *  선택이었는데, 그러면 「취소가 없는 폼」이라는 두 번째 모양을 이 컴포넌트가 계속
   *  들고 있어야 합니다. 모양이 하나면 고칠 곳도 하나입니다. */
  onCancel: () => void;
}) {
  // 방향 칸을 그릴지 말지가 이 값에 달려 있어서, 고르개만 제어 컴포넌트입니다. 나머지
  // 칸은 예전 그대로 DOM 이 들고 있습니다 — 그것까지 상태로 올릴 이유가 없습니다.
  const [channel, setChannel] = useState(CHANNELS[0][0]);
  const [save, saving] = useAction(async (event: React.FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        const form = event.currentTarget;
        const data = Object.fromEntries(new FormData(form) as never) as Record<string, string>;
        // Posted to the route the Jinja form posts to, so the 미팅 stage rule and the
        // contact check live in one place.
        await postForm(`/customers/${contactId}/interactions`, {
          ...data,
          conversation_id: conversationId ? String(conversationId) : "",
        });
        form.reset();
        // `form.reset()` 은 DOM 만 되돌립니다 — 고르개는 제어 컴포넌트라 손으로 되돌립니다.
        setChannel(CHANNELS[0][0]);
        onSaved();
  });

  return (
    <form className="record-form" onSubmit={save}>
      <label><span className="field-label">타입</span>
        <select className="select" name="channel" value={channel}
                onChange={(e) => setChannel(e.target.value)}>
          {CHANNELS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      {/* **방향은 이메일에만 묻습니다** (2026-09-03 운영자 지시). 전화·미팅·메모는 한 건을
          통째로 적는 기록이라 발송·수신으로 갈리지 않고, 골라 봐야 줄에 「미팅 진행
          주고받음」이 찍힐 뿐입니다. 안 물을 때는 칸을 **감추는 게 아니라 안 그립니다** —
          `select` 를 숨겨 두면 그 값이 그대로 전송돼, 이메일로 골랐다가 전화로 바꾼 기록에
          「발송」이 남습니다. 안 보내면 라우트가 기본값 `note` 로 받습니다(모델 기본값과
          같은 값입니다). */}
      {DIRECTIONAL_CHANNELS.has(channel) && (
        <label><span className="field-label">방향</span>
          {/* 기본은 「주고받음」입니다 — 한 건을 통째로 적는 것이 이 폼의 원래 쓰임이라,
              안 고르고 저장한 기록의 뜻이 바뀌면 안 됩니다. */}
          <select className="select" name="direction" defaultValue="note">
            {DIRECTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
      )}
      <label><span className="field-label">담당자</span>
        <input className="input" name="handler" maxLength={120} placeholder="이 건을 진행한 사람" />
      </label>
      <label><span className="field-label">일시</span>
        <input className="input" type="datetime-local" name="happened_at" />
      </label>
      {/* One record = one exchange, written up once. 방향은 그 기록이 무엇인지 말할
          뿐, 한 대화를 「누가 말했나」 줄로 쪼개라는 뜻이 아닙니다 — 그래서 기본이
          「주고받음」이고 본문은 여전히 한 칸입니다. */}
      <label className="quick-form__wide"><span className="field-label">오간 내용</span>
        <textarea className="textarea" name="summary" rows={4} required
                  placeholder="고객이 요청한 내용과 우리가 답한 내용을 한 번에 정리해서 적어주세요." />
      </label>
      {/* 주제 · 맥락·다음 액션 · 관련 자료 URL 세 칸은 뺐습니다 (2026-09-03 운영자 지시,
          「앞으로도 안 쓸 것」). **열의 운명은 셋이 다릅니다:**

          - `artifact_url` 은 열까지 지웠습니다(이관 0107). 채우는 곳이 이 폼 하나였고
            그리는 화면이 없었습니다 — 사람이 안 적으면 아무 데도 안 남습니다.
          - `subject` 와 `context` 는 **남습니다.** 사람이 안 적을 뿐 코드가 계속 채우고
            화면이 계속 읽습니다: 허브스팟 메일 제목·딜 이름이 `subject` 로 들어와 요약이 빈
            기록의 본문 자리를 채우고 `one_line` 이 요약을 만들 때 읽으며, 한 줄 요약이 `context` 로 들어와
            미리보기가 됩니다(`customer_ops` · `hubspot_reconcile` · `POST /contacts/history-digest`).
            폼에서 뺀 것과 열이 죽은 것은 다른 이야기입니다. */}
      {/* 오른쪽 아래에 붙이고, 취소는 저장 **왼쪽**입니다 — 되돌리는 쪽이 왼쪽, 진행하는
          쪽이 오른쪽. 이 콘솔의 다른 확인 창과 같은 순서입니다. */}
      <div className="quick-form__wide"
           style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <button type="button" className="btn btn--subtle" onClick={onCancel}>취소</button>
        <SubmitButton busy={saving}>기록 저장</SubmitButton>
      </div>
    </form>
  );
}

/** 기록 한 줄 — **접혀 있습니다.** 메일 본문을 그대로 펼쳐 두면 목록이 아니라 메일함이
 *  되고, 열 개가 쌓이면 아무것도 훑을 수 없습니다(2026-08-20 운영자 지시). 미리보기는
 *  가져올 때 만들어 둔 한 줄이고, 누르면 그 자리에서 본문이 열립니다.
 *
 *  머리줄에서 **채널 태그는 이메일이 아닐 때만** 붙습니다. 거의 모든 줄이 이메일이라
 *  「이메일」은 모든 줄에 같은 말을 하나씩 더 얹을 뿐이고, 「전화」·「미팅」은 그 줄에서
 *  가장 먼저 알아야 할 것입니다. 방향도 「우리 → 고객」이라는 태그 대신 **화살표와 한
 *  단어**입니다 — 이 목록은 이미 한 고객의 것이라 「고객」은 어느 줄에나 적혀 있습니다.
 *
 *  `hideSubject` 는 처음에 **한 스레드짜리 목록**을 위한 것이었습니다(「이 티켓의 기록」).
 *  거기서는 줄마다 같은 메일 제목이 반복되고, 우리 메일 줄은 그 제목을 **번역해서** 쓰기
 *  때문에 같은 제목이 원문·국문으로 나란히 놓여 다른 두 건처럼 보였습니다(2026-08-25
 *  운영자 지적).
 *
 *  **이제 리드 히스토리도 켭니다** (2026-09-03 운영자 지시). 예전에는 그쪽에서 제목이
 *  대화를 가르는 유일한 열쇠였는데(`threadKey` 로 제목을 묶었습니다), 지금은 묶는 열쇠가
 *  **티켓**이라(`groupByTicket`) 제목이 그 일을 하지 않습니다. 남은 것은 줄마다 얹히는
 *  굵은 글씨 한 줄뿐이었고, 운영자가 읽고 싶은 것은 「무슨 이야기였나」입니다 —
 *  「이 티켓의 기록」과 같은 모양으로 요약만 보여 줍니다.
 *
 *  제목은 **버려지지 않습니다**: 요약이 빈 기록에서는 아래 `body` 가 제목을 대신 씁니다.
 *  그 줄에는 제목이 가진 전부이기 때문입니다. */
export function InteractionItem({ item, hideSubject = false, hideHandler = false }: {
  item: Interaction;
  hideSubject?: boolean;
  /** 담당자를 빼고 그립니다. **티켓 기록이 그렇게 씁니다** (2026-08-26 운영자 지시) —
   *  그 목록은 「이 티켓에 무슨 일이 있었나」를 훑는 자리라 누가 했는지는 줄마다 붙을
   *  값이 아닙니다.
   *
   *  플래그로 두는 이유: 이 칸이 **한 가지 뜻이 아닙니다.** 고객 상세의 티켓 블록은
   *  같은 자리에 메일의 **상태**를 적고("나간 메일과 아직 안 나간 초안은 반드시
   *  구별돼야 한다"), 사라진 티켓에서 옮겨 온 메일은 「지난 티켓」을 적습니다. 통째로
   *  지우면 그 둘이 같이 사라집니다. */
  hideHandler?: boolean;
}) {
  const dir = interactionMark(item.channel, item.direction);
  // 제목을 안 그리는 자리에서는 본문 없는 줄이 통째로 빈칸이 됩니다 — 그때는 제목이
  // 그 기록의 전부라 본문 자리에 씁니다.
  const body = (item.summary || "").trim() || (hideSubject ? item.subject || "" : "");
  // 미리보기는 `context` 가 있으면 그것입니다. 가져온 메일에서는 한 줄 요약이고,
  // 사람이 적은 기록에서는 「맥락·다음 액션」이라 어느 쪽이든 한 줄로 읽힙니다.
  // 없으면 본문 앞머리 — 인사말로 시작하는 메일에서는 이게 아무것도 안 알려 줍니다.
  const preview = (item.context || body).replace(/\s+/g, " ");
  // **본문이 있으면 언제나 접습니다.** 예전에는 짧고 `context` 도 없는 기록만 통째로
  // 펼쳐 뒀는데, 그러면 같은 목록에서 어떤 줄은 한 줄이고 어떤 줄은 열 줄이라 훑을 수가
  // 없습니다 — 기록을 하나 더 적을수록 목록이 목록이 아니게 됩니다(2026-08-26 운영자 지시).
  const foldable = body.length > 0;
  const head = (
    <>
      <div className="row wrap" style={{ gap: 6 }}>
        <span className="t-xs history-item__dir">
          <Icon name={dir.icon} size={13} />
          {dir.label}
        </span>
        {/* 채널 태그는 뺐습니다 — 라벨이 이미 「왓츠앱 수신」이라 바로 옆에 「WhatsApp」을
            한 번 더 다는 셈입니다. */}
        {!hideHandler && item.handler && <span className="tag">{item.handler}</span>}
        {/* **`kst()` 로 찍습니다.** API 가 주는 것은 오프셋 없는 UTC 라, 잘라서 그대로
            쓰면 한국 시각보다 9시간 이른 값이 찍힙니다 — 같은 목록의 메일 줄은 변환해서
            쓰고 있어서, 1분 차이로 오간 두 건이 9시간 떨어져 보였습니다. */}
        <time className="t-xs t-subtle tnum">{kst(item.happened_at)}</time>
      </div>
      {item.subject && !hideSubject && (
        <strong className="history-item__title">{item.subject}</strong>
      )}
    </>
  );
  return (
    <article className={`history-item history-item--${dir.tone}`}>
      <div className="history-item__rail"><span /></div>
      <div>
        {foldable ? (
          <details>
            <summary style={{ cursor: "pointer", listStyle: "none" }}>
              {head}
              <div className="t-sm t-subtle" style={{ lineHeight: 1.6 }}>
                {preview.slice(0, 120)}
                {preview.length > 120 ? "… " : " "}
                <span style={{ color: "var(--accent)" }}>전체보기</span>
              </div>
            </summary>
            <div className="history-item__body" style={{ marginTop: 8 }}>{body}</div>
          </details>
        ) : (
          <>
            {head}
            <div className="history-item__body">{body}</div>
          </>
        )}
      </div>
    </article>
  );
}
