package main

import (
	"encoding/json"
	"errors"
	"net/http"
	"path/filepath"
	"strings"
	"time"
)

// ── 영업 인사이트 ──────────────────────────────────────────────────────────
// 수주 고객 화면과 반대 방향의 물음이다: 「우리가 아는 고객이 어떻게 쓰나」가 아니라 **「우리가
// 모르는 사람 중 누가 눈에 띄나」**와 「제품 전체가 어디로 가나」. 스냅샷은 B2B 만이 아니라 전부를
// 담고 있으니(스페이스 59만 · 사용자 59만) 셀프서브에서 크게 쓰는 스페이스가 곧 영업 대상이다.
//
// 결과 두 갈래:
//   - spaces — 눈에 띄는 스페이스 ≤ 300. 엔터프라이즈 연결(esa)이거나 30일 크레딧 1,500 이상이거나
//     30일 내보내기 10건 이상이거나 사용자가 둘 이상. **어느 것이 우리 고객인지는 콘솔이 안다**
//     (계약의 space_seq) — 여기서는 모른 채 고르고, 화면이 우리 고객을 뺀다.
//   - 전체 흐름 — 가입·구독·체크아웃·크레딧·언어쌍. 관측치 5 미만 그룹은 낸다(minGroup).
//
// 식별자는 space_seq 뿐이다(허브스팟 연락처의 「space seq」 칸으로 사람에게 이어진다). plan_name 은
// 엔터프라이즈 티어에서 「<회사> Biz」 꼴이라 회사를 말하지만 그것이 이 화면의 목적이다 — 사람의
// 이름·메일은 스냅샷에 없고, 값은 이 PC 를 안 떠난다.
const salesSQL = `
WITH nowat AS (SELECT CAST('{{asof}}' AS TIMESTAMP) AS t),
ps AS (SELECT project_seq, space_seq FROM read_csv_auto('{{d}}/perso_video_translator.project_space.csv', union_by_name=true)),
pel AS (
  SELECT p.create_date, p.job_status, p.user_seq, p.source_language_code, p.target_language_code, p.upload_source_type, ps.space_seq
  FROM read_csv_auto('{{d}}/perso_video_translator.project_export_log.csv', union_by_name=true) p
  JOIN ps USING (project_seq)
),
cuh AS (
  SELECT space_seq, create_date, plan_tier,
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
u AS (SELECT create_date, last_login_date, login_provider FROM read_csv_auto('{{d}}/perso_user.user.part*.csv', union_by_name=true)),
ch AS (SELECT create_date, is_expire, space_seq FROM read_csv_auto('{{d}}/perso_payment.checkout_history.csv', union_by_name=true)),
sh AS (SELECT create_date, event_type, plan_name FROM read_csv_auto('{{d}}/perso_payment.subscription_history.csv', union_by_name=true)),
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
toppair AS (SELECT space_seq, arg_max(pair, n) AS top_pair FROM pairs GROUP BY 1),
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
  ORDER BY ent DESC, credits_30d DESC, exports_30d DESC LIMIT 300
)
SELECT
  to_json((SELECT list(struct_pack(
      space_seq := space_seq, tier := tier, plan_name := plan_name, sub_status := sub_status, ent := ent,
      credits_30d := credits_30d, credits_90d := credits_90d, exports_30d := exports_30d, exports_6m := exports_6m,
      failed_6m := failed_6m, users := users, members := members, seat := seat,
      first_job := first_job, last_job := last_job, top_pair := top_pair
    ) ORDER BY credits_30d DESC, exports_30d DESC) FROM picked)) AS spaces,
  (SELECT count(*) FROM rows) AS spaces_active_6m,
  to_json((SELECT list(struct_pack(period := period, n := n) ORDER BY period) FROM (
      SELECT strftime(date_trunc('month', create_date), '%Y-%m') AS period, count(*) AS n FROM u
      WHERE create_date >= date_trunc('month', (SELECT t FROM nowat)) - INTERVAL 11 MONTH GROUP BY 1))) AS joins_monthly,
  to_json((SELECT struct_pack(
      total := count(*),
      active_30d := count(*) FILTER (WHERE last_login_date >= (SELECT t FROM nowat) - INTERVAL 30 DAY),
      active_90d := count(*) FILTER (WHERE last_login_date >= (SELECT t FROM nowat) - INTERVAL 90 DAY)
    ) FROM u)) AS users_all,
  to_json((SELECT list(struct_pack(provider := provider, n := n) ORDER BY n DESC) FROM (
      SELECT coalesce(login_provider, '(없음)') AS provider, count(*) AS n FROM u GROUP BY 1 HAVING count(*) >= 5))) AS login,
  to_json((SELECT list(struct_pack(period := period, created := created, deleted := deleted) ORDER BY period) FROM (
      SELECT strftime(date_trunc('month', create_date), '%Y-%m') AS period,
             count(*) FILTER (WHERE event_type = 'customer.subscription.created') AS created,
             count(*) FILTER (WHERE event_type = 'customer.subscription.deleted') AS deleted
      FROM sh WHERE create_date >= date_trunc('month', (SELECT t FROM nowat)) - INTERVAL 11 MONTH GROUP BY 1))) AS subs_monthly,
  to_json((SELECT list(struct_pack(plan := plan, n := n) ORDER BY n DESC) FROM (
      SELECT coalesce(plan_name, '(없음)') AS plan, count(*) AS n FROM sh
      WHERE event_type = 'customer.subscription.created' AND create_date >= (SELECT t FROM nowat) - INTERVAL 6 MONTH
      GROUP BY 1 HAVING count(*) >= 5))) AS subs_new_by_plan,
  to_json((SELECT list(struct_pack(tier := tier, ent := ent, spaces := spaces, exports := exports, credits := credits) ORDER BY spaces DESC) FROM (
      SELECT tier, ent, count(*) AS spaces, sum(exports_30d) AS exports, round(sum(credits_30d)) AS credits
      FROM rows WHERE exports_30d > 0 GROUP BY 1, 2 HAVING count(*) >= 5))) AS tiers_30d,
  to_json((SELECT list(struct_pack(period := period, tier := tier, credits := credits) ORDER BY period, tier) FROM (
      SELECT strftime(date_trunc('month', create_date), '%Y-%m') AS period, coalesce(plan_tier, '(없음)') AS tier, round(sum(signed)) AS credits
      FROM cuh GROUP BY 1, 2 HAVING count(*) >= 5))) AS credits_monthly,
  to_json((SELECT list(struct_pack(period := period, sessions := sessions, expired := expired, spaces := spaces) ORDER BY period) FROM (
      SELECT strftime(date_trunc('month', create_date), '%Y-%m') AS period, count(*) AS sessions,
             count(*) FILTER (WHERE is_expire = 1) AS expired, count(DISTINCT space_seq) AS spaces
      FROM ch GROUP BY 1))) AS checkouts_monthly,
  to_json((SELECT list(struct_pack(pair := pair, n := n) ORDER BY n DESC) FROM (
      SELECT coalesce(source_language_code, '?') || ' → ' || coalesce(target_language_code, '?') AS pair, count(*) AS n
      FROM pel GROUP BY 1 HAVING count(*) >= 5 ORDER BY n DESC LIMIT 10))) AS pairs_6m,
  to_json((SELECT list(struct_pack(source := source, n := n) ORDER BY n DESC) FROM (
      SELECT coalesce(upload_source_type, '(미기록)') AS source, count(*) AS n FROM pel GROUP BY 1 HAVING count(*) >= 5))) AS sources_6m,
  (SELECT round(100.0 * count(*) FILTER (WHERE job_status = 'FAILED') / nullif(count(*) FILTER (WHERE job_status IN ('COMPLETED', 'FAILED')), 0), 2) FROM pel) AS fail_rate_all,
  (SELECT min(create_date) FROM pel) AS jobs_from,
  (SELECT min(create_date) FROM cuh) AS credits_from
`

