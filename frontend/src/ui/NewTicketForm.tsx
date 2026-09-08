import { useState } from "react";
import { getJSON, postForm } from "../lib/api";
import { SubmitButton, useAction } from "./ActionButton";

type Known = { found: boolean; full_name?: string; company?: string; tickets?: number };

/** 티켓을 손으로 만드는 폼 — 보드 New 열의 `+` 가 띄웁니다 (2026-09-08 운영자 지시).
 *
 *  전화로 받은 문의, 행사장 명함, 영업이 먼저 연락한 건 — 허브스팟 폼도 메일도 안 지나서
 *  이 콘솔에 행이 안 생기던 것들입니다.
 *
 *  **필수는 셋뿐입니다**(이메일 · 이름 · 제목). 이메일은 연락처의 신원이고, 이름은 우리
 *  열이 비을 수 없으며, 제목은 티켓의 이름이자 목록에 그려지는 글자입니다. 나머지를
 *  필수로 만들면 「지금 아는 것만 적고 나중에 채운다」가 안 됩니다 — 전화를 받는 중에
 *  회사 이름을 물어봐야 하는 폼입니다.
 */
export function NewTicketForm({ onSaved, onCancel }: {
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [known, setKnown] = useState<Known | null>(null);
  const [name, setName] = useState("");
  const [company, setCompany] = useState("");

  /** **이미 아는 주소면 채워 줍니다** (운영자: 「기존에 리드 히스토리에 있을 수도 있으니
   *  정보 불러올 수도 있도록」). 같은 사람을 두 번 적으면 이름 철자가 갈리고, 그때
   *  화면에는 같은 회사가 둘로 보입니다.
   *
   *  **덮어쓰지는 않습니다** — 운영자가 이미 적어 둔 칸은 그대로 둡니다. 주소를 고치다
   *  중간 글자에서 우연히 걸린 옛 연락처가 방금 친 이름을 지우면 안 됩니다. */
  async function lookup(email: string) {
    if (!email.includes("@")) return setKnown(null);
    try {
      const found = await getJSON<Known>(
        `/api/ui/contacts/lookup?email=${encodeURIComponent(email)}`,
      );
      setKnown(found.found ? found : null);
      if (found.found) {
        setName((current) => current || found.full_name || "");
        setCompany((current) => current || found.company || "");
      }
    } catch {
      // 조회가 실패해도 폼은 그대로 씁니다 — 채워 주는 편의이지 관문이 아닙니다.
      setKnown(null);
    }
  }

  const [save, saving] = useAction(async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    // 허브스팟에 티켓을 만든 뒤 그 티켓 화면으로 보냅니다(라우트가 303).
    await postForm(
      "/pipeline/tickets",
      Object.fromEntries(new FormData(event.currentTarget) as never) as Record<string, string>,
    );
    onSaved();
  });

  return (
    <form className="record-form" onSubmit={save}>
      <label className="quick-form__wide"><span className="field-label">이메일 *</span>
        <input className="input" name="email" type="email" required
               onBlur={(e) => lookup(e.target.value)}
               placeholder="buyer@example.com" />
      </label>
      {known && (
        <div className="quick-form__wide t-xs t-subtle">
          이미 아는 연락처입니다 — 이름·회사를 채웠습니다.
          {typeof known.tickets === "number" && known.tickets > 0
            && ` 이 고객의 문의가 이미 ${known.tickets}건 있습니다.`}
        </div>
      )}
      <label><span className="field-label">이름 *</span>
        <input className="input" name="full_name" required maxLength={200}
               value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label><span className="field-label">회사</span>
        <input className="input" name="company" maxLength={200}
               value={company} onChange={(e) => setCompany(e.target.value)} />
      </label>
      <label className="quick-form__wide"><span className="field-label">문의 제목 *</span>
        <input className="input" name="subject" required maxLength={300}
               placeholder="티켓 이름이자 목록에 뜨는 글자입니다" />
      </label>
      <label className="quick-form__wide"><span className="field-label">내용</span>
        <textarea className="textarea" name="content" rows={4}
                  placeholder="통화 내용, 명함에 적힌 것, 지금 아는 만큼." />
      </label>
      {/* **허브스팟에 실제로 만들어집니다.** 되돌리려면 저쪽에서 지워야 하므로, 누르기
          전에 그렇게 적습니다 — 이 콘솔의 다른 되돌릴 수 없는 동작과 같은 규칙입니다. */}
      <div className="quick-form__wide t-xs t-subtle">
        허브스팟 파이프라인의 <strong>New</strong> 단계에 티켓이 만들어지고, 그 연락처에
        붙습니다. 없는 연락처면 같이 만듭니다.
      </div>
      <div className="quick-form__wide"
           style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
        <button type="button" className="btn btn--subtle" onClick={onCancel}>취소</button>
        <SubmitButton busy={saving}>티켓 만들기</SubmitButton>
      </div>
    </form>
  );
}
