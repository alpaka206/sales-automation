// 영업 인사이트 메일 초안의 기본 문구 — **서버에 저장된 것**을 받습니다 (2026-10-16 운영자: 「메일 초안 문구를 영업
// 인사이트 화면에서 고칠 수 있게」). 칸의 목록 · 기본 문구는 서버의 `src/db/insight_drafts.py` 한 곳이고, 고친 칸은 표
// `insight_drafts` 에 삽니다 — 이메일 템플릿 표와 따로라 인바운드 답장 AI 가 읽지 않습니다. 저장은 바깥 겹
// (`screens/SalesInsights.tsx`)이 `POST /operations/insights/drafts` 로 합니다.
//
// **이 모듈은 서버와 말하는 쪽입니다** — 사용 데이터를 쥔 모듈을 import 하지 않고, 그 모듈들도 이것을 (타입 말고는)
// import 하지 않습니다(`tests/test_usage_data_stays_off_server.py` 의 `SERVER_SIDE`).
import { useQuery } from "@tanstack/react-query";
import { getJSON } from "./api";
import type { InsightTab } from "./insightRules";

export type DraftLang = "ko" | "en";
/** 한 칸 — 카드 × 언어 × 부분(`subject` 제목 · `body` 공통 문단 · `reason.<근거>` 근거별 문단). */
export type DraftPart = {
  tab: InsightTab; lang: DraftLang; part: string;
  /** 편집 창에 서는 이름 — 「제목」 · 「공통 문단」 · 「근거 — 무활동이 기준 이상이거나 …」 */
  label: string;
  value: string;
  default: string;
  /** 아무도 안 고쳤으면(기본 문구) null */
  updated_by: string | null;
  updated_at: string | null;
};
export type InsightDrafts = {
  /** 쓸 수 있는 자리표시와 그 뜻 — 편집 창의 안내 */
  placeholders: { token: string; help: string }[];
  parts: DraftPart[];
};

export function useInsightDrafts() {
  const { data, error, isPending } = useQuery({
    queryKey: ["insight-drafts"],
    queryFn: () => getJSON<InsightDrafts>("/api/ui/insight-drafts"),
  });
  return {
    data: data ?? null,
    problem: !data && error ? error.message : null,
    busy: isPending,
  };
}
