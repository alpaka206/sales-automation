package main

// 수주 고객 한 곳 = 스페이스 몇 개. 이 파일의 지표는 전부 **스페이스 목록**을 받아 그 안에서만
// 집계한다. 콘솔이 계약에 적어 둔 `space_seq` 를 보내고, 계산은 여기서 끝나고, 나가는 것은
// 집계뿐이다.
//
// **엔드포인트당 DuckDB 질의 하나**다. `-json` 모드는 빈 결과를 줄 자체로 안 내보내서
// 여러 문장을 위치로 읽으면 어긋난다 — 대신 `to_json()` 으로 중첩 JSON 한 객체를 받는다
// (`json` 확장은 CLI 에 정적으로 들어 있어 확장 로딩을 끈 채로도 된다).
//
// 어느 스냅샷 시각을 「지금」으로 볼지는 벽시계가 아니라 **manifest 의 generated_at** 이다.
// 스냅샷이 사흘 묵었으면 「최근 30일」도 그 시각 기준으로 밀린다 — 안 그러면 pull 이 며칠
// 실패한 PC 에서 「30일 무활동」이 저절로 생긴다.
//
// **실측으로 확인한 사실** (2026-09-15, 44개 표):
//   - `project_export_log` 에는 space_seq 가 없다 → `project_space` 로 잇는다 (100% 연결,
//     프로젝트당 스페이스 1개).
//   - `credit_usage_history`(vt) 와 `space_credit_usage_history`(payment) 는
//     `credit_history_seq = history_seq` 로 95.7% 이어진다. 후자의 `initial_credit` 은
//     이름과 달리 **사용량**이다(중앙값 1~12) — 지급액이 아니다. 지급 원장은 스냅샷에 없다.
//   - 두 크레딧 표 모두 2025-12-01 부터다. 「전체 기간」이라 적혔지만 그 앞이 없다.
//   - `payment_history` 에 엔터프라이즈 스페이스 행은 0 이다 — B2B 결제는 여기서 안 다룬다.

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

const maxSpacesPerCall = 50

// parseSpaces — `?s=1,2,3`. 숫자만, 최대 50개. SQL 에 그대로 들어가므로 **여기서 거른다**.
func parseSpaces(raw string) ([]int64, error) {
	if strings.TrimSpace(raw) == "" {
		return nil, errors.New("s= 에 space_seq 를 쉼표로 주세요")
	}
	seen := map[int64]bool{}
	out := []int64{}
	for _, part := range strings.Split(raw, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		v, err := strconv.ParseInt(part, 10, 64)
		if err != nil || v <= 0 {
			return nil, fmt.Errorf("space_seq 가 숫자가 아닙니다: %q", part)
		}
		if !seen[v] {
			seen[v] = true
			out = append(out, v)
		}
	}
	if len(out) == 0 {
		return nil, errors.New("s= 가 비었습니다")
	}
	if len(out) > maxSpacesPerCall {
		return nil, fmt.Errorf("한 번에 %d개까지입니다", maxSpacesPerCall)
	}
	return out, nil
}

func spaceList(spaces []int64) string {
	parts := make([]string, len(spaces))
	for i, s := range spaces {
		parts[i] = strconv.FormatInt(s, 10)
	}
	return strings.Join(parts, ",")
}

// snapshotAt — manifest 의 generated_at. 없으면 커밋 시각, 그것도 없으면 지금.
func (s *Snapshot) snapshotAt() time.Time {
	var m struct {
		GeneratedAt string `json:"generated_at"`
	}
	if b, err := os.ReadFile(filepath.Join(s.Data, "manifest.json")); err == nil {
		if json.Unmarshal(b, &m) == nil {
			if t, err := time.Parse(time.RFC3339Nano, m.GeneratedAt); err == nil {
				return t
			}
		}
	}
	if t, err := time.Parse(time.RFC3339, s.CommittedAt()); err == nil {
		return t
	}
	return time.Now().UTC()
}

