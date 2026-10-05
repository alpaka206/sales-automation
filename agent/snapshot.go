package main

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

// ── 출력 계약 ────────────────────────────────────────────────────────────
// 「금지 컬럼을 찾아 지운다」가 아니라 **나갈 수 있는 모양을 정한다**. 블랙리스트는
// 이름을 바꾼 식별자·제목·파일명·오류 메시지 속 데이터를 놓친다.
const (
	maxRows = 2000 // 지표(metrics.go) 한 장의 행 상한 — 이보다 많으면 그건 집계가 아니다
	// 가공 파일 안 배열 하나의 상한. 예전 2,000 은 HTTP 응답 하나의 상한이었고, 이제 파일 하나가 스페이스
	// 수백 개의 작업 기록·시작/끝 사건을 통째로 싣는다. 이 울타리가 막는 것은 「표 하나를 그대로 붓는」 사고다.
	maxRowsExport = 200000
	maxCellChars  = 120 // 한 셀에 원문 수천 자를 담는 길을 막는다
	minGroup      = 5   // 관측치가 이보다 적은 그룹은 뺀다
)

var idLike = regexp.MustCompile(`(?i)(^|_)(seq|id|uuid|email|token|key|url|path|name_raw)$`)

// Snapshot 하나 = 가공할 스냅샷 한 벌. 받아 오는 일(pull)은 이제 Actions 의 checkout 이 하고, 이
// 프로그램은 네트워크 없이(unshare -n) 읽기만 한다.
type Snapshot struct {
	Repo   string
	Data   string
	duckdb string
}

func NewSnapshot(repo, duck string) (*Snapshot, error) {
	abs, err := filepath.Abs(repo)
	if err != nil {
		return nil, err
	}
	data := filepath.Join(abs, "data")
	if _, err := os.Stat(filepath.Join(data, "manifest.json")); err != nil {
		return nil, fmt.Errorf("스냅샷 폴더를 못 찾았습니다: %s\n  `data/manifest.json` 이 있는 폴더를 --snapshot 으로 주세요", data)
	}
	// 경로가 SQL 문자열 안에 그대로 들어간다(read_csv_auto('…')). 따옴표가 있으면 SQL 이 깨진다.
	if strings.ContainsRune(data, '\'') {
		return nil, errors.New("스냅샷 경로에 작은따옴표가 있습니다 — 다른 폴더로 옮겨 주세요")
	}
	if duck == "" {
		if duck, err = ensureDuckDB(); err != nil {
			return nil, err
		}
	} else if duck, err = filepath.Abs(duck); err != nil {
		return nil, err
	}
	return &Snapshot{Repo: abs, Data: data, duckdb: duck}, nil
}

// -- git (읽기만) ----------------------------------------------------

