import { postForm } from "../lib/api";
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
const DIRECTIONS: Record<string, string> = {
  incoming: "고객 → 우리", outgoing: "우리 → 고객", inbound: "고객 → 우리",
};

export function channelLabel(value: string) {
  return CHANNELS.find(([key]) => key === value)?.[1] ?? value;
}
export function directionLabel(value: string) {
  return DIRECTIONS[value] ?? value;
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
      <label><span className="field-label">담당자</span>
        <input className="input" name="handler" maxLength={120} placeholder="이 건을 진행한 사람" />
      </label>
      <label><span className="field-label">주제</span>
        <input className="input" name="subject" placeholder="예: 견적 조건 협의" />
      </label>
      <label><span className="field-label">일시</span>
        <input className="input" type="datetime-local" name="happened_at" />
      </label>
      {/* One record = one exchange, written up once. Splitting it into "who spoke" rows
          is what the direction field used to force. */}
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
      <div className="quick-form__wide row-between">
        <span className="t-xs t-subtle">일시를 비우면 지금 시각으로 기록됩니다.</span>
        <SubmitButton busy={saving}>기록 저장</SubmitButton>
      </div>
    </form>
  );
}

export function InteractionItem({ item }: { item: Interaction }) {
  return (
    <article className="history-item">
      <div className="history-item__rail"><span /></div>
      <div>
        <div className="row wrap">
          <span className="tag">{channelLabel(item.channel)}</span>
          {/* `note` means "no direction" — a tag saying nothing on every row. */}
          {item.direction && item.direction !== "note" && (
            <span className="tag">{directionLabel(item.direction)}</span>
          )}
          {item.handler && <span className="tag">{item.handler}</span>}
          <time className="t-xs t-subtle tnum">{item.happened_at?.slice(0, 16).replace("T", " ")}</time>
        </div>
        {item.subject && <strong className="history-item__title">{item.subject}</strong>}
        <div className="history-item__body">{item.summary}</div>
        {item.context && <div className="t-xs t-subtle" style={{ marginTop: 6 }}>{item.context}</div>}
      </div>
    </article>
  );
}
