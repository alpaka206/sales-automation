// PERSO 데이터 에이전트.
//
// **원본은 이 PC 를 안 떠난다.** 스냅샷 CSV 를 DuckDB 로 그 자리에서 읽어 **집계만**
// 내보낸다. 계산도 원본도 이 프로세스 안에서 끝나고, 화면으로 나가는 것은 상한을 지난
// 집계표뿐이다.
//
// 돌리는 법: 스냅샷 clone 폴더 **옆에** 두고 실행한다. 폴더 이름이 다르면 --repo 로 준다.
package main

import (
	"crypto/rand"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

// 빌드가 박는다: `go build -ldflags "-X main.version=1.2.3"`. 릴리스 워크플로가 태그
// `agent-v1.2.3` 에서 떼어 넣고, 손으로 빌드하면 "dev" 다. 콘솔이 `/v1/status` 로 읽어 자기가
// 요구하는 최소 버전(frontend/src/lib/agent.ts 의 AGENT_MIN_VERSION)과 비교한다 — SQL 이 내는
// 키가 바뀌면 콘솔은 그 상수를 올리고, 낡은 에이전트를 만난 화면은 「업데이트 필요」를 띄운다.
var version = "dev"

// 포트를 하나로 박지 않는다. 다른 프로그램이 쓰고 있으면 그쪽으로 요청이 간다.
var portCandidates = []int{43110, 43111, 43112, 43113, 43114, 43115, 43116, 43117, 43118, 43119}

// 이 출처에서 온 요청만 받는다. `*` 는 절대 쓰지 않는다 — 아무 사이트나 로컬 데이터를 읽는다.
var defaultOrigins = []string{
	"https://sales-automation-4if2.onrender.com",
	"http://127.0.0.1:8010", // 로컬에서 콘솔을 띄워 시험할 때
}

type agent struct {
	snap    *Snapshot
	token   string
	port    int
	origins []string
}

// ── 방어 ────────────────────────────────────────────────────────────────

// hostOK — DNS rebinding 방어. 공격자 도메인이 루프백으로 해석돼도 Host 가 다르다.
func (a *agent) hostOK(r *http.Request) bool {
	host := strings.ToLower(strings.TrimSpace(r.Host))
	p := strconv.Itoa(a.port)
	return host == "127.0.0.1:"+p || host == "localhost:"+p || host == "[::1]:"+p
}

// originOK — 허용 출처면 그 값을, 출처가 없으면 빈 문자열을, 아니면 false.
func (a *agent) originOK(r *http.Request) (string, bool) {
	origin := strings.TrimSpace(r.Header.Get("Origin"))
	if origin == "" {
		return "", true // 같은 출처(자기 화면)나 브라우저 아닌 호출
	}
	if origin == fmt.Sprintf("http://127.0.0.1:%d", a.port) {
		return origin, true
	}
	for _, allowed := range a.origins {
		if origin == allowed {
			return origin, true
		}
	}
	return "", false
}

func (a *agent) authed(r *http.Request) bool {
	got := strings.TrimSpace(strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer "))
	return subtle.ConstantTimeCompare([]byte(got), []byte(a.token)) == 1
}

func send(w http.ResponseWriter, code int, payload any, origin string) {
	body, err := json.Marshal(payload)
	if err != nil {
		code, body = 500, []byte(`{"error":"응답을 만들지 못했습니다"}`)
	}
	h := w.Header()
	h.Set("Content-Type", "application/json; charset=utf-8")
	h.Set("Cache-Control", "no-store")
	h.Set("X-Content-Type-Options", "nosniff")
	if origin != "" {
		h.Set("Access-Control-Allow-Origin", origin) // `*` 아님
		h.Set("Vary", "Origin")
		h.Set("Access-Control-Allow-Headers", "Authorization")
		// 쿠키를 안 쓴다 → Allow-Credentials 없음 → CSRF 표면이 사라진다
	}
	w.WriteHeader(code)
	_, _ = w.Write(body)
}

type errBody struct {
	Error  string `json:"error"`
	Metric string `json:"metric,omitempty"`
}

// gate — Host·Origin 을 먼저 본다. 둘 중 하나라도 아니면 아무것도 답하지 않는다.
func (a *agent) gate(w http.ResponseWriter, r *http.Request) (string, bool) {
	if !a.hostOK(r) {
		send(w, http.StatusForbidden, errBody{Error: "Host 가 로컬이 아닙니다"}, "")
		return "", false
	}
	origin, ok := a.originOK(r)
	if !ok {
		send(w, http.StatusForbidden, errBody{Error: "허용되지 않은 출처입니다"}, "")
		return "", false
	}
	return origin, true
}

// ── 라우트 ──────────────────────────────────────────────────────────────

func (a *agent) routes() *http.ServeMux {
	mux := http.NewServeMux()

	// 자기 화면 — **저장소 폴더를 웹 루트로 두지 않는다.** 이 한 페이지만 준다.
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		origin, ok := a.gate(w, r)
		if !ok {
			return
		}
		if r.URL.Path != "/" && r.URL.Path != "/index.html" {
			send(w, http.StatusNotFound, errBody{Error: "없는 경로입니다"}, origin)
			return
		}
		a.page(w)
	})

	// 살아 있나. 토큰 없이 답하되 **아무 데이터도 안 준다** (콘솔이 켜짐을 알아야 한다).
	mux.HandleFunc("/v1/health", func(w http.ResponseWriter, r *http.Request) {
		origin, ok := a.gate(w, r)
		if !ok {
			return
		}
		send(w, http.StatusOK, map[string]any{"ok": true, "agent": "perso-agent/" + version, "version": version, "port": a.port}, origin)
	})

	mux.HandleFunc("/v1/status", a.guarded(func(w http.ResponseWriter, _ *http.Request, origin string) {
		send(w, http.StatusOK, map[string]any{
			"as_of": a.snap.AsOf(), "repo": a.snap.Repo,
			"snapshot_at": a.snap.snapshotAt().UTC().Format(time.RFC3339),
			"folder_mb":   a.snap.FolderMB(), "metrics": metricNames(),
			"space_metrics": []string{"summary", "credits", "jobs", "usage", "evidence"},
			"version":       version,
		}, origin)
	}))

	mux.HandleFunc("/v1/metrics", a.guarded(func(w http.ResponseWriter, _ *http.Request, origin string) {
		list := make([]map[string]any, 0, len(metrics))
		for _, m := range metrics {
			params := map[string]string{}
			if m.HasMonth {
				params["months"] = "최근 몇 달 (기본 12)"
			}
			list = append(list, map[string]any{"name": m.Name, "label": m.Label, "params": params})
		}
		send(w, http.StatusOK, map[string]any{"metrics": list}, origin)
	}))

	mux.HandleFunc("/v1/metrics/", a.guarded(func(w http.ResponseWriter, r *http.Request, origin string) {
		name := strings.TrimPrefix(r.URL.Path, "/v1/metrics/")
		m := metricByName(name)
		if m == nil {
			send(w, http.StatusNotFound, errBody{Error: "모르는 지표입니다: " + name}, origin)
			return
		}
		months, _ := strconv.Atoi(r.URL.Query().Get("months"))
		result, err := a.snap.RunMetric(m, months)
		if err != nil {
			// **오류에 원문을 붙이지 않는다** — 계약 위반 사유만 적는다.
			send(w, http.StatusInternalServerError, errBody{Error: err.Error(), Metric: name}, origin)
			return
		}
		send(w, http.StatusOK, result, origin)
	}))

	// 수주 고객 화면용 — 스페이스 목록을 받아 그 안에서만 집계한다 (spaces.go).
	for name := range spaceMetrics {
		mux.HandleFunc("/v1/spaces/"+name, a.guarded(a.spacesHandler(name)))
	}
	// 영업 인사이트 — 스페이스 목록 없이 전체를 본다 (sales.go). 눈에 띄는 스페이스와 제품 전체 흐름.
	mux.HandleFunc("/v1/sales", a.guarded(a.salesHandler))

	// 상태를 바꾸는 것은 무인증 GET 으로 열지 않는다.
	mux.HandleFunc("/v1/refresh", a.guarded(func(w http.ResponseWriter, r *http.Request, origin string) {
		if r.Method != http.MethodPost {
			send(w, http.StatusMethodNotAllowed, errBody{Error: "POST 로 부르세요"}, origin)
			return
		}
		a.snap.Pull()
		send(w, http.StatusOK, map[string]any{"as_of": a.snap.AsOf()}, origin)
	}))

	return mux
}

