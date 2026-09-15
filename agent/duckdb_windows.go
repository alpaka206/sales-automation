package main

import _ "embed"

//go:embed bin/duckdb-windows-amd64.zip
var duckdbZip []byte

const duckdbExeName = "duckdb.exe"
