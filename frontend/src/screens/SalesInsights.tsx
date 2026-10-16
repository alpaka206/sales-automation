import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getJSON, postForm } from "../lib/api";
import { useInsightMarks, type MarkChange, type MarkTarget } from "../lib/insightMarks";
import { useInsightRules, type InsightTab } from "../lib/insightRules";
import { useUsageSource } from "../lib/usageSource";
import { SalesInsightsView } from "./insights/SalesInsightsView";
import type { ListData } from "./won/shared";

/** 영업 인사이트 — **바깥 겹**. 우리 서버하고만 말합니다 (2026-10-15 운영자 조건 4: 「영업 인사이트 화면 파일을
 *  tests/test_usage_data_stays_off_server.py 검사 대상에 넣어줘」).
 *
 *  이 화면은 두 겹입니다:
 *  - **바깥(이 파일)** — 서버에서 받아 오는 것(수주 고객 목록 · 판정 기준 · 줄의 상태 · 사용 데이터의 열쇠)과 서버에
 *    보내는 것만 있습니다. 사용 데이터(스냅샷) 모듈은 안쪽 화면 하나 말고는 import 하지 않습니다 — 그래서 이 파일은
 *    스냅샷 숫자를 손에 쥘 수 없습니다.
 *  - **안쪽(`insights/` 폴더)** — 매일 가공된 스냅샷을 브라우저가 GitHub 에서 직접 받아 판정하고 그립니다. 서버를
 *    못 부릅니다(`DATA_SIDE` — 그 폴더의 파일은 전부 검사 대상).
 *
 *  안쪽에서 바깥으로 넘어오는 것은 사람이 버튼을 눌렀을 때의 값뿐이고 — 「기준 수정」에 적은 숫자, 상세 패널에서 고른
 *  상태와 적은 메모(그 줄의 열쇠와 함께) — 무엇이 넘어오는지를 그 테스트가 고정합니다. 쓰기는 다른 화면과 같은
 *  폼 POST 입니다 — JSON 쓰기 API 는 만들지 않습니다.
 */
export function SalesInsights() {
  const usage = useUsageSource();
  const rules = useInsightRules();
  const marks = useInsightMarks();
  const queryClient = useQueryClient();
  // 우리 수주 고객 — 「우리 고객」을 거르는 space_seq 와 이탈 방지 · 추가 계약의 계약. 서버에서 오는 쪽이라 안쪽으로
  // 내려 주기만 합니다.
  const { data: won } = useQuery({ queryKey: ["won-customers"], queryFn: () => getJSON<ListData>("/api/ui/won-customers") });

  /** 「기준 수정」의 저장 — 그 카드의 기준 열쇠만 골라, 사람이 적은 숫자를 글자로 보냅니다. */
  const saveRules = async (tab: InsightTab, values: Record<string, number>) => {
    const body: Record<string, string> = {};
    for (const rule of rules.list ?? []) {
      if (rule.tab === tab && rule.key in values) body[rule.key] = String(values[rule.key]);
    }
    await postForm("/operations/insights/rules", body);
    await queryClient.invalidateQueries({ queryKey: ["insight-rules"] });
  };

  /** 줄의 열쇠 — 업셀링은 space_seq, 이탈 방지 · 추가 계약은 계약. */
  const rowBody = (target: MarkTarget): Record<string, string> => {
    const body: Record<string, string> = { tab: target.tab };
    if (target.tab === "up") body.space_seq = String(target.space_seq);
    else body.contract_id = String(target.contract_id);
    return body;
  };

  /** 상세 패널의 상태 · 메모 저장 — 그 줄의 열쇠와 사람이 고른 상태 · 적은 메모뿐.
   *  업셀링 줄의 번호가 서버에 남는 것이 「사용 데이터는 서버로 안 간다」의 세 번째 예외입니다(2026-10-15 A안). */
  const saveMark = async (target: MarkTarget, change: MarkChange) => {
    const body = rowBody(target);
    if (change.status !== undefined) body.status = change.status;
    if (change.memo !== undefined) body.memo = change.memo;
    await postForm("/operations/insights/marks", body);
    await queryClient.invalidateQueries({ queryKey: ["insight-marks"] });
  };

  /** 메일 초안을 복사했다는 기록 — 그 줄의 열쇠와 초안의 제목만(본문은 안 보낸다). **메일은 보내지 않습니다** —
   *  초안은 사람이 복사해 자기 메일 프로그램에서 보냅니다(운영자 조건 3). */
  const logCopy = async (target: MarkTarget, subject: string) => {
    const body = rowBody(target);
    body.subject = subject;
    await postForm("/operations/insights/copies", body);
    await queryClient.invalidateQueries({ queryKey: ["insight-marks"] });
  };

  return (
    <SalesInsightsView usage={usage} won={won ?? null} rules={rules} marks={marks}
                       onSaveRules={saveRules} onMark={saveMark} onCopied={logCopy} />
  );
}
