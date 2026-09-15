// 수주 고객 목록·상세가 에이전트에서 사용량 요약을 받아 오는 훅. 판정 자체는 `usage.ts`
// (순수)이고, 여기는 **언제 무엇을 부르나**만 있습니다. 받은 값은 상태로만 들고 있고
// 어디로도 보내지 않습니다.
import { useEffect, useMemo, useState } from "react";
import { agentFetch, type Pair, type SpaceResult } from "../../lib/agent";
import type { Contract, Row } from "./shared";
import { diagnose, mergeSummaries, parseSpaceSeqs, type Diagnosis, type SpaceEvidence, type SpaceSummary, type SummaryData } from "./usage";

const BATCH = 50; // 에이전트의 한 번 상한과 같다

export type UsageIndex = {
  bySpace: Map<number, SpaceSummary>;
  failRateAll: number | null;
  creditsFrom: string | null;
  snapshotAt: string;         // YYYY-MM-DD — 판정의 「지금」
  snapshotStamp: string;      // 화면 도장용 (YYYY-MM-DD HH:mm, 브라우저 시간대)
};

/** 여러 계약의 스페이스를 모아 한 번에(≤50씩) 받아 옵니다. 에이전트가 없으면 아무것도 안 합니다. */
export function useUsageIndex(pair: Pair | null, spaces: number[]) {
  const key = spaces.slice().sort((a, b) => a - b).join(",");
  const [index, setIndex] = useState<UsageIndex | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!pair || !key) { setIndex(null); return; }
    let live = true;
    setBusy(true); setProblem(null);
    const list = key.split(",").map(Number);
    const batches: number[][] = [];
    for (let i = 0; i < list.length; i += BATCH) batches.push(list.slice(i, i + BATCH));
    Promise.all(batches.map((b) => agentFetch<SpaceResult<SummaryData>>(pair, `/v1/spaces/summary?s=${b.join(",")}`)))
      .then((results) => {
        if (!live) return;
        const bySpace = new Map<number, SpaceSummary>();
        for (const r of results) for (const s of r.data.spaces) bySpace.set(s.space_seq, s);
        const first = results[0];
        const at = new Date(first.snapshot_at);
        setIndex({
          bySpace,
          failRateAll: first.data.fail_rate_all,
          creditsFrom: first.data.credits_from,
          snapshotAt: first.snapshot_at.slice(0, 10),
          snapshotStamp: `${at.getFullYear()}-${String(at.getMonth() + 1).padStart(2, "0")}-${String(at.getDate()).padStart(2, "0")} `
            + `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`,
        });
      })
      .catch((error) => { if (live) { setIndex(null); setProblem(error instanceof Error ? error.message : String(error)); } })
      .finally(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [pair, key]);

  return { index, problem, busy };
}

export type RowUsage =
  | { kind: "no-agent" }
  | { kind: "no-space" }                  // 계약에 space_seq 가 없다
  | { kind: "unknown"; spaces: number[] } // 적혀는 있는데 스냅샷에 없다 — 오타이거나 다른 서비스
  | { kind: "ok"; summary: SpaceSummary; diagnosis: Diagnosis; spaces: number[] };

/** 계약 하나의 사용 상태. */
export function usageFor(contract: Contract | null | undefined, index: UsageIndex | null): RowUsage {
  if (!index) return { kind: "no-agent" };
  const spaces = parseSpaceSeqs(contract?.space_seq);
  if (!contract || !spaces.length) return { kind: "no-space" };
  const rows = spaces.map((s) => index.bySpace.get(s)).filter((s): s is SpaceSummary => !!s);
  const summary = mergeSummaries(rows);
  if (!summary) return { kind: "unknown", spaces };
  return { kind: "ok", summary, spaces,
    diagnosis: diagnose(contract, summary, index.snapshotAt, index.failRateAll, index.creditsFrom) };
}

/** 목록의 모든 활성 계약이 가리키는 스페이스. */
export function spacesOfRows(rows: Row[] | undefined): number[] {
  const out = new Set<number>();
  for (const row of rows ?? []) for (const s of parseSpaceSeqs(row.active?.space_seq)) out.add(s);
  return [...out];
}

export function useRowUsages(rows: Row[] | undefined, index: UsageIndex | null) {
  return useMemo(() => {
    const map = new Map<number, RowUsage>();
    for (const row of rows ?? []) map.set(row.client_id, usageFor(row.active, index));
    return map;
  }, [rows, index]);
}

// ── 대조 근거 (지급 묶음 · 국내 결제) ───────────────────────────────────
export type EvidenceIndex = { bySpace: Map<number, SpaceEvidence>; snapshotAt: string };

/** `/v1/spaces/evidence` — 요약과 같은 묶음(≤50)으로 한 번에. 자동 대조(`reconcile.ts`)가 읽습니다. */
export function useEvidence(pair: Pair | null, spaces: number[]) {
  const key = spaces.slice().sort((a, b) => a - b).join(",");
  const [index, setIndex] = useState<EvidenceIndex | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    if (!pair || !key) { setIndex(null); return; }
    let live = true;
    const list = key.split(",").map(Number);
    const batches: number[][] = [];
    for (let i = 0; i < list.length; i += BATCH) batches.push(list.slice(i, i + BATCH));
    Promise.all(batches.map((b) => agentFetch<SpaceResult<{ spaces: SpaceEvidence[] }>>(pair, `/v1/spaces/evidence?s=${b.join(",")}`)))
      .then((results) => {
        if (!live) return;
        const bySpace = new Map<number, SpaceEvidence>();
        for (const r of results) for (const s of r.data.spaces ?? []) bySpace.set(s.space_seq, s);
        setIndex({ bySpace, snapshotAt: results[0].snapshot_at.slice(0, 10) });
      })
      .catch((error) => { if (live) { setIndex(null); setProblem(error instanceof Error ? error.message : String(error)); } });
    return () => { live = false; };
  }, [pair, key]);
  return { index, problem };
}