// 공통 CTE. 파일을 매번 읽는다 — DuckDB 가 CSV 를 그 자리에서 읽어서 0.3~1초다. 미리 적재해
// 두면 pull 로 파일이 바뀌었을 때 옛 것을 읽는다.
const spacesCTE = `
WITH sp AS (SELECT unnest([{{spaces}}]::BIGINT[]) AS space_seq),
nowat AS (SELECT CAST('{{asof}}' AS TIMESTAMP) AS t),
cuh AS (
  SELECT space_seq, create_date, action_type, credit_history_seq,
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

// ── 1. 목록용 요약 (여러 스페이스 한 번에) ──────────────────────────────
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

// ── 2. 크레딧 소진 ───────────────────────────────────────────────────────
const creditsSQL = spacesCTE + `,
scuh AS (
  SELECT s.credit_seq, s.earn_type, s.is_free, s.initial_credit, s.create_date
  FROM read_csv_auto('{{d}}/perso_payment.space_credit_usage_history.csv', union_by_name=true) s
  WHERE s.history_seq IN (SELECT credit_history_seq FROM cuh)
),
bucket AS (
  SELECT credit_seq, earn_type, is_free, min(create_date) AS first_use, max(create_date) AS last_use,
         round(sum(initial_credit)) AS consumed, count(*) AS n
  FROM scuh GROUP BY 1, 2, 3
)
SELECT
  to_json((SELECT list(struct_pack(period := period, used := used) ORDER BY period) FROM (
      SELECT strftime(date_trunc('month', create_date), '%Y-%m') AS period, round(sum(signed)) AS used FROM cuh GROUP BY 1))) AS monthly,
  to_json((SELECT list(struct_pack(period := period, used := used) ORDER BY period) FROM (
      SELECT strftime(date_trunc('week', create_date), '%Y-%m-%d') AS period, round(sum(signed)) AS used FROM cuh
      WHERE create_date >= (SELECT t FROM nowat) - INTERVAL 182 DAY GROUP BY 1))) AS weekly,
  to_json((SELECT list(struct_pack(period := period, used := used) ORDER BY period) FROM (
      SELECT strftime(date_trunc('day', create_date), '%Y-%m-%d') AS period, round(sum(signed)) AS used FROM cuh
      WHERE create_date >= (SELECT t FROM nowat) - INTERVAL 90 DAY GROUP BY 1))) AS daily,
  to_json((SELECT list(struct_pack(no := no, earn_type := earn_type, is_free := is_free, first_use := first_use,
                                   last_use := last_use, consumed := consumed, n := n) ORDER BY no) FROM (
      SELECT row_number() OVER (ORDER BY first_use, credit_seq) AS no, earn_type, is_free, first_use, last_use, consumed, n
      FROM bucket))) AS buckets,
  (SELECT round(sum(signed)) FROM cuh) AS used_total,
  (SELECT round(sum(CASE WHEN action_type = 'ROLLBACK' THEN -signed ELSE 0 END)) FROM cuh) AS rolled_back,
  (SELECT min(create_date) FROM cuh) AS first_use,
  (SELECT max(create_date) FILTER (WHERE action_type = 'EXECUTE') FROM cuh) AS last_use
