package main

// **맥에서 `persodata://` 는 앱 번들의 Info.plist 가 등록한다** (`CFBundleURLSchemes`,
// `agent-release.yml` 의 「Mac 앱 번들」). Launch Services 는 번들 단위로만 스킴을 읽으므로 코드가
// 할 일이 없다 — 앱을 한 번 열면 그 뒤로 콘솔의 「에이전트 열기」가 앱을 띄운다. 맨 실행 파일로
// 돌릴 때는(터미널) 등록되는 것이 없다.
func registerScheme(_ []string) {}

func unregisterScheme() {}