// guarded — Host·Origin·프리플라이트·토큰을 한자리에서 지난다. 라우트마다 적으면
// 다음에 붙는 라우트가 그 앞을 안 지난다.
func (a *agent) guarded(h func(http.ResponseWriter, *http.Request, string)) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		origin, ok := a.gate(w, r)
		if !ok {
			return
		}
		if r.Method == http.MethodOptions {
			if origin != "" {
				hh := w.Header()
				hh.Set("Access-Control-Allow-Origin", origin)
				hh.Set("Access-Control-Allow-Headers", "Authorization")
				hh.Set("Access-Control-Max-Age", "600")
				hh.Set("Vary", "Origin")
			}
			w.WriteHeader(http.StatusNoContent)
			return
		}
		if !a.authed(r) {
			send(w, http.StatusUnauthorized, errBody{Error: "토큰이 필요합니다"}, origin)
			return
		}
		h(w, r, origin)
	}
}

func (a *agent) page(w http.ResponseWriter) {
	html := strings.ReplaceAll(pageHTML, "__TOKEN__", a.token)
	html = strings.ReplaceAll(html, "__PORT__", strconv.Itoa(a.port))
	h := w.Header()
	h.Set("Content-Type", "text/html; charset=utf-8")
	// 자체 번들만 쓴다 — 외부 SDK·CDN 을 불러오면 전송 경로가 다시 생긴다.
	h.Set("Content-Security-Policy",
		"default-src 'none'; connect-src 'self'; style-src 'unsafe-inline'; "+
			"script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'")
	h.Set("Cache-Control", "no-store")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(html))
}

