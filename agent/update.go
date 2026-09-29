package main

// 자동 업데이트 (2026-09-29 운영자 지시: 「한번 다운로드 했으면 윈도우든 맥이든 에이전트 자동으로
// 업데이트」).
//
// **켤 때 한 번** 본다. 새 버전이 있으면 받아서 검증하고, 제 자리에 바꿔 넣고, 같은 인자로 다시 띄운 뒤
// 이 프로세스는 끝난다. 켜 둔 동안은 안 본다 — 다시 띄우면 토큰이 바뀌어 콘솔이 짝을 잃고 브라우저 탭이
// 하나 더 열린다. 그건 사람이 켤 때 일어나야 하는 일이다.
//
// **이 파일이 에이전트에서 바깥으로 요청을 보내는 유일한 자리다.** 받기만 하고(GET, 본문 없음), 가는
// 곳은 이 저장소의 GitHub Release 하나다 — github.com 과, 자산을 내려받을 때 거기서 넘겨 주는
// *.githubusercontent.com. 스냅샷 데이터는 이 파일에 한 글자도 안 들어온다.
// tests/test_agent_stays_local.py 가 그 셋(이 파일만 · GET 만 · 그 주소만)을 고정한다.
//
// 실패하면 **지금 버전 그대로 뜬다.** 업데이트 때문에 에이전트가 안 뜨는 일이 있으면 안 된다.

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync"
	"time"
)

// 릴리스가 사는 곳. 시험 빌드만 `-ldflags "-X main.updateFrom=http://127.0.0.1:…/releases"` 로 바꾼다.
var updateFrom = "https://github.com/alpaka206/sales-automation/releases"

// 다시 띄운 프로세스에 붙이는 플래그. 그 프로세스는 업데이트를 안 본다 — 릴리스가 잘못 올라가도(버전을
// 안 올린 빌드 등) 끝없이 다시 뜨지 않는다. 맥의 `open` 은 환경 변수를 안 넘겨서 플래그로 한다.
const justUpdatedFlag = "--just-updated"

const maxAssetBytes = 200 << 20

var (
	updateMu   sync.Mutex
	updateNote = "안 봄"
)

func setUpdateNote(note string) {
	updateMu.Lock()
	defer updateMu.Unlock()
	updateNote = note
}

func updateState() string {
	updateMu.Lock()
	defer updateMu.Unlock()
	return updateNote
}

// 시험에서 바꿔 끼우는 자리 셋 — 받은 실행 파일의 버전 묻기, 맥 서명 검사, 다시 띄우기.
var (
	probeVersion = func(exe string) (string, error) {
		out, err := exec.Command(exe, "--version").Output()
		return strings.TrimSpace(string(out)), err
	}
	verifySignature = codesignMatches
	relaunch        = startAgain
)

// autoUpdate — 새 버전으로 바꿔 넣고 다시 띄웠으면 true(부른 쪽은 바로 끝낸다).
func autoUpdate(justUpdated bool) bool {
	go cleanLeftovers()
	if justUpdated {
		setUpdateNote(fmt.Sprintf("방금 %s 로 올렸습니다", version))
		return false
	}
	if version == "dev" {
		setUpdateNote("손 빌드라 안 봄")
		return false
	}
	exe, err := os.Executable()
	if err != nil {
		setUpdateNote("실행 파일 위치를 모릅니다")
		return false
	}
	if resolved, err := filepath.EvalSymlinks(exe); err == nil {
		exe = resolved
	}
	u := newUpdater(updateFrom)
	latest, err := u.latest()
	if err != nil {
		setUpdateNote("확인 실패 — " + err.Error())
		return false
	}
	if !versionNewer(latest, version) {
		setUpdateNote(fmt.Sprintf("최신(%s)", version))
		return false
	}
	fmt.Printf("업데이트: %s → %s 받는 중…\n", version, latest)
	if err := u.install(latest, exe); err != nil {
		setUpdateNote(fmt.Sprintf("%s 로 못 올렸습니다 — %s", latest, err))
		fmt.Printf("  → %s\n", updateState())
		return false
	}
	target := exe
	if bundle := appBundleOf(exe); bundle != "" {
		target = bundle
	}
	// 플래그는 **맨 앞에** — `persodata://…` 가 끼어 있으면 Go 의 flag 는 거기서 멈춘다.
	if err := relaunch(target, append([]string{justUpdatedFlag}, os.Args[1:]...)); err != nil {
		// 파일은 이미 새것이다. 이 프로세스(옛 버전)로 그대로 서비스하고, 다음에 켤 때 새것이 뜬다.
		setUpdateNote(fmt.Sprintf("%s 로 바꿔 두었습니다 — 다음에 켤 때부터 적용(%s)", latest, err))
		return false
	}
	fmt.Printf("  → %s 로 다시 띄웠습니다\n", latest)
	return true
}

