package main

// 수주 고객 화면의 세 카드(크레딧 소진 · 작업 성능 · 사용 구성)가 쓰는 **스페이스 하나씩의 재료** (2026-10-01).
//
// 예전에는 화면이 계약의 스페이스 목록을 로컬 에이전트에 보내 그 목록으로 SQL 을 돌렸다. 이제 가공은 하루
// 한 번 돌고 목록을 받을 곳이 없으므로, 가공 범위 전부를 **스페이스마다 한 줄씩** 내고 브라우저가 계약의
// 스페이스끼리 합친다(frontend/src/screens/won/usageMerge.ts). 그래서 여기 값은 합칠 수 있는 모양이다:
//   - 합은 **반올림 전**이다. 반올림한 값은 더할 수 없다 — 합친 뒤에 브라우저가 한 번 반올림한다.
//   - 평균은 합과 개수로 낸다. 상위 5 · 동시 작업 피크 · 사람 수는 나눠 센 것을 더할 수 없어 원재료를 낸다
//     (종류 전부 · 시작/끝 사건 · 사람의 차례 번호).
//
// 정의의 기준은 옛 목록 SQL(git 092d34f 의 agent/spaces.go — creditsSQL · jobsSQL · usageSQL)을 **그
// 스페이스 하나짜리 목록**으로 돌린 값이다. 목록 SQL 이 「목록 전체」로 묶던 자리(PARTITION BY · JOIN ·
// 프로젝트별 첫 소진)에는 space_seq 를 더했다 — 안 더하면 다른 스페이스의 줄이 섞인다.
//
// **b · p · u · pf 는 식별자가 아니다.** b·p 는 이번 가공 안에서만 통하는 차례 번호(credit_seq ·
// project_seq 의 순서를 지키는 dense_rank — 화면이 원래 순서대로 줄 세울 수 있게)이고, u 는 사용자 번호를
// 가공마다 새 소금(salt)으로 섞은 뒤 매긴 차례라 날마다 바뀐다 — 어제 파일과 오늘 파일의 u 를 이어 붙일 수
// 없다. pf 는 그 프로젝트의 첫 소진 시각(epoch µs)이다. 그래서 계약 검사(idLike)에 걸리는 이름을 안 쓴다.

