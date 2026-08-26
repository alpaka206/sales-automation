import { Icon } from "./Icon";
import { postForm } from "../lib/api";
import { kst } from "../lib/format";
import { SubmitButton, useAction } from "./ActionButton";

// The 소통·미팅 기록 form, defined ONCE — the port of partials/interaction_form.html.
// After the first reply the thread leaves HubSpot and the customer answers wherever they
// like, so the operator types what happened. Three surfaces offer it and they must ask
// for the same fields or the records cannot be read as one history.
export const CHANNELS: [string, string][] = [
  ["email", "이메일"], ["whatsapp", "WhatsApp"], ["phone", "전화"], ["sms", "문자"],
  ["kakao", "카카오톡"], ["meeting", "미팅"], ["hubspot", "HubSpot"],
  ["invoice", "Invoice"], ["contract", "계약"], ["manual", "기타 메모"],
];
export function channelLabel(value: string) {
  return CHANNELS.find(([key]) => key === value)?.[1] ?? value;
}

// 방향 — **고르는 말과 줄에 찍히는 말이 다릅니다** (2026-08-25 운영자 지시). 폼에서는
// 「무엇을 적는가」라 발송·수신이고, 목록에서는 「이 티켓에 무슨 일이 있었나」라 문의
// 접수·문의 회신입니다. 값 셋은 이미 쓰이던 것 그대로입니다 — `inbound`/`outgoing` 은
// 허브스팟에서 가져온 줄이 쓰고, `note` 는 한 건에 양쪽이 다 들어 있는 기록입니다.
// 새 값을 만들면 옛 줄과 새 줄이 다른 말로 같은 것을 가리킵니다.
export const DIRECTIONS: [string, string][] = [
  ["outgoing", "발송"], ["inbound", "수신"], ["note", "주고받음"],
];

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
  channel: string;
  direction: string;
  handler: string | null;
  subject: string | null;
  summary: string;
  context: string | null;
  artifact_url: string | null;
  happened_at: string;
};

export function InteractionForm({
  contactId,
  conversationId,
  onSaved,
}: {
  contactId: number;
  conversationId?: number | null;
  onSaved: () => void;
}) {
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
        onSaved();
  });

  return (
    <form className="record-form" onSubmit={save}>
      <label><span className="field-label">채널</span>
        <select className="select" name="channel">
          {CHANNELS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label><span className="field-label">방향</span>
        {/* 기본은 「주고받음」입니다 — 한 건을 통째로 적는 것이 이 폼의 원래 쓰임이라,
            안 고르고 저장한 기록의 뜻이 바뀌면 안 됩니다. */}
        <select className="select" name="direction" defaultValue="note">
          {DIRECTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </label>
      <label><span className="field-label">담당자</span>
        <input className="input" name="handler" maxLength={120} placeholder="이 건을 진행한 사람" />
      </label>
      <label><span className="field-label">주제</span>
        <input className="input" name="subject" placeholder="예: 견적 조건 협의" />
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
      <label className="quick-form__wide"><span className="field-label">맥락 · 다음 액션</span>
        <textarea className="textarea" name="context" rows={2} placeholder="맥락·컨택 방식·추후 액션" />
      </label>
      <label className="quick-form__wide"><span className="field-label">관련 자료 URL</span>
        <input className="input" name="artifact_url" placeholder="회의록·Invoice·계약서 URL" />
      </label>
      {/* 안내 문구를 뺐으므로 버튼만 남습니다 — space-between 은 그 하나를 왼쪽 끝으로
          밀어붙입니다. 제출 버튼은 오른쪽 아래에 있어야 합니다. */}
      <div className="quick-form__wide" style={{ display: "flex", justifyContent: "flex-end" }}>
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
 *  `hideSubject` 는 **한 스레드짜리 목록**을 위한 것입니다(「이 티켓의 기록」). 거기서는
 *  줄마다 같은 메일 제목이 반복되고, 우리 메일 줄은 그 제목을 **번역해서** 쓰기 때문에
 *  같은 제목이 원문·국문으로 나란히 놓여 다른 두 건처럼 보였습니다(2026-08-25 운영자
 *  지적). 고객 기록·리드 히스토리에서는 켜지 않습니다 — 거기서는 제목이 대화를 가르는
 *  유일한 열쇠입니다(`threadKey`). */
export function InteractionItem({ item, hideSubject = false }: {
  item: Interaction;
  hideSubject?: boolean;
}) {
  const dir = directionMark(item.direction);
  // 제목을 안 그리는 자리에서는 본문 없는 줄이 통째로 빈칸이 됩니다 — 그때는 제목이
  // 그 기록의 전부라 본문 자리에 씁니다.
  const body = (item.summary || "").trim() || (hideSubject ? item.subject || "" : "");
  // 미리보기는 `context` 가 있으면 그것입니다. 가져온 메일에서는 한 줄 요약이고,
  // 사람이 적은 기록에서는 「맥락·다음 액션」이라 어느 쪽이든 한 줄로 읽힙니다.
  // 없으면 본문 앞머리 — 인사말로 시작하는 메일에서는 이게 아무것도 안 알려 줍니다.
  const preview = (item.context || body).replace(/\s+/g, " ");
  const foldable = body.length > 0 && (!!item.context || body.length > 120);
  const head = (
    <>
      <div className="row wrap" style={{ gap: 6 }}>
        <span className="t-xs history-item__dir">
          <Icon name={dir.icon} size={13} />
          {dir.label}
        </span>
        {item.channel !== "email" && <span className="tag">{channelLabel(item.channel)}</span>}
        {item.handler && <span className="tag">{item.handler}</span>}
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
