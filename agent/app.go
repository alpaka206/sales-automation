package main

// 맥의 `Perso Agent.app` 으로 실행될 때 달라지는 것들 (2026-09-17).
//
// **왜 앱인가.** 공증까지 받은 맨 실행 파일도 Finder 에서 더블클릭하면 「악성 코드가 없음을 확인할 수
// 없습니다」가 뜬다. macOS 14·15 러너에서 격리 속성을 달고 잰 결과: `spctl -t install` 은
// `accepted · Notarized Developer ID` 인데, 더블클릭이 쓰는 `spctl -t exec` 은
// `rejected (the code is valid but does not seem to be an app)` 이다. 앱이 아니면 공증과 무관하게
// 막힌다 — 그래서 번들로 싸고 티켓을 붙인다(`agent-release.yml`).
//
// 앱으로 뜨면 터미널 창이 없다. 그래서 셋이 달라진다:
//   - 스냅샷 폴더를 **앱 옆**에서 찾는다(실행 파일은 `.app/Contents/MacOS` 안에 있다). 맥이 받은 앱을
//     임시 경로로 옮겨 실행하면(App Translocation) 앱 옆이 어딘지 알 수 없어서, 홈·다운로드·데스크톱·
//     문서 폴더도 본다.
//   - 실패를 **대화 상자로** 알린다. 표준 출력은 아무도 안 본다 — 조용히 꺼지면 「안 된다」만 남는다.
//   - 이미 떠 있으면 하나 더 띄우지 않고 콘솔만 연다. 창이 없어 떠 있는지 알 길이 없으니 누구나 다시 누른다.

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// appBundleOf — 실행 파일이 `X.app/Contents/MacOS/…` 에 있으면 `X.app` 의 경로, 아니면 "".
func appBundleOf(exe string) string {
	dir := filepath.Dir(exe)
	if filepath.Base(dir) != "MacOS" || filepath.Base(filepath.Dir(dir)) != "Contents" {
		return ""
	}
	bundle := filepath.Dir(filepath.Dir(dir))
	if !strings.HasSuffix(bundle, ".app") {
		return ""
	}
	return bundle
}

func currentBundle() string {
	exe, err := os.Executable()
	if err != nil {
		return ""
	}
	return appBundleOf(exe)
}

func isRepo(dir string) bool {
	_, err := os.Stat(filepath.Join(dir, "data", "manifest.json"))
	return err == nil
}

// findRepoIn — `dir` 바로 아래에서 스냅샷 폴더를 찾는다. `git clone` 이면 `perso-data-snapshot`,
// GitHub 의 zip 을 풀면 `perso-data-snapshot-main/perso-data-snapshot-main` 처럼 두 겹이다.
// 어느 쪽이든 `data/manifest.json` 이 있는 첫 폴더. 없으면 "".
func findRepoIn(dir string) string {
	for _, name := range []string{"perso-data-snapshot", "perso-data-snapshot-main"} {
		if isRepo(filepath.Join(dir, name)) {
			return filepath.Join(dir, name)
		}
		if isRepo(filepath.Join(dir, name, name)) {
			return filepath.Join(dir, name, name)
		}
	}
	if isRepo(dir) {
		return dir
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return ""
	}
	for _, e := range entries {
		if !e.IsDir() || !strings.HasPrefix(e.Name(), "perso-data-snapshot") {
			continue
		}
		sub := filepath.Join(dir, e.Name())
		if isRepo(sub) {
			return sub
		}
		if inner, err := os.ReadDir(sub); err == nil {
			for _, in := range inner {
				if in.IsDir() && isRepo(filepath.Join(sub, in.Name())) {
					return filepath.Join(sub, in.Name())
				}
			}
		}
	}
	return ""
}

// searchDirs — 스냅샷을 찾아볼 곳, 앞이 먼저. 실행 파일(앱이면 앱) 옆이 첫째다.
func searchDirs(exe, home string) []string {
	here := filepath.Dir(exe)
	if bundle := appBundleOf(exe); bundle != "" {
		here = filepath.Dir(bundle)
	}
	dirs := []string{here}
	if home != "" {
		dirs = append(dirs, home, filepath.Join(home, "Downloads"), filepath.Join(home, "Desktop"),
			filepath.Join(home, "Documents"))
	}
	return dirs
}

// defaultRepo — 못 찾으면 관례 이름을 돌려주고 NewSnapshot 이 어디를 봤는지 적어 준다.
func defaultRepo() string {
	exe, err := os.Executable()
	if err != nil {
		return "perso-data-snapshot"
	}
	home, _ := os.UserHomeDir()
	dirs := searchDirs(exe, home)
	for _, dir := range dirs {
		if found := findRepoIn(dir); found != "" {
			return found
		}
	}
	return filepath.Join(dirs[0], "perso-data-snapshot")
}

// withoutPSN — 옛 macOS 는 Finder 로 연 앱에 `-psn_0_12345` 를 붙인다. flag 가 모르는 인자라 그대로
// 두면 「flag provided but not defined」로 바로 꺼진다.
func withoutPSN(args []string) []string {
	out := args[:0:0]
	for _, a := range args {
		if !strings.HasPrefix(a, "-psn_") {
			out = append(out, a)
		}
	}
	return out
}

// runningAgentPort — 이 PC 에 이미 떠 있는 에이전트의 포트. 없으면 0.
func runningAgentPort() int {
	client := http.Client{Timeout: 400 * time.Millisecond}
	for _, p := range portCandidates {
		resp, err := client.Get(fmt.Sprintf("http://127.0.0.1:%d/v1/health", p))
		if err != nil {
			continue
		}
		var body struct {
			Agent string `json:"agent"`
		}
		_ = json.NewDecoder(resp.Body).Decode(&body)
		resp.Body.Close()
		if strings.HasPrefix(body.Agent, "perso-agent/") {
			return p
		}
	}
	return 0
}

// fail — 실패를 알리고 끝낸다. 앱으로 떴으면 대화 상자로, 아니면 터미널에.
func fail(err error) {
	fmt.Fprintln(os.Stderr, err)
	if runtime.GOOS == "darwin" && currentBundle() != "" {
		msg := strings.NewReplacer(`\`, `\\`, `"`, `\"`).Replace(err.Error())
		script := fmt.Sprintf(`display dialog "%s" with title "Perso Agent" buttons {"확인"} default button 1 with icon stop`, msg)
		_ = exec.Command("osascript", "-e", script).Run()
	}
	os.Exit(1)
}
