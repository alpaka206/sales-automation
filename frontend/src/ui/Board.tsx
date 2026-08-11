import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getJSON, postForm } from "../lib/api";
import { kst } from "../lib/format";
import { Icon } from "./Icon";
import { ActionButton } from "./ActionButton";
import { Modal } from "./Modal";
import { InteractionForm } from "./InteractionForm";
import { SyncBanner, syncStateFrom, type SyncState } from "./SyncBanner";

export type Card = {
  conversation_id: number;
  ticket_id: string | null;
  subject: string | null;
  contact_id: number;
  company: string | null;
  name: string;
  email: string | null;
  country: string | null;
  client_id: number | null;
  link_message_id: number | null;
  last_activity: string;
  stage: string;
};
export type Stage = { key: string; label: string; total: number; cards: Card[] };

type Page = { cards: Card[]; next_offset: number; has_more: boolean };

/** 카드 한 장을 다른 열로 옮긴 결과. 열 머리의 수(total)도 같이 맞춘다 — 카드는 옮겨졌는데
 *  숫자만 그대로면 서버 답이 오기 전까지 둘이 어긋나 보인다. */
function withCardMoved(stages: Stage[], card: Card, to: string): Stage[] {
  return stages.map((column) => {
    if (column.key === card.stage) {
      return {
        ...column,
        total: Math.max(column.total - 1, 0),
        cards: column.cards.filter((c) => c.conversation_id !== card.conversation_id),
      };
    }
    if (column.key === to) {
      return { ...column, total: column.total + 1, cards: [{ ...card, stage: to }, ...column.cards] };
    }
    return column;
  });
}

