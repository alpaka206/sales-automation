import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
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
  const [params] = useSearchParams();
  const backfill = params.get("backfill");
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

      {/* 최신화는 폴러 다음 회차에 돕니다. 아무 표시가 없으면 버튼이 안 눌린 것으로 읽혀
          운영자가 계속 누르게 됩니다. */}
      {backfill && (
        <div className="card mb-gap">
          <p className="t-xs" style={{ margin: 0 }}>
            {backfill === "queued"
              ? "허브스팟 최신화를 예약했습니다. 다음 폴러 회차(최대 10분)에 파이프라인 전체를 훑어 빠진 티켓을 채웁니다."
              : "허브스팟 최신화를 예약하지 못했습니다. 운영 로그를 확인해 주세요."}
          </p>
        </div>
      )}

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
        {/* 라우트는 오래전부터 있었는데 누를 곳이 없었습니다. 평소에는 10분 폴러가 알아서
            맞추지만, 오래전에 만들어져 그 뒤로 한 번도 안 건드려진 티켓은 폴러의 검색
            창(마지막 스윕 이후 변경분) 밖에 있어 안 걸립니다. 그때 누르는 버튼입니다 —
            파이프라인 전체를 처음부터 훑습니다. 읽기 전용이고 메일도 초안도 안 만듭니다. */}
        <form method="post" action="/pipeline/backfill">
          <button className="btn btn--subtle btn--sm" type="submit"
                  title="허브스팟 파이프라인 전체를 다시 훑어 빠진 티켓을 채웁니다">
            <Icon name="refresh" size={14} /> 허브스팟에서 최신화
          </button>
        </form>
      </div>

      <Board stages={data.stages} manualLogStages={data.manual_log_stages}
             dealDetails={data.deal_details} />
    </>
  );
}
