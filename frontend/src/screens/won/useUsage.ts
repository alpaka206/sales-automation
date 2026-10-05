// 수주 고객 목록·상세가 사용량 요약을 받아 오는 훅. 판정 자체는 `usage.ts`(순수)이고, 여기는
// **무엇을 읽어 어떻게 고르나**만 있습니다. 받는 일은 `lib/usageData.ts` 가 하고(GitHub 에서 직접),
// 받은 값은 상태로만 들고 있고 어디로도 보내지 않습니다.
//
// 요약과 대조 근거는 가공 범위 전체가 파일 하나씩이라 한 번 받아 두고, 계약의 스페이스는 그 안에서
// 고릅니다 — 계약을 바꿔도 다시 받지 않습니다.
import { useMemo } from "react";
import { isStale, spacesKey, stamp, useUsageFile } from "../../lib/usageData";
import type { UsageSource } from "../../lib/usageSource";
import type { Contract, Row } from "./shared";
import { diagnose, mergeSummaries, parseSpaceSeqs, type Diagnosis, type SpaceEvidence, type SpaceSummary, type SummaryData } from "./usage";

export type UsageIndex = {
  bySpace: Map<number, SpaceSummary>;
  failRateAll: number | null;
  creditsFrom: string | null;
  snapshotAt: string;         // YYYY-MM-DD — 판정의 「지금」
  snapshotStamp: string;      // 화면 도장용 (YYYY-MM-DD HH:mm, 브라우저 시간대)
  stale: boolean;             // 스냅샷이 36시간 넘게 묵었다 — 가공이 하루를 건너뛰었다
};

/** 이 스페이스들의 요약. 연결이 없으면 아무것도 안 합니다. 스페이스가 없어도 요약 파일은 받습니다 —
 *  도장(스냅샷 시각)을 찍고, Space ID 없는 계약을 「미연결」로 말하려면 「연결 없음」과 갈라야 합니다. */
export function useUsageIndex(source: UsageSource | null, spaces: number[]) {
  const key = spacesKey(spaces);
  const file = useUsageFile<SummaryData>(source, "summary.json");
  const index = useMemo<UsageIndex | null>(() => {
    if (!file.data) return null;
    const { snapshot_at: at, data } = file.data;
    const want = new Set(spaces); // `key` 가 이 목록의 열쇠라 목록이 바뀌면 다시 고릅니다
    return {
      bySpace: new Map((data.spaces ?? []).filter((s) => want.has(s.space_seq)).map((s) => [s.space_seq, s])),
      failRateAll: data.fail_rate_all,
      creditsFrom: data.credits_from,
      snapshotAt: at.slice(0, 10),
      snapshotStamp: stamp(at),
      stale: isStale(at),
    };
  }, [file.data, key]);
  return { index, problem: file.problem, busy: file.busy };
}

export type RowUsage =
  | { kind: "no-data" }
  | { kind: "loading" }                   // 연결은 됐고 받는 중 — 「연결 안 됨」을 그리면 안 되는 동안
  | { kind: "no-space" }                  // 계약에 space_seq 가 없다
  | { kind: "unknown"; spaces: number[] } // 적혀는 있는데 가공 범위에 없다 — 오타이거나 다른 서비스
  // `missing` — 계약의 번호 중 가공 범위에 없어 숫자에 안 들어간 것. 하나라도 있으면 화면이 그 번호를 단다:
  // 조용히 줄어든 숫자는 틀린 숫자로 읽힌다.
  | { kind: "ok"; summary: SpaceSummary; diagnosis: Diagnosis; spaces: number[]; missing: number[] };

/** 계약 하나의 사용 상태. */
export function usageFor(contract: Contract | null | undefined, index: UsageIndex | null): RowUsage {
  if (!index) return { kind: "no-data" };
  const spaces = parseSpaceSeqs(contract?.space_seq);
  if (!contract || !spaces.length) return { kind: "no-space" };
  const rows = spaces.map((s) => index.bySpace.get(s)).filter((s): s is SpaceSummary => !!s);
  const summary = mergeSummaries(rows);
  if (!summary) return { kind: "unknown", spaces };
  return { kind: "ok", summary, spaces, missing: spaces.filter((s) => !index.bySpace.has(s)),
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

/** `evidence.json` — 근거가 있는 스페이스만 실린 파일 하나. 자동 대조(`reconcile.ts`)가 읽습니다.
 *  스페이스가 없으면 받지 않습니다 — 맞댈 계약이 없습니다. */
export function useEvidence(source: UsageSource | null, spaces: number[]) {
  const key = spacesKey(spaces);
  const file = useUsageFile<{ spaces: SpaceEvidence[] | null }>(source, key ? "evidence.json" : null);
  const index = useMemo<EvidenceIndex | null>(() => {
    if (!file.data) return null;
    const want = new Set(spaces);
    return {
      bySpace: new Map((file.data.data.spaces ?? []).filter((s) => want.has(s.space_seq)).map((s) => [s.space_seq, s])),
      snapshotAt: file.data.snapshot_at.slice(0, 10),
    };
  }, [file.data, key]);
  return { index, problem: file.problem };
}
