package main

// 가공 파일의 모양 — DuckDB 없이 가짜 줄로 잰다. 화면(usageMerge.ts · usageData.ts)이 이 모양을 믿고 합친다.

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"
)

func ent(v int64) *int64 { return &v }

// 엔터프라이즈는 enterprise_seq 의 차례(e1, e2 …), 나머지는 space_seq % 16 (p00 ~ p15).
func TestGroupsFollowEnterpriseOrderThenRemainder(t *testing.T) {
	got := groupsOf([]member{
		{Space: 101, Ent: ent(900)}, {Space: 102, Ent: ent(37)}, {Space: 103, Ent: ent(900)},
		{Space: 16, Ent: nil}, {Space: 47, Ent: nil}, {Space: 5, Ent: nil},
	})
	want := map[int64]string{101: "e2", 102: "e1", 103: "e2", 16: "p00", 47: "p15", 5: "p05"}
	for s, g := range want {
		if got[s] != g {
			t.Errorf("space %d → %q, 기대 %q", s, got[s], g)
		}
	}
	if len(got) != len(want) {
		t.Fatalf("스페이스 수가 다릅니다: %v", got)
	}
}

// 가공 범위에는 엔터프라이즈 크레딧을 쓴 스페이스도 든다 — 계약이 끝나 플랜이 무료로 돌아간 B2B 스페이스가
// 빠지면 그 계약의 화면이 「가공 범위에 없음」으로 멈춘다. SQL 이라 DuckDB 가 있을 때만 돈다(윈도우·맥 빌드는
// 실린 것, 리눅스는 PATH 의 duckdb — CI 에는 없어 건너뛴다). 재료는 작은 가짜 CSV 다.
func TestUniverseKeepsSpacesThatUsedEnterpriseCredits(t *testing.T) {
	duck, err := ensureDuckDB()
	if err != nil {
		t.Skip("DuckDB 가 없습니다: ", err)
	}
	data := filepath.Join(t.TempDir(), "data")
	if err := os.MkdirAll(data, 0o755); err != nil {
		t.Fatal(err)
	}
	for name, body := range map[string]string{
		"perso.enterprise_space_association.csv":          "enterprise_seq,space_seq\n10,1\n",
		"perso_payment.subscriber.part1.csv":              "space_seq,plan_seq\n2,100\n3,101\n",
		"perso_payment.plan.csv":                          "seq,priority_tier\n100,PRO\n101,FREE\n",
		"perso_video_translator.credit_usage_history.csv": "space_seq,credit_history_seq\n4,500\n5,501\n",
		"perso_payment.space_credit_usage_history.csv":    "history_seq,earn_type\n500,enterprise\n501,subscription\n",
	} {
		if err := os.WriteFile(filepath.Join(data, name), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	members, err := (&Snapshot{Repo: filepath.Dir(data), Data: data, duckdb: duck}).universe()
	if err != nil {
		t.Fatal(err)
	}
	got := map[int64]string{}
	for _, m := range members {
		got[m.Space] = "-"
		if m.Ent != nil {
			got[m.Space] = strconv.FormatInt(*m.Ent, 10)
		}
	}
	// 1 엔터프라이즈 · 2 유료 플랜 · 4 엔터프라이즈 크레딧을 썼다. 3 은 무료 플랜, 5 는 엔터프라이즈가 아닌 크레딧만.
	want := map[int64]string{1: "10", 2: "-", 4: "-"}
	if len(got) != len(want) || got[1] != want[1] || got[2] != want[2] || got[4] != want[4] {
		t.Fatalf("가공 범위가 다릅니다: %v (기대 %v)", got, want)
	}
}

func raw(s string) json.RawMessage { return json.RawMessage(s) }

func TestFactsFilesHoldEverySpaceOnceInOrder(t *testing.T) {
	groups := map[int64]string{30: "e1", 10: "e1", 20: "p04"}
	facts := map[int64]json.RawMessage{10: raw(`{"x":10}`), 20: raw(`{"x":20}`), 30: raw(`{"x":30}`)}
	files, err := assembleFacts(groups, facts, facts, facts)
	if err != nil {
		t.Fatal(err)
	}
	enc, err := encode(files)
	if err != nil {
		t.Fatal(err)
	}
	keys := make([]string, 0, len(enc))
	for k := range enc {
		keys = append(keys, k)
	}
	slices.Sort(keys)
	if strings.Join(keys, ",") != "facts/e1.json,facts/index.json,facts/p04.json" {
		t.Fatalf("파일 목록이 다릅니다: %v", keys)
	}
	var index map[string]string
	if err := json.Unmarshal(enc["facts/index.json"], &index); err != nil || len(index) != 3 || index["10"] != "e1" || index["20"] != "p04" {
		t.Fatalf("index 가 틀렸습니다: %v %v", index, err)
	}
	var e1 struct {
		Format int    `json:"format"`
		Group  string `json:"group"`
		Spaces []struct {
			SpaceSeq int64           `json:"space_seq"`
			Credits  json.RawMessage `json:"credits"`
			Jobs     json.RawMessage `json:"jobs"`
			Usage    json.RawMessage `json:"usage"`
		} `json:"spaces"`
	}
	if err := json.Unmarshal(enc["facts/e1.json"], &e1); err != nil {
		t.Fatal(err)
	}
	if e1.Format != 1 || e1.Group != "e1" || len(e1.Spaces) != 2 || e1.Spaces[0].SpaceSeq != 10 || e1.Spaces[1].SpaceSeq != 30 {
		t.Fatalf("묶음 파일이 틀렸습니다 (space_seq 순이어야 합니다): %+v", e1)
	}
	if string(e1.Spaces[1].Usage) != `{"x":30}` {
		t.Fatalf("재료가 그 스페이스 것이 아닙니다: %s", e1.Spaces[1].Usage)
	}
}

// 한 스페이스라도 재료가 빠지면 파일을 만들지 않는다 — 빈 칸을 「활동 없음」으로 읽으면 틀린 숫자가 된다.
func TestMissingFactsAreRefused(t *testing.T) {
	groups := map[int64]string{1: "p01", 2: "p02"}
	full := map[int64]json.RawMessage{1: raw(`{}`), 2: raw(`{}`)}
	part := map[int64]json.RawMessage{1: raw(`{}`)}
	if _, err := assembleFacts(groups, full, part, full); err == nil {
		t.Fatal("재료가 빠졌는데 통과했습니다")
	}
}

func TestEvidenceKeepsOnlySpacesWithSomething(t *testing.T) {
	row := raw(`{"spaces":[{"space_seq":1,"buckets":null,"payments":null},
	                       {"space_seq":2,"buckets":[{"n":1}],"payments":null},
	                       {"space_seq":3,"buckets":null,"payments":[{"status":"paid"}]}]}`)
	v, err := keepEvidence(row)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := json.Marshal(v)
	var got struct {
		Spaces []struct {
			SpaceSeq int64 `json:"space_seq"`
		} `json:"spaces"`
	}
	if err := json.Unmarshal(body, &got); err != nil || len(got.Spaces) != 2 || got.Spaces[0].SpaceSeq != 2 || got.Spaces[1].SpaceSeq != 3 {
		t.Fatalf("근거가 있는 스페이스만 남아야 합니다: %s", body)
	}
	empty, _ := keepEvidence(raw(`{"spaces":null}`))
	if b, _ := json.Marshal(empty); string(b) != `{"spaces":[]}` {
		t.Fatalf("근거가 없으면 빈 배열입니다: %s", b)
	}
}

// 화면이 읽는 manifest — 칸 일곱 개, 더도 덜도 없다.
func TestManifestCarriesExactlyTheAgreedFields(t *testing.T) {
	at := time.Date(2026, 10, 1, 0, 0, 0, 0, time.UTC)
	body, err := json.Marshal(manifest{Format: 1, SnapshotAt: at.Format(time.RFC3339), SnapshotCommit: "abc1234",
		ExportedAt: at.Add(15 * time.Minute).Format(time.RFC3339), Exporter: "dev", Spaces: 2240, Groups: 135})
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(body, &m); err != nil {
		t.Fatal(err)
	}
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	slices.Sort(keys)
	if strings.Join(keys, ",") != "exported_at,exporter,format,groups,snapshot_at,snapshot_commit,spaces" {
		t.Fatalf("manifest 칸이 다릅니다: %v", keys)
	}
	if m["format"] != float64(1) || m["snapshot_at"] != "2026-10-01T00:00:00Z" || m["spaces"] != float64(2240) {
		t.Fatalf("manifest 값이 다릅니다: %s", body)
	}
	if err := checkContract(body, allowKeys); err != nil {
		t.Fatalf("manifest 가 계약에 걸립니다: %v", err)
	}
}

// 자리표시가 하나라도 남으면 DuckDB 가 그 글자를 그대로 SQL 로 읽는다.
func TestEveryTemplateIsFullyFilled(t *testing.T) {
	at := time.Date(2026, 9, 30, 0, 0, 7, 0, time.UTC)
	for name, tpl := range map[string]string{
		"universe": universeSQL, "summary": summarySQL, "evidence": evidenceSQL, "sales": salesSQL,
		"shared": sharedHistorySQL, "credits": creditsFactsSQL, "jobs": jobsFactsSQL, "usage": usageFactsSQL,
	} {
		// 윈도우 경로(\)도 SQL 안에서는 / 여야 한다 — DuckDB 문자열에서 \ 는 그대로 남지만 사람이 읽기 어렵다.
		sql := fill(tpl, filepath.Join(t.TempDir(), "data"), []int64{3, 1, 2}, at, "00ff")
		if strings.Contains(sql, "{{") {
			t.Errorf("%s 에 채우지 않은 자리표시가 남았습니다", name)
		}
		if strings.Contains(sql, `\`) {
			t.Errorf("%s 의 경로가 / 로 바뀌지 않았습니다", name)
		}
	}
	sql := fill(spacesCTE, "/d", []int64{3, 1, 2}, at, "")
	if !strings.Contains(sql, "unnest([3,1,2]::BIGINT[])") || !strings.Contains(sql, "CAST('2026-09-30 00:00:07' AS TIMESTAMP)") {
		t.Fatalf("목록·기준 시각이 틀리게 들어갔습니다:\n%s", sql)
	}
}

// 소금은 가공마다 새것 — 같은 사람의 u 가 날마다 달라야 한다.
func TestSaltIsFreshHexEachExport(t *testing.T) {
	a, b := newSalt(), newSalt()
	if a == b || !regexp.MustCompile(`^[0-9a-f]{32}$`).MatchString(a) {
		t.Fatalf("소금이 16바이트 16진이 아니거나 되풀이됩니다: %q %q", a, b)
	}
}

func TestEncodeRefusesContractBreaks(t *testing.T) {
	if _, err := encode(map[string]any{"facts/p00.json": map[string]any{"spaces": []any{map[string]any{"user_seq": 7}}}}); err == nil {
		t.Fatal("식별자 키가 있는 파일이 통과했습니다")
	}
}

// 다 쓰거나 아무것도 안 남거나. 이미 있는 폴더는 건드리지 않는다.
func TestPublishIsAllOrNothing(t *testing.T) {
	dir := t.TempDir()
	out := filepath.Join(dir, "out")
	files := map[string][]byte{"manifest.json": []byte(`{}`), "facts/p00.json": []byte(`{}`)}
	if err := publish(out, files); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(out, "facts", "p00.json")); err != nil {
		t.Fatalf("파일이 없습니다: %v", err)
	}
	if _, err := os.Stat(out + ".tmp"); !os.IsNotExist(err) {
		t.Fatal("임시 폴더가 남았습니다")
	}
	if err := publish(out, files); err == nil {
		t.Fatal("이미 있는 폴더를 덮었습니다")
	}
	// 쓰다 실패하면(여기서는 파일 자리에 폴더가 있다) 임시 폴더째 지운다.
	bad := filepath.Join(dir, "bad")
	if err := publish(bad, map[string][]byte{"a": []byte("1"), "a/b": []byte("2")}); err == nil {
		t.Fatal("실패해야 합니다")
	}
	for _, p := range []string{bad, bad + ".tmp"} {
		if _, err := os.Stat(p); !os.IsNotExist(err) {
			t.Fatalf("실패한 뒤에 %s 가 남았습니다", p)
		}
	}
}