// ── 1. 크레딧 소진 ───────────────────────────────────────────────────────
const creditsFactsSQL = spacesCTE + `,
mon AS (
  SELECT space_seq, list(struct_pack(period := period, used := used) ORDER BY period) AS monthly
  FROM (SELECT space_seq, strftime(date_trunc('month', create_date), '%Y-%m') AS period, sum(signed) AS used
        FROM cuh GROUP BY 1, 2)
  GROUP BY 1
),
wk AS (
  SELECT space_seq, list(struct_pack(period := period, used := used) ORDER BY period) AS weekly
  FROM (SELECT space_seq, strftime(date_trunc('week', create_date), '%Y-%m-%d') AS period, sum(signed) AS used
        FROM cuh WHERE create_date >= (SELECT t FROM nowat) - INTERVAL 182 DAY GROUP BY 1, 2)
  GROUP BY 1
),
-- 지급 묶음 — 옛 SQL 은 「history_seq 가 목록의 크레딧 기록에 있으면」(IN) 한 번 실었다. 스페이스로 나누려면
-- history_seq → 스페이스 짝이 필요하고, 한 history_seq 가 두 스페이스에 걸리면 그 줄은 두 스페이스에 한 번씩
-- 실린다(둘을 합치면 두 번 센다). 그 수는 sharedHistorySQL 이 따로 세어 경고로 남긴다.
hm AS (SELECT DISTINCT credit_history_seq, space_seq FROM cuh),
scuh AS (
  SELECT h.space_seq, s.credit_seq, s.earn_type, s.is_free, s.initial_credit, s.create_date
  FROM read_csv_auto('{{d}}/perso_payment.space_credit_usage_history.csv', union_by_name=true) s
  JOIN hm h ON h.credit_history_seq = s.history_seq
),
bucket AS (
  SELECT space_seq, list(struct_pack(b := b, earn_type := earn_type, is_free := is_free,
                                     first_use := first_use, consumed := consumed) ORDER BY b, earn_type, is_free) AS buckets
  FROM (SELECT space_seq, dense_rank() OVER (ORDER BY credit_seq) AS b, earn_type, is_free,
               min(create_date) AS first_use, sum(initial_credit) AS consumed
        FROM scuh GROUP BY space_seq, credit_seq, earn_type, is_free)
  GROUP BY 1
),
-- 작업별 소진 기록 — 한 줄 = 한 작업 실행: 같은 프로젝트·같은 초의 차감 행(파이프라인 단계마다 한 행)을
-- 합친다. 작업 상세는 같은 스페이스·같은 프로젝트의 export 기록 중 5분 안에서 가장 가까운 것; 없으면(작업 창
-- 밖) 빈칸. 프로젝트 번호가 없는 줄은 옛 SQL 처럼 JOIN pf 에서 빠진다. **500건으로 자르지 않는다** — 자르면
-- 계약의 스페이스를 합친 뒤의 최신 500건이 달라진다.
grp AS (
  SELECT project_seq, space_seq, action_type, plan_tier, date_trunc('second', create_date) AS t,
         round(sum(signed)) AS credits, count(*) AS steps
  FROM cuh GROUP BY 1, 2, 3, 4, 5
),
near AS (
  SELECT g.*, e.job_status, e.speaker_count,
         CASE WHEN e.source_language_code IS NULL THEN NULL
              ELSE e.source_language_code || ' → ' || e.target_language_code END AS pair,
         round(e.original_video_duration_ms / 60000.0, 1) AS minutes,
         (e.lib_sync = 1) AS lip_sync,
         row_number() OVER (PARTITION BY g.space_seq, g.project_seq, g.t, g.action_type, g.plan_tier
                            ORDER BY abs(date_diff('second', g.t, e.create_date))) AS rn
  FROM grp g
  LEFT JOIN pel e ON e.space_seq = g.space_seq AND e.project_seq = g.project_seq
                 AND abs(date_diff('second', g.t, e.create_date)) <= 300
),
pf AS (SELECT space_seq, project_seq, min(create_date) AS f FROM cuh GROUP BY 1, 2),
rec AS (
  SELECT n.*, epoch_us(pf.f) AS first_us, dense_rank() OVER (ORDER BY n.project_seq) AS p
  FROM near n JOIN pf ON pf.space_seq = n.space_seq AND pf.project_seq = n.project_seq
  WHERE n.rn = 1
),
recs AS (
  SELECT space_seq, list(struct_pack(at := t, pf := first_us, p := p, action := action_type, credits := credits,
                                     steps := steps, status := job_status, pair := pair, minutes := minutes,
                                     lip_sync := lip_sync, speakers := speaker_count) ORDER BY t DESC, p DESC) AS records
  FROM rec GROUP BY 1
)
SELECT sp.space_seq,
       to_json(struct_pack(monthly := mon.monthly, weekly := wk.weekly, buckets := bucket.buckets, records := recs.records)) AS credits
FROM sp LEFT JOIN mon USING (space_seq) LEFT JOIN wk USING (space_seq)
        LEFT JOIN bucket USING (space_seq) LEFT JOIN recs USING (space_seq)
ORDER BY sp.space_seq
`

// 한 history_seq 가 둘 이상의 스페이스에 걸리는 수 — 지급 묶음 소진이 그만큼 두 번 실린다(2026-09-14 스냅샷 실측 0).
const sharedHistorySQL = spacesCTE + `
SELECT count(*) AS shared FROM (
  SELECT credit_history_seq FROM (SELECT DISTINCT credit_history_seq, space_seq FROM cuh)
  WHERE credit_history_seq IN (
    SELECT history_seq FROM read_csv_auto('{{d}}/perso_payment.space_credit_usage_history.csv', union_by_name=true))
  GROUP BY 1 HAVING count(*) > 1
)
`

