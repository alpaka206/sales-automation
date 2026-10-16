import { useQuery } from "@tanstack/react-query";
import { getJSON } from "../lib/api";
import { useUsageSource } from "../lib/usageSource";
import { SalesInsightsView } from "./insights/SalesInsightsView";
import type { ListData } from "./won/shared";

/** 영업 인사이트 — **바깥 겹**. 우리 서버하고만 말합니다 (2026-10-15 운영자 조건 4: 「영업 인사이트 화면 파일을
 *  tests/test_usage_data_stays_off_server.py 검사 대상에 넣어줘」).
 *
 *  이 화면은 두 겹입니다:
 *  - **바깥(이 파일)** — 서버에서 받아 오는 것(수주 고객 목록 · 사용 데이터의 열쇠)과 서버에 보내는 것만 있습니다.
 *    사용 데이터(스냅샷) 모듈은 안쪽 화면 하나 말고는 import 하지 않습니다 — 그래서 이 파일은 스냅샷 숫자를 손에
 *    쥘 수 없습니다.
 *  - **안쪽(`insights/` 폴더)** — 매일 가공된 스냅샷을 브라우저가 GitHub 에서 직접 받아 판정하고 그립니다. 서버를
 *    못 부릅니다(`DATA_SIDE` — 그 폴더의 파일은 전부 검사 대상).
 *
 *  안쪽에서 바깥으로 넘어오는 것은 사람이 버튼을 눌렀을 때의 값뿐이고, 무엇이 넘어오는지를 그 테스트가 고정합니다.
 */
export function SalesInsights() {
  const usage = useUsageSource();
  // 우리 수주 고객 — 「우리 고객」을 거르는 space_seq 와 계약. 서버에서 오는 쪽이라 안쪽으로 내려 주기만 합니다.
  const { data: won } = useQuery({ queryKey: ["won-customers"], queryFn: () => getJSON<ListData>("/api/ui/won-customers") });
  return <SalesInsightsView usage={usage} won={won ?? null} />;
}