`

// ── 3. 작업 성능 ─────────────────────────────────────────────────────────
const jobsSQL = spacesCTE + `,
lar AS (
  -- 실패 종류: 엔진 오류 코드가 있으면 그것(NO_VOICE_DETECTED_VAD 처럼 사람이 읽는 원인),
  -- 없으면 실패 사유(AUDIO_PIPELINE_FAILED …). ENGINE_ERROR 한 줄로 뭉치면 원인이 안 보인다.
  SELECT l.failure_reason, l.engine_error_code,
         coalesce(nullif(l.engine_error_code, ''), l.failure_reason, '(미기록)') AS kind
  FROM read_csv_auto('{{d}}/perso_video_translator.live_api_response.csv', union_by_name=true) l
  JOIN pel ON pel.seq = l.export_log_seq
),
kinds AS (SELECT kind, count(*) AS n FROM lar GROUP BY 1),
top_kinds AS (SELECT kind, n FROM kinds ORDER BY n DESC, kind LIMIT 5),
-- 플랜 한도 — 스페이스의 구독(subscriber.plan_seq) → plan_option(video_translator) 의 JSON.
-- 동시 처리 한도는 계약 폼에도 있지만(concurrent_jobs) 스냅샷이 아는 값이 실제 값이다.
-- 스페이스가 여럿이고 플랜이 다르면 큰 쪽을 든다. 990/990 엔터프라이즈 스페이스가 풀린다(2026-09-14 실측).
po AS (
  SELECT TRY_CAST(json_extract_string(detail, '$.concurrentJobs') AS INTEGER) AS conc,
         TRY_CAST(TRY_CAST(json_extract_string(detail, '$.queueLimit') AS DOUBLE) AS INTEGER) AS q
  FROM read_csv_auto('{{d}}/perso_payment.plan_option.csv', union_by_name=true) o
  JOIN read_csv_auto('{{d}}/perso_payment.subscriber.part*.csv', union_by_name=true) s ON s.plan_seq = o.plan_seq
  WHERE o.type = 'video_translator' AND s.space_seq IN (SELECT space_seq FROM sp)
),
done AS (
  SELECT date_diff('minute', create_date, update_date) AS proc_min,
         original_video_duration_ms / 60000.0 AS dur_min
  FROM pel WHERE job_status = 'COMPLETED' AND update_date >= create_date
),
ev AS (
  SELECT create_date AS t, 1 AS d FROM pel WHERE job_status IN ('COMPLETED', 'FAILED') AND update_date > create_date
  UNION ALL
  SELECT update_date AS t, -1 AS d FROM pel WHERE job_status IN ('COMPLETED', 'FAILED') AND update_date > create_date
),
run AS (SELECT t, sum(d) OVER (ORDER BY t, d ROWS UNBOUNDED PRECEDING) AS running FROM ev)
SELECT
  to_json((SELECT list(struct_pack(status := status, n := n) ORDER BY n DESC) FROM (
      SELECT coalesce(job_status, '(없음)') AS status, count(*) AS n FROM pel GROUP BY 1))) AS status,
  to_json((SELECT list(struct_pack(period := period, ok := ok, failed := failed) ORDER BY period) FROM (
      SELECT strftime(date_trunc('month', create_date), '%Y-%m') AS period,
             count(*) FILTER (WHERE job_status = 'COMPLETED') AS ok,
             count(*) FILTER (WHERE job_status = 'FAILED') AS failed
      FROM pel GROUP BY 1))) AS monthly,
  to_json((SELECT list(struct_pack(reason := reason, n := n) ORDER BY n DESC) FROM (
      SELECT coalesce(failure_reason, '(미기록)') AS reason, count(*) AS n FROM lar GROUP BY 1))) AS reasons,
  to_json((SELECT list(struct_pack(code := code, n := n) ORDER BY n DESC) FROM (
      SELECT coalesce(engine_error_code, '(미기록)') AS code, count(*) AS n FROM lar GROUP BY 1))) AS errors,
  to_json((SELECT list(struct_pack(kind := kind, n := n) ORDER BY n DESC, kind) FROM top_kinds)) AS fail_kinds,
  (SELECT coalesce(sum(n), 0) FROM kinds) - (SELECT coalesce(sum(n), 0) FROM top_kinds) AS fail_other,
  to_json((SELECT struct_pack(
      n := count(*),
      avg := round(avg(proc_min), 1),
      p50 := round(quantile_cont(proc_min, 0.5), 1),
      p90 := round(quantile_cont(proc_min, 0.9), 1),
      max := max(proc_min),
      per_video_minute := round(sum(proc_min) / nullif(sum(dur_min), 0), 2),
      avg_video_minutes := round(avg(dur_min), 1)
    ) FROM done)) AS processing,
  to_json((SELECT struct_pack(n := count(execution_delay_minute), avg := round(avg(execution_delay_minute)),
                              max := max(execution_delay_minute)) FROM pel)) AS wait,
  to_json((SELECT struct_pack(green := count(*) FILTER (WHERE speed_type = 'GREEN'),
                              red := count(*) FILTER (WHERE speed_type = 'RED')) FROM pel)) AS speed,
  (SELECT max(running) FROM run) AS concurrency_peak,
  (SELECT arg_max(t, running) FROM run) AS concurrency_peak_at,
  to_json((SELECT struct_pack(concurrent := max(conc), queue := max(q)) FROM po)) AS limits,
  (SELECT min(create_date) FROM pel) AS jobs_from,
  (SELECT max(create_date) FROM pel) AS last_job
