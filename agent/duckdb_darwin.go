package main

import _ "embed"

// macOS 는 universal 바이너리 하나가 arm64·amd64 를 다 덮으므로 GOARCH 로 안 가릅니다.
//
//go:embed bin/duckdb-darwin-universal.zip
var duckdbZip []byte

const duckdbExeName = "duckdb"
