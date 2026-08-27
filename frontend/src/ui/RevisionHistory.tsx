import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getJSON } from "../lib/api";
import { kst } from "../lib/format";
import { Icon } from "./Icon";
import { Modal } from "./Modal";
import { LoadingBlock } from "./Loading";

/** 이전 판본 하나. 목록과 본문을 한 번에 받습니다 — 판본 본문은 짧고, 하나 눌러 볼 때마다
 *  왕복을 한 번 더 하면 그게 이 창의 체감 속도가 됩니다. */
type Revision = {
  id: number;
  version: number;
  title: string;
  body: string;
  status: string;
  change_note: string | null;
  edited_by: string | null;
  created_at: string;
  extra: Record<string, string>;
};

type History = { kind: string; kind_label: string; revisions: Revision[] };

/** 무슨 일이 있었나. 서버가 영어 한 낱말로 남기고(코드가 읽는 값), 화면이 사람 말로 옮깁니다.
 *
 *  **`edited` 는 여기 없습니다.** 이 표의 행은 거의 전부 수정이라, 줄마다 「고침」이라고
 *  적으면 판 번호 옆에서 아무것도 구별해 주지 않습니다 (2026-08-27 운영자 지시:
 *  「v2 고침 → v2」). 지움·되돌림은 드물고 의미가 있어서 남깁니다. 모르는 값도 그대로
 *  보여 줍니다 — 지어낸 말보다 낫습니다. */
const NOTES: Record<string, string> = {
  edited: "",
  deleted: "지움",
  restored: "되돌림",
};

/** `extra` 에 실려 오는 부속 칸의 사람 이름.
 *
 *  **본문만 보여 주면 안 됩니다.** 정책 문서에서 「메일 제목」 칸만 비우는 편집이 실제로
 *  있었고(2026-08-27), 그때 본문은 한 글자도 안 바뀌어서 판본 기록이 「아무 일도 없었다」로
 *  보였습니다. 바뀐 것이 여기 있는 칸이면 여기에 보여야 합니다. */
const EXTRA_LABELS: Record<string, string> = {
  subject: "메일 제목",
  usage_note: "언제 쓰는가",
  mode: "종류",
  language: "언어",
  channel: "채널",
  description: "설명",
};

export function noteLabel(note: string | null): string {
  if (!note) return "";
  return NOTES[note] ?? note;
}

/** 「히스토리」 창. 이메일 템플릿과 정책 문서가 **같은 컴포넌트**를 씁니다 — 보고 싶은 것이
 *  같기 때문입니다: 언제, 누가, 무엇을, 그때 값은 무엇이었나.
 *
 *  판본은 고치기 **직전**에 남습니다. 그래서 맨 위 행은 「지금 본문」이 아니라 「직전
 *  본문」이고, 창이 그렇게 적어 둡니다 — 안 적으면 맨 위가 현재라고 읽힙니다. */
export function RevisionHistory({
  kind,
  documentId,
  title,
  onClose,
}: {
  kind: "email_template" | "policy_source";
  documentId: number;
  title: string;
  onClose: () => void;
}) {
  const [openId, setOpenId] = useState<number | null>(null);
  const { data, isPending } = useQuery({
    queryKey: ["revisions", kind, documentId],
    queryFn: () => getJSON<History>(`/api/ui/documents/${kind}/${documentId}/revisions`),
  });

  const revisions = data?.revisions ?? [];
  return (
    <Modal
      full
      title={`히스토리 — ${title}`}
      onClose={onClose}
    >
      {isPending && <LoadingBlock />}
      {!isPending && revisions.length === 0 && (
        <p className="t-sm t-subtle">현재 히스토리가 없습니다.</p>
      )}
      {revisions.map((rev) => {
        const open = openId === rev.id;
        const extras = Object.entries(rev.extra || {}).filter(([, value]) => value);
        return (
          <div key={rev.id} className="revision">
            <button type="button" className="revision__head"
                    aria-expanded={open}
                    onClick={() => setOpenId(open ? null : rev.id)}>
              <span className="row" style={{ gap: 8, minWidth: 0 }}>
                <span className="tag tnum">v{rev.version}</span>
                {noteLabel(rev.change_note) && (
                  <span className="t-sm">{noteLabel(rev.change_note)}</span>
                )}
                {rev.edited_by && <span className="t-xs t-subtle">{rev.edited_by}</span>}
              </span>
              <span className="row" style={{ gap: 10 }}>
                <time className="t-xs t-subtle tnum">{kst(rev.created_at)}</time>
                <span className="t-xs" style={{ color: "var(--accent)" }}>
                  {open ? "닫기" : "보기"}
                </span>
              </span>
            </button>
            {open && (
              <div className="revision__body">
                {/* 부속 칸 먼저입니다 — 제목·용도만 고치는 편집이 잦고, 그때 본문은
                    그대로라 아래 본문만 보면 아무것도 안 바뀐 것처럼 보입니다. */}
                {extras.length > 0 && (
                  <dl className="info-list">
                    <div className="info-row">
                      <dt>문서 이름</dt><dd>{rev.title}</dd>
                    </div>
                    {extras.map(([key, value]) => (
                      <div className="info-row" key={key}>
                        <dt>{EXTRA_LABELS[key] || key}</dt>
                        <dd>{value}</dd>
                      </div>
                    ))}
                  </dl>
                )}
                <div className="revision__label">본문</div>
                <pre className="revision__text">
                  {rev.body || "(본문이 비어 있었습니다)"}
                </pre>
              </div>
            )}
          </div>
        );
      })}
    </Modal>
  );
}

/** 편집기 바닥의 「히스토리」 버튼 + 창. 두 화면이 같은 자리에 같은 것을 답니다.
 *
 *  **버튼에 판 번호를 안 답니다.** 지금 몇 판인지는 목록의 「버전」 열이 말하고, 버튼은
 *  누르면 무엇이 열리는지만 말하면 됩니다 (2026-08-27 운영자 지시). */
export function RevisionHistoryButton(props: {
  kind: "email_template" | "policy_source";
  documentId: number;
  title: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="btn btn--subtle btn--editor" onClick={() => setOpen(true)}>
        <Icon name="file" size={14} /> 히스토리
      </button>
      {open && <RevisionHistory {...props} onClose={() => setOpen(false)} />}
    </>
  );
}
