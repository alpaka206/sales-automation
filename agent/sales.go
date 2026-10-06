package main

import (
	"encoding/json"
	"time"
)

// ── 영업 인사이트 ──────────────────────────────────────────────────────────
// 수주 고객 화면과 반대 방향의 물음이다: 「우리가 아는 고객이 어떻게 쓰나」가 아니라 **「우리가
// 모르는 사람 중 누가 눈에 띄나」**. 스냅샷은 B2B 만이 아니라 전부를 담고 있으니(스페이스 59만 ·
// 사용자 59만) 셀프서브에서 크게 쓰는 스페이스가 곧 영업 대상이다.
//
// 결과는 「주목할 스페이스」 표가 읽는 것 셋뿐이다 (2026-10-06 운영자: 「주목할 스페이스 말고 아래에 있는거
// 모두 다 삭제」) — 그 아래 있던 제품 전체 흐름(가입 · 구독 · 체크아웃 · 플랜별 크레딧 · 언어쌍 · 업로드 경로 ·
// 로그인)은 화면과 함께 빠졌고, 그래서 사용자 · 체크아웃 · 구독 이력 표는 더 읽지도 않는다:
//   - spaces — 눈에 띄는 스페이스 ≤ 300. 엔터프라이즈 연결(esa)이거나 30일 크레딧 1,500 이상이거나
//     30일 내보내기 10건 이상이거나 사용자가 둘 이상. **어느 것이 우리 고객인지는 콘솔이 안다**
//     (계약의 space_seq) — 여기서는 모른 채 고르고, 화면이 우리 고객을 뺀다.
//   - spaces_active_6m — 6개월 안에 내보내기가 있는 스페이스 수(표 머리의 「N개 중」).
//   - fail_rate_all — 전사 실패율(「실패가 잦다」 신호의 기준).
//
// 식별자는 space_seq 뿐이다(허브스팟 연락처의 「space seq」 칸으로 사람에게 이어진다). plan_name 은
// 엔터프라이즈 티어에서 「<회사> Biz」 꼴이라 회사를 말하지만 그것이 이 화면의 목적이다 — 사람의
// 이름·메일은 스냅샷에 없고, 값은 비공개 가공 레포에만 산다.
const salesSQL = `
WITH nowat AS (SELECT CAST('{{asof}}' AS TIMESTAMP) AS t),
ps AS (SELECT project_seq, space_seq FROM read_csv_auto('{{d}}/perso_video_translator.project_space.csv', union_by_name=true)),
pel AS (
  SELECT p.create_date, p.job_status, p.user_seq, p.source_language_code, p.target_language_code, ps.space_seq
  FROM read_csv_auto('{{d}}/perso_video_translator.project_export_log.csv', union_by_name=true) p
  JOIN ps USING (project_seq)
),
cuh AS (
  SELECT space_seq, create_date,
         CASE WHEN action_type = 'EXECUTE' THEN actual_used_quota ELSE -actual_used_quota END AS signed
  FROM read_csv_auto('{{d}}/perso_video_translator.credit_usage_history.csv', union_by_name=true)
),
sb AS (
  SELECT s.space_seq, s.status AS sub_status, pl.priority_tier AS tier, pl.name AS plan_name
  FROM read_csv_auto('{{d}}/perso_payment.subscriber.part*.csv', union_by_name=true) s
  LEFT JOIN read_csv_auto('{{d}}/perso_payment.plan.csv', union_by_name=true) pl ON pl.seq = s.plan_seq
),
esa AS (SELECT DISTINCT space_seq FROM read_csv_auto('{{d}}/perso.enterprise_space_association.csv', union_by_name=true)),
spc AS (SELECT seq AS space_seq, seat FROM read_csv_auto('{{d}}/perso.space.part*.csv', union_by_name=true)),
sm AS (
  SELECT space_seq, count(*) AS members
  FROM read_csv_auto('{{d}}/perso.space_member.part*.csv', union_by_name=true) WHERE status = 'approval' GROUP BY 1
),
act AS (
  SELECT space_seq,
         count(*) FILTER (WHERE create_date >= (SELECT t FROM nowat) - INTERVAL 30 DAY) AS exports_30d,
         count(*) AS exports_6m,
         count(*) FILTER (WHERE job_status = 'FAILED') AS failed_6m,
         count(DISTINCT user_seq) AS users,
         min(create_date) AS first_job, max(create_date) AS last_job
  FROM pel GROUP BY 1
),
cred AS (
  SELECT space_seq,
         round(sum(signed) FILTER (WHERE create_date >= (SELECT t FROM nowat) - INTERVAL 30 DAY)) AS credits_30d,
         round(sum(signed) FILTER (WHERE create_date >= (SELECT t FROM nowat) - INTERVAL 90 DAY)) AS credits_90d
  FROM cuh GROUP BY 1
),
pairs AS (
  SELECT space_seq, coalesce(source_language_code, '?') || ' → ' || coalesce(target_language_code, '?') AS pair, count(*) AS n
  FROM pel GROUP BY 1, 2
),
-- 동점이면 이름 순 — arg_max 는 같은 n 중 아무거나 골라서 같은 스냅샷인데 날마다 「주 언어쌍」이 바뀌었다.
toppair AS (SELECT space_seq, first(pair ORDER BY n DESC, pair) AS top_pair FROM pairs GROUP BY 1),
rows AS (
  SELECT a.space_seq, coalesce(sb.tier, '(구독 없음)') AS tier, sb.plan_name, sb.sub_status,
         (a.space_seq IN (SELECT space_seq FROM esa)) AS ent,
         coalesce(c.credits_30d, 0) AS credits_30d, coalesce(c.credits_90d, 0) AS credits_90d,
         a.exports_30d, a.exports_6m, a.failed_6m, a.users,
         coalesce(sm.members, 0) AS members, coalesce(spc.seat, 0) AS seat,
         strftime(a.first_job, '%Y-%m-%d') AS first_job, strftime(a.last_job, '%Y-%m-%d') AS last_job, tp.top_pair
  FROM act a
  LEFT JOIN cred c USING (space_seq) LEFT JOIN sb USING (space_seq) LEFT JOIN sm USING (space_seq)
  LEFT JOIN spc USING (space_seq) LEFT JOIN toppair tp USING (space_seq)
),
picked AS (
  SELECT * FROM rows
  WHERE ent OR credits_30d >= 1500 OR exports_30d >= 10 OR users >= 2
  ORDER BY ent DESC, credits_30d DESC, exports_30d DESC, space_seq LIMIT 300
)
SELECT
  to_json((SELECT list(struct_pack(
      space_seq := space_seq, tier := tier, plan_name := plan_name, sub_status := sub_status, ent := ent,
      credits_30d := credits_30d, credits_90d := credits_90d, exports_30d := exports_30d, exports_6m := exports_6m,
      failed_6m := failed_6m, users := users, members := members, seat := seat,
      first_job := first_job, last_job := last_job, top_pair := top_pair
    ) ORDER BY credits_30d DESC, exports_30d DESC, space_seq) FROM picked)) AS spaces,
  (SELECT count(*) FROM rows) AS spaces_active_6m,
  (SELECT round(100.0 * count(*) FILTER (WHERE job_status = 'FAILED') / nullif(count(*) FILTER (WHERE job_status IN ('COMPLETED', 'FAILED')), 0), 2) FROM pel) AS fail_rate_all
`

// RunSales 는 스페이스 목록 없이 도는 한 질의 — 그 한 행이 그대로 sales.json 이다. 계약(식별자 없음 ·
// 120자)은 다른 파일과 같고 space_seq 만 허용한다(export.go 의 encode 가 잰다).
func (s *Snapshot) RunSales(at time.Time) (json.RawMessage, error) {
	return s.runRow(salesSQL, nil, at)
}
