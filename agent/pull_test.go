package main

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// 원격은 이 PC 의 폴더다(file://). 실제 GitHub 에는 안 닿는다.

func runGit(t *testing.T, dir string, args ...string) string {
	t.Helper()
	cmd := exec.Command("git", append([]string{"-C", dir}, args...)...)
	cmd.Env = append(os.Environ(), "GIT_AUTHOR_NAME=t", "GIT_AUTHOR_EMAIL=t@t", "GIT_COMMITTER_NAME=t", "GIT_COMMITTER_EMAIL=t@t")
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("git %v: %v\n%s", args, err, out)
	}
	return strings.TrimSpace(string(out))
}

func write(t *testing.T, path, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

func read(t *testing.T, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return strings.ReplaceAll(string(b), "\r\n", "\n") // 윈도우 git 의 core.autocrlf
}

// publish — 원격에 하루치 스냅샷을 하나 더 올린다.
func publish(t *testing.T, remote, day string) {
	t.Helper()
	write(t, filepath.Join(remote, "data", "manifest.json"), `{"generated_at":"`+day+`"}`)
	write(t, filepath.Join(remote, "data", "a.csv"), "day\n"+day+"\n")
	runGit(t, remote, "add", "-A")
	runGit(t, remote, "commit", "-q", "-m", day)
}

func newRemote(t *testing.T) (string, string) {
	t.Helper()
	remote := filepath.Join(t.TempDir(), "remote")
	if err := os.MkdirAll(remote, 0o755); err != nil {
		t.Fatal(err)
	}
	runGit(t, remote, "init", "-q")
	runGit(t, remote, "symbolic-ref", "HEAD", "refs/heads/main")
	publish(t, remote, "2026-09-01")
	return remote, "file:///" + strings.TrimPrefix(filepath.ToSlash(remote), "/")
}

func TestPullCatchesUpAndUndoesLocalEdits(t *testing.T) {
	remote, url := newRemote(t)
	client := filepath.Join(t.TempDir(), "perso-data-snapshot")
	runGit(t, filepath.Dir(client), "clone", "-q", url, client)
	for _, day := range []string{"2026-09-02", "2026-09-03", "2026-09-04"} {
		publish(t, remote, day) // 며칠 밀렸다
	}
	write(t, filepath.Join(client, "data", "a.csv"), "엑셀로 열어 저장한 파일\n") // 예전 pull --ff-only 는 여기서 영영 멈췄다
	write(t, filepath.Join(client, "메모.txt"), "사람이 옆에 둔 파일")

	s := &Snapshot{Repo: client}
	s.pull()
	if note, _ := s.pullState(); note != "성공" {
		t.Fatalf("pull: %s", note)
	}
	if got, want := runGit(t, client, "rev-parse", "HEAD"), runGit(t, remote, "rev-parse", "HEAD"); got != want {
		t.Fatalf("최신이 아닙니다: %s != %s", got, want)
	}
	if got := read(t, filepath.Join(client, "data", "a.csv")); got != "day\n2026-09-04\n" {
		t.Fatalf("로컬에서 바뀐 파일이 그대로입니다: %q", got)
	}
	if got := read(t, filepath.Join(client, "메모.txt")); got != "사람이 옆에 둔 파일" {
		t.Fatal("추적하지 않는 파일을 건드렸습니다")
	}
}

func TestStaleLockIsClearedButALiveOneIsNot(t *testing.T) {
	remote, url := newRemote(t)
	client := filepath.Join(t.TempDir(), "perso-data-snapshot")
	runGit(t, filepath.Dir(client), "clone", "-q", url, client)
	lock := filepath.Join(client, ".git", "index.lock")
	s := &Snapshot{Repo: client}

	// 끊긴 pull 이 한 시간 전에 남긴 잠금 — 예전에는 그 뒤 모든 pull 이 실패했다.
	write(t, lock, "")
	old := time.Now().Add(-time.Hour)
	_ = os.Chtimes(lock, old, old)
	publish(t, remote, "2026-09-02")
	s.pull()
	if note, _ := s.pullState(); note != "성공" {
		t.Fatalf("pull: %s", note)
	}
	if _, err := os.Stat(lock); !os.IsNotExist(err) {
		t.Fatal("오래된 잠금이 남았습니다")
	}

	// 방금 생긴 잠금은 다른 git 이 쓰는 중이다 — 지우지 않는다.
	write(t, lock, "")
	publish(t, remote, "2026-09-03")
	s.pull()
	note, _ := s.pullState()
	if !strings.HasPrefix(note, "다른 git 작업이") {
		t.Fatalf("pull: %s", note)
	}
	if _, err := os.Stat(lock); err != nil {
		t.Fatal("살아 있는 잠금을 지웠습니다")
	}
}

func TestZipDownloadBecomesARepoInPlace(t *testing.T) {
	remote, url := newRemote(t)
	publish(t, remote, "2026-09-02")
	// 이 PC 의 원격을 실제 스냅샷 주소로 보이게 한다 — 테스트가 GitHub 에 닿지 않게.
	t.Setenv("GIT_CONFIG_COUNT", "1")
	t.Setenv("GIT_CONFIG_KEY_0", "url."+url+".insteadOf")
	t.Setenv("GIT_CONFIG_VALUE_0", snapshotRemote)

	// GitHub 「Download ZIP」: 두 겹 폴더, .git 없음, 옛 데이터.
	client := filepath.Join(t.TempDir(), "perso-data-snapshot-main", "perso-data-snapshot-main")
	write(t, filepath.Join(client, "data", "manifest.json"), `{"generated_at":"2026-09-01"}`)
	write(t, filepath.Join(client, "data", "a.csv"), "day\n2026-09-01\n")

	s := &Snapshot{Repo: client}
	s.pull()
	if note, _ := s.pullState(); note != "성공" {
		t.Fatalf("pull: %s", note)
	}
	if got := read(t, filepath.Join(client, "data", "a.csv")); got != "day\n2026-09-02\n" {
		t.Fatalf("최신으로 안 바뀌었습니다: %q", got)
	}
	// 다음 날도 받는다.
	publish(t, remote, "2026-09-03")
	s.pull()
	if got := read(t, filepath.Join(client, "data", "a.csv")); got != "day\n2026-09-03\n" {
		t.Fatalf("두 번째 받기가 안 됐습니다: %q", got)
	}
	if got := runGit(t, client, "config", "remote.origin.url"); got != snapshotRemote {
		t.Fatalf("원격: %s", got)
	}
}

func TestATestFolderIsLeftAlone(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "snap")
	write(t, filepath.Join(dir, "data", "manifest.json"), "{}")
	s := &Snapshot{Repo: dir}
	s.pull()
	if note, _ := s.pullState(); !strings.HasPrefix(note, "git 저장소가 아닙니다") {
		t.Fatalf("pull: %s", note)
	}
	if _, err := os.Stat(filepath.Join(dir, ".git")); !os.IsNotExist(err) {
		t.Fatal("시험 폴더를 git 으로 바꿨습니다")
	}
}

func TestPullFailureSaysWhatToDo(t *testing.T) {
	cases := map[string]string{
		"fatal: could not read Username for 'https://github.com': terminal prompts disabled":               "GitHub 로그인이 필요합니다",
		"remote: Repository not found.\nfatal: repository 'https://github.com/est-perso/x.git/' not found": "GitHub 로그인이 필요합니다",
		"remote: The 'est-perso' organization has enabled or enforced SAML SSO.":                           "GitHub 로그인이 필요합니다",
		"fatal: unable to access 'https://github.com/x/': Could not resolve host: github.com":              "GitHub 에 닿지 못했습니다",
		"error: unable to unlink old 'data/a.csv': Invalid argument":                                       "일부 파일을 못 바꿨습니다",
	}
	for out, want := range cases {
		if got := pullFailure(128, out); !strings.HasPrefix(got, want) {
			t.Errorf("%q → %q", out, got)
		}
	}
	if got := pullFailure(127, "exec: \"git\": executable file not found"); !strings.HasPrefix(got, "git 이 없습니다") {
		t.Error(got)
	}
}
