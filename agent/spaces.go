package main

// 수주 고객 한 곳 = 스페이스 몇 개. 이 파일의 SQL 은 **스페이스 목록**을 받아 그 안에서만 집계한다.
// 가공은 그 목록에 가공 범위(엔터프라이즈·유료 스페이스) 전부를 넣고 한 번 돈다(export.go) — 값은
// 스페이스마다 따로 셈하므로(GROUP BY space_seq) 목록이 커져도 한 스페이스의 줄은 안 바뀐다. 화면은
// 계약의 스페이스 줄만 골라 쓴다.
//
// **질의 하나가 한 행을 낸다.** `-json` 모드는 빈 결과를 줄 자체로 안 내보내서 여러 문장을 위치로
// 읽으면 어긋난다 — 대신 `to_json()` 으로 중첩 JSON 한 객체를 받는다 (`json` 확장은 CLI 에 정적으로
// 들어 있어 확장 로딩을 끈 채로도 된다).
//
// **실측으로 확인한 사실** (2026-09-15, 44개 표):
//   - `project_export_log` 에는 space_seq 가 없다 → `project_space` 로 잇는다 (100% 연결,
//     프로젝트당 스페이스 1개).
//   - `credit_usage_history`(vt) 와 `space_credit_usage_history`(payment) 는
//     `credit_history_seq = history_seq` 로 95.7% 이어진다. 후자의 `initial_credit` 은
//     이름과 달리 **사용량**이다(중앙값 1~12) — 지급액이 아니다. 지급 원장은 스냅샷에 없다.
//   - 두 크레딧 표 모두 2025-12-01 부터다. 「전체 기간」이라 적혔지만 그 앞이 없다.
//   - `payment_history` 에 엔터프라이즈 스페이스 행은 0 이다 — B2B 결제는 여기서 안 다룬다.

// 공통 CTE. 파일을 매번 읽는다 — DuckDB 가 CSV 를 그 자리에서 읽어서 0.3~1초다.
const spacesCTE = `
WITH sp AS (SELECT unnest([{{spaces}}]::BIGINT[]) AS space_seq),
nowat AS (SELECT CAST('{{asof}}' AS TIMESTAMP) AS t),
cuh AS (
  SELECT space_seq, create_date, action_type, credit_history_seq, project_seq, plan_tier,
         CASE WHEN action_type = 'EXECUTE' THEN actual_used_quota ELSE -actual_used_quota END AS signed
  FROM read_csv_auto('{{d}}/perso_video_translator.credit_usage_history.csv', union_by_name=true)
  WHERE space_seq IN (SELECT space_seq FROM sp)
),
ps AS (
  SELECT project_seq, space_seq
  FROM read_csv_auto('{{d}}/perso_video_translator.project_space.csv', union_by_name=true)
  WHERE space_seq IN (SELECT space_seq FROM sp)
),
pel AS (
  SELECT p.seq, p.project_seq, ps.space_seq, p.user_seq, p.job_status, p.speed_type,
         p.source_language_code, p.target_language_code, p.lib_sync, p.upload_source_type,
         p.original_video_duration_ms, p.execution_delay_minute, p.speaker_count,
         p.create_date, p.update_date
  FROM read_csv_auto('{{d}}/perso_video_translator.project_export_log.csv', union_by_name=true) p
  JOIN ps ON ps.project_seq = p.project_seq
)
`

// ── 1. 목록용 요약 (summary.json) ───────────────────────────────────────
const summarySQL = spacesCTE + `,
u AS (
  SELECT space_seq,
         round(sum(signed)) AS used_total,
         round(sum(CASE WHEN create_date >= (SELECT t FROM nowat) - INTERVAL 30 DAY THEN signed ELSE 0 END)) AS used_30d,
         round(sum(CASE WHEN create_date >= (SELECT t FROM nowat) - INTERVAL 90 DAY THEN signed ELSE 0 END)) AS used_90d,
         min(create_date) AS first_use,
         max(create_date) FILTER (WHERE action_type = 'EXECUTE') AS last_use
  FROM cuh GROUP BY 1
),
pc AS (SELECT space_seq, project_seq, count(*) AS exports FROM pel GROUP BY 1, 2),
j AS (
  SELECT space_seq,
         count(*) FILTER (WHERE job_status = 'COMPLETED') AS jobs_ok,
         count(*) FILTER (WHERE job_status = 'FAILED') AS jobs_failed,
         max(create_date) AS last_job
  FROM pel GROUP BY 1
),
jp AS (SELECT space_seq, count(*) AS projects, count(*) FILTER (WHERE exports >= 2) AS reworked FROM pc GROUP BY 1),
sb AS (
  SELECT s.space_seq, s.status AS sub_status, s.next_billing_day, s.billing_anchor_date, pl.name AS plan_name
  FROM read_csv_auto('{{d}}/perso_payment.subscriber.part*.csv', union_by_name=true) s
  LEFT JOIN read_csv_auto('{{d}}/perso_payment.plan.csv', union_by_name=true) pl ON pl.seq = s.plan_seq
  WHERE s.space_seq IN (SELECT space_seq FROM sp)
),
pel_all AS (SELECT job_status, create_date FROM read_csv_auto('{{d}}/perso_video_translator.project_export_log.csv', union_by_name=true)),
cuh_all AS (SELECT min(create_date) AS first FROM read_csv_auto('{{d}}/perso_video_translator.credit_usage_history.csv', union_by_name=true))
SELECT
  to_json((SELECT list(struct_pack(
      space_seq := sp.space_seq,
      known := (sb.space_seq IS NOT NULL),
      plan_name := sb.plan_name, sub_status := sb.sub_status,
      next_billing_day := sb.next_billing_day, billing_anchor_date := sb.billing_anchor_date,
      used_total := coalesce(u.used_total, 0), used_30d := coalesce(u.used_30d, 0), used_90d := coalesce(u.used_90d, 0),
      first_use := u.first_use, last_use := u.last_use,
      jobs_ok := coalesce(j.jobs_ok, 0), jobs_failed := coalesce(j.jobs_failed, 0), last_job := j.last_job,
      projects := coalesce(jp.projects, 0), reworked := coalesce(jp.reworked, 0)
    ) ORDER BY sp.space_seq)
    FROM sp LEFT JOIN u ON u.space_seq = sp.space_seq LEFT JOIN j ON j.space_seq = sp.space_seq
            LEFT JOIN jp ON jp.space_seq = sp.space_seq LEFT JOIN sb ON sb.space_seq = sp.space_seq)) AS spaces,
  (SELECT round(100.0 * count(*) FILTER (WHERE job_status = 'FAILED')
          / nullif(count(*) FILTER (WHERE job_status IN ('COMPLETED', 'FAILED')), 0), 2) FROM pel_all) AS fail_rate_all,
  (SELECT first FROM cuh_all) AS credits_from,
  (SELECT min(create_date) FROM pel_all) AS jobs_from
`

