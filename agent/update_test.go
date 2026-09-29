package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestTagFromLocation(t *testing.T) {
	got, err := tagFromLocation("https://github.com/alpaka206/sales-automation/releases/tag/agent-v1.4.2")
	if err != nil || got != "1.4.2" {
		t.Fatalf("got %q, %v", got, err)
	}
	for _, bad := range []string{"", "https://github.com/x/releases", "https://github.com/x/releases/tag/v1.4.2"} {
		if _, err := tagFromLocation(bad); err == nil {
			t.Errorf("%q 를 받아들였습니다", bad)
		}
	}
}

func TestVersionNewer(t *testing.T) {
	cases := []struct {
		latest, current string
		want            bool
	}{
		{"1.5.0", "1.4.2", true},
		{"1.10.0", "1.9.9", true}, // 글자 비교면 틀린다
		{"2.0.0", "1.99.99", true},
		{"1.4.2", "1.4.2", false},
		{"1.4.1", "1.4.2", false}, // 내려가지 않는다
	}
	for _, c := range cases {
		if got := versionNewer(c.latest, c.current); got != c.want {
			t.Errorf("versionNewer(%s, %s) = %v", c.latest, c.current, got)
		}
	}
}

func TestAssetFor(t *testing.T) {
	cases := []struct {
		goos, goarch string
		bundled      bool
		want         string
	}{
		{"windows", "amd64", false, "perso-agent.exe"},
		{"darwin", "arm64", true, "perso-agent-mac.zip"},
		{"darwin", "amd64", true, "perso-agent-mac.zip"},
		{"darwin", "arm64", false, "perso-agent-mac-arm64"},
		{"darwin", "amd64", false, "perso-agent-mac-intel"},
	}
	for _, c := range cases {
		if got, err := assetFor(c.goos, c.goarch, c.bundled); err != nil || got != c.want {
			t.Errorf("assetFor(%s,%s,%v) = %q, %v", c.goos, c.goarch, c.bundled, got, err)
		}
	}
	if _, err := assetFor("linux", "amd64", false); err == nil {
		t.Error("릴리스가 없는 타깃을 받아들였습니다")
	}
}

func TestParseSums(t *testing.T) {
	h := strings.Repeat("a", 64)
	sums := parseSums(strings.NewReader(h + "  perso-agent.exe\n" + h + " *perso-agent-mac.zip\n쓰레기 줄\n"))
	if sums["perso-agent.exe"] != h || sums["perso-agent-mac.zip"] != h || len(sums) != 2 {
		t.Fatalf("%v", sums)
	}
}

func TestRedirectsStayOnGitHub(t *testing.T) {
	u := newUpdater("https://github.com/alpaka206/sales-automation/releases")
	for raw, want := range map[string]bool{
		"https://github.com/x":                             true,
		"https://objects.githubusercontent.com/x":          true,
		"https://release-assets.githubusercontent.com/x":   true,
		"http://objects.githubusercontent.com/x":           false, // 평문 아님
		"https://evil.example.com/x":                       false,
		"https://githubusercontent.com.evil.example.com/x": false,
		"http://github.com/x":                              false,
	} {
		parsed, _ := url.Parse(raw)
		if got := u.allowedHost(parsed); got != want {
			t.Errorf("%s → %v", raw, got)
		}
	}
}

// 가짜 릴리스 서버로 받기 → 체크섬 → 바꿔 넣기까지. 실행 파일 버전 확인은 바꿔 끼운다.
func TestInstallFileSwapsAndKeepsOld(t *testing.T) {
	payload := []byte("new agent")
	sum := sha256.Sum256(payload)
	const asset = "perso-agent.exe"
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/releases/latest":
			http.Redirect(w, r, "/releases/tag/agent-v9.9.9", http.StatusFound)
		case strings.HasSuffix(r.URL.Path, "/SHA256SUMS.txt"):
			fmt.Fprintf(w, "%s  %s\n", hex.EncodeToString(sum[:]), asset)
		case strings.HasSuffix(r.URL.Path, "/"+asset):
			_, _ = w.Write(payload)
		default:
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()

	u := newUpdater(srv.URL + "/releases")
	latest, err := u.latest()
	if err != nil || latest != "9.9.9" {
		t.Fatalf("latest = %q, %v", latest, err)
	}

	dir := t.TempDir()
	exe := filepath.Join(dir, "perso-agent.exe")
	if err := os.WriteFile(exe, []byte("old agent"), 0o755); err != nil {
		t.Fatal(err)
	}
	saved := probeVersion
	defer func() { probeVersion = saved }()
	probeVersion = func(string) (string, error) { return "perso-agent 9.9.9", nil }

	fresh, err := u.download(t.Context(), srv.URL+"/releases/download/agent-v9.9.9/"+asset, dir, hex.EncodeToString(sum[:]))
	if err != nil {
		t.Fatal(err)
	}
	if err := installFile(fresh, exe, "9.9.9"); err != nil {
		t.Fatal(err)
	}
	if got, _ := os.ReadFile(exe); string(got) != "new agent" {
		t.Fatalf("바뀌지 않았습니다: %q", got)
	}
	if got, _ := os.ReadFile(exe + ".old"); string(got) != "old agent" {
		t.Fatalf(".old 가 옛 파일이 아닙니다: %q", got)
	}

	// 체크섬이 틀리면 받은 것을 버리고 지금 파일은 그대로다.
	if _, err := u.download(t.Context(), srv.URL+"/releases/download/agent-v9.9.9/"+asset, dir, strings.Repeat("0", 64)); err == nil {
		t.Fatal("틀린 체크섬을 받아들였습니다")
	}
	// 받은 실행 파일의 버전이 다르면 바꾸지 않는다.
	probeVersion = func(string) (string, error) { return "perso-agent 1.0.0", nil }
	other := filepath.Join(dir, "other")
	_ = os.WriteFile(other, payload, 0o644)
	if err := installFile(other, exe, "9.9.9"); err == nil {
		t.Fatal("버전이 다른 파일로 바꿨습니다")
	}
	if got, _ := os.ReadFile(exe); string(got) != "new agent" {
		t.Fatalf("실패했는데 파일이 바뀌었습니다: %q", got)
	}
	leftovers, _ := filepath.Glob(filepath.Join(dir, "*.new*"))
	if len(leftovers) != 0 {
		t.Fatalf("받다 만 파일이 남았습니다: %v", leftovers)
	}
}
