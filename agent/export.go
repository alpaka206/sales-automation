package main

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"log"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"
)

// 가공 범위 — 엔터프라이즈에 묶인 스페이스 ∪ 무료가 아닌 플랜을 구독한 스페이스 ∪ 엔터프라이즈 크레딧을 쓴 적이
// 있는 스페이스. 셋째가 없으면 계약이 끝나 플랜이 무료로 돌아간 B2B 스페이스가 빠진다 — 화면이 그 계약을
// 「가공 범위에 없다」로 적고, 상세 세 카드와 지급 회차 자동 대조가 거기서 멈춘다(옛 에이전트는 어느 스페이스든
// 답했다). 수주 고객의 스페이스는 대개 이 안에 있고, 밖이면 화면이 그 번호를 적는다(2026-09-14 스냅샷 실측:
// 엔터프라이즈 990개 · 119곳 · 가장 큰 곳 203개, 그 밖의 유료 1,101개, 엔터프라이즈 크레딧으로만 걸리는 3개 —
// 합 2,094). 엔터프라이즈 번호는 파일 묶음을 정하는 데만 쓰고 파일에는 안 나간다.
const universeSQL = `
WITH esa AS (
  SELECT space_seq, min(enterprise_seq) AS ent
  FROM read_csv_auto('{{d}}/perso.enterprise_space_association.csv', union_by_name=true)
  WHERE space_seq IS NOT NULL GROUP BY 1
),
paid AS (
  SELECT DISTINCT s.space_seq
  FROM read_csv_auto('{{d}}/perso_payment.subscriber.part*.csv', union_by_name=true) s
  JOIN read_csv_auto('{{d}}/perso_payment.plan.csv', union_by_name=true) pl ON pl.seq = s.plan_seq
  WHERE pl.priority_tier IS NOT NULL AND lower(pl.priority_tier) <> 'free' AND s.space_seq IS NOT NULL
),
earned AS (
  SELECT DISTINCT c.space_seq
  FROM read_csv_auto('{{d}}/perso_video_translator.credit_usage_history.csv', union_by_name=true) c
  JOIN read_csv_auto('{{d}}/perso_payment.space_credit_usage_history.csv', union_by_name=true) s
    ON s.history_seq = c.credit_history_seq
  WHERE s.earn_type = 'enterprise' AND c.space_seq IS NOT NULL
)
SELECT u.space_seq, esa.ent
FROM (SELECT space_seq FROM esa UNION SELECT space_seq FROM paid UNION SELECT space_seq FROM earned) u
LEFT JOIN esa USING (space_seq)
ORDER BY u.space_seq
`

// 가공 파일의 모양 번호 — manifest.json 과 묶음 파일마다 싣는다. 화면(frontend/src/lib/usageData.ts 의
// FORMAT)은 이 값이 자기 것과 다르면 숫자를 그리지 않고 그렇다고 말한다: 화면은 main 에 들어가면 곧 배포되고
// 재료는 다음 가공에야 바뀌어서, 모양이 다른 재료를 합치면 빈 칸 · NaN 이 「기록 없음」처럼 서는 사이가 실제로
// 생긴다. 재료(facts.go)나 파일의 모양을 바꾸면 이 값과 FORMAT 을 같은 커밋에서 같이 올린다.
const dataFormat = 1

// 나가는 파일에서 식별자성 이름이 허용되는 키 — 화면이 계약의 스페이스를 찾는 space_seq 하나.
var allowKeys = map[string]bool{"space_seq": true}

type member struct {
	Space int64
	Ent   *int64 // 엔터프라이즈가 아니면 nil
}

type spaceFacts struct {
	SpaceSeq int64           `json:"space_seq"`
	Credits  json.RawMessage `json:"credits"`
	Jobs     json.RawMessage `json:"jobs"`
	Usage    json.RawMessage `json:"usage"`
}

type factsFile struct {
	Format int          `json:"format"`
	Group  string       `json:"group"`
	Spaces []spaceFacts `json:"spaces"`
}

type manifest struct {
	Format         int    `json:"format"`
	SnapshotAt     string `json:"snapshot_at"`
	SnapshotCommit string `json:"snapshot_commit"`
	ExportedAt     string `json:"exported_at"`
	Exporter       string `json:"exporter"`
	Spaces         int    `json:"spaces"`
	Groups         int    `json:"groups"`
}