// ── 2. 대조 근거 (evidence.json) ────────────────────────────────────────
// 콘솔이 **자기 기록**(지급 회차·분납 회차)과 맞대어 완료 처리하는 데 쓰는 두 가지:
//   - 엔터프라이즈 지급 묶음의 소진 시작 — 「지급이 있었다」의 증거(지급액은 스냅샷에 없다)
//   - 국내 카드 결제(portone) — enterprise_seq 로 이어진다. 결제 완료 4 · 대기 11 · 취소 5
//     (2026-09-14 실측, 완료 건은 계약 규모의 금액이다). Stripe 는 셀프서브뿐이라 안 본다.
//
// 결제 행이 enterprise 단위라 같은 enterprise 의 스페이스마다 같은 행이 실린다 — 콘솔이 겹침을 뺀다.
const evidenceSQL = `
WITH sp AS (SELECT unnest([{{spaces}}]::BIGINT[]) AS space_seq),
nowat AS (SELECT CAST('{{asof}}' AS TIMESTAMP) AS t),
cuh AS (
  SELECT space_seq, credit_history_seq
  FROM read_csv_auto('{{d}}/perso_video_translator.credit_usage_history.csv', union_by_name=true)
  WHERE space_seq IN (SELECT space_seq FROM sp)
),
scuh AS (
  SELECT s.history_seq, s.credit_seq, s.earn_type, s.initial_credit, s.create_date
  FROM read_csv_auto('{{d}}/perso_payment.space_credit_usage_history.csv', union_by_name=true) s
  WHERE s.earn_type = 'enterprise' AND s.history_seq IN (SELECT credit_history_seq FROM cuh)
),
cb AS (
  SELECT c.space_seq, s.credit_seq, min(s.create_date) AS first_use, max(s.create_date) AS last_use,
         round(sum(s.initial_credit)) AS consumed, count(*) AS n
  FROM scuh s JOIN cuh c ON c.credit_history_seq = s.history_seq GROUP BY 1, 2
),
esa AS (
  SELECT enterprise_seq, space_seq FROM read_csv_auto('{{d}}/perso.enterprise_space_association.csv', union_by_name=true)
  WHERE space_seq IN (SELECT space_seq FROM sp)
),
pp AS (
  SELECT e.space_seq, p.status, p.method, p.currency, p.amount, p.plan_name,
         CAST(p.paid_date AS VARCHAR) AS paid_date, CAST(p.failed_date AS VARCHAR) AS failed_date,
         p.failure_code, CAST(p.requested_date AS VARCHAR) AS requested_date, CAST(p.expire_date AS VARCHAR) AS expire_date,
         CAST(p.created_date AS VARCHAR) AS created_date
  FROM read_csv_auto('{{d}}/perso_payment.portone_payment_history.csv', union_by_name=true) p
  JOIN esa e ON e.enterprise_seq = p.enterprise_seq
)
SELECT
  to_json((SELECT list(struct_pack(
      space_seq := sp.space_seq,
      buckets := (SELECT list(struct_pack(first_use := first_use, last_use := last_use, consumed := consumed, n := n) ORDER BY first_use)
                  FROM cb WHERE cb.space_seq = sp.space_seq),
      payments := (SELECT list(struct_pack(status := status, method := method, currency := currency, amount := amount,
                                           plan_name := plan_name, paid_date := paid_date, failed_date := failed_date,
                                           failure_code := failure_code, requested_date := requested_date,
                                           expire_date := expire_date, created_date := created_date)
                               ORDER BY coalesce(paid_date, created_date))
                   FROM pp WHERE pp.space_seq = sp.space_seq)
    ) ORDER BY sp.space_seq) FROM sp)) AS spaces
`
