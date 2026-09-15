package main

import "fmt"

// **맥에서 `persodata://` 는 맨 바이너리로 등록할 수 없다.** Launch Services 가 스킴을
// 읽는 곳은 `.app` 번들의 `Info.plist` 이고(`CFBundleURLSchemes`), 등록되는 단위도 번들
// 하나다. 파일 하나를 폴더 옆에 두는 이 배포 방식과는 맞지 않는다.
//
// 그래서 맥은 「에이전트 열기」 대신 **직접 실행 → 브라우저가 열린다**로 간다. 그 길은
// 어차피 모든 브라우저에서 되는 길이고(Safari 포함), 콘솔 화면이 붙지 못하는 브라우저에
// 대해서도 답이 된다. 번들을 만들 거면 서명·공증까지 같이 가야 한다 — Apple Developer
// 계정이 필요하고, 그건 아직 운영자가 정하지 않은 항목이다.
func registerScheme(_ []string) {
	fmt.Println("맥에서는 persodata:// 를 등록하지 않습니다 — 이 파일을 직접 실행하면 브라우저가 열립니다.")
}

func unregisterScheme() {
	fmt.Println("맥에서는 등록하는 것이 없어 지울 것도 없습니다.")
}
