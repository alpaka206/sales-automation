// PERSO 사용 데이터 가공기 (2026-10-01).
//
// 스냅샷(비공개 저장소의 CSV)을 DuckDB 로 읽어 **집계만** JSON 파일로 낸다. 예전에는 운영자 PC 마다 로컬
// 에이전트(127.0.0.1 HTTP 서버)를 띄워 화면이 그때그때 물었다 — 설치·업데이트·스냅샷 받기가 사람마다 따로
// 깨졌다. 이제는 하루 한 번 비공개 가공 레포의 GitHub Actions 가 이 프로그램을 **네트워크 없이**(unshare -n)
// 돌려 그 레포의 `data` 브랜치에 올리고(export/perso-usage-data.yml), 콘솔 화면이 GitHub 에서 직접 받는다.
// 우리 서버는 그 데이터를 거치지 않는다.
//
// 돌리는 법: perso-export --snapshot <스냅샷 폴더> --out <새 폴더> [--duckdb <DuckDB CLI>]
// 이 프로그램에는 요청을 보내는 코드도 받는 코드도 없다(tests/test_usage_data_stays_off_server.py 가 고정한다).
package main

import (
	"flag"
	"fmt"
	"log"
	"os"
)

// 빌드가 박는다: `go build -ldflags "-X main.version=<sha>"`. 가공 워크플로가 공개 저장소의 짧은 커밋을
// 넣고, manifest.json 의 exporter 가 이 값이다 — 그날 데이터가 어느 코드로 만들어졌는지 남는다.
var version = "dev"

func main() {
	log.SetFlags(log.Ltime)
	snapshot := flag.String("snapshot", "", "스냅샷 폴더 (data/manifest.json 이 있는 곳)")
	out := flag.String("out", "", "결과를 쓸 폴더 — 아직 없어야 한다 (다 쓴 뒤 이 이름으로 옮긴다)")
	duck := flag.String("duckdb", "", "DuckDB CLI 경로 (리눅스는 이것 또는 PATH 의 duckdb, 윈도우·맥은 비우면 실린 것)")
	showVersion := flag.Bool("version", false, "버전만 찍고 끝낸다")
	flag.Parse()
	if *showVersion {
		fmt.Println("perso-export " + version)
		return
	}
	if *snapshot == "" || *out == "" || flag.NArg() > 0 {
		fmt.Fprintln(os.Stderr, "쓰는 법: perso-export --snapshot DIR --out DIR [--duckdb PATH]")
		os.Exit(2)
	}
	snap, err := NewSnapshot(*snapshot, *duck)
	if err != nil {
		log.Fatal(err)
	}
	if err := export(snap, *out); err != nil {
		log.Fatalf("가공 실패 — 아무것도 쓰지 않았습니다: %v", err)
	}
}
