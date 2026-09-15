package main

import (
	"archive/zip"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
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
	maxRows      = 2000 // 집계가 이보다 많으면 그건 집계가 아니다
	maxCellChars = 120  // 한 셀에 원문 수천 자를 담는 길을 막는다
	minGroup     = 5    // 관측치가 이보다 적은 그룹은 뺀다
)

var idLike = regexp.MustCompile(`(?i)(^|_)(seq|id|uuid|email|token|key|url|path|name_raw)$`)

func utcNow() string { return time.Now().UTC().Format(time.RFC3339) }

// Snapshot 하나 = 「검증된 정상 버전」. 계산 중에 파일이 바뀌면 안 된다.
//
// 48개 CSV 를 갱신하는 도중 계산하면 **어제 사용자 테이블과 오늘 결제 테이블이 섞인
// 숫자**가 나온다. 오류 없이 틀린 값을 보여 주는 것이 갱신 실패보다 나쁘다. 그래서 계산
// 전후로 커밋 sha 를 비교하고, 달라졌으면 그 결과를 버린다.
type Snapshot struct {
	Repo     string
	Data     string
	duckdb   string
	pullNote string
	pulledAt string
}

func NewSnapshot(repo string) (*Snapshot, error) {
	abs, err := filepath.Abs(repo)
	if err != nil {
		return nil, err
	}
	data := filepath.Join(abs, "data")
	if _, err := os.Stat(filepath.Join(data, "manifest.json")); err != nil {
		return nil, fmt.Errorf("스냅샷 폴더를 못 찾았습니다: %s\n  `data/manifest.json` 이 있는 폴더를 --repo 로 주세요", data)
	}
	duck, err := ensureDuckDB()
	if err != nil {
		return nil, err
	}
	return &Snapshot{Repo: abs, Data: data, duckdb: duck, pullNote: "안 함"}, nil
}

// -- git ------------------------------------------------------------
func (s *Snapshot) git(args ...string) (int, string) {
	cmd := exec.Command("git", append([]string{"-C", s.Repo}, args...)...)
	out, err := cmd.CombinedOutput()
	text := strings.TrimSpace(string(out))
	if err == nil {
		return 0, text
	}
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		return ee.ExitCode(), text
	}
	return 127, "git 을 실행할 수 없습니다"
}

func (s *Snapshot) Commit() string {
	if code, out := s.git("rev-parse", "--short", "HEAD"); code == 0 && out != "" {
		return strings.Fields(out)[0]
	}
	return "(git 아님)"
}

func (s *Snapshot) CommittedAt() string {
	if code, out := s.git("log", "-1", "--format=%cI"); code == 0 && out != "" {
		return out
	}
	return ""
}

// Pull 은 **실패해도 계속한다** — 이전 데이터로 서비스하고 사유를 남긴다.
func (s *Snapshot) Pull() {
	if _, err := os.Stat(filepath.Join(s.Repo, ".git")); err != nil {
		s.pullNote = "git 저장소가 아닙니다 (시험용 폴더)"
		return
	}
	code, out := s.git("pull", "--ff-only")
	s.pulledAt = utcNow()
	first := strings.TrimSpace(strings.SplitN(out, "\n", 2)[0])
	if code == 0 {
		// **성공에는 사유를 안 붙인다.** git 의 첫 줄은 `From <원격 주소>` 라 화면 한
		// 줄을 통째로 먹는데, 「어느 데이터를 보고 있나」는 옆의 커밋 sha 와 기준 시각이
		// 이미 정확하게 말한다. 실패는 반대다 — 그 문장이 곧 할 일이라 그대로 싣는다.
		s.pullNote = "성공"
		return
	}
	// 사유를 그대로 보여 준다. 「실패했습니다」만 적으면 할 수 있는 일이 없다.
	if first == "" {
		first = "사유 없음"
	}
	s.pullNote = fmt.Sprintf("실패(코드 %d) — %s", code, first)
}

type asOf struct {
	Commit      string `json:"commit"`
	CommittedAt string `json:"committed_at"`
	PulledAt    string `json:"pulled_at"`
	Pull        string `json:"pull"`
	Stale       bool   `json:"stale"`
}

