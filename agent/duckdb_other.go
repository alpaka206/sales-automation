//go:build !windows && !darwin

package main

// 리눅스용 DuckDB 는 안 싣는다. 가공 워크플로가 snapshot.go 의 duckdbVersion 을 받아 체크섬
// (duckdb-linux-amd64.sha256)을 맞춘 뒤 --duckdb 로 넘긴다 — 비어 있으면 ensureDuckDB 가 PATH 를 본다.
var duckdbZip []byte

const duckdbExeName = "duckdb"
