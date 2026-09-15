import { useCallback, useEffect, useState } from "react";
import { Icon } from "../ui/Icon";
import { ActionButton } from "../ui/ActionButton";
import { DataTable, type Column } from "../ui/DataTable";
import { AGENT_DOWNLOADS, AGENT_RELEASES, agentFetch, useAgent, type AsOf } from "../lib/agent";

/** 스냅샷 데이터를 **로컬 에이전트**에서 가져와 그리는 화면.
 *
 *  이 화면은 우리 서버에서 오지만 **데이터는 우리 서버를 안 지납니다.** 브라우저가
 *  `http://127.0.0.1:<포트>` 를 직접 부르고, 집계만 받아 그립니다. 원본 CSV 도 계산도
 *  그 PC 안에서 끝납니다. 연결 방식과 브라우저별 제약은 `lib/agent.ts` 에 있습니다.
 */

type Metric = {
  metric: string; label: string; as_of: AsOf;
  columns: string[]; rows: (string | number | null)[][];
  suppressed_groups: number; computed_ms: number;
};

function Table({ metric }: { metric: Metric }) {
  // 표는 콘솔의 `DataTable` 하나입니다 — 열 정의만 넘깁니다. 에이전트가 준 열 이름이 곧 머리글.
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
          {metric.rows.length.toLocaleString()}행 · {metric.computed_ms}ms
          {metric.suppressed_groups > 0 && ` · 작은 그룹 ${metric.suppressed_groups}개 제외`}
        </div>
      </div>
      <DataTable columns={columns} rows={metric.rows.slice(0, 100)} rowKey={(_, i) => i} empty="행이 없습니다" />
    </section>
  );
}

export function DataAgent() {
  const agent = useAgent();
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [problem, setProblem] = useState<string | null>(null);

  const loadMetrics = useCallback(async () => {
    if (!agent.pair || !agent.status) { setMetrics([]); return; }
    const got: Metric[] = [];
    for (const name of agent.status.metrics) {
      try { got.push(await agentFetch<Metric>(agent.pair, `/v1/metrics/${name}`)); }
      catch { /* 한 지표가 계약을 위반해도 나머지는 그린다 */ }
    }
    setMetrics(got);
  }, [agent.pair, agent.status]);

  useEffect(() => { void loadMetrics(); }, [loadMetrics]);

  const isSafari = /^((?!chrome|android|crios|fxios).)*safari/i.test(navigator.userAgent);
  const isMac = /Mac/i.test(navigator.platform);
  const status = agent.status;
  const shown = problem ?? agent.problem;

  if (agent.busy) {
    return (
      <>
        <div className="page-header"><div><h1 className="page-title">데이터 분석</h1></div></div>
        <div className="card">에이전트에 연결하는 중…</div>
      </>
    );
  }

  return (
    <>
      <div className="row-between" style={{ marginBottom: 14 }}>
        <div className="page-header" style={{ marginBottom: 0 }}>
          <div><h1 className="page-title">데이터 분석</h1></div>
        </div>
        <div className="row" style={{ gap: 8 }}>
          {/* 에이전트를 깨웁니다. **설치돼 있어야** 동작하고, 설치 여부는 안정적으로
              알 수 없습니다(브라우저마다 감지 방법이 다르고 전부 우회 기법입니다).
              그래서 안 열리면 아래 안내로 떨어집니다. */}
          <a className="btn btn--sm" href="persodata://open">
            <Icon name="play" size={14} /> 에이전트 열기
          </a>
          {agent.pair && (
            <ActionButton className="btn btn--sm" pending="다시 받는 중"
                          onClick={() => agentFetch(agent.pair!, "/v1/refresh", "POST")
                            .then(() => agent.reconnect())
                            .catch((e) => setProblem(e instanceof Error ? e.message : String(e)))}>
              <Icon name="refresh" size={14} /> 지금 받기
            </ActionButton>
          )}
        </div>
      </div>

      {/* **`as_of` 없이 그리는 숫자는 없습니다.** 낡은 값이 맞는 값처럼 보이는 것이
          이 저장소가 여러 번 당한 사고입니다. */}
      {status && (
        <div className={`card mb-gap${status.as_of.stale ? " card--warn" : ""}`}>
          <div className="row-between">
            <div>
              데이터 기준 <strong>{status.snapshot_at?.slice(0, 16).replace("T", " ") ?? status.as_of.committed_at?.slice(0, 16).replace("T", " ") ?? "(알 수 없음)"}</strong>
              {" · "}커밋 <code>{status.as_of.commit}</code>
              {" · "}pull: {status.as_of.pull}
              {status.as_of.stale && <strong> · ⚠️ 낡은 데이터입니다</strong>}
            </div>
            <div className="t-sm td-subtle tnum">{status.folder_mb.toLocaleString()}MB</div>
          </div>
          <div className="t-xs t-subtle" style={{ marginTop: 6 }}>
            원본 CSV 와 계산은 이 PC 에서만 돕니다 — 집계만 이 화면으로 옵니다. 수주 고객 화면의
            「마지막 작업」·「사용 상태」와 사용 현황 세 섹션도 같은 에이전트가 답합니다.
          </div>
        </div>
      )}

      {!agent.pair && (
        <div className="card mb-gap">
          <strong>에이전트가 연결되지 않았습니다.</strong>
          <div className="t-sm td-subtle" style={{ marginTop: 6 }}>
            내려받은 <code>perso-agent</code> 를 스냅샷 폴더(<code>perso-data-snapshot</code>) 옆에서 실행하면,
            그것이 이 화면을 다시 열면서 연결합니다. 위 「에이전트 열기」로도 깨울 수 있습니다(이미 설치돼 있을 때).
          </div>
          <Downloads isMac={isMac} />
        </div>
      )}

      {shown && (
        <div className="card card--warn mb-gap">
          <strong>에이전트를 부르지 못했습니다.</strong>
          <div className="t-sm" style={{ marginTop: 6 }}>사유: {shown}</div>
          <ul className="t-sm td-subtle" style={{ margin: "8px 0 0 18px" }}>
            <li>에이전트가 켜져 있나요? 켜져 있어야 이 화면이 숫자를 받습니다.</li>
            <li>Chrome·Edge 는 처음 한 번 <strong>「로컬 네트워크 접근 허용」</strong>을 묻습니다.
                거부했다면 주소창 왼쪽 자물쇠에서 다시 허용할 수 있습니다.</li>
            {isSafari && (
              <li><strong>Safari 는 이 길을 막습니다</strong>(프롬프트도 없습니다).
                  에이전트가 띄운 <code>http://127.0.0.1:{agent.pair?.port ?? 43110}</code> 을 직접 열면
                  <strong> 같은 화면·같은 숫자</strong>를 봅니다.</li>
            )}
          </ul>
        </div>
      )}

      {agent.pair && status && <Downloads isMac={isMac} compact />}

      {metrics.map((m) => <Table key={m.metric} metric={m} />)}
    </>
  );
}

