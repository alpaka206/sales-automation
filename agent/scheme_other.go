//go:build !windows && !darwin

package main

// 리눅스에는 배포하지 않는다 — 이 파일은 GitHub Actions 의 리눅스 러너가 `go vet · go test` 를
// 돌릴 때 컴파일이 되게 하는 빈 구현이다(크로스 빌드는 GOOS 로 windows·darwin 파일을 집는다).
// 처음 태그(agent-v1.1.0)가 이 함수들이 없어서 vet 에서 떨어졌다(2026-09-15).
func registerScheme(_ []string) {}

func unregisterScheme() {}
