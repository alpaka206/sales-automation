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
  // 보드가 그리는 그 열의 총계입니다 — 위 칩과 아래 열이 다른 수를 말할 수 없습니다.
  const negotiating = data.stages.find((stage) => stage.key === "negotiation")?.total ?? 0;
  return (
    <>
      {/* 숫자는 화면 맨 위, 제목 옆입니다(운영자 지시). 예전에는 「답변 대기중인 문의」 카드
          머리에 붙어 있어서, 그 표에 대한 숫자로 읽혔습니다 — 협상중은 그 표에 아예 안 나오는
          단계라 거기 둘 수 없었고, 그래서 없었습니다.

          「협상중」은 아래 보드의 Negotiating 열 총계를 그대로 씁니다. 서버에 세는 곳을 하나
          더 만들면 같은 화면의 두 숫자가 언젠가 어긋납니다. */}
      <div className="row wrap" style={{ gap: 10, marginBottom: "var(--gap)" }}>
        <h1 className="page-title page-title--lead">문의 대시보드</h1>
        <span className="chip">오늘 접수 <b className="tnum">{c.received_today}</b></span>
        <span className="chip">답변 대기 <b className="tnum">{c.awaiting_total}</b></span>
        <span className="chip">협상중 <b className="tnum">{negotiating}</b></span>
      </div>

      {/* 최신화는 폴러 다음 회차에 돕니다. 아무 표시가 없으면 버튼이 안 눌린 것으로 읽혀
          운영자가 계속 누르게 됩니다. */}
      <section className="card card--flush mb-gap">
        <div className="section-header table-heading">
          <div className="section-header__l">
            <span className="section-header__icon"><Icon name="messages" size={17} /></span>
            <div className="section-header__title">답변 대기중인 문의</div>
          </div>
          <Link to="/messages" className="chip">전체 보기</Link>
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

      {/* `table-heading` 은 카드 **안쪽** 머리글입니다 — 좌우 22px 이 카드 패딩과 줄을
          맞추라고 있는 값입니다. 이 머리글은 카드 밖에 홀로 서 있어서 그 22px 이 아이콘
          왼쪽의 빈칸이 되고, 위의 「문의 대시보드」 제목과 줄이 어긋났습니다. */}
      <div className="section-header">
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
