import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { getJSON } from "../lib/api";
import { QueueTable, type QueueRow } from "../ui/QueueTable";
import { Loading, Refreshing } from "../ui/Loading";

type MessagesData = {
  messages: QueueRow[];
  filter_status: string;
  filter_stage: string;
  filter_sort: string;
  stage_chips: [string, string][];
  stage_labels: Record<string, string>;
  category_labels: Record<string, string>;
  unqualified: string[];
  now: string;
};

const STATUS_CHIPS = [
  ["awaiting", "발송 대기"],
  ["sent", "발송 완료"],
] as const;
const SORT_CHIPS = [
  ["oldest", "오래된 순"],
  ["newest", "최신순"],
] as const;

export function Messages() {
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "awaiting";
  const stage = params.get("stage") ?? "";
  const sort = params.get("sort") ?? "oldest";

  const { data, isPending, isFetching } = useQuery({
    queryKey: ["messages", status, stage, sort],
    queryFn: () =>
      getJSON<MessagesData>(`/api/ui/messages?status=${status}&stage=${stage}&sort=${sort}`),
    // The Jinja list polled itself every 15s; the cache revalidates on the same beat.
    refetchInterval: 15_000,
    // A filter is a different cache key, so switching one used to blank the table while
    // the new rows loaded. Keep showing the rows that are there — dimmed, with the
    // spinner — instead of throwing away what the operator is reading.
    placeholderData: keepPreviousData,
  });

  const set = (next: Record<string, string>) =>
    setParams({ status, stage, sort, ...next }, { replace: true });

  return (
    <>
      <div className="page-header">
        <div><h1 className="page-title">회신 및 검토</h1></div>
      </div>

      <div className="filter-bar mb-gap">
        <div className="chip-row">
          {STATUS_CHIPS.map(([value, label]) => (
            <button
              key={value}
              type="button"
              className={`chip${status === value ? " is-active" : ""}`}
              onClick={() => set({ status: value })}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="chip-row">
          {SORT_CHIPS.map(([value, label]) => (
            <button
              key={value}
              type="button"
              className={`chip${sort === value ? " is-active" : ""}`}
              onClick={() => set({ sort: value })}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Empty for 발송 대기: that bucket holds one stage, so 전체/New would be two chips
          over the same rows. The server decides — this only renders what it sends. */}
      {data && data.stage_chips.length > 0 && (
        <div className="chip-row mb-gap">
          {data.stage_chips.map(([value, label]) => (
            <button
              key={value}
              type="button"
              className={`chip${data.filter_stage === value ? " is-active" : ""}`}
              onClick={() => set({ stage: value })}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      <div className="card card--flush">
        {isPending || !data ? (
          <Loading columns={7} />
        ) : (
          <Refreshing active={isFetching}>
            <QueueTable
              rows={data.messages}
              now={data.now}
              stageLabels={data.stage_labels}
              categoryLabels={data.category_labels}
              unqualified={data.unqualified}
              emptyText="조건에 맞는 답변이 없습니다."
            />
          </Refreshing>
        )}
      </div>
    </>
  );
}