// ── 실행 ────────────────────────────────────────────────────────────────

// listen — 빈 포트를 찾아 **그 리스너를 그대로 쓴다.** 시험 삼아 열고 닫은 뒤 다시 열면
// 그 사이에 다른 프로그램이 가져갈 수 있다.
func listen() (net.Listener, int, error) {
	for _, p := range portCandidates {
		// **127.0.0.1 에만** 바인딩한다. 0.0.0.0 이면 같은 와이파이의 누구나 본다.
		ln, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", p))
		if err == nil {
			return ln, p, nil
		}
	}
	return nil, 0, fmt.Errorf("쓸 수 있는 포트가 없습니다 (%d~%d)",
		portCandidates[0], portCandidates[len(portCandidates)-1])
}

func newToken() string {
	b := make([]byte, 24)
	if _, err := rand.Read(b); err != nil {
		log.Fatalf("토큰을 만들지 못했습니다: %v", err)
	}
	return base64.RawURLEncoding.EncodeToString(b)
}

func openBrowser(url string) {
	var cmd *exec.Cmd
	switch runtime.GOOS {
	case "windows":
		cmd = exec.Command("rundll32", "url.dll,FileProtocolHandler", url)
	case "darwin":
		cmd = exec.Command("open", url)
	default:
		cmd = exec.Command("xdg-open", url)
	}
	if err := cmd.Start(); err != nil {
		log.Printf("브라우저를 열지 못했습니다: %v", err)
	}
}

type originList []string

func (o *originList) String() string { return strings.Join(*o, ",") }
func (o *originList) Set(v string) error {
	*o = append(*o, strings.TrimRight(strings.TrimSpace(v), "/"))
	return nil
}

// launchFlags — 이번에 사람이 준 플래그. URL 스킴 등록에 그대로 실려서, 링크로 열린
// 에이전트가 손으로 실행한 것과 **같게** 뜬다.
//
// 빼는 것 둘: `persodata://…`(스킴이 붙여 주는 인자라 그대로 두면 겹쳐서 쌓인다)와
// `--no-browser`(링크로 열렸는데 브라우저를 안 열면 아무 일도 안 일어난 것과 같다).
func launchFlags() []string {
	out := []string{}
	for _, a := range os.Args[1:] {
		if strings.HasPrefix(strings.ToLower(a), "persodata:") || a == "--no-browser" || a == "-no-browser" {
			continue
		}
		out = append(out, a)
	}
	return out
}

