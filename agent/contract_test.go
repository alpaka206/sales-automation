package main

// 출력 계약은 **원본이 새는지 안 새는지를 가르는 자리**다. 나머지는 틀리면 화면이
// 비지만, 이 함수가 틀리면 화면에 원본이 그려지고 그건 되돌릴 수 없다.

import (
	"encoding/json"
	"strings"
	"testing"
)

func row(pairs ...string) map[string]json.RawMessage {
	m := map[string]json.RawMessage{}
	for i := 0; i+1 < len(pairs); i += 2 {
		m[pairs[i]] = json.RawMessage(pairs[i+1])
	}
	return m
}

func TestSmallGroupsAreDropped(t *testing.T) {
	// 관측치가 minGroup 미만인 그룹은 사실상 한 사람을 가리킨다.
	raw := []map[string]json.RawMessage{
		row("status", `"trialing"`, "n", `583142`),
		row("status", `"canceled"`, "n", `3`),
	}
	out, suppressed, err := applyContract([]string{"status", "n"}, raw)
	if err != nil {
		t.Fatal(err)
	}
	if suppressed != 1 || len(out) != 1 {
		t.Fatalf("작은 그룹이 안 걸렸습니다: out=%v suppressed=%d", out, suppressed)
	}
	if out[0][0] != "trialing" {
		t.Fatalf("컬럼 순서가 선언과 다릅니다: %v", out[0])
	}
}

func TestARowExplosionIsRefused(t *testing.T) {
	// 집계가 2,000행을 넘으면 그건 집계가 아니라 원본이다.
	raw := make([]map[string]json.RawMessage, maxRows+1)
	for i := range raw {
		raw[i] = row("k", `"x"`, "n", `9`)
	}
	if _, _, err := applyContract([]string{"k", "n"}, raw); err == nil {
		t.Fatal("행 상한을 넘겼는데 통과했습니다")
	}
}

func TestALongCellIsRefused(t *testing.T) {
	// 프로젝트 제목·파일명이 셀 하나로 나가는 길을 막는다.
	long := `"` + strings.Repeat("가", maxCellChars+1) + `"`
	if _, _, err := applyContract([]string{"title", "n"},
		[]map[string]json.RawMessage{row("title", long, "n", `9`)}); err == nil {
		t.Fatal("긴 셀이 통과했습니다")
	}
}

func TestUndeclaredColumnsAreRefused(t *testing.T) {
	// SQL 만 고치고 `Cols` 를 안 고치면 안 적힌 컬럼이 조용히 나간다 — 여기서 걸린다.
	raw := []map[string]json.RawMessage{row("status", `"a"`, "n", `9`, "user_seq", `7`)}
	if _, _, err := applyContract([]string{"status", "n"}, raw); err == nil {
		t.Fatal("선언에 없는 컬럼이 통과했습니다")
	}
	if _, _, err := applyContract([]string{"status", "missing"},
		[]map[string]json.RawMessage{row("status", `"a"`, "n", `9`)}); err == nil {
		t.Fatal("선언한 컬럼이 없는데 통과했습니다")
	}
}

func TestEveryMetricDeclaresNonIdentifierColumns(t *testing.T) {
	// 지표를 새로 넣는 사람이 식별자 컬럼을 적으면 그 자리에서 걸려야 한다.
	for _, m := range metrics {
		for _, c := range m.Cols {
			if idLike.MatchString(c) {
				t.Errorf("%s 의 컬럼 %q 가 식별자성입니다", m.Name, c)
			}
		}
		if !strings.Contains(m.SQL, "{{d}}") {
			t.Errorf("%s 의 SQL 이 스냅샷 폴더를 안 씁니다", m.Name)
		}
		if strings.Contains(m.SQL, "{{months}}") != m.HasMonth {
			t.Errorf("%s 의 months 인자 선언이 SQL 과 안 맞습니다", m.Name)
		}
	}
}
