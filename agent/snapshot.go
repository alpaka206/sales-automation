package main

import (
	"archive/zip"
	"bytes"
	"context"
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
	"sync"
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
	Repo   string
	Data   string
	duckdb string
	// pull 이 한 시간마다 뒤에서도 도니(main.go) 이 셋은 잠그고 읽고 쓴다.
	mu       sync.Mutex
	pullNote string
	pulledAt string
	running  chan struct{} // 도는 pull 이 끝나면 닫힌다. 없으면 nil
	// 계산(읽기)과 새 데이터로 바꿔 끼우기(쓰기)가 겹치지 않게. 받기(fetch)는 작업 폴더를 안
	// 건드려서 잠그지 않는다 — 몇 분이 걸려도 그동안 계산은 옛 데이터로 돈다.
	data sync.RWMutex
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

// zip 으로 받은 폴더를 git 으로 바꿀 때 붙이는 원격. 비공개라 각자 PC 의 GitHub 로그인으로 받는다 —
// 바이너리에 토큰을 넣지 않는다(tests/test_agent_stays_local.py).
const snapshotRemote = "https://github.com/est-perso/perso-data-snapshot.git"

// 받기 상한. 예전에는 pull 전체가 2분이었고, 그러면 **며칠 밀린 PC 는 영영 못 받았다** — 2분에 끊기면
// git 은 받던 것을 버리고 다음 회차에 처음부터 다시 받는다. 뒤에서 돌므로 길게 둔다.
const fetchLimit = 30 * time.Minute

func (s *Snapshot) git(args ...string) (int, string) {
	return s.gitFor(2*time.Minute, args...)
}

func (s *Snapshot) gitFor(limit time.Duration, args ...string) (int, string) {
	ctx, cancel := context.WithTimeout(context.Background(), limit)
	defer cancel()
	// core.longpaths: 윈도우의 260자 경로 한도. 스냅샷을 깊은 폴더(OneDrive · 압축 두 겹)에 두면 받은 팩
	// 파일 이름이 넘는다(「Filename too long」, 실측). 다른 OS 에서는 무시되는 설정이다.
	cmd := exec.CommandContext(ctx, "git", append([]string{"-c", "core.longpaths=true", "-C", s.Repo}, args...)...)
	// 보이지 않는 터미널 프롬프트에서 멈추지 않게. 윈도우의 로그인 창(Git Credential Manager)은 이
	// 값과 무관하게 뜬다(GCM 은 이 값을 터미널 프롬프트에만 쓴다) — 그 창이 처음 한 번의 GitHub 로그인이다.
	cmd.Env = append(os.Environ(), "GIT_TERMINAL_PROMPT=0")
	out, err := cmd.CombinedOutput()
	text := strings.TrimSpace(string(out))
	if err == nil {
		return 0, text
	}
	if ctx.Err() != nil {
		return 124, text
	}
	var ee *exec.ExitError
	if errors.As(err, &ee) {
		return ee.ExitCode(), text
	}
	return 127, err.Error()
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

// PullWait — pull 을 시작하고(이미 돌고 있으면 그것을) 최대 `wait` 만큼 기다린다. 다 못 기다리면
// pull 은 뒤에서 계속되고, 화면의 `pull:` 이 「진행 중」이라 적는다.
func (s *Snapshot) PullWait(wait time.Duration) {
	s.mu.Lock()
	if s.running == nil {
		done := make(chan struct{})
		s.running = done
		go func() {
			s.pull()
			s.mu.Lock()
			s.running = nil
			s.mu.Unlock()
			close(done)
		}()
	}
	done := s.running
	s.mu.Unlock()
	select {
	case <-done:
	case <-time.After(wait):
	}
}

// pull 은 **실패해도 계속한다** — 이전 데이터로 서비스하고 사유를 남긴다.
//
// `git pull --ff-only` 를 안 쓴다. 이 폴더는 발행된 스냅샷의 **사본**이라 여기서 바뀐 것은 전부
// 사고다(엑셀로 연 CSV 를 저장, 끊긴 pull 이 반쯤 바꿔 둔 파일). pull 은 그런 폴더에서 영영
// 「로컬 변경이 덮어써집니다」로 멈췄다. 대신 받아서(fetch) 발행된 것과 **똑같이 맞춘다**(reset).
// 추적하지 않는 파일(사람이 옆에 둔 것)은 안 건드린다.
func (s *Snapshot) pull() {
	gitDir := filepath.Join(s.Repo, ".git")
	if _, err := os.Stat(gitDir); err != nil {
		if !strings.HasPrefix(strings.ToLower(filepath.Base(s.Repo)), "perso-data-snapshot") {
			s.setPull("git 저장소가 아닙니다 (시험용 폴더)", false)
			return
		}
		// GitHub 의 「Download ZIP」으로 받은 폴더 — 제자리에서 git 으로 바꾼다. 그래야 다음부터 받는다.
		if code, out := s.git("init", "-q"); code != 0 {
			s.setPull(pullFailure(code, out), true)
			return
		}
		if code, out := s.git("remote", "add", "origin", snapshotRemote); code != 0 {
			s.setPull(pullFailure(code, out), true)
			return
		}
		log.Printf("zip 으로 받은 스냅샷을 git 으로 바꿉니다: %s", s.Repo)
	}
	for _, lock := range clearStaleLocks(gitDir) {
		log.Printf("끊긴 git 작업이 남긴 잠금을 지웠습니다: %s", lock)
	}

	branch := "main"
	if code, out := s.git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"); code == 0 {
		if _, b, ok := strings.Cut(out, "/"); ok && b != "" {
			branch = b
		}
	}
	s.setPull("진행 중 — 받는 중입니다 (GitHub 로그인 창이 떠 있으면 로그인하세요)", false)
	// **--depth=1**: 필요한 것은 최신 한 벌뿐이다. 없으면 며칠 밀린 PC 가 그 사이의 모든 판본을 받는다.
	code, out := s.gitFor(fetchLimit, "fetch", "--depth=1", "--no-tags", "origin", branch)
	if code != 0 {
		s.setPull(pullFailure(code, out), true)
		return
	}
	_, head := s.git("rev-parse", "HEAD")
	_, target := s.git("rev-parse", "FETCH_HEAD")
	if head == target {
		// 같은 판본이어도 파일이 어긋났으면(엑셀 저장 · 지난번에 다 못 바꾼 파일) 맞춘다.
		if code, dirty := s.git("status", "--porcelain", "--untracked-files=no"); code == 0 && dirty == "" {
			s.setPull("성공", true)
			return
		}
	}
	s.data.Lock()
	code, out = s.gitFor(5*time.Minute, "reset", "--hard", "-q", "FETCH_HEAD")
	s.data.Unlock()
	if code != 0 {
		s.setPull(pullFailure(code, out), true)
		return
	}
	// **성공에는 사유를 안 붙인다.** 「어느 데이터를 보고 있나」는 옆의 커밋 sha 와 기준 시각이 말한다.
	s.setPull("성공", true)
}

// clearStaleLocks — 끊긴 git 작업(창을 닫음 · 절전 · 상한)이 남긴 `*.lock`. 남아 있으면 그 뒤의 모든
// pull 이 「index.lock: File exists」로 실패한다(재현됨). 10분 넘은 것만 지운다 — 살아 있는 git 작업은
// 잠금을 몇 초만 쥔다.
func clearStaleLocks(gitDir string) []string {
	var removed []string
	cutoff := time.Now().Add(-10 * time.Minute)
	_ = filepath.WalkDir(gitDir, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if d.IsDir() {
			if d.Name() == "objects" {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(d.Name(), ".lock") {
			return nil
		}
		if info, err := d.Info(); err == nil && info.ModTime().Before(cutoff) && os.Remove(p) == nil {
			removed = append(removed, p)
		}
		return nil
	})
	return removed
}

// pullFailure — git 의 실패 문장을 **할 일**로 바꾼다. 원문 첫 줄은 괄호로 남긴다(무엇이었는지 짚을 수 있게).
func pullFailure(code int, out string) string {
	first := strings.TrimSpace(strings.SplitN(out, "\n", 2)[0])
	if r := []rune(first); len(r) > 160 {
		first = string(r[:160]) + "…"
	}
	low := strings.ToLower(out)
	has := func(needles ...string) bool {
		for _, n := range needles {
			if strings.Contains(low, n) {
				return true
			}
		}
		return false
	}
	var todo string
	switch {
	case code == 127:
		todo = "git 이 없습니다 — Windows 는 git-scm.com 에서 Git 을 설치하고, Mac 은 터미널에서 xcode-select --install"
	case code == 124:
		todo = "시간 안에 다 받지 못했습니다 — 네트워크·VPN 을 확인하세요. 다음 회차에 다시 받습니다"
	case has("could not read username", "authentication failed", "terminal prompts disabled",
		"repository not found", "saml", "error: 403", "invalid username or password",
		"permission denied (publickey)", "logon failed"):
		todo = "GitHub 로그인이 필요합니다 — 이 폴더에서 터미널로 git pull 을 한 번 하세요(로그인 창이 뜨면 로그인). " +
			"est-perso 조직이 SSO 를 쓰면 토큰의 SSO 승인도 필요합니다"
	case has("could not resolve host", "failed to connect", "timed out", "connection was reset",
		"connection reset", "ssl", "schannel", "proxy"):
		todo = "GitHub 에 닿지 못했습니다 — 네트워크·VPN·프록시를 확인하세요"
	case has("unable to unlink", "permission denied", "invalid argument", "being used by another process"):
		todo = "일부 파일을 못 바꿨습니다 — 엑셀 등으로 연 CSV 를 닫으면 다음 받기에서 마저 바뀝니다"
	case has(".lock", "file exists"):
		todo = "다른 git 작업이 이 폴더를 쓰고 있습니다 — 다음 받기에서 이어집니다"
	default:
		todo = fmt.Sprintf("실패(코드 %d)", code)
	}
	if first == "" {
		return todo
	}
	return todo + " (" + first + ")"
}

func (s *Snapshot) setPull(note string, stamp bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.pullNote = note
	if stamp {
		s.pulledAt = utcNow()
	}
}

func (s *Snapshot) pullState() (string, string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.pullNote, s.pulledAt
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
	note, pulled := s.pullState()
	return asOf{Commit: s.Commit(), CommittedAt: committed, PulledAt: pulled,
		Pull: note, Stale: stale}
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
	// 새 데이터로 바꿔 끼우는 동안에는 기다린다 — 반쯤 바뀐 CSV 를 읽지 않게.
	s.data.RLock()
	defer s.data.RUnlock()
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