// fill — SQL 틀의 자리표시를 채운다. 들어가는 것은 숫자 목록 · 시각 · 경로 · 16진 소금뿐이다.
func fill(tpl, data string, spaces []int64, at time.Time, salt string) string {
	parts := make([]string, len(spaces))
	for i, s := range spaces {
		parts[i] = strconv.FormatInt(s, 10)
	}
	return strings.NewReplacer(
		"{{d}}", filepath.ToSlash(data),
		"{{spaces}}", strings.Join(parts, ","),
		"{{asof}}", at.UTC().Format("2006-01-02 15:04:05"),
		"{{salt}}", salt,
	).Replace(tpl)
}

// newSalt — 사람의 차례 번호(u)를 섞는 소금. 가공마다 새로 만들고 어디에도 안 남긴다 — 그래서 날마다 u 가
// 바뀌어 다른 날 파일과 이어 붙일 수 없다.
func newSalt() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b) // Go 1.24 부터 실패하지 않는다(실패하면 프로세스가 죽는다)
	return hex.EncodeToString(b)
}

// groupsOf — 파일 묶음. 엔터프라이즈 스페이스는 그 회사 묶음(e1, e2 … — enterprise_seq 의 차례)이라 계약
// 하나가 대개 파일 하나로 끝난다. 나머지는 space_seq 를 16 으로 나눈 나머지(p00 ~ p15) — 회사가 없으니
// 고르게 나눈다.
func groupsOf(members []member) map[int64]string {
	var ents []int64
	for _, m := range members {
		if m.Ent != nil {
			ents = append(ents, *m.Ent)
		}
	}
	slices.Sort(ents)
	ents = slices.Compact(ents)
	out := make(map[int64]string, len(members))
	for _, m := range members {
		if m.Ent != nil {
			i, _ := slices.BinarySearch(ents, *m.Ent)
			out[m.Space] = "e" + strconv.Itoa(i+1)
		} else {
			out[m.Space] = fmt.Sprintf("p%02d", m.Space%16)
		}
	}
	return out
}

// assembleFacts — facts/index.json 과 묶음 파일들. 범위의 스페이스는 **활동이 없어도** 한 줄씩 다 싣는다 —
// 화면이 「없음」과 「가공 범위 밖」을 가를 수 있게.
func assembleFacts(groups map[int64]string, credits, jobs, usage map[int64]json.RawMessage) (map[string]any, error) {
	spaces := make([]int64, 0, len(groups))
	for s := range groups {
		spaces = append(spaces, s)
	}
	slices.Sort(spaces)
	index := make(map[string]string, len(spaces))
	byGroup := map[string][]spaceFacts{}
	for _, s := range spaces {
		c, j, u := credits[s], jobs[s], usage[s]
		if c == nil || j == nil || u == nil {
			return nil, fmt.Errorf("스페이스 하나의 재료가 빠졌습니다 (%s 묶음)", groups[s])
		}
		g := groups[s]
		index[strconv.FormatInt(s, 10)] = g
		byGroup[g] = append(byGroup[g], spaceFacts{SpaceSeq: s, Credits: c, Jobs: j, Usage: u})
	}
	files := map[string]any{"facts/index.json": index}
	for g, list := range byGroup {
		files["facts/"+g+".json"] = factsFile{Format: dataFormat, Group: g, Spaces: list}
	}
	return files, nil
}

// keepEvidence — 근거(지급 묶음 · 국내 카드 결제)가 하나라도 있는 스페이스만 남긴다.
func keepEvidence(row json.RawMessage) (any, error) {
	var r struct {
		Spaces []map[string]json.RawMessage `json:"spaces"`
	}
	if err := json.Unmarshal(row, &r); err != nil {
		return nil, errors.New("대조 근거를 읽지 못했습니다")
	}
	kept := []map[string]json.RawMessage{}
	for _, s := range r.Spaces {
		if !isNull(s["buckets"]) || !isNull(s["payments"]) {
			kept = append(kept, s)
		}
	}
	return map[string]any{"spaces": kept}, nil
}

func isNull(v json.RawMessage) bool {
	t := bytes.TrimSpace(v)
	return len(t) == 0 || bytes.Equal(t, []byte("null"))
}

