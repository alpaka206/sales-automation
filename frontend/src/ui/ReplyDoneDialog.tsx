import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { postForm } from "../lib/api";
import { kst } from "../lib/format";
import { ActionButton } from "./ActionButton";
import { Icon } from "./Icon";
import { Modal } from "./Modal";

export type QueueReply = {
  /** 어느 회신인지 — 서버가 준 그대로 돌려보낸다. */
  ref: string;
  at: string;
  channel: string;
  text: string;
  /** 한국어가 아닌 회신의 한국어 요약. 원문을 대신하지 않는다 — 요약이 빠뜨린 질문을 놓치면 안 된다. */
  korean: string | null;
};

/** 답변 대기의 [완료] — 어느 회신을 뺄지 묻는 창.
 *
 *  아무것도 안 고른 채로 열린다: 질문을 실수로 빼는 것이 가장 비싼 실수다. 회신이 하나면 고를 것이 없으니 그것을
 *  보여 주고 묻기만 한다. 지우는 것이 아니라 목록에서 빼는 것이라 삭제 확인 창(`DeleteDialog`)이 아니고, 「삭제」라고
 *  적지 않는다. */
export function ReplyDoneDialog({
  conversationId,
  replies,
  onClose,
}: {
  conversationId: number;
  replies: QueueReply[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const single = replies.length === 1;
  const [picked, setPicked] = useState<Set<string>>(() => new Set(single ? [replies[0].ref] : []));
  const [error, setError] = useState("");
  const allPicked = picked.size === replies.length;

  const toggle = (ref: string) => {
    const next = new Set(picked);
    if (next.has(ref)) next.delete(ref);
    else next.add(ref);
    setPicked(next);
  };
  const submit = async () => {
    setError("");
    try {
      await postForm(`/tickets/${conversationId}/replies-done`, { refs: [...picked].join(",") });
      await queryClient.invalidateQueries();
      onClose();
    } catch (failed) {
      setError(`실패: ${String(failed)}`);
    }
  };

  const content = (reply: QueueReply) => (
    <div style={{ minWidth: 0 }}>
      <div className="t-xs t-subtle">
        <span className="tnum">{kst(reply.at, "md-hm")}</span>
        {reply.channel && ` · ${reply.channel}`}
      </div>
      <div className="t-sm" style={{ overflowWrap: "anywhere" }}>{reply.text || "(내용 없음)"}</div>
      {reply.korean && <div className="t-xs t-subtle" style={{ marginTop: 2 }}>{reply.korean}</div>}
    </div>
  );

  return (
    <Modal
      wide
      title={single ? "이 회신을 완료 처리할까요?" : "어떤 회신을 완료 처리할까요?"}
      description="답할 필요가 없는 회신을 답변 대기에서 뺍니다. 메일과 기록은 티켓에 그대로 남고, 고객이 새로 쓰면 다시 뜹니다."
      onClose={onClose}
      actions={
        <ActionButton className="btn btn--ok" pending="처리 중" disabled={picked.size === 0} onClick={submit}>
          <Icon name="check" size={15} /> 완료 처리{!single && picked.size > 0 && ` ${picked.size}건`}
        </ActionButton>
      }
    >
      <div className="done-pick">
        {single ? (
          <div className="done-pick__row">{content(replies[0])}</div>
        ) : (
          <>
            {replies.map((reply) => (
              <label key={reply.ref} className="done-pick__row" style={{ cursor: "pointer" }}>
                <input type="checkbox" checked={picked.has(reply.ref)} onChange={() => toggle(reply.ref)}
                       style={{ marginTop: 3 }} />
                {content(reply)}
              </label>
            ))}
            <div>
              <button type="button" className="btn btn--ghost btn--sm"
                      onClick={() => setPicked(new Set(allPicked ? [] : replies.map((reply) => reply.ref)))}>
                {allPicked ? "전체 해제" : "전체 선택"}
              </button>
            </div>
          </>
        )}
        {error && <div className="t-xs" role="alert" style={{ color: "var(--danger)" }}>{error}</div>}
      </div>
    </Modal>
  );
}