func (s *Snapshot) AsOf() asOf {
	committed := s.CommittedAt()
	// 낡았는지는 **데이터의 시각**(manifest 의 generated_at)으로 잰다. 커밋 시각은 그 근사치이고
	// git 저장소가 아닌 폴더에는 아예 없다 — 그때 「낡음」으로 떨어지면 시험 폴더가 늘 빨갛다.
	stale := time.Since(s.snapshotAt()) > 36*time.Hour
	return asOf{Commit: s.Commit(), CommittedAt: committed, PulledAt: s.pulledAt,
		Pull: s.pullNote, Stale: stale}
}

func (s *Snapshot) FolderMB() int64 {
	var total int64
	_ = filepath.WalkDir(s.Repo, func(_ string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return nil
		}
		if info, err := d.Info(); err == nil {
			total += info.Size()
		}
		return nil
	})
	return total / (1024 * 1024)
}

// -- 계산 -----------------------------------------------------------
type metricResult struct {
	Metric     string         `json:"metric"`
	Label      string         `json:"label"`
	Params     map[string]int `json:"params"`
	AsOf       asOf           `json:"as_of"`
	Columns    []string       `json:"columns"`
	Rows       [][]any        `json:"rows"`
	Suppressed int            `json:"suppressed_groups"`
	ComputedMS int64          `json:"computed_ms"`
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

	before := s.Commit()
	t0 := time.Now()

	sql := strings.ReplaceAll(m.SQL, "{{d}}", filepath.ToSlash(s.Data))
	sql = strings.ReplaceAll(sql, "{{months}}", fmt.Sprint(months))
	raw, err := s.query(sql)
	if err != nil {
		return nil, err
	}

	if after := s.Commit(); before != after {
		return nil, errors.New("계산 중에 스냅샷이 바뀌었습니다 — 결과를 버립니다. 다시 시도하세요")
	}

	rows, suppressed, err := applyContract(m.Cols, raw)
	if err != nil {
		return nil, err
	}
	params := map[string]int{}
	if m.HasMonth {
		params["months"] = months
	}
	return &metricResult{Metric: m.Name, Label: m.Label, Params: params, AsOf: s.AsOf(),
		Columns: m.Cols, Rows: rows, Suppressed: suppressed,
		ComputedMS: time.Since(t0).Milliseconds()}, nil
}

// query 는 DuckDB CLI 를 **별개 프로세스**로 부른다. CGO 를 안 쓰므로 이 바이너리가
// 윈도우·맥으로 그대로 크로스 컴파일된다.
func (s *Snapshot) query(sql string) ([]map[string]json.RawMessage, error) {
	// `~/.duckdbrc` 를 상속하지 않고(-init os.DevNull), 확장 자동 설치·로딩을 끈다 —
	// DuckDB 는 파일·네트워크 접근과 확장 로딩 능력이 있다.
	script := "SET autoinstall_known_extensions=false;\nSET autoload_known_extensions=false;\n" + sql + ";\n"
	cmd := exec.Command(s.duckdb, "-json", "-init", os.DevNull, "-batch")
	cmd.Stdin = strings.NewReader(script)
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Run(); err != nil {
		// **DuckDB 의 오류 문구를 화면으로 보내지 않는다.** CSV 파싱 오류는 문제가 된
		// 행을 그대로 싣는다 — 그것이 곧 원본이다. 자세한 것은 이 PC 의 로그에만 남는다.
		log.Printf("duckdb 실패: %v — %s", err, strings.TrimSpace(stderr.String()))
		return nil, errors.New("계산에 실패했습니다 (자세한 사유는 에이전트 로그에 있습니다)")
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

// applyContract — **나갈 수 있는 모양인지 기계가 잰다.**
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

// ── DuckDB CLI — 함께 실려 오고, 첫 실행에 풀린다 ────────────────────────
// **받아오지 않는다.** 이 회사 망은 HTTPS 를 재서명하는 어플라이언스를 지나므로 첫 실행
// 다운로드는 이미 한 번 깨졌다(pip 가 그 자리에서 죽었다). 그래서 zip 째로 안에 넣는다.
const duckdbVersion = "1.4.1"

func ensureDuckDB() (string, error) {
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