// encode — 파일마다 JSON 으로 바꾸고 **쓰기 전에** 계약을 잰다. 위 SQL 이 어떤 컬럼을 내든 식별자 이름 ·
// 긴 원문 · 행 폭발은 여기서 걸린다 — SQL 을 고치는 사람이 이 검사를 지나야 한다.
func encode(files map[string]any) (map[string][]byte, error) {
	out := make(map[string][]byte, len(files))
	for path, v := range files {
		body, err := json.Marshal(v)
		if err != nil {
			return nil, fmt.Errorf("%s 를 만들지 못했습니다", path)
		}
		if err := checkContract(body, allowKeys); err != nil {
			return nil, fmt.Errorf("%s: %w", path, err)
		}
		out[path] = body
	}
	return out, nil
}

// publish — `<out>.tmp` 에 다 쓴 뒤 한 번에 옮긴다. 중간에 실패하면 반쯤 쓴 폴더가 아니라 **아무것도** 안
// 남는다 — 워크플로가 반쪽짜리를 data 브랜치에 올리는 일이 없게. 이미 있는 폴더는 지우지 않고 거절한다:
// 경로를 잘못 준 한 번이 남의 폴더를 통째로 지우면 안 된다.
func publish(out string, files map[string][]byte) error {
	tmp := out + ".tmp"
	for _, p := range []string{out, tmp} {
		if _, err := os.Lstat(p); !errors.Is(err, fs.ErrNotExist) {
			return fmt.Errorf("%s 가 이미 있습니다 — 지우고 다시 실행하세요", p)
		}
	}
	err := func() error {
		for path, body := range files {
			p := filepath.Join(tmp, filepath.FromSlash(path))
			if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
				return err
			}
			if err := os.WriteFile(p, body, 0o644); err != nil {
				return err
			}
		}
		return os.Rename(tmp, out)
	}()
	if err != nil {
		os.RemoveAll(tmp)
	}
	return err
}

// ── DuckDB 를 부르는 쪽 ─────────────────────────────────────────────────

func (s *Snapshot) universe() ([]member, error) {
	rows, err := s.query(fill(universeSQL, s.Data, nil, time.Time{}, ""))
	if err != nil {
		return nil, err
	}
	out := make([]member, 0, len(rows))
	for _, r := range rows {
		var m member
		if json.Unmarshal(r["space_seq"], &m.Space) != nil || json.Unmarshal(r["ent"], &m.Ent) != nil {
			return nil, errors.New("가공 범위를 읽지 못했습니다")
		}
		out = append(out, m)
	}
	if len(out) == 0 {
		return nil, errors.New("가공 범위가 비었습니다 — 스냅샷의 엔터프라이즈·구독 표를 확인하세요")
	}
	return out, nil
}

// runRow — 한 행을 내는 질의(요약 · 근거 · 영업 인사이트)의 그 행.
func (s *Snapshot) runRow(tpl string, spaces []int64, at time.Time) (json.RawMessage, error) {
	rows, err := s.query(fill(tpl, s.Data, spaces, at, ""))
	if err != nil {
		return nil, err
	}
	if len(rows) != 1 {
		return nil, fmt.Errorf("결과가 한 행이어야 합니다 (%d행)", len(rows))
	}
	return json.Marshal(rows[0])
}

// runFacts — 스페이스마다 한 줄(space_seq + 재료 JSON 한 칸 `col`)을 내는 질의(facts.go).
func (s *Snapshot) runFacts(tpl, col string, spaces []int64, at time.Time, salt string) (map[int64]json.RawMessage, error) {
	rows, err := s.query(fill(tpl, s.Data, spaces, at, salt))
	if err != nil {
		return nil, err
	}
	out := make(map[int64]json.RawMessage, len(rows))
	for _, r := range rows {
		var seq int64
		v, ok := r[col]
		if len(r) != 2 || !ok || json.Unmarshal(r["space_seq"], &seq) != nil {
			return nil, fmt.Errorf("%s 재료의 모양이 다릅니다", col)
		}
		out[seq] = v
	}
	if len(out) != len(spaces) {
		return nil, fmt.Errorf("%s 재료가 %d줄입니다 (스페이스 %d개)", col, len(out), len(spaces))
	}
	return out, nil
}