/** 내려받기. 저장소가 공개라 GitHub Release 자산은 인증 없이 받아집니다 — 바이너리는 git 에
 *  넣지 않고(100MB 가 커밋마다 따라온다), Actions 가 태그마다 빌드해 Release 에 올립니다. */
function Downloads({ isMac, compact }: { isMac: boolean; compact?: boolean }) {
  const body = (
    <>
      <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: compact ? 0 : 10 }}>
        <a className={`btn btn--sm${!isMac ? " btn--primary" : ""}`} href={AGENT_DOWNLOADS.windows}>Windows (.exe)</a>
        <a className={`btn btn--sm${isMac ? " btn--primary" : ""}`} href={AGENT_DOWNLOADS.macArm}>Mac (Apple Silicon)</a>
        <a className="btn btn--sm" href={AGENT_DOWNLOADS.macIntel}>Mac (Intel)</a>
        <a className="t-sm td-subtle" href={AGENT_RELEASES} target="_blank" rel="noreferrer">모든 버전</a>
      </div>
      <div className="t-xs t-subtle" style={{ marginTop: 8 }}>
        스냅샷 저장소를 먼저 clone 해 두세요 (<code>git clone … perso-data-snapshot</code>) — 비공개 저장소라
        각자의 GitHub 권한으로 받습니다. 에이전트에는 토큰이 없습니다.
        {isMac && <> Mac 은 내려받은 파일에 격리 속성이 붙어 처음 한 번 「시스템 설정 → 개인정보 보호 및 보안 → 그래도 열기」가 필요합니다.
          터미널에서 <code>curl -LO</code> 로 받으면 그 단계가 없습니다.</>}
      </div>
    </>
  );
  return compact
    ? <div className="card mb-gap"><div className="row-between" style={{ alignItems: "flex-start" }}><div className="t-sm" style={{ marginRight: 12 }}><strong>다른 PC 에 설치</strong></div><div>{body}</div></div></div>
    : body;
}
