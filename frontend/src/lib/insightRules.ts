// 영업 인사이트의 판정 기준 — **서버에 저장된 값**을 받습니다 (2026-10-15 운영자: 「기준값은 서버에 저장하고
// 화면에서 바꿀 수 있게. 누가 언제 바꿨는지 기록」). 목록 · 기본값 · 범위는 서버의 `src/db/insights.py` RULES
// 한 곳이고, 이 모듈은 그것을 읽어 옵니다(`GET /api/ui/insight-rules`).
//
// 수주 고객 「사용 진단」도 같은 값을 씁니다(잔여 예상 · 부족 예상 · 과소/초과사용 — `shared`). 그래서 이
// 기준은 영업 인사이트만의 것이 아니고, 두 화면이 각자 상수를 들면 같은 고객을 다르게 부릅니다.
//
// **이 모듈은 서버와 말하는 쪽입니다** — 사용 데이터를 쥔 모듈(`lib/usageData.ts` · `screens/won/usage*.ts` …)을
// import 하지 않고, 그 모듈들도 이것을 import 하지 않습니다. 화면이 받은 값을 판정 함수에 넘겨 줍니다
// (`usage.ts` 의 `rulesFrom` → `diagnose`). `tests/test_usage_data_stays_off_server.py` 가 두 방향을 고정합니다.
import { useQuery } from "@tanstack/react-query";
import { getJSON } from "./api";

export type InsightTab = "up" | "churn" | "grow";

export type InsightRule = {
  key: string;
  tab: InsightTab;
  label: string;
  unit: string;
  min: number;
  max: number;
  step: number;
  /** 수주 고객 「사용 진단」이 같이 쓰는 값 */
  shared: boolean;
  /** 범위 오류에 덧붙는 말 — 업셀링 하한은 「가공 후보 하한」 */
  why: string;
  default: number;
  value: number;
  /** 아무도 안 바꿨으면(기본값) null */
  updated_by: string | null;
  updated_at: string | null;
};

export function useInsightRules() {
  const { data, error, isPending } = useQuery({
    queryKey: ["insight-rules"],
    queryFn: () => getJSON<{ rules: InsightRule[] }>("/api/ui/insight-rules"),
  });
  return {
    /** 받기 전 · 못 받았으면 null — 그동안 판정하지 않습니다(화면은 「—」). */
    list: data?.rules ?? null,
    // 한 번 받은 값이 있으면 다시 받기 실패는 문제가 아닙니다 — 그 값으로 계속 판정합니다.
    problem: !data && error ? error.message : null,
    busy: isPending,
  };
}