// export — 스냅샷 한 벌을 가공해 out 에 쓴다. 로그에는 개수·크기·시간만 남긴다 — Actions 로그는 가공 레포를
// 볼 수 있는 사람 누구나 본다.
func export(snap *Snapshot, out string) error {
	t0 := time.Now()
	at := snap.snapshotAt()
	commit := snap.Commit()
	log.Printf("perso-export %s · 스냅샷 %s · 커밋 %s", version, at.UTC().Format(time.RFC3339), commit)

	members, err := snap.universe()
	if err != nil {
		return fmt.Errorf("범위: %w", err)
	}
	groups := groupsOf(members)
	spaces := make([]int64, 0, len(members))
	ents := 0
	for _, m := range members {
		spaces = append(spaces, m.Space)
		if m.Ent != nil {
			ents++
		}
	}
	slices.Sort(spaces)
	log.Printf("가공 범위: 스페이스 %d개 (엔터프라이즈 %d · 그 밖 %d)", len(spaces), ents, len(spaces)-ents)

	files := map[string]any{}
	var credits, jobs, usage map[int64]json.RawMessage
	steps := []struct {
		name string
		run  func() error
	}{
		{"summary", func() error {
			row, err := snap.runRow(summarySQL, spaces, at)
			files["summary.json"] = row
			return err
		}},
		{"evidence", func() error {
			row, err := snap.runRow(evidenceSQL, spaces, at)
			if err != nil {
				return err
			}
			files["evidence.json"], err = keepEvidence(row)
			return err
		}},
		{"sales", func() error {
			row, err := snap.RunSales(at)
			files["sales.json"] = row
			return err
		}},
		{"metrics", func() error {
			list := make([]*metricResult, 0, len(metrics))
			for i := range metrics {
				r, err := snap.RunMetric(&metrics[i], 0)
				if err != nil {
					return fmt.Errorf("%s: %w", metrics[i].Name, err)
				}
				list = append(list, r)
			}
			files["metrics.json"] = map[string]any{"metrics": list}
			return nil
		}},
		{"shared", func() error {
			row, err := snap.runRow(sharedHistorySQL, spaces, at)
			if err != nil {
				return err
			}
			var r struct{ Shared int64 }
			if err := json.Unmarshal(row, &r); err != nil {
				return err
			}
			if r.Shared > 0 {
				log.Printf("경고: 크레딧 기록 번호(history_seq) %d개가 둘 이상의 스페이스에 걸립니다 — 그 지급 묶음 "+
					"소진은 스페이스마다 한 번씩 실려, 그 스페이스들을 한 계약에서 합치면 두 번 셉니다", r.Shared)
			}
			return nil
		}},
		{"credits", func() (err error) { credits, err = snap.runFacts(creditsFactsSQL, "credits", spaces, at, ""); return }},
		{"jobs", func() (err error) { jobs, err = snap.runFacts(jobsFactsSQL, "jobs", spaces, at, ""); return }},
		{"usage", func() (err error) { usage, err = snap.runFacts(usageFactsSQL, "usage", spaces, at, newSalt()); return }},
	}
	for _, st := range steps {
		t := time.Now()
		if err := st.run(); err != nil {
			return fmt.Errorf("%s: %w", st.name, err)
		}
		log.Printf("  %-8s %5.1fs", st.name, time.Since(t).Seconds())
	}

	facts, err := assembleFacts(groups, credits, jobs, usage)
	if err != nil {
		return err
	}
	for path, v := range facts {
		files[path] = v
	}
	files["manifest.json"] = manifest{
		Format: dataFormat, SnapshotAt: at.UTC().Format(time.RFC3339), SnapshotCommit: commit,
		ExportedAt: time.Now().UTC().Format(time.RFC3339), Exporter: version,
		Spaces: len(spaces), Groups: len(facts) - 1, // index.json 을 뺀 묶음 파일 수
	}

	encoded, err := encode(files)
	if err != nil {
		return err
	}
	if err := publish(out, encoded); err != nil {
		return err
	}

	var total, factsTotal, biggest int
	for path, body := range encoded {
		total += len(body)
		if strings.HasPrefix(path, "facts/") {
			factsTotal += len(body)
			biggest = max(biggest, len(body))
		}
	}
	mb := func(n int) float64 { return float64(n) / (1 << 20) }
	log.Printf("묶음 %d개 · facts %.1fMB (가장 큰 파일 %.1fMB)", len(facts)-1, mb(factsTotal), mb(biggest))
	log.Printf("끝: 파일 %d개 · 합계 %.1fMB · %.0fs", len(encoded), mb(total), time.Since(t0).Seconds())
	return nil
}
