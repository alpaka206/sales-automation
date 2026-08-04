import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getJSON, postForm } from "../lib/api";
import { kst } from "../lib/format";
import { Icon } from "./Icon";
import { Modal } from "./Modal";
import { InteractionForm } from "./InteractionForm";
import { SyncBanner, syncStateFrom, type SyncState } from "./SyncBanner";

export type Card = {
  conversation_id: number;
  ticket_id: string | null;
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

export function Board({ stages, manualLogStages }: { stages: Stage[]; manualLogStages: string[] }) {
  const queryClient = useQueryClient();
  // Pages fetched by "더 보기", kept beside the query data: the query owns the first
  // page, this owns what the operator asked for on top of it.
  const [extra, setExtra] = useState<Record<string, Card[]>>({});
  const [dragging, setDragging] = useState<Card | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [sync, setSync] = useState<SyncState>(null);
  const [logging, setLogging] = useState<Card | null>(null);

  async function move(card: Card, stage: string) {
    if (card.stage === stage) return;      // a no-op drop skips the write entirely
    setSaving(true);
    try {
      const response = await postForm(
        `/pipeline/conversations/${card.conversation_id}/stage`,
        { stage },
      );
      // The handler redirects to /?sync=ok|partial|local. Whether HubSpot and the sales
      // workbook took the move is the part worth saying, and it is only said here.
      setSync(syncStateFrom(response));
      setExtra({});
      await queryClient.invalidateQueries();
    } catch {
      setSync("partial");
    } finally {
      setSaving(false);
    }
  }

  async function loadMore(stage: Stage, loaded: number) {
    const page = await getJSON<Page>(`/api/ui/pipeline/${stage.key}/cards?offset=${loaded}`);
    setExtra((prev) => ({ ...prev, [stage.key]: [...(prev[stage.key] ?? []), ...page.cards] }));
  }

  return (
    <>
      <SyncBanner state={sync} onDismiss={() => setSync(null)} />
      <div className={`kanban${saving ? " is-saving" : ""}`}>
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
                      <strong>{card.company || card.name}</strong>
                      <span>{card.name} · {card.country || "국가 미확인"}</span>
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
                  <button
                    type="button"
                    className="kanban-more btn btn--subtle btn--sm"
                    style={{ width: "100%" }}
                    onClick={() => void loadMore(stage, cards.length)}
                  >
                    {stage.total - cards.length}건 더 보기
                  </button>
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