func (s *Snapshot) git(args ...string) (string, error) {
	// zip 으로 받은 폴더는 git 이 아니다. 그때 `-C` 는 **위 폴더의 저장소**를 찾아가 엉뚱한 커밋을 댄다.
	if _, err := os.Stat(filepath.Join(s.Repo, ".git")); err != nil {
		return "", err
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	out, err := exec.CommandContext(ctx, "git", append([]string{"-C", s.Repo}, args...)...).Output()
	return strings.TrimSpace(string(out)), err
}

func (s *Snapshot) Commit() string {
	if out, err := s.git("rev-parse", "--short", "HEAD"); err == nil && out != "" {
		return strings.Fields(out)[0]
	}
	return "(git 아님)"
}

func (s *Snapshot) CommittedAt() string {
	out, _ := s.git("log", "-1", "--format=%cI")
	return out
}

// snapshotAt — manifest 의 generated_at. 없으면 커밋 시각, 그것도 없으면 지금.
//
// 「지금」은 벽시계가 아니라 이 값이다. 스냅샷이 사흘 묵었으면 「최근 30일」도 그 시각 기준으로
// 밀린다 — 안 그러면 스냅샷이 며칠 안 바뀐 날 「30일 무활동」이 저절로 생긴다.
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

// -- 계산 -----------------------------------------------------------
type metricResult struct {
	Metric     string   `json:"metric"`
	Label      string   `json:"label"`
	Columns    []string `json:"columns"`
	Rows       [][]any  `json:"rows"`
	Suppressed int      `json:"suppressed_groups"`
}

func (s *Snapshot) RunMetric(m *metric, months int) (*metricResult, error) {
	if months < 1 || months > 60 {
		months = 12
	}
	// 나갈 컬럼이 식별자성인지 **기계가 잰다.** 사람이 지키는 규칙으로 두지 않는다.
	for _, c := range m.Cols {
		if idLike.MatchString(c) {
			return nil, fmt.Errorf("식별자성 컬럼이 결과에 있습니다: %s", c)
		}
	}
	sql := strings.ReplaceAll(m.SQL, "{{d}}", filepath.ToSlash(s.Data))
	sql = strings.ReplaceAll(sql, "{{months}}", fmt.Sprint(months))
	raw, err := s.query(sql)
	if err != nil {
		return nil, err
	}
	rows, suppressed, err := applyContract(m.Cols, raw)
	if err != nil {
		return nil, err
	}
	return &metricResult{Metric: m.Name, Label: m.Label, Columns: m.Cols, Rows: rows, Suppressed: suppressed}, nil
}

// quoted — DuckDB 오류 문구 속 따옴표 안. CSV 파싱·형변환 오류는 문제가 된 값을 그대로 싣는다 — 그것이 곧
// 원본이고, 가공 로그는 Actions 화면에 남는다.
var quoted = regexp.MustCompile(`'[^']*'|"[^"]*"`)

// query 는 DuckDB CLI 를 **별개 프로세스**로 부른다. CGO 를 안 쓰므로 이 바이너리가
// 리눅스·윈도우·맥으로 그대로 크로스 컴파일된다.
func (s *Snapshot) query(sql string) ([]map[string]json.RawMessage, error) {
	// `~/.duckdbrc` 를 상속하지 않고(-init os.DevNull), 확장 자동 설치·로딩을 끈다 —
	// DuckDB 는 파일·네트워크 접근과 확장 로딩 능력이 있다. 진행 막대는 표준 출력(JSON)에 섞이지 않게 끈다.
	script := "SET autoinstall_known_extensions=false;\nSET autoload_known_extensions=false;\n" +
		"SET enable_progress_bar=false;\n" + sql + ";\n"
	cmd := exec.Command(s.duckdb, "-json", "-init", os.DevNull, "-batch")
	cmd.Stdin = strings.NewReader(script)
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Run(); err != nil {
		// **오류 문구를 통째로 남기지 않는다** — 첫 줄만, 따옴표 안은 지우고. 무엇이 틀렸는지 자세히 보려면
		// 스냅샷이 있는 PC 에서 같은 SQL 을 DuckDB 로 돌린다.
		first, _, _ := strings.Cut(strings.TrimSpace(stderr.String()), "\n")
		first = quoted.ReplaceAllString(first, "'…'")
		if r := []rune(first); len(r) > 200 {
			first = string(r[:200]) + "…"
		}
		log.Printf("duckdb 실패: %v — %s", err, first)
		return nil, errors.New("계산에 실패했습니다 (사유는 바로 위 로그 한 줄)")
	}
	body := bytes.TrimSpace(stdout.Bytes())
	if len(body) == 0 {
		return nil, nil
	}
	var out []map[string]json.RawMessage
	if err := json.Unmarshal(body, &out); err != nil {
		log.Printf("duckdb 출력을 읽지 못했습니다: %v", err)
		return nil, errors.New("계산 결과를 읽지 못했습니다")
	}
	return out, nil
}

// applyContract — **나갈 수 있는 모양인지 기계가 잰다.** (지표 한 장)
func applyContract(cols []string, raw []map[string]json.RawMessage) ([][]any, int, error) {
	if len(raw) > maxRows {
		return nil, 0, fmt.Errorf("집계가 %d행입니다 (상한 %d) — 집계가 아닙니다", len(raw), maxRows)
	}
	if len(raw) == 0 {
		return [][]any{}, 0, nil
	}
	// 적어 둔 컬럼과 실제로 나온 컬럼이 같은지 본다. SQL 만 고치고 `Cols` 를 안 고치면
	// 여기서 걸린다 — 안 그러면 안 적힌 컬럼이 조용히 나간다.
	if len(raw[0]) != len(cols) {
		return nil, 0, fmt.Errorf("결과 컬럼 수가 선언과 다릅니다 (%d ≠ %d)", len(raw[0]), len(cols))
	}
	for _, c := range cols {
		if _, ok := raw[0][c]; !ok {
			return nil, 0, fmt.Errorf("선언한 컬럼이 결과에 없습니다: %s", c)
		}
	}

	// 마지막 숫자 컬럼을 관측치로 보고 최소 묶음 크기를 적용한다.
	countIdx := -1
	for i := len(cols) - 1; i >= 0; i-- {
		allNum := true
		for _, r := range raw {
			if !isJSONNumberOrNull(r[cols[i]]) {
				allNum = false
				break
			}
		}
		if allNum {
			countIdx = i
			break
		}
	}

	out := make([][]any, 0, len(raw))
	suppressed := 0
	for _, r := range raw {
		if countIdx >= 0 {
			if n, ok := jsonNumber(r[cols[countIdx]]); ok && n < minGroup {
				suppressed++
				continue
			}
		}
		cells := make([]any, len(cols))
		for i, c := range cols {
			v, err := cell(r[c])
			if err != nil {
				return nil, 0, err
			}
			cells[i] = v
		}
		out = append(out, cells)
	}
	return out, suppressed, nil
}

func isJSONNumberOrNull(v json.RawMessage) bool {
	t := bytes.TrimSpace(v)
	if len(t) == 0 || bytes.Equal(t, []byte("null")) {
		return true
	}
	c := t[0]
	return c == '-' || (c >= '0' && c <= '9')
}

func jsonNumber(v json.RawMessage) (float64, bool) {
	var n json.Number
	if err := json.Unmarshal(v, &n); err != nil {
		return 0, false
	}
	f, err := n.Float64()
	return f, err == nil
}

func cell(v json.RawMessage) (any, error) {
	var decoded any
	d := json.NewDecoder(bytes.NewReader(v))
	d.UseNumber()
	if err := d.Decode(&decoded); err != nil {
		return nil, errors.New("결과 값을 읽지 못했습니다")
	}
	if s, ok := decoded.(string); ok && len(s) > maxCellChars {
		return nil, fmt.Errorf("셀이 %d자입니다 (상한 %d) — 원문일 수 있습니다", len(s), maxCellChars)
	}
	return decoded, nil
}

// checkContract — 나갈 파일 하나를 통째로 걸으며 계약을 잰다. `allow` 에 있는 키만 식별자성
// 이름을 쓸 수 있다(화면이 계약의 스페이스를 찾는 space_seq).
func checkContract(data []byte, allow map[string]bool) error {
	var v any
	d := json.NewDecoder(bytes.NewReader(data))
	d.UseNumber()
	if err := d.Decode(&v); err != nil {
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
		if len(x) > maxRowsExport {
			return fmt.Errorf("배열이 %d줄입니다 (상한 %d) — 집계가 아닙니다", len(x), maxRowsExport)
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

// ── DuckDB CLI ───────────────────────────────────────────────────────────
// 윈도우·맥 빌드는 zip 째로 싣고 첫 실행에 푼다(손으로 돌려 볼 때). 리눅스(가공 워크플로)는 싣지 않는다 —
// 워크플로가 이 버전을 받아 체크섬(duckdb-linux-amd64.sha256)을 맞춘 뒤 --duckdb 로 넘긴다.
const duckdbVersion = "1.4.1"

func ensureDuckDB() (string, error) {
	if len(duckdbZip) == 0 {
		if p, err := exec.LookPath("duckdb"); err == nil {
			return p, nil
		}
		return "", errors.New("DuckDB CLI 를 못 찾았습니다 — --duckdb 로 경로를 주거나 PATH 에 duckdb 를 두세요 (버전 " + duckdbVersion + ")")
	}
	cache, err := os.UserCacheDir()
	if err != nil {
		cache = os.TempDir()
	}
	dir := filepath.Join(cache, "perso-agent", "duckdb-"+duckdbVersion)
	exe := filepath.Join(dir, duckdbExeName)
	if st, err := os.Stat(exe); err == nil && st.Size() > 0 {
		return exe, nil
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", fmt.Errorf("DuckDB 를 풀 폴더를 못 만들었습니다: %w", err)
	}
	zr, err := zip.NewReader(bytes.NewReader(duckdbZip), int64(len(duckdbZip)))
	if err != nil {
		return "", fmt.Errorf("함께 실린 DuckDB 를 읽지 못했습니다: %w", err)
	}
	for _, f := range zr.File {
		if f.FileInfo().IsDir() || filepath.Base(f.Name) != duckdbExeName {
			continue
		}
		// 임시 이름으로 쓰고 rename 한다 — 중간에 죽으면 반쪽짜리가 남아 다음 실행이
		// 그것을 「이미 있다」로 읽는다.
		tmp := exe + ".part"
		src, err := f.Open()
		if err != nil {
			return "", err
		}
		dst, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o700)
		if err != nil {
			src.Close()
			return "", err
		}
		_, cerr := io.Copy(dst, src)
		src.Close()
		if err := dst.Close(); err != nil && cerr == nil {
			cerr = err
		}
		if cerr != nil {
			os.Remove(tmp)
			return "", fmt.Errorf("DuckDB 를 풀지 못했습니다: %w", cerr)
		}
		if err := os.Rename(tmp, exe); err != nil {
			return "", err
		}
		log.Printf("DuckDB %s 를 풀었습니다: %s", duckdbVersion, exe)
		return exe, nil
	}
	return "", fmt.Errorf("함께 실린 zip 에 %s 가 없습니다", duckdbExeName)
}