type updater struct {
	base   *url.URL
	client *http.Client
}

func newUpdater(base string) *updater {
	parsed, _ := url.Parse(strings.TrimRight(base, "/"))
	u := &updater{base: parsed}
	u.client = &http.Client{
		Timeout: 3 * time.Minute,
		// **GitHub 밖으로는 안 따라간다.** 자산 링크는 github.com → *.githubusercontent.com 으로 넘어가는데,
		// 그 밖으로 넘기는 응답은 무엇이든 거절한다.
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) >= 5 {
				return errors.New("넘겨주기가 너무 많습니다")
			}
			if !u.allowedHost(req.URL) {
				return fmt.Errorf("허용되지 않은 곳으로 넘어가려 합니다: %s", req.URL.Host)
			}
			return nil
		},
	}
	return u
}

func (u *updater) allowedHost(target *url.URL) bool {
	if u.base == nil {
		return false
	}
	host := strings.ToLower(target.Hostname())
	if host == strings.ToLower(u.base.Hostname()) {
		return target.Scheme == u.base.Scheme
	}
	return target.Scheme == "https" && (host == "githubusercontent.com" || strings.HasSuffix(host, ".githubusercontent.com"))
}

// get — 받기만 한다. 본문을 싣는 요청은 이 에이전트에 없다.
func (u *updater) get(ctx context.Context, target string, follow bool) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, target, nil)
	if err != nil {
		return nil, err
	}
	client := u.client
	if !follow {
		copied := *u.client
		copied.CheckRedirect = func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }
		client = &copied
	}
	return client.Do(req)
}

var tagPattern = regexp.MustCompile(`/tag/agent-v(\d+\.\d+\.\d+)$`)

