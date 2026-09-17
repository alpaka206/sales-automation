package main

import (
	"os"
	"path/filepath"
	"testing"
)

// 앱으로 뜨면 실행 파일은 `.app/Contents/MacOS` 안에 있다 — 스냅샷은 그 안이 아니라 앱 옆에서 찾는다.
func TestAppBundleIsRecognisedFromTheExecutablePath(t *testing.T) {
	exe := filepath.Join("Users", "kim", "Downloads", "Perso Agent.app", "Contents", "MacOS", "perso-agent")
	if got := appBundleOf(exe); got != filepath.Join("Users", "kim", "Downloads", "Perso Agent.app") {
		t.Fatalf("번들을 못 알아봤습니다: %q", got)
	}
	if appBundleOf(filepath.Join("Users", "kim", "perso-agent-mac-arm64")) != "" {
		t.Fatal("맨 실행 파일을 앱으로 봤습니다")
	}
	dirs := searchDirs(exe, filepath.Join("Users", "kim"))
	if dirs[0] != filepath.Join("Users", "kim", "Downloads") {
		t.Fatalf("첫 자리는 앱 옆이어야 합니다: %v", dirs)
	}
}

// GitHub zip 을 풀면 두 겹이다. App Translocation 으로 앱 옆을 모를 때는 홈·다운로드 등을 본다.
func TestSnapshotIsFoundInTheZipShapeAndInCommonFolders(t *testing.T) {
	home := t.TempDir()
	inner := filepath.Join(home, "Downloads", "perso-data-snapshot-main", "perso-data-snapshot-main")
	if err := os.MkdirAll(filepath.Join(inner, "data"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(inner, "data", "manifest.json"), []byte("{}"), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := findRepoIn(filepath.Join(home, "Downloads")); got != inner {
		t.Fatalf("두 겹 폴더를 못 찾았습니다: %q", got)
	}
	translocated := filepath.Join(home, "private", "AppTranslocation", "X", "d", "Perso Agent.app", "Contents", "MacOS", "perso-agent")
	var found string
	for _, dir := range searchDirs(translocated, home) {
		if found = findRepoIn(dir); found != "" {
			break
		}
	}
	if found != inner {
		t.Fatalf("임시 경로로 옮겨 실행돼도 다운로드 폴더에서 찾아야 합니다: %q", found)
	}
}

func TestFinderPSNArgumentIsDropped(t *testing.T) {
	got := withoutPSN([]string{"perso-agent", "-psn_0_12345", "--no-browser"})
	if len(got) != 2 || got[1] != "--no-browser" {
		t.Fatalf("%v", got)
	}
}
