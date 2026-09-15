package main

import "syscall"

// **더블클릭으로 열린 창은 한국어 윈도우에서 CP949 다.** Go 는 UTF-8 로 쓰므로 안내문이
// 통째로 깨진 글자로 나온다 — 사용자가 처음 보는 화면이 그것이고, 거기 적힌 것이 토큰과
// 주소다. 그래서 창을 UTF-8 로 돌려놓는다. 실패하면 그냥 넘어간다(파이프로 돌릴 때는
// 콘솔이 없다).
func init() {
	proc := syscall.NewLazyDLL("kernel32.dll").NewProc("SetConsoleOutputCP")
	_, _, _ = proc.Call(65001) // CP_UTF8
}