`

// ── 4. 사용 구성 ─────────────────────────────────────────────────────────
const usageSQL = spacesCTE + `,
pairs AS (
  -- 코드 그대로 낸다(en → tr). language.description 은 en-US 같은 태그라 이름이 아니고,
  -- 한국어 표시명은 화면이 붙인다 — 표시는 화면의 일이다.
  SELECT coalesce(source_language_code, '?') || ' → ' || coalesce(target_language_code, '?') AS pair, count(*) AS n
  FROM pel GROUP BY 1
),
top5 AS (SELECT pair, n FROM pairs ORDER BY n DESC, pair LIMIT 5),
-- 멤버별 내보내기는 **최근 30일** — 좌석 활용률 카드가 「최근 30일」이고 「사용」 수와 같은 창이어야
-- 막대 수와 그 수가 맞는다. 6개월치는 seats.active_6m 이 셀 뿐이다.
byuser AS (SELECT user_seq, count(*) AS jobs FROM pel
           WHERE user_seq IS NOT NULL AND create_date >= (SELECT t FROM nowat) - INTERVAL 30 DAY GROUP BY 1),
sm AS (
  SELECT status, member_role
  FROM read_csv_auto('{{d}}/perso.space_member.part*.csv', union_by_name=true)
  WHERE space_seq IN (SELECT space_seq FROM sp)
),
spc AS (SELECT seat FROM read_csv_auto('{{d}}/perso.space.part*.csv', union_by_name=true) WHERE seq IN (SELECT space_seq FROM sp))
SELECT
  to_json((SELECT list(struct_pack(pair := pair, n := n) ORDER BY n DESC, pair) FROM top5)) AS languages,
  (SELECT coalesce(sum(n), 0) FROM pairs) - (SELECT coalesce(sum(n), 0) FROM top5) AS languages_other,
  to_json((SELECT list(struct_pack(bin := bin, n := n) ORDER BY ord) FROM (
      SELECT CASE WHEN m < 5 THEN '5분 미만' WHEN m < 15 THEN '5–15분' WHEN m < 30 THEN '15–30분' ELSE '30분 이상' END AS bin,
             CASE WHEN m < 5 THEN 1 WHEN m < 15 THEN 2 WHEN m < 30 THEN 3 ELSE 4 END AS ord, count(*) AS n
      FROM (SELECT original_video_duration_ms / 60000.0 AS m FROM pel WHERE original_video_duration_ms IS NOT NULL)
      GROUP BY 1, 2))) AS lengths,
  to_json((SELECT list(struct_pack(source := source, n := n) ORDER BY n DESC) FROM (
      SELECT coalesce(upload_source_type, '(미기록)') AS source, count(*) AS n FROM pel GROUP BY 1))) AS sources,
  to_json((SELECT struct_pack(
      seats := (SELECT coalesce(sum(seat), 0) FROM spc),
      spaces_found := (SELECT count(*) FROM spc),
      members := (SELECT count(*) FROM sm WHERE status = 'approval'),
      owners := (SELECT count(*) FROM sm WHERE status = 'approval' AND member_role = 'space_owner'),
      left := (SELECT count(*) FROM sm WHERE status <> 'approval'),
      active_30d := (SELECT count(DISTINCT user_seq) FROM pel WHERE create_date >= (SELECT t FROM nowat) - INTERVAL 30 DAY),
      active_6m := (SELECT count(DISTINCT user_seq) FROM pel)
    ))) AS seats,
  to_json((SELECT list(struct_pack(rank := rank, jobs := jobs) ORDER BY rank) FROM (
      SELECT row_number() OVER (ORDER BY jobs DESC, user_seq) AS rank, jobs FROM byuser ORDER BY jobs DESC LIMIT 20))) AS members,
  to_json((SELECT struct_pack(lip_sync := count(*) FILTER (WHERE lib_sync = 1), total := count(*),
                              avg_speakers := round(avg(speaker_count), 1)) FROM pel)) AS extras,
  (SELECT min(create_date) FROM pel) AS jobs_from
