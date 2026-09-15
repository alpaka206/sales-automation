package main

import (
	"fmt"
	"os"

	"golang.org/x/sys/windows/registry"
)

// 사이트의 「에이전트 열기」가 `persodata://open` 을 열면 이 exe 가 뜬다. **웹에서 로컬
// 프로그램을 실행하는 길은 커스텀 URL 스킴 하나뿐이다.**
//
// 쓰는 곳은 `HKCU` 다 — **관리자 권한이 필요 없고**, 지울 때 그 사용자 것만 지워진다.
// `HKLM` 은 관리자가 필요해서 이 배포 방식(개인 PC 에 파일 하나)과 안 맞는다.
const schemeKey = `Software\Classes\persodata`

// registerScheme 는 **지금 이 프로세스를 띄운 플래그를 그대로** 등록한다.
//
// 안 그러면 링크로 열린 에이전트가 플래그 없이 떠서 기본 콘솔 출처(운영 사이트)를 여는데,
// 시험 중에는 그 화면이 없다. 「URL 이 콘솔 주소를 알려 주게」 하는 길도 있지만 그건
// **Origin 허용목록을 무의미하게 만든다** — 아무 페이지나 자기 출처를 신뢰하게 시킬 수
// 있다. 그래서 신뢰할 출처를 정하는 것은 언제나 사람이 준 플래그다.
func registerScheme(args []string) {
	exe, err := os.Executable()
	if err != nil {
		return
	}
	want := fmt.Sprintf(`"%s"`, exe)
	for _, a := range args {
		want += fmt.Sprintf(` "%s"`, a)
	}
	want += ` "%1"`

	// 이미 이 exe 로 등록돼 있으면 아무것도 안 한다 — 실행할 때마다 레지스트리를 쓰지
	// 않는다. 경로가 다르면(폴더를 옮겼다) 고쳐 준다.
	if k, err := registry.OpenKey(registry.CURRENT_USER, schemeKey+`\shell\open\command`, registry.QUERY_VALUE); err == nil {
		got, _, _ := k.GetStringValue("")
		k.Close()
		if got == want {
			return
		}
	}

	root, _, err := registry.CreateKey(registry.CURRENT_USER, schemeKey, registry.SET_VALUE)
	if err != nil {
		fmt.Fprintf(os.Stderr, "persodata:// 를 등록하지 못했습니다: %v\n", err)
		return
	}
	defer root.Close()
	_ = root.SetStringValue("", "URL:PERSO 데이터 에이전트")
	_ = root.SetStringValue("URL Protocol", "") // 이 빈 값이 「URL 스킴이다」라는 표시다
	cmd, _, err := registry.CreateKey(registry.CURRENT_USER, schemeKey+`\shell\open\command`, registry.SET_VALUE)
	if err != nil {
		fmt.Fprintf(os.Stderr, "persodata:// 를 등록하지 못했습니다: %v\n", err)
		return
	}
	defer cmd.Close()
	if err := cmd.SetStringValue("", want); err != nil {
		fmt.Fprintf(os.Stderr, "persodata:// 를 등록하지 못했습니다: %v\n", err)
		return
	}
	fmt.Println("persodata:// 를 이 사용자 계정에 등록했습니다 (관리자 권한 없음).")
	fmt.Printf("  지우려면: reg delete \"HKCU\\%s\" /f\n", schemeKey)
}

func unregisterScheme() {
	if _, err := registry.OpenKey(registry.CURRENT_USER, schemeKey, registry.QUERY_VALUE); err != nil {
		fmt.Println("persodata:// 는 등록돼 있지 않습니다 — 지울 것이 없습니다.")
		return
	}
	_ = registry.DeleteKey(registry.CURRENT_USER, schemeKey+`\shell\open\command`)
	_ = registry.DeleteKey(registry.CURRENT_USER, schemeKey+`\shell\open`)
	_ = registry.DeleteKey(registry.CURRENT_USER, schemeKey+`\shell`)
	if err := registry.DeleteKey(registry.CURRENT_USER, schemeKey); err != nil {
		fmt.Fprintf(os.Stderr, "persodata:// 를 지우지 못했습니다: %v\n", err)
		return
	}
	fmt.Println("persodata:// 등록을 지웠습니다.")
}