// latest — `releases/latest` 가 넘겨 주는 태그에서 버전을 뗀다. API(시간당 60회, 사무실 IP 공유)를 안 쓴다.
func (u *updater) latest() (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	resp, err := u.get(ctx, u.base.String()+"/latest", false)
	if err != nil {
		return "", shortErr(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 300 || resp.StatusCode >= 400 {
		return "", fmt.Errorf("최신 릴리스를 못 찾았습니다 (HTTP %d)", resp.StatusCode)
	}
	return tagFromLocation(resp.Header.Get("Location"))
}

func tagFromLocation(location string) (string, error) {
	m := tagPattern.FindStringSubmatch(strings.TrimRight(location, "/"))
	if m == nil {
		return "", fmt.Errorf("최신 릴리스 태그를 못 읽었습니다: %q", location)
	}
	return m[1], nil
}

// versionNewer — 세 자리 비교. 낮은 버전으로는 안 내려간다(릴리스를 되돌려도 이미 올린 PC 는 그대로).
func versionNewer(latest, current string) bool {
	parse := func(v string) [3]int {
		var out [3]int
		for i, part := range strings.SplitN(v, ".", 3) {
			fmt.Sscanf(part, "%d", &out[i])
		}
		return out
	}
	a, b := parse(latest), parse(current)
	for i := 0; i < 3; i++ {
		if a[i] != b[i] {
			return a[i] > b[i]
		}
	}
	return false
}

// assetFor — 이 실행 파일에 맞는 릴리스 자산 이름. `agent-release.yml` 이 올리는 이름과 같아야 한다.
func assetFor(goos, goarch string, bundled bool) (string, error) {
	switch {
	case goos == "windows" && goarch == "amd64":
		return "perso-agent.exe", nil
	case goos == "darwin" && bundled:
		return "perso-agent-mac.zip", nil
	case goos == "darwin" && goarch == "arm64":
		return "perso-agent-mac-arm64", nil
	case goos == "darwin" && goarch == "amd64":
		return "perso-agent-mac-intel", nil
	}
	return "", fmt.Errorf("%s/%s 용 릴리스가 없습니다", goos, goarch)
}

func parseSums(r io.Reader) map[string]string {
	sums := map[string]string{}
	scanner := bufio.NewScanner(r)
	for scanner.Scan() {
		fields := strings.Fields(scanner.Text())
		if len(fields) == 2 && len(fields[0]) == 64 {
			sums[strings.TrimPrefix(fields[1], "*")] = strings.ToLower(fields[0])
		}
	}
	return sums
}

// install — 받고, 체크섬·버전(맥은 서명까지)을 확인하고, 제 자리에 바꿔 넣는다. 어느 단계든 실패하면
// 지금 파일은 그대로다.
func (u *updater) install(latest, exe string) error {
	bundle := appBundleOf(exe)
	asset, err := assetFor(runtime.GOOS, runtime.GOARCH, bundle != "")
	if err != nil {
		return err
	}
	if bundle != "" && strings.Contains(bundle, "/AppTranslocation/") {
		// 맥이 다운로드 폴더의 앱을 읽기 전용 임시 위치로 옮겨 실행한 경우 — 거기는 못 바꾼다.
		return errors.New("앱을 「응용 프로그램」 폴더로 옮기면 자동 업데이트됩니다")
	}
	tag := "agent-v" + latest
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Minute)
	defer cancel()

	resp, err := u.get(ctx, fmt.Sprintf("%s/download/%s/SHA256SUMS.txt", u.base, tag), true)
	if err != nil {
		return shortErr(err)
	}
	sums := parseSums(io.LimitReader(resp.Body, 1<<20))
	resp.Body.Close()
	want := sums[asset]
	if resp.StatusCode != http.StatusOK || want == "" {
		return fmt.Errorf("체크섬 목록에 %s 가 없습니다 (HTTP %d)", asset, resp.StatusCode)
	}

	// 받은 파일은 바꿔 넣을 자리와 **같은 폴더**에 둔다 — 이름 바꾸기가 원자적이려면 같은 볼륨이어야 한다.
	place := filepath.Dir(exe)
	if bundle != "" {
		place = filepath.Dir(bundle)
	}
	fresh, err := u.download(ctx, fmt.Sprintf("%s/download/%s/%s", u.base, tag, asset), place, want)
	if err != nil {
		return err
	}
	defer os.Remove(fresh)

	if bundle != "" {
		return installBundle(fresh, bundle, latest)
	}
	return installFile(fresh, exe, latest)
}

func (u *updater) download(ctx context.Context, target, dir, wantSum string) (string, error) {
	resp, err := u.get(ctx, target, true)
	if err != nil {
		return "", shortErr(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("받기 실패 (HTTP %d)", resp.StatusCode)
	}
	file, err := os.CreateTemp(dir, ".perso-agent-update-*")
	if err != nil {
		return "", fmt.Errorf("그 폴더에 쓸 수 없습니다: %w", err)
	}
	hash := sha256.New()
	n, copyErr := io.Copy(io.MultiWriter(file, hash), io.LimitReader(resp.Body, maxAssetBytes+1))
	closeErr := file.Close()
	if copyErr != nil || closeErr != nil || n > maxAssetBytes {
		os.Remove(file.Name())
		if n > maxAssetBytes {
			return "", errors.New("받은 파일이 너무 큽니다")
		}
		return "", fmt.Errorf("받다가 끊겼습니다: %v", firstErr(copyErr, closeErr))
	}
	if got := hex.EncodeToString(hash.Sum(nil)); got != wantSum {
		os.Remove(file.Name())
		return "", errors.New("체크섬이 맞지 않습니다 — 받은 파일을 버렸습니다")
	}
	return file.Name(), nil
}

// installFile — 윈도우 exe · 맥 맨 실행 파일. **실행 중인 파일은 덮어쓸 수 없지만 이름은 바꿀 수 있다**
// (윈도우). 그래서 지금 것을 `.old` 로 비키고 새것을 그 이름에 둔다. `.old` 는 새 프로세스가 지운다.
func installFile(fresh, exe, latest string) error {
	candidate := exe + ".new" + filepath.Ext(exe)
	os.Remove(candidate)
	if err := os.Rename(fresh, candidate); err != nil {
		return err
	}
	if err := os.Chmod(candidate, 0o755); err != nil {
		os.Remove(candidate)
		return err
	}
	if err := expectVersion(candidate, latest); err != nil {
		os.Remove(candidate)
		return err
	}
	old := exe + ".old"
	os.Remove(old)
	if err := os.Rename(exe, old); err != nil {
		os.Remove(candidate)
		return fmt.Errorf("지금 파일을 비킬 수 없습니다: %w", err)
	}
	if err := os.Rename(candidate, exe); err != nil {
		_ = os.Rename(old, exe) // 되돌린다 — 실행 파일이 사라진 채로 두지 않는다
		os.Remove(candidate)
		return fmt.Errorf("새 파일을 넣지 못했습니다: %w", err)
	}
	return nil
}

// installBundle — 맥 앱. zip 을 풀어 **Apple 서명과 개발자 팀이 지금 앱과 같은지** 보고, 번들을 통째로 바꾼다.
// 다운로드 폴더에서 격리 표시 없이 받은 것이라 Gatekeeper 가 다시 안 보므로, 서명은 여기서 직접 본다.
func installBundle(zipPath, bundle, latest string) error {
	stage, err := os.MkdirTemp(filepath.Dir(bundle), ".perso-agent-update-")
	if err != nil {
		return fmt.Errorf("그 폴더에 쓸 수 없습니다: %w", err)
	}
	defer os.RemoveAll(stage)
	if out, err := exec.Command("ditto", "-x", "-k", zipPath, stage).CombinedOutput(); err != nil {
		return fmt.Errorf("압축을 못 풀었습니다: %s", strings.TrimSpace(string(out)))
	}
	fresh := filepath.Join(stage, "Perso Agent.app") // 워크플로가 싸는 이름 — 받은 사람이 앱 이름을 바꿨어도
	if err := verifySignature(fresh, bundle); err != nil {
		return err
	}
	if err := expectVersion(filepath.Join(fresh, "Contents", "MacOS", "perso-agent"), latest); err != nil {
		return err
	}
	old := bundle + ".old"
	os.RemoveAll(old)
	if err := os.Rename(bundle, old); err != nil {
		return fmt.Errorf("지금 앱을 비킬 수 없습니다: %w", err)
	}
	if err := os.Rename(fresh, bundle); err != nil {
		_ = os.Rename(old, bundle)
		return fmt.Errorf("새 앱을 넣지 못했습니다: %w", err)
	}
	return nil
}

func expectVersion(exe, latest string) error {
	got, err := probeVersion(exe)
	if err != nil {
		return fmt.Errorf("받은 에이전트가 실행되지 않습니다: %v", err)
	}
	if got != "perso-agent "+latest {
		return fmt.Errorf("받은 에이전트의 버전이 %q 입니다(기대 %s)", got, latest)
	}
	return nil
}

var teamPattern = regexp.MustCompile(`(?m)^TeamIdentifier=(\S+)$`)

func codesignTeam(app string) (string, error) {
	out, err := exec.Command("codesign", "-dv", "--verbose=2", app).CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("서명을 못 읽었습니다: %s", strings.TrimSpace(string(out)))
	}
	m := teamPattern.FindStringSubmatch(string(out))
	if m == nil || m[1] == "not set" {
		return "", errors.New("서명에 개발자 팀이 없습니다")
	}
	return m[1], nil
}

func codesignMatches(fresh, current string) error {
	if out, err := exec.Command("codesign", "--verify", "--deep", "--strict", fresh).CombinedOutput(); err != nil {
		return fmt.Errorf("받은 앱의 서명이 올바르지 않습니다: %s", strings.TrimSpace(string(out)))
	}
	want, err := codesignTeam(current)
	if err != nil {
		return fmt.Errorf("지금 앱의 서명을 못 읽어 바꾸지 않습니다: %w", err)
	}
	got, err := codesignTeam(fresh)
	if err != nil {
		return err
	}
	if got != want {
		return fmt.Errorf("받은 앱의 개발자 팀(%s)이 지금 앱(%s)과 다릅니다", got, want)
	}
	return nil
}

// startAgain — 같은 인자로 새것을 띄운다. 맥 앱은 Launch Services 로(`open -n`), 나머지는 직접.
func startAgain(target string, args []string) error {
	if strings.HasSuffix(target, ".app") {
		return exec.Command("open", append([]string{"-n", target, "--args"}, args...)...).Run() // open 은 곧바로 돌아온다
	}
	cmd := exec.Command(target, args...)
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr // 같은 창을 이어 쓴다
	return cmd.Start()
}

// cleanLeftovers — 지난 업데이트가 비켜 둔 `.old`. 옛 프로세스가 막 끝나는 중이면 윈도우가 아직 잡고 있어
// 몇 번 다시 해 본다. 못 지우면 다음에 켤 때 지운다.
func cleanLeftovers() {
	exe, err := os.Executable()
	if err != nil {
		return
	}
	old := exe + ".old"
	if bundle := appBundleOf(exe); bundle != "" {
		old = bundle + ".old"
	}
	for i := 0; i < 5; i++ {
		if _, err := os.Stat(old); errors.Is(err, os.ErrNotExist) {
			return
		}
		if os.RemoveAll(old) == nil {
			return
		}
		time.Sleep(time.Second)
	}
}

// shortErr — 사내망·오프라인이면 흔한 일이라 한 줄로. 주소 전체를 화면에 늘어놓지 않는다.
func shortErr(err error) error {
	var urlErr *url.Error
	if errors.As(err, &urlErr) {
		return fmt.Errorf("GitHub 에 닿지 못했습니다: %v", urlErr.Err)
	}
	return err
}

func firstErr(errs ...error) error {
	for _, err := range errs {
		if err != nil {
			return err
		}
	}
	return nil
}
