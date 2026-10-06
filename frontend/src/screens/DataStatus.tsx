import { Icon } from "../ui/Icon";
import { DataTable, type Column } from "../ui/DataTable";
import { stamp, useUsageFile, useUsageStatus } from "../lib/usageData";
import { useUsageSource } from "../lib/usageSource";

/** 가공된 스냅샷의 상태와 제품 전체 지표를 그리는 화면(「데이터 분석」).
 *
 *  이 화면은 우리 서버에서 오지만 **데이터는 우리 서버를 안 지납니다.** 서버는 가공 레포의 이름과
 *  읽기 토큰만 건네고(`lib/usageSource.ts`), 브라우저가 GitHub 에서 직접 받아 그립니다
 *  (`lib/usageData.ts`). 2026-10-01 까지는 PC 마다 깐 로컬 에이전트가 이 자리였고, 내려받기 · 버전
 *  확인 · 「지금 받기」는 그것과 함께 없어졌습니다 — 가공은 하루 한 번 저절로 돕니다.
 */

type Metric = {
  metric: string; label: string;
  columns: string[]; rows: (string | number | null)[][];
  suppressed_groups: number;
};

function Table({ metric }: { metric: Metric }) {
  // 표는 콘솔의 `DataTable` 하나입니다 — 열 정의만 넘깁니다. 가공기가 준 열 이름이 곧 머리글.
  type Cells = (string | number | null)[];
  const columns: Column<Cells>[] = metric.columns.map((name, j) => ({
    label: name,
    className: metric.rows.some((r) => typeof r[j] === "number") ? "tnum" : undefined,
    cell: (row) => (typeof row[j] === "number" ? (row[j] as number).toLocaleString() : (row[j] ?? "—")),
  }));
  return (
    <section className="mb-gap">
      <div className="section-header table-heading">
        <div className="section-header__l">
          <span className="section-header__icon"><Icon name="file" size={16} /></span>
          <div className="section-header__title">{metric.label}</div>
        </div>
        <div className="t-sm td-subtle tnum">
          {metric.rows.length.toLocaleString()}행
          {metric.suppressed_groups > 0 && ` · 작은 그룹 ${metric.suppressed_groups}개 제외`}
        </div>
      </div>
      <DataTable columns={columns} rows={metric.rows.slice(0, 100)} rowKey={(_, i) => i} empty="행이 없습니다" />
    </section>
  );
}

export function DataStatus() {
  const usage = useUsageSource();
  const status = useUsageStatus(usage.source);
  const metrics = useUsageFile<{ metrics: Metric[] }>(usage.source, "metrics.json");
  const manifest = status.manifest;
  const problem = usage.problem ?? status.problem ?? metrics.problem;

  const header = (
    <div className="page-header">
      <div>
        <h1 className="page-title">데이터 분석</h1>
        <div className="t-sm td-subtle" style={{ marginTop: 4 }}>
          매일 09:15 에 가공 레포의 GitHub Actions 가 스냅샷을 가공해 올립니다 — 브라우저가 GitHub 에서 직접 받아
          그리고, 우리 서버는 데이터를 거치지 않습니다.
        </div>
      </div>
    </div>
  );

  if (usage.busy || status.busy) {
    return <>{header}<div className="card">사용 데이터를 불러오는 중…</div></>;
  }

  return (
    <>
      {header}

      {/* **시각 없이 그리는 숫자는 없습니다.** 낡은 값이 맞는 값처럼 보이는 것이
          이 저장소가 여러 번 당한 사고입니다. */}
      {manifest && (
        <div className={`card mb-gap${status.stale ? " card--warn" : ""}`}>
          <div className="row-between">
            <div>
              스냅샷 시각 <strong>{stamp(manifest.snapshot_at)}</strong>
              {" · "}가공 시각 <strong>{stamp(manifest.exported_at)}</strong>
              {" · "}커밋 <code>{manifest.snapshot_commit}</code>
              {status.stale && <strong> · ⚠️ 낡은 데이터입니다</strong>}
            </div>
            <div className="t-sm td-subtle tnum">스페이스 {manifest.spaces.toLocaleString()}개</div>
          </div>
          {status.stale && (
            <div className="t-xs t-subtle" style={{ marginTop: 6 }}>
              스냅샷이 36시간 넘게 묵었습니다 — 가공 레포의 Actions 를 확인하세요.
            </div>
          )}
        </div>
      )}

      {usage.configured === false && (
        <div className="card card--warn mb-gap">
          <strong>사용 데이터가 연결되지 않았습니다.</strong>
          <div className="t-sm td-subtle" style={{ marginTop: 6 }}>
            관리자가 아직 연결하지 않았습니다 — Render 대시보드의 <code>USAGE_DATA_REPO</code> · <code>USAGE_DATA_TOKEN</code>
          </div>
        </div>
      )}

      {problem && (
        <div className="card card--warn mb-gap">
          <strong>사용 데이터를 받지 못했습니다.</strong>
          <div className="t-sm" style={{ marginTop: 6 }}>사유: {problem}</div>
        </div>
      )}

      {metrics.data?.data.metrics.map((m) => <Table key={m.metric} metric={m} />)}
    </>
  );
}
