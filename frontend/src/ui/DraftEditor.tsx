import type { ReactNode } from "react";
import { Icon } from "./Icon";
import { ActionButton } from "./ActionButton";
import { kst } from "../lib/format";

/** 본문 표기와 언어 이름 — 편집기와 **같이 왔습니다.** 둘 다 쓰는 곳이 이 파일 안뿐이라,
 *  화면에 남겨 두면 편집기를 읽으러 온 사람이 두 파일을 오갑니다. */
const MARKS: { key: string; mark: ReactNode; wrap: [string, string]; title: string }[] = [
  { key: "b", mark: <b>B</b>, wrap: ["**", "**"], title: "굵게 (**글자**)" },
  { key: "i", mark: <i style={{ fontFamily: "Georgia,serif" }}>I</i>, wrap: ["*", "*"], title: "기울임 (*글자*)" },
  { key: "u", mark: <u>U</u>, wrap: ["__", "__"], title: "밑줄 (__글자__)" },
  { key: "a", mark: <Icon name="link" size={14} />, wrap: ["[", "](https://)"],
    title: "링크 ([글자](주소)) — 고른 글자가 링크 글자가 됩니다" },
];

const LANGUAGE_LABELS: Record<string, string> = {
  ko: "한국어", en: "English", ja: "日本語", zh: "中文",
};

function languageLabel(code: string) {
  const key = code.trim().toLowerCase();
  return LANGUAGE_LABELS[key] ?? code.trim().toUpperCase();
}



function isMostlyKoreanText(text: string): boolean {
  const letters = text.match(/\p{L}/gu) ?? [];
  if (letters.length === 0) return false;
  const hangul = letters.filter((ch) => /[가-힣ᄀ-ᇿ㄰-㆏]/u.test(ch)).length;
  return hangul / letters.length >= 0.5;
}

export type Draft = {
  subject: string; body: string; signature: string; sender: string; cc: string;
};

/** 검토 중인 회신 초안 — **이 콘솔에서 고객에게 나가는 글을 고치는 유일한 자리**입니다.
 *
 *  화면(`MessageDetail`)에서 떼어냈습니다 (2026-09-08). 떼어낸 이유는 파일이 커서가
 *  아니라 **이 덩어리가 하나의 일**이기 때문입니다: 초안 한 벌을 받아 고치고, 다 되면
 *  부르는 쪽에 알립니다. 그 경계가 분명해서 prop 이 열넷으로 끝납니다 — 초안 상태를
 *  `draft` 한 벌로 묶기 전에는 스물이 넘었고, 그때는 떼는 것이 오히려 나빴습니다.
 *
 *  **상태를 안 갖습니다.** 초안은 부르는 쪽이 들고 있고 여기는 그리기만 합니다 — 그래야
 *  「번역하기」가 본문을 바꾸거나 화면이 다른 티켓으로 넘어갈 때 이 컴포넌트가 옛 값을
 *  들고 있는 일이 없습니다.
 */