// defaultRepo — 실행 파일 옆에서 스냅샷 폴더를 찾는다. `git clone` 이면 `perso-data-snapshot`
// 이고, GitHub 의 zip 을 풀면 `perso-data-snapshot-main/perso-data-snapshot-main` 처럼 두 겹이
// 된다. 어느 쪽이든 `data/manifest.json` 이 있는 첫 폴더를 고른다 — 못 찾으면 관례 이름을
// 돌려주고 NewSnapshot 이 어디를 봤는지 적어 준다.
func defaultRepo() string {
	exe, err := os.Executable()
	if err != nil {
		return "perso-data-snapshot"
	}
	here := filepath.Dir(exe)
	isRepo := func(dir string) bool {
		_, err := os.Stat(filepath.Join(dir, "data", "manifest.json"))
		return err == nil
	}
	for _, name := range []string{"perso-data-snapshot", "perso-data-snapshot-main"} {
		dir := filepath.Join(here, name)
		if isRepo(dir) {
			return dir
		}
		if isRepo(filepath.Join(dir, name)) {
			return filepath.Join(dir, name)
		}
	}
	if isRepo(here) {
		return here
	}
	if entries, err := os.ReadDir(here); err == nil {
		for _, e := range entries {
			if !e.IsDir() || !strings.HasPrefix(e.Name(), "perso-data-snapshot") {
				continue
			}
			dir := filepath.Join(here, e.Name())
			if isRepo(dir) {
				return dir
			}
			if inner, err := os.ReadDir(dir); err == nil {
				for _, in := range inner {
					if in.IsDir() && isRepo(filepath.Join(dir, in.Name())) {
						return filepath.Join(dir, in.Name())
					}
				}
			}
		}
	}
	return filepath.Join(here, "perso-data-snapshot")
}

func main() {
	log.SetFlags(log.Ltime)
	var extra originList
	repo := flag.String("repo", defaultRepo(), "스냅샷 clone 폴더 (기본: 실행 파일 옆)")
	flag.Var(&extra, "console", "허용할 콘솔 출처 (여러 번 줄 수 있음, 준 것이 먼저)")
	noBrowser := flag.Bool("no-browser", false, "브라우저를 열지 않는다")
	unregister := flag.Bool("unregister", false, "persodata:// 등록만 지우고 끝낸다")
	showVersion := flag.Bool("version", false, "버전만 찍고 끝낸다")
	// **`persodata://open` 으로 열리면 그 URL 이 argv[1] 로 온다.** Go 의 flag 는 첫
	// 비플래그에서 멈추므로 그대로 무해하게 무시된다 — 그 값으로 하는 일이 없다.
	flag.Parse()
	if *showVersion {
		fmt.Println("perso-agent " + version)
		return
	}

	if *unregister {
		unregisterScheme()
		return
	}
	registerScheme(launchFlags())

	snap, err := NewSnapshot(*repo)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Printf("perso-agent %s\n", version)
	fmt.Printf("스냅샷: %s\n", snap.Repo)
	fmt.Println("pull 확인 중…")
	snap.Pull()
	note, _ := snap.pullState()
	fmt.Printf("  → %s\n", note)
	// 켜 둔 채로 며칠이 가도 최신이게 — 스냅샷은 매일 새로 만들어지므로 한 시간마다 다시 pull 한다
	// (새 것이 없으면 git 이 곧바로 끝난다). 계산 중에 바뀌면 RunMetric 이 잡아 다시 계산하게 한다.
	go func() {
		for range time.Tick(time.Hour) {
			snap.Pull()
		}
	}()

	ln, port, err := listen()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	a := &agent{snap: snap, token: newToken(), port: port,
		origins: append([]string(extra), defaultOrigins...)}

	local := fmt.Sprintf("http://127.0.0.1:%d/", port)
	console := fmt.Sprintf("%s/app/data#agent=%d&token=%s", a.origins[0], port, a.token)
	fmt.Printf("\n  콘솔 화면 : %s\n", console)
	fmt.Printf("  로컬 화면 : %s   (Safari 처럼 콘솔에서 못 붙는 브라우저용)\n", local)
	fmt.Printf("  토큰      : %s… (프로세스마다 새로 만듭니다)\n", a.token[:8])
	fmt.Print("\n  이 창을 닫으면 끝납니다.\n\n")

	if !*noBrowser {
		time.AfterFunc(500*time.Millisecond, func() { openBrowser(console) })
	}
	srv := &http.Server{Handler: a.routes(), ReadHeaderTimeout: 10 * time.Second}
	if err := srv.Serve(ln); err != nil {
		log.Fatal(err)
	}
}
