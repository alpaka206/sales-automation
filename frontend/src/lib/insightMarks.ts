// 영업 인사이트 줄의 상태 · 메모 · 기록 — **서버에 저장된 것**을 받습니다 (2026-10-15 운영자: 상태 · 메모 · 기록을
// 팀이 같이 본다). 저장은 바깥 겹(`screens/SalesInsights.tsx`)이 `POST /operations/insights/marks` 로 합니다.
//
// **업셀링 줄의 번호(space_seq)가 서버에 남는 것이 「사용 데이터는 서버로 안 간다」의 세 번째 예외입니다** (2026-10-15
// 운영자가 개발자에게 물어 정한 A안). 사람이 상태 버튼 · 「메모 저장」을 누를 때만, 번호 · 상태 · 메모만 가고, 서버도
// 그 밖의 칸은 거절합니다(`src/db/insights.save_mark`).
//
// **이 모듈은 서버와 말하는 쪽입니다** — 사용 데이터를 쥔 모듈을 import 하지 않고, 그 모듈들도 이것을 (타입 말고는)
// import 하지 않습니다(`tests/test_usage_data_stays_off_server.py` 의 `SERVER_SIDE`).
import { useQuery } from "@tanstack/react-query";
import { getJSON } from "./api";
import type { InsightTab } from "./insightRules";

export type InsightStatus = { key: string; label: string };
export type InsightMark = {
  tab: InsightTab; space_seq: number | null; contract_id: number | null;
  status: string; memo: string | null; updated_by: string | null; updated_at: string | null;
};
export type InsightLogLine = {
  tab: InsightTab; space_seq: number | null; contract_id: number | null;
  action: string; detail: string | null; actor: string | null; created_at: string;
};
export type InsightMarks = {
  /** 카드마다의 상태 — 첫 칸(todo)이 아무도 안 누른 줄의 상태다. */
  statuses: Record<InsightTab, InsightStatus[]>;
  marks: InsightMark[];
  /** 최근 것부터. 기준 변경은 빠져 있다. */
  log: InsightLogLine[];
};

/** 어느 줄의 상태인가 — 업셀링은 스페이스 번호, 이탈 방지 · 추가 계약은 계약. */
export type MarkTarget = { tab: "up"; space_seq: number } | { tab: "churn" | "grow"; contract_id: number };
/** 사람이 바꾼 것 — 상태 버튼 하나, 또는 「메모 저장」. */
export type MarkChange = { status?: string; memo?: string };

export function useInsightMarks() {
  const { data, error, isPending } = useQuery({
    queryKey: ["insight-marks"],
    queryFn: () => getJSON<InsightMarks>("/api/ui/insight-marks"),
  });
  return {
    data: data ?? null,
    problem: !data && error ? error.message : null,
    busy: isPending,
  };
}
