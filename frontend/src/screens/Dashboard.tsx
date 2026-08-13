import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getJSON } from "../lib/api";
import { Icon } from "../ui/Icon";
import { QueueTable, type QueueRow } from "../ui/QueueTable";
import { Board, type Stage } from "../ui/Board";
import { LoadingBlock } from "../ui/Loading";

type DashboardData = {
  queue: QueueRow[];
  now: string;
  counters: {
    received_today: number;
    awaiting_total: number;
  };
  stage_labels: Record<string, string>;
  category_labels: Record<string, string>;
  unqualified: string[];
  manual_log_stages: string[];
  /** 단계 → 그 단계에서 고를 수 있는 Deal Detail. Won 과 Lost 만 있습니다. */
  deal_details: Record<string, string[]>;
  stages: Stage[];
};

export function Dashboard() {
  const { data, isPending, error } = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => getJSON<DashboardData>("/api/ui/dashboard"),
    // The queue used to be re-fetched by a 30s HTMX poll that swapped the whole panel.
    // Same cadence, but it revalidates data instead of replacing DOM.
    refetchInterval: 60_000,
  });

  if (error) return <div className="banner banner--warn"><div><div className="banner__title">불러오지 못했습니다</div><div className="banner__body">{String(error)}</div></div></div>;
  if (isPending || !data) return <LoadingBlock />;

  const c = data.counters;
  return (
    <>
      <h1 className="sr-only">문의 대시보드</h1>

      <section className="card card--flush mb-gap">
        <div className="section-header table-heading">
          <div className="section-header__l">
            <span className="section-header__icon"><Icon name="messages" size={17} /></span>
            <div className="section-header__title">답변 대기중인 문의</div>
          </div>
          <div className="queue-counters">
            <span><em>오늘 접수</em><b className="tnum">{c.received_today}</b></span>
            {/* New/Negotiating 를 뺀 이유: 발송 대기가 New 만 보여주므로 ALL 이 곧 New
                입니다. 같은 수를 세 칸에 적으면 다르다고 읽힙니다. */}
            <span><em>ALL</em><b className="tnum">{c.awaiting_total}</b></span>
            <Link to="/messages" className="chip">전체 보기</Link>
          </div>
        </div>
        <QueueTable
          rows={data.queue}
          now={data.now}
          stageLabels={data.stage_labels}
          categoryLabels={data.category_labels}
          unqualified={data.unqualified}
          emptyText="답변을 기다리는 문의가 없습니다."
        />
      </section>

      <div className="section-header table-heading">
        <div className="section-header__l">
          <span className="section-header__icon"><Icon name="sliders" size={17} /></span>
          <div className="section-header__title">문의 파이프라인</div>
        </div>
      </div>

      <Board stages={data.stages} manualLogStages={data.manual_log_stages}
             dealDetails={data.deal_details} />
    </>
  );
}