export function DraftEditor({
  bubbleKey, draft, patch, msg, ticket, senders, signatures,
  note, koreanDraft, translationRequired, onAct, onTranslate, onPreview,
  onSend, onReject,
}: {
  bubbleKey: number;
  draft: Draft;
  patch: (part: Partial<Draft>) => void;
  /** 편집기가 실제로 읽는 칸만 적습니다. 화면의 큰 타입을 통째로 끌어오면 안 쓰는
   *  값까지 넘겨야 하고, 그러면 이 컴포넌트가 화면에 묶입니다. */
  msg: {
    id: number; status: string; channel: string; send_error: string | null;
    target_language: string | null; created_at: string;
  };
  ticket: { inquiry_language: string | null };
  senders?: {
    senders: { id: string; address: string; is_default: boolean }[];
    default_address: string; error: string | null;
  };
  signatures: { key: string; name: string }[];
  note: string;
  koreanDraft: string | null;
  translationRequired: boolean;
  onAct: (action: string, extra?: Record<string, string>) => Promise<void>;
  onTranslate: () => Promise<void>;
  onPreview: () => Promise<void>;
  onSend: () => void;
  onReject: () => void;
}) {
  const { subject, body, signature, sender, cc } = draft;

  /** 본문에서 고른 글자를 표기로 감쌉니다. 아무것도 안 골랐으면 커서 자리에 껍데기만
   *  넣고 그 안에 커서를 둡니다 — 표기를 외우지 않아도 쓸 수 있게.
   *
   *  **편집기와 같이 왔습니다**: 이 함수가 만지는 것은 본문 하나뿐이라, 화면에 남겨 두면
   *  편집기를 떼어낸 뒤에도 화면이 본문을 만지는 코드를 들고 있게 됩니다. */
  function wrapSelection([before, after]: [string, string]) {
    const field = document.getElementById("msg-body") as HTMLTextAreaElement | null;
    if (!field) return;
    const { selectionStart: from, selectionEnd: to } = field;
    const picked = body.slice(from, to);
    patch({ body: body.slice(0, from) + before + picked + after + body.slice(to) });
    requestAnimationFrame(() => {
      field.focus();
      const at = from + before.length + picked.length;
      field.setSelectionRange(picked ? at : from + before.length, picked ? at : from + before.length);
    });
  }

  return (
      <div key={bubbleKey} className="bubble bubble--out bubble--current">
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
          <span className="bubble__time tnum">{kst(msg.created_at)}</span>
        </div>
        <label className="field-label" htmlFor="msg-subject">제목</label>
        <input className="input" id="msg-subject" value={subject}
               onChange={(e) => patch({ subject: e.target.value })} style={{ marginBottom: 12 }} />
        <label className="field-label" htmlFor="msg-body">본문</label>
        {/* 도구는 메일 편집기처럼 **본문 상자 안쪽 아래**입니다 — 글자를 고른
            손이 곧바로 닿는 자리. 상자 테두리는 이 wrapper 가 그리고 textarea 는
            테두리를 벗습니다(안에 든 것처럼 보이도록).

            WYSIWYG 이 아닌 이유: 이 칸의 글자가 그대로 메일이 되는 것이 이 화면의
            전제입니다(모델이 쓰고, 번역이 지나가고, 사람이 고칩니다). 숨은 서식을
            들고 있으면 그 셋이 서로 모르는 상태가 되고, 화면과 나간 메일이 갈립니다. */}
        <div className="draft-editor">
          <textarea className="draft-textarea" id="msg-body" value={body}
                    onChange={(e) => patch({ body: e.target.value })} />
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
            목록은 서버가 만듭니다(`/api/ui/senders`): 살아 있는 이메일 채널
            중 운영자가 허락한 것과 연결된 개인 사서함입니다. 화면이 스스로
            목록을 지으면 고를 수는 있는데 발송이 거절하는 값이 생깁니다.

            **티켓별이 아닙니다** (2026-09-09). 예전에는 티켓마다 물어서 「그
            티켓에 붙을 스레드가 있는 계정」만 남겼는데, 허용 목록이 계정 하나라
            그 검사는 거의 아무것도 안 거르면서 — 없다고 나와도 발송은
            `cross_inbox_attempt` 로 성공합니다 — 티켓을 열 때마다 허브스팟
            왕복 서넛에서 아홉을 냈습니다. */}
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
                    onChange={(e) => patch({ sender: e.target.value })}>
              {/* **「자동」이 무엇인지 서버가 말해 줍니다.** 목록에서 찾지
                  않는 이유: 기본값이 고르개에 없는 주소일 때가 있습니다(허브스팟
                  기계 주소, 또는 운영자가 고르개에서 뺀 주소) — 목록에서만
                  찾으면 그 티켓은 어느 주소로 나갈지가 화면에 안 적힙니다.

                  **「이 대화의 주소」 같은 두루뭉술한 말은 안 씁니다**
                  (2026-09-03 운영자 지시). 주소를 못 가져왔으면 못 가져왔다고
                  적습니다 — 그건 조회가 실패했다는 뜻이라 다른 이야기입니다. */}
              {/* **같은 주소가 두 번 뜨지 않습니다** (2026-09-08 운영자 지시).
                  예전에는 「자동 — perso.ai@estsoft.com」과 목록의
                  `perso.ai@estsoft.com` 이 나란히 서서, 무엇이 다른지 화면만
                  봐서는 알 수 없었습니다.

                  **없앤 쪽이 목록입니다.** 남긴 「자동」은 값이 비어 있고
                  (`channel_account_id = NULL`), 그건 「그 주소로 보내되 그
                  티켓에서 못 쓰면 스레드가 정하는 값으로 물러선다」는 뜻입니다.
                  명시로 고른 값은 **안 물러섭니다** — 폼으로만 들어온 티켓은
                  그 인박스에 대화가 없어서 발송이 그대로 실패합니다. 화면에서
                  지운 것은 글자이지 안전장치가 아닙니다. */}
              <option value="">
                {senders?.default_address || "발신 주소를 확인하지 못했습니다"}
              </option>
              {senders?.senders
                ?.filter((x) => x.address !== senders?.default_address)
                .map((x) => (
                  <option key={x.id} value={x.id}>{x.address}</option>
                ))}
            </select>
          </>
        )}

        {/* **참조(CC)** — 받는 주소도 보내는 주소도 안 건드리고 얹기만 합니다
            (2026-09-07 운영자 지시, 이관 0112). 비워 두면 이 칸이 생기기 전과
            똑같이 나갑니다.

            **후보 목록은 없습니다** (2026-09-08 운영자 지시). 한동안 그 티켓의
            스레드 참여자를 칩으로 띄웠는데, 그 목록을 만들려고 티켓의 모든
            스레드 × 모든 메시지를 받아 메모리에 쌓고는 주소 몇 개만 쓰고
            버렸습니다 — 그것도 티켓을 **열 때마다**. 참조에 넣을 사람은
            운영자가 이미 알고 있어서, 적는 편이 고르는 것보다 빠릅니다.

            철자를 다듬는 곳은 서버 한 곳이라(`parse_cc_addresses`) 메일
            클라이언트에서 복사해 붙인 것도 그대로 받습니다. */}
        <label className="field-label" htmlFor="msg-cc" style={{ marginTop: 12 }}>
          참조 (CC)
        </label>
        <input className="input" id="msg-cc" value={cc}
               onChange={(e) => patch({ cc: e.target.value })}
               placeholder="비워 두면 참조 없이 나갑니다. 여러 명은 쉼표로." />
        <label className="field-label" htmlFor="msg-signature" style={{ marginTop: 12 }}>서명</label>
        <select className="select" id="msg-signature" value={signature}
                onChange={(e) => patch({ signature: e.target.value })} style={{ marginBottom: 12 }}>
          <option value="">서명 없음</option>
          {signatures.map((s) => (
            <option key={s.key} value={s.key}>{s.name}</option>
          ))}
        </select>

        <div className="action-bar">
          {translationRequired ? (
            <ActionButton className="btn btn--subtle" pending="번역 중" onClick={onTranslate}>
              <Icon name="translate" size={15} /> 번역하기 ({msg.target_language})
            </ActionButton>
          ) : (
            <button type="button" className="btn btn--ok"
                    aria-haspopup="dialog" onClick={onSend}>
              <Icon name="check" size={15} /> 검토 완료 · 발송
            </button>
          )}
          <ActionButton className="btn btn--subtle" pending="여는 중" onClick={onPreview}>
            <Icon name="file" size={15} /> 미리보기
          </ActionButton>
          <ActionButton className="btn btn--subtle" pending="저장 중"
                        onClick={() => onAct("edit")}>
            <Icon name="edit" size={15} /> 저장
          </ActionButton>
          <button type="button" className="btn btn--danger"
                  aria-haspopup="dialog" onClick={onReject}>
            <Icon name="x" size={15} /> 거절
          </button>
        </div>
        {note && <div style={{ marginTop: 14 }} role="status" className="t-sm">{note}</div>}
      </div>
  );
}