// ── 2. 작업 성능 ─────────────────────────────────────────────────────────
const jobsFactsSQL = spacesCTE + `,
lar AS (
  -- 실패 종류: 엔진 오류 코드가 있으면 그것(NO_VOICE_DETECTED_VAD 처럼 사람이 읽는 원인),
  -- 없으면 실패 사유(AUDIO_PIPELINE_FAILED …). ENGINE_ERROR 한 줄로 뭉치면 원인이 안 보인다.
  SELECT pel.space_seq, coalesce(nullif(l.engine_error_code, ''), l.failure_reason, '(미기록)') AS kind
  -- **실패한 작업만** 센다. 이 표는 실패 전용이 아니다: 4,357 줄을 export 기록과 맞대면
  -- FAILED 4,246 · COMPLETED 105 · 빈칸 5 · PROCESSING 1 이고, 그 111 줄은 **전부**
  -- '(미기록)' 으로 떨어진다(2026-09-14 스냅샷 실측). 그래서 실패가 0건인 엔터프라이즈
  -- 스페이스 하나가 94 줄을 싣고 있었고, 카드에 「100.0% · 실패 0건」과 「사유 미기록 94건」이
  -- 나란히 섰다 — 운영자가 「작업 성공률 100퍼 맞는지 확인해봐」라고 한 자리다.
  FROM read_csv_auto('{{d}}/perso_video_translator.live_api_response.csv', union_by_name=true) l
  JOIN pel ON pel.seq = l.export_log_seq AND pel.job_status = 'FAILED'
),
-- 종류 **전부**를 낸다 — 상위 5와 「기타」는 스페이스를 합친 뒤에야 정해진다.
kinds AS (
  SELECT space_seq, list(struct_pack(kind := kind, n := n) ORDER BY n DESC, kind) AS kinds
  FROM (SELECT space_seq, kind, count(*) AS n FROM lar GROUP BY 1, 2)
  GROUP BY 1
),
-- 플랜 한도 — 스페이스의 구독(subscriber.plan_seq) → plan_option(video_translator) 의 JSON.
-- 동시 처리 한도는 계약 폼에도 있지만(concurrent_jobs) 스냅샷이 아는 값이 실제 값이다.
-- 플랜이 여럿이면 큰 쪽을 든다. 990/990 엔터프라이즈 스페이스가 풀린다(2026-09-14 실측).
po AS (
  SELECT s.space_seq, max(TRY_CAST(json_extract_string(o.detail, '$.concurrentJobs') AS INTEGER)) AS conc
  FROM read_csv_auto('{{d}}/perso_payment.plan_option.csv', union_by_name=true) o
  JOIN read_csv_auto('{{d}}/perso_payment.subscriber.part*.csv', union_by_name=true) s ON s.plan_seq = o.plan_seq
  WHERE o.type = 'video_translator' AND s.space_seq IN (SELECT space_seq FROM sp)
  GROUP BY 1
),
j AS (
  SELECT space_seq,
         count(*) FILTER (WHERE job_status = 'COMPLETED') AS ok,
         count(*) FILTER (WHERE job_status = 'FAILED') AS failed,
         count(execution_delay_minute) AS wait_n, sum(execution_delay_minute) AS wait_sum
  FROM pel GROUP BY 1
),
done AS (
  SELECT space_seq, count(*) AS proc_n, sum(proc_min) AS proc_sum, max(proc_min) AS proc_max, sum(dur_min) AS dur_sum
  FROM (SELECT space_seq, date_diff('minute', create_date, update_date) AS proc_min,
               original_video_duration_ms / 60000.0 AS dur_min
        FROM pel WHERE job_status = 'COMPLETED' AND update_date >= create_date)
  GROUP BY 1
),
-- 동시 작업 — 피크는 스페이스를 섞어서 다시 세야 하므로 시작(+1)·끝(−1) 사건을 그대로 낸다.
ev AS (
  SELECT space_seq, list([epoch_us(t), d] ORDER BY t, d) AS events
  FROM (SELECT space_seq, create_date AS t, 1 AS d FROM pel
        WHERE job_status IN ('COMPLETED', 'FAILED') AND update_date > create_date
        UNION ALL
        SELECT space_seq, update_date AS t, -1 AS d FROM pel
        WHERE job_status IN ('COMPLETED', 'FAILED') AND update_date > create_date)
  GROUP BY 1
)
SELECT sp.space_seq, to_json(struct_pack(
    ok := coalesce(j.ok, 0), failed := coalesce(j.failed, 0), kinds := kinds.kinds,
    proc_n := coalesce(done.proc_n, 0), proc_sum := done.proc_sum, proc_max := done.proc_max, dur_sum := done.dur_sum,
    wait_n := coalesce(j.wait_n, 0), wait_sum := j.wait_sum,
    events := ev.events, conc := po.conc)) AS jobs
FROM sp LEFT JOIN j USING (space_seq) LEFT JOIN kinds USING (space_seq) LEFT JOIN done USING (space_seq)
        LEFT JOIN ev USING (space_seq) LEFT JOIN po USING (space_seq)
ORDER BY sp.space_seq
`