type salesResult struct {
	AsOf       asOf            `json:"as_of"`
	SnapshotAt string          `json:"snapshot_at"`
	Data       json.RawMessage `json:"data"`
	ComputedMS int64           `json:"computed_ms"`
}

// RunSales 는 스페이스 목록 없이 도는 한 질의 — 계약(식별자 없음 · 2,000행 · 120자)은 스페이스
// 지표와 같고, space_seq 만 허용한다.
func (s *Snapshot) RunSales() (*salesResult, error) {
	before := s.Commit()
	t0 := time.Now()
	at := s.snapshotAt()
	sql := strings.ReplaceAll(salesSQL, "{{d}}", filepath.ToSlash(s.Data))
	sql = strings.ReplaceAll(sql, "{{asof}}", at.UTC().Format("2006-01-02 15:04:05"))
	rows, err := s.query(sql)
	if err != nil {
		return nil, err
	}
	if after := s.Commit(); before != after {
		return nil, errors.New("계산 중에 스냅샷이 바뀌었습니다 — 결과를 버립니다. 다시 시도하세요")
	}
	if len(rows) != 1 {
		return nil, errors.New("결과가 한 행이어야 합니다")
	}
	data, err := json.Marshal(rows[0])
	if err != nil {
		return nil, errors.New("결과를 만들지 못했습니다")
	}
	if err := checkContract(data, map[string]bool{"space_seq": true}); err != nil {
		return nil, err
	}
	return &salesResult{AsOf: s.AsOf(), SnapshotAt: at.UTC().Format(time.RFC3339), Data: data,
		ComputedMS: time.Since(t0).Milliseconds()}, nil
}

func (a *agent) salesHandler(w http.ResponseWriter, _ *http.Request, origin string) {
	result, err := a.snap.RunSales()
	if err != nil {
		send(w, http.StatusInternalServerError, errBody{Error: err.Error(), Metric: "sales"}, origin)
		return
	}
	send(w, http.StatusOK, result, origin)
}