export function Board({ stages, manualLogStages }: { stages: Stage[]; manualLogStages: string[] }) {
  const queryClient = useQueryClient();
  // Pages fetched by "더 보기", kept beside the query data: the query owns the first
  // page, this owns what the operator asked for on top of it.
  const [extra, setExtra] = useState<Record<string, Card[]>>({});
  const [dragging, setDragging] = useState<Card | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const [sync, setSync] = useState<SyncState>(null);
  const [logging, setLogging] = useState<Card | null>(null);

  async function move(card: Card, stage: string) {
    if (card.stage === stage) return;      // a no-op drop skips the write entirely
    // 놓는 순간 카드를 옮깁니다. 이 POST 는 우리 DB 를 쓴 뒤 HubSpot 티켓과 워크북까지
    // 갔다 오므로 초 단위로 걸리는데, 그 동안 카드가 원래 자리에 그대로 있으면 드롭이
    // 안 먹은 것처럼 보이고 한참 뒤에 혼자 움직인다. 서버가 지는 쪽은 없다 —
    // _set_conversation_stage 가 로컬 이동을 먼저 커밋하고, HubSpot·워크북은 그 뒤에
    // 따라가되 그쪽이 안 되어도 되돌리지 않는다. 그래서 여기서 미리 그려도 어긋나지 않는다.
    const previous = queryClient.getQueryData<{ stages: Stage[] }>(["dashboard"]);
    queryClient.setQueryData(["dashboard"], (old?: { stages: Stage[] }) =>
      old ? { ...old, stages: withCardMoved(old.stages, card, stage) } : old,
    );
    setExtra({});   // 「더 보기」로 받아 둔 쪽에 있던 카드였다면 그쪽에서도 사라져야 한다
    try {
      const response = await postForm(
        `/pipeline/conversations/${card.conversation_id}/stage`,
        { stage },
      );
      // The handler redirects to /?sync=ok|partial|local. Whether HubSpot and the sales
      // workbook took the move is the part worth saying, and it is only said here.
      setSync(syncStateFrom(response));
      // 이 화면 것만. 키 없이 부르면 앱이 캐시해 둔 모든 질의가 무효가 되고, 왕복 하나가
      // 200ms 인 환경에서는 카드 한 장 옮긴 값으로 화면 전체를 다시 받는다.
      await queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    } catch {
      // 저장이 안 되면 있던 자리로 되돌린다 — 화면에만 옮겨진 채로 두면 옮겼다고 믿게 된다.
      if (previous) queryClient.setQueryData(["dashboard"], previous);
      setSync("partial");
    }
  }

  async function loadMore(stage: Stage, loaded: number) {
    const page = await getJSON<Page>(`/api/ui/pipeline/${stage.key}/cards?offset=${loaded}`);
    setExtra((prev) => ({ ...prev, [stage.key]: [...(prev[stage.key] ?? []), ...page.cards] }));
  }

  return (
    <>
      <SyncBanner state={sync} onDismiss={() => setSync(null)} />
      {/* 저장하는 동안 보드를 흐리게 하고 클릭을 막던 클래스가 있었습니다. 카드가 이미
          옮겨져 보이는데 화면만 몇 초 얼어 있는 셈이라 앞뒤가 안 맞았습니다 — 기다릴 것이
          없으면 기다리는 표시도 없어야 합니다. HubSpot·워크북이 받았는지는 위 배너가
          늦게라도 말해 줍니다. */}
      <div className="kanban">
        {stages.map((stage) => {
          const cards = [...stage.cards, ...(extra[stage.key] ?? [])];
          const canLog = manualLogStages.includes(stage.key);
          return (
            <section key={stage.key} className="kanban-column" id={`stage-${stage.key}`}>
              <header className="kanban-column__header">
                <strong>{stage.label}</strong>
                {/* The column's real size, not how many cards it drew. */}
                <span className="kanban-column__count tnum">{stage.total}</span>
              </header>
              <div
                className={`kanban-column__body${over === stage.key ? " is-over" : ""}`}
                onDragOver={(event) => {
                  event.preventDefault();
                  event.dataTransfer.dropEffect = "move";
                  if (over !== stage.key) setOver(stage.key);
                }}
                onDragLeave={(event) => {
                  // Only when the pointer leaves the column itself: dragleave also fires
                  // for every child it crosses, which flickers the highlight.
                  if (!event.currentTarget.contains(event.relatedTarget as Node)) {
                    setOver((current) => (current === stage.key ? null : current));
                  }
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  setOver(null);
                  if (dragging) void move(dragging, stage.key);
                  setDragging(null);
                }}
              >
                {cards.length === 0 && (
                  <div className="kanban-empty">여기로 카드를 옮길 수 있습니다.</div>
                )}
                {cards.map((card) => (
                  <article
                    key={card.conversation_id}
                    className={`pipeline-card${canLog ? " pipeline-card--logged" : ""}${
                      dragging?.conversation_id === card.conversation_id ? " is-dragging" : ""
                    }`}
                    draggable
                    onDragStart={(event) => {
                      setDragging(card);
                      event.dataTransfer.effectAllowed = "move";
                      // Firefox refuses to start a drag unless some data is set.
                      event.dataTransfer.setData("text/plain", String(card.conversation_id));
                      // Drag the whole card box, grabbed where the pointer went down —
                      // without this the ghost is a browser-chosen fragment.
                      const box = event.currentTarget.getBoundingClientRect();
                      event.dataTransfer.setDragImage(
                        event.currentTarget,
                        event.clientX - box.left,
                        event.clientY - box.top,
                      );
                    }}
                    onDragEnd={() => { setDragging(null); setOver(null); }}
                  >
                    <Link
                      to={card.link_message_id
                        ? `/messages/${card.link_message_id}`
                        : `/customers/${card.contact_id}`}
                      draggable={false}
                    >
                      {/* 제목은 티켓 이름입니다. 회사 이름이었는데, 한 회사가 문의를
                          여러 건 넣으면 카드 제목이 전부 같은 글자가 됩니다. */}
                      <strong>{card.subject || "(제목 없음)"}</strong>
                      <span>
                        {[card.company, card.name].filter(Boolean).join(" · ")}
                        {" · "}{card.country || "국가 미확인"}
                      </span>
                      <small>{card.email || "-"}</small>
                      <small>문의 #{card.ticket_id || card.conversation_id} · Client ID {card.client_id ?? "—"}</small>
                      <small>{kst(card.last_activity, "md-hm")}</small>
                    </Link>
                    {canLog && (
                      <button
                        type="button"
                        className="pipeline-card__log"
                        draggable={false}
                        title="소통 기록 추가"
                        aria-label="소통 기록 추가"
                        aria-haspopup="dialog"
                        onClick={() => setLogging(card)}
                      >
                        <Icon name="plus" size={14} />
                      </button>
                    )}
                  </article>
                ))}
                {cards.length < stage.total && (
                  <ActionButton
                    className="kanban-more btn btn--subtle btn--sm"
                    style={{ width: "100%" }}
                    pending="불러오는 중"
                    onClick={() => loadMore(stage, cards.length)}
                  >
                    {stage.total - cards.length}건 더 보기
                  </ActionButton>
                )}
              </div>
            </section>
          );
        })}
      </div>

      {/* ONE form for the whole board, pointed at the card whose + was clicked. */}
      {logging && (
        <Modal
          title="소통 기록 추가"
          description={`${logging.company || logging.name} · 이 문의에 대해 오간 연락을 남깁니다.`}
          wide
          onClose={() => setLogging(null)}
        >
          <div style={{ marginTop: 16 }}>
            <InteractionForm
              contactId={logging.contact_id}
              conversationId={logging.conversation_id}
              onSaved={() => {
                setLogging(null);
                void queryClient.invalidateQueries();
              }}
            />
          </div>
        </Modal>
      )}
    </>
  );
}
