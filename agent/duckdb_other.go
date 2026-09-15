//go:build !windows && !darwin

package main

// 리눅스용 DuckDB 는 안 싣는다(배포 대상이 아니다). 러너의 vet · test 가 컴파일되게만 한다 —
// 테스트는 DuckDB 를 실행하지 않는다.
var duckdbZip []byte

const duckdbExeName = "duckdb"
