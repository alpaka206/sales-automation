import { useEffect, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { getJSON } from "../lib/api";
import { Icon } from "../ui/Icon";
import { QueueTable, type QueueRow } from "../ui/QueueTable";
import { Board, type Stage } from "../ui/Board";
import { LoadingBlock } from "../ui/Loading";

// 어느 줄이 어느 탭인지는 서버가 정합니다(`dashboard._dashboard_context`). 맨 뒤의 「전체」가 기본입니다.
const QUEUE_TABS = [
  { key: "new", label: "신규 문의", empty: "새로 들어온 문의가 없습니다." },
  { key: "reply", label: "회신 도착", empty: "도착한 회신이 없습니다." },
  { key: "all", label: "전체", empty: "답변을 기다리는 문의가 없습니다." },
] as const;
const DEFAULT_TAB = QUEUE_TABS[2];

type DashboardData = {
  /** 탭 → 그 탭의 총계와 오래 기다린 순 앞쪽 줄. 숫자와 줄이 같은 계산에서 나옵니다. */
  queue: Record<(typeof QUEUE_TABS)[number]["key"], { total: number; rows: QueueRow[] }>;
  now: string;
  stage_labels: Record<string, string>;
  category_labels: Record<string, string>;
  unqualified: string[];
  manual_log_stages: string[];
  /** 단계 → 그 단계에서 고를 수 있는 Deal Detail. Won 과 Lost 만 있습니다. */
  deal_details: Record<string, string[]>;
  stages: Stage[];
  /** 토큰이 죽어 수집이 멈춘 개인 메일함. 설정 화면에만 적혀 있으면 아무도 못 본다. */
  broken_mailboxes?: { email: string; reason: string }[];
};

/** 문의 파이프라인 — 돋보기와 보드.
 *
 *  찾기는 서버가 합니다 — 보드는 열마다 15장만 그려서 화면에서 거르면 그 뒤의 티켓을 못 찾습니다. 질의 키가
 *  `dashboard` 로 시작해서 단계를 옮기거나 태그를 고른 뒤의 무효화에 결과도 같이 다시 받습니다. */
function Pipeline({ stages, manualLogStages, dealDetails }: {
  stages: Stage[];
  manualLogStages: string[];
  dealDetails: Record<string, string[]>;
}) {
  const [typed, setTyped] = useState("");
  const [query, setQuery] = useState("");
  // 치는 동안은 묻지 않습니다 — 한 글자마다 왕복하면 답이 뒤섞여 도착합니다.
  useEffect(() => {
    const timer = setTimeout(() => setQuery(typed.trim()), 250);
    return () => clearTimeout(timer);
  }, [typed]);
  const { data: found, error } = useQuery({
    queryKey: ["dashboard", "search", query],
    queryFn: () =>
      getJSON<{ total: number; stages: Stage[] }>(`/api/ui/pipeline/search?q=${encodeURIComponent(query)}`),
    enabled: query !== "",
    placeholderData: keepPreviousData,
  });
  const result = query ? found : undefined;

  return (
    <>
      {/* `table-heading` 은 카드 **안쪽** 머리글입니다 — 좌우 22px 이 카드 패딩과 줄을
          맞추라고 있는 값입니다. 이 머리글은 카드 밖에 홀로 서 있어서 그 22px 이 아이콘
          왼쪽의 빈칸이 되고, 위의 「문의 대시보드」 제목과 줄이 어긋났습니다. */}
      <div className="section-header">
        <div className="section-header__l">
          <span className="section-header__icon"><Icon name="sliders" size={17} /></span>
          <div className="section-header__title">문의 파이프라인</div>
        </div>
        <div className="row" style={{ gap: 10 }}>
          {result && <span className="t-sm t-subtle tnum">{result.total}건</span>}
          <div className="search">
            <Icon name="search" size={15} />
            <input className="input" type="search" value={typed} placeholder="이름 · 이메일 검색"
                   aria-label="티켓 검색 (이름 · 이메일)" onChange={(event) => setTyped(event.target.value)} />
          </div>
        </div>
      </div>

      {query && error ? (
        <div className="banner banner--warn" role="alert">
          <div><div className="banner__title">검색하지 못했습니다: {String(error)}</div></div>
        </div>
      ) : query && !result ? (
        <LoadingBlock />
      ) : result?.total === 0 ? (
        <div className="card"><div className="empty"><div className="empty__text">「{query}」에 해당하는 티켓이 없습니다.</div></div></div>
      ) : (
        // 찾는 말이 바뀌면 「더 보기」로 받아 둔 카드를 버려야 합니다 — 앞 검색의 카드가 섞입니다.
        <Board key={query} query={query} stages={result ? result.stages : stages}
               manualLogStages={manualLogStages} dealDetails={dealDetails} />
      )}
    </>
  );
}

export function Dashboard() {
  // 고른 탭은 주소에 둡니다 — 줄을 열었다가 「뒤로」 오면(`QueueTable` 의 `from`) 같은 탭입니다.
  const [params, setParams] = useSearchParams();
  const tab = QUEUE_TABS.find(({ key }) => key === params.get("tab")) ?? DEFAULT_TAB;
  const { data, isPending, error } = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => getJSON<DashboardData>("/api/ui/dashboard"),
    // The queue used to be re-fetched by a 30s HTMX poll that swapped the whole panel.
    // Same cadence, but it revalidates data instead of replacing DOM.
    refetchInterval: 60_000,
  });

  if (error) return <div className="banner banner--warn"><div><div className="banner__title">불러오지 못했습니다</div><div className="banner__body">{String(error)}</div></div></div>;
  if (isPending || !data) return <LoadingBlock />;

  return (
    <>
      {/* 제목 옆은 아래 표의 탭입니다 (2026-11-05 운영자 지시) — 고객 회신과 우리 초안이 한 표에 섞여 있어서 갈랐습니다.
          숫자는 그 탭의 줄 수이고, 셋 다 이미 받아 둔 값이라 눌러도 다시 받지 않습니다. */}
      <div className="row wrap" style={{ gap: 10, marginBottom: "var(--gap)" }}>
        <h1 className="page-title page-title--lead">문의 대시보드</h1>
        <div className="chip-row" role="tablist" aria-label="답변 대기중인 문의 구분">
          {QUEUE_TABS.map(({ key, label }) => (
            <button key={key} type="button" role="tab" aria-selected={key === tab.key}
                    className={`chip${key === tab.key ? " is-active" : ""}`}
                    onClick={() => setParams(key === DEFAULT_TAB.key ? {} : { tab: key }, { replace: true })}>
              {label} <b className="tnum">{data.queue[key].total}</b>
            </button>
          ))}
        </div>
      </div>

      {!!data.broken_mailboxes?.length && (
        <div className="banner banner--warn mb-gap" role="alert">
          <div>
            <div className="banner__title">개인 메일함 연결이 끊겨 그 메일이 안 들어오고 있습니다</div>
            <div className="banner__body">
              {data.broken_mailboxes.map((box) => box.email).join(", ")} — <Link to="/settings/mailboxes">설정 → 개인 메일함</Link>에서 다시 연결하세요.
              다시 연결하면 끊겼던 동안의 메일도 들어옵니다.
            </div>
          </div>
        </div>
      )}

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
          rows={data.queue[tab.key].rows}
          now={data.now}
          stageLabels={data.stage_labels}
          categoryLabels={data.category_labels}
          unqualified={data.unqualified}
          emptyText={tab.empty}
          doneColumn
        />
      </section>

      <Pipeline stages={data.stages} manualLogStages={data.manual_log_stages} dealDetails={data.deal_details} />
    </>
  );
}
