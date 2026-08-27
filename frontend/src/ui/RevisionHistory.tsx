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

/** 무슨 일이 있었나. 서버가 영어 한 낱말로 남기고(코드가 읽는 값), 화면이 사람 말로 옮깁니다 —
 *  두 벌을 저장하면 반드시 어긋납니다. 모르는 값은 그대로 보여 줍니다: 지어낸 말보다 낫습니다. */
const NOTES: Record<string, string> = {
  created: "만듦",
  edited: "고침",
  deleted: "지움",
  restored: "되돌림",
};

export function noteLabel(note: string | null): string {
  if (!note) return "고침";
  return NOTES[note] ?? note;
}

/** 「판본 기록」 창. 이메일 템플릿과 정책 문서가 **같은 컴포넌트**를 씁니다 — 보고 싶은 것이
 *  같기 때문입니다: 언제, 누가, 무엇을, 그때 본문은 무엇이었나.
 *
 *  판본은 고치기 **직전**에 남습니다. 그래서 맨 위 행은 「지금 본문」이 아니라 「직전
 *  본문」이고, 창이 그렇게 적어 둡니다 — 안 적으면 맨 위가 현재라고 읽힙니다. */
export function RevisionHistory({
  kind,
  documentId,
  title,
  currentVersion,
  onClose,
}: {
  kind: "email_template" | "policy_source";
  documentId: number;
  title: string;
  currentVersion?: number;
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
      wide
      title={`판본 기록 — ${title}`}
      description={
        currentVersion
          ? `지금은 v${currentVersion} 입니다. 아래는 저장·삭제 직전에 남긴 이전 판본입니다.`
          : "저장·삭제 직전에 남긴 이전 판본입니다."
      }
      onClose={onClose}
    >
      {isPending && <LoadingBlock />}
      {!isPending && revisions.length === 0 && (
        <p className="t-sm t-subtle">
          아직 남은 판본이 없습니다. 다음에 저장하면 지금 본문이 여기 남습니다.
        </p>
      )}
      {revisions.map((rev) => {
        const open = openId === rev.id;
        return (
          <div key={rev.id} className="history-item" style={{ marginBottom: 8 }}>
            <div>
              <button
                type="button"
                className="row-between"
                style={{ width: "100%", background: "none", border: 0, padding: 0, cursor: "pointer", textAlign: "left" }}
                onClick={() => setOpenId(open ? null : rev.id)}
              >
                <span className="row" style={{ gap: 8 }}>
                  <span className="tag tnum">v{rev.version}</span>
                  <span className="t-sm">{noteLabel(rev.change_note)}</span>
                  {rev.edited_by && <span className="t-xs t-subtle">{rev.edited_by}</span>}
                </span>
                <span className="row" style={{ gap: 8 }}>
                  <time className="t-xs t-subtle tnum">{kst(rev.created_at)}</time>
                  <span className="t-xs" style={{ color: "var(--accent)" }}>
                    {open ? "닫기" : "본문 보기"}
                  </span>
                </span>
              </button>
              {open && (
                <div className="msg-body msg-body--inset mono" style={{ marginTop: 8, fontSize: 12.5 }}>
                  {rev.body || <span className="t-subtle">본문이 비어 있었습니다.</span>}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </Modal>
  );
}

/** 편집기 바닥의 「판본 기록」 버튼 + 창. 두 화면이 같은 자리에 같은 것을 답니다. */
export function RevisionHistoryButton(props: {
  kind: "email_template" | "policy_source";
  documentId: number;
  title: string;
  currentVersion?: number;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" className="btn btn--subtle btn--sm" onClick={() => setOpen(true)}>
        <Icon name="file" size={14} /> 판본 기록
        {props.currentVersion ? <span className="tnum"> · v{props.currentVersion}</span> : null}
      </button>
      {open && <RevisionHistory {...props} onClose={() => setOpen(false)} />}
    </>
  );
}