// ── 3. 사용 구성 ─────────────────────────────────────────────────────────
const usageFactsSQL = spacesCTE + `,
pairs AS (
  -- 코드 그대로 낸다(en → tr). 한국어 표시명은 화면이 붙인다 — 표시는 화면의 일이다.
  SELECT space_seq, list(struct_pack(pair := pair, n := n) ORDER BY n DESC, pair) AS pairs
  FROM (SELECT space_seq, coalesce(source_language_code, '?') || ' → ' || coalesce(target_language_code, '?') AS pair,
               count(*) AS n
        FROM pel GROUP BY 1, 2)
  GROUP BY 1
),
lens AS (
  SELECT space_seq, list(struct_pack(bin := bin, ord := ord, n := n) ORDER BY ord) AS lengths
  FROM (SELECT space_seq,
               CASE WHEN m < 5 THEN '5분 미만' WHEN m < 15 THEN '5–15분' WHEN m < 30 THEN '15–30분' ELSE '30분 이상' END AS bin,
               CASE WHEN m < 5 THEN 1 WHEN m < 15 THEN 2 WHEN m < 30 THEN 3 ELSE 4 END AS ord, count(*) AS n
        FROM (SELECT space_seq, original_video_duration_ms / 60000.0 AS m FROM pel WHERE original_video_duration_ms IS NOT NULL)
        GROUP BY 1, 2, 3)
  GROUP BY 1
),
srcs AS (
  SELECT space_seq, list(struct_pack(source := source, n := n) ORDER BY n DESC, source) AS sources
  FROM (SELECT space_seq, coalesce(upload_source_type, '(미기록)') AS source, count(*) AS n FROM pel GROUP BY 1, 2)
  GROUP BY 1
),
-- 콘텐츠 카테고리 — project_sensitive.project_category (2026-09-16 운영자). 프로젝트당 한 줄이어야 하는데
-- 드물게 둘이라(실측 92,393 대 92,389) 최신 것 하나만 — 안 그러면 내보내기 수가 부풀어 「총 영상」이 틀린다.
-- 세는 단위는 다른 카드와 같은 **내보내기**(pel)라 파이 가운데 수가 언어쌍·길이와 맞는다. '(미기록)' 까지 전부 낸다.
cats AS (
  SELECT space_seq, list(struct_pack(category := category, n := n) ORDER BY n DESC, category) AS categories
  FROM (
    SELECT pel.space_seq, coalesce(c.category, '(미기록)') AS category, count(*) AS n
    FROM pel LEFT JOIN (
      SELECT project_seq, arg_max(project_category, seq) AS category
      FROM read_csv_auto('{{d}}/perso_video_translator.project_sensitive.csv', union_by_name=true)
      WHERE project_seq IN (SELECT project_seq FROM ps) GROUP BY 1
    ) c ON c.project_seq = pel.project_seq
    GROUP BY 1, 2)
  GROUP BY 1
),
-- 보이스 클론 — space_voice 의 살아 있는 줄. 등록한 사람 수는 좌석 대비를 내는 데 쓴다(사람은 안 나간다).
sv AS (SELECT space_seq, user_seq FROM read_csv_auto('{{d}}/perso_video_translator.space_voice.csv', union_by_name=true)
       WHERE space_seq IN (SELECT space_seq FROM sp) AND is_active = 1),
-- 사람 → u. 이번 가공에서 **한 번** 매겨 두 칸(voice_users · users_30d)이 같이 쓴다 — 두 스페이스에 걸친 같은
-- 사람을 한 명으로 세야 하기 때문이다. 순서는 소금 친 해시라 user_seq 의 순서도 안 남고, 소금이 날마다 바뀌어
-- 다른 날 파일과 이어지지 않는다. 해시가 우연히 같으면 user_seq 로 가른다(같은 번호에 두 사람이 묶이지 않게).
users AS (
  SELECT user_seq, row_number() OVER (ORDER BY hash(CAST(user_seq AS VARCHAR) || '{{salt}}'), user_seq) AS u
  FROM (SELECT user_seq FROM pel WHERE user_seq IS NOT NULL UNION SELECT user_seq FROM sv WHERE user_seq IS NOT NULL)
),
vo AS (SELECT space_seq, count(*) AS voices FROM sv GROUP BY 1),
vu AS (
  SELECT space_seq, list(u ORDER BY u) AS voice_users
  FROM (SELECT DISTINCT sv.space_seq, users.u FROM sv JOIN users USING (user_seq))
  GROUP BY 1
),
spc AS (
  SELECT seq AS space_seq, sum(seat) AS seats
  FROM read_csv_auto('{{d}}/perso.space.part*.csv', union_by_name=true)
  WHERE seq IN (SELECT space_seq FROM sp) GROUP BY 1
),
sm AS (
  SELECT space_seq, count(*) AS members
  FROM read_csv_auto('{{d}}/perso.space_member.part*.csv', union_by_name=true)
  WHERE space_seq IN (SELECT space_seq FROM sp) AND status = 'approval' GROUP BY 1
),
-- 멤버별 내보내기는 **최근 30일** — 좌석 활용률 카드가 「최근 30일」이고 「사용」 수와 같은 창이어야
-- 막대 수와 그 수가 맞는다.
byuser AS (
  SELECT space_seq, list(struct_pack(u := u, jobs := jobs) ORDER BY jobs DESC, u) AS users_30d
  FROM (SELECT pel.space_seq, users.u, count(*) AS jobs
        FROM pel JOIN users USING (user_seq)
        WHERE pel.create_date >= (SELECT t FROM nowat) - INTERVAL 30 DAY GROUP BY 1, 2)
  GROUP BY 1
)
SELECT sp.space_seq, to_json(struct_pack(
    pairs := pairs.pairs, lengths := lens.lengths, sources := srcs.sources, categories := cats.categories,
    voices := coalesce(vo.voices, 0), voice_users := vu.voice_users,
    seats := coalesce(spc.seats, 0), members := coalesce(sm.members, 0),
    users_30d := byuser.users_30d)) AS usage
FROM sp LEFT JOIN pairs USING (space_seq) LEFT JOIN lens USING (space_seq) LEFT JOIN srcs USING (space_seq)
        LEFT JOIN cats USING (space_seq) LEFT JOIN vo USING (space_seq) LEFT JOIN vu USING (space_seq)
        LEFT JOIN spc USING (space_seq) LEFT JOIN sm USING (space_seq) LEFT JOIN byuser USING (space_seq)
ORDER BY sp.space_seq
`