`

// ── 5. 대조 근거 (여러 스페이스 한 번에) ────────────────────────────────
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

// ── 실행 ────────────────────────────────────────────────────────────────

type spaceResult struct {
	Metric     string          `json:"metric"`
	Spaces     []int64         `json:"spaces"`
	AsOf       asOf            `json:"as_of"`
	SnapshotAt string          `json:"snapshot_at"`
	Data       json.RawMessage `json:"data"`
	ComputedMS int64           `json:"computed_ms"`
}

var spaceMetrics = map[string]string{
	"summary":  summarySQL,
	"credits":  creditsSQL,
	"jobs":     jobsSQL,
	"usage":    usageSQL,
	"evidence": evidenceSQL,
}

func (s *Snapshot) RunSpaces(metric string, spaces []int64) (*spaceResult, error) {
	tpl, ok := spaceMetrics[metric]
	if !ok {
		return nil, fmt.Errorf("모르는 지표입니다: %s", metric)
	}
	before := s.Commit()
	t0 := time.Now()
	at := s.snapshotAt()

	sql := strings.ReplaceAll(tpl, "{{d}}", filepath.ToSlash(s.Data))
	sql = strings.ReplaceAll(sql, "{{spaces}}", spaceList(spaces))
	sql = strings.ReplaceAll(sql, "{{asof}}", at.UTC().Format("2006-01-02 15:04:05"))

	rows, err := s.query(sql)
	if err != nil {
		return nil, err
	}
	if after := s.Commit(); before != after {
		return nil, errors.New("계산 중에 스냅샷이 바뀌었습니다 — 결과를 버립니다. 다시 시도하세요")
	}
	if len(rows) != 1 {
		return nil, fmt.Errorf("결과가 한 행이어야 합니다 (%d행)", len(rows))
	}
	data, err := json.Marshal(rows[0])
	if err != nil {
		return nil, errors.New("결과를 만들지 못했습니다")
	}
	// **나가기 전에 모양을 잰다.** 위 SQL 이 어떤 컬럼을 내든, 식별자·긴 원문·행 폭발은
	// 여기서 걸린다 — SQL 을 고치는 사람이 이 검사를 지나야 한다.
	if err := checkContract(data, map[string]bool{"space_seq": true}); err != nil {
		return nil, err
	}
	return &spaceResult{Metric: metric, Spaces: spaces, AsOf: s.AsOf(),
		SnapshotAt: at.UTC().Format(time.RFC3339), Data: data,
		ComputedMS: time.Since(t0).Milliseconds()}, nil
}

// checkContract — 중첩 JSON 을 통째로 걸으며 계약을 잰다. `allow` 에 있는 키만 식별자성
// 이름을 쓸 수 있다(콘솔이 보낸 space_seq 를 되돌려 주는 것).
func checkContract(data []byte, allow map[string]bool) error {
	var v any
	if err := json.Unmarshal(data, &v); err != nil {
		return errors.New("결과를 읽지 못했습니다")
	}
	return walkContract(v, allow)
}

func walkContract(v any, allow map[string]bool) error {
	switch x := v.(type) {
	case map[string]any:
		for k, child := range x {
			if !allow[k] && idLike.MatchString(k) {
				return fmt.Errorf("식별자성 컬럼이 결과에 있습니다: %s", k)
			}
			if err := walkContract(child, allow); err != nil {
				return err
			}
		}
	case []any:
		if len(x) > maxRows {
			return fmt.Errorf("집계가 %d행입니다 (상한 %d) — 집계가 아닙니다", len(x), maxRows)
		}
		for _, child := range x {
			if err := walkContract(child, allow); err != nil {
				return err
			}
		}
	case string:
		if len(x) > maxCellChars {
			return fmt.Errorf("셀이 %d자입니다 (상한 %d) — 원문일 수 있습니다", len(x), maxCellChars)
		}
	}
	return nil
}

// ── HTTP ────────────────────────────────────────────────────────────────

func (a *agent) spacesHandler(metric string) func(http.ResponseWriter, *http.Request, string) {
	return func(w http.ResponseWriter, r *http.Request, origin string) {
		spaces, err := parseSpaces(r.URL.Query().Get("s"))
		if err != nil {
			send(w, http.StatusBadRequest, errBody{Error: err.Error(), Metric: metric}, origin)
			return
		}
		result, err := a.snap.RunSpaces(metric, spaces)
		if err != nil {
			send(w, http.StatusInternalServerError, errBody{Error: err.Error(), Metric: metric}, origin)
			return
		}
		send(w, http.StatusOK, result, origin)
	}
}
