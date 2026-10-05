package main

import (
	"strings"
	"testing"
)

// 파일 하나를 통째로 걸으며 같은 계약을 잰다 — 식별자 키·긴 원문·행 폭발이 몇 겹 아래에 있어도.
func TestNestedContractIsEnforced(t *testing.T) {
	ok := []byte(`{"spaces":[{"space_seq":1,"usage":{"users_30d":[{"u":1,"jobs":3}]},"records":[{"b":1,"p":2,"pf":3}]}]}`)
	if err := checkContract(ok, allowKeys); err != nil {
		t.Fatalf("정상 결과가 거절됐습니다: %v", err)
	}
	// user_seq 가 세 겹 아래에 있어도 걸린다.
	leak := []byte(`{"a":[{"b":{"members":[{"user_seq":7,"jobs":3}]}}]}`)
	if err := checkContract(leak, allowKeys); err == nil {
		t.Fatal("중첩된 user_seq 가 통과했습니다")
	}
	long := []byte(`{"a":{"title":"` + strings.Repeat("가", maxCellChars+1) + `"}}`)
	if err := checkContract(long, allowKeys); err == nil {
		t.Fatal("긴 셀이 통과했습니다")
	}
}

// 가공 파일의 배열 상한은 HTTP 시절(2,000)보다 크다 — 큰 엔터프라이즈 묶음의 작업 기록이 통째로 실린다.
// 그래도 상한은 있다.
func TestExportArraysAreBoundedAtTheExportLimit(t *testing.T) {
	arr := func(n int) []byte { return []byte(`{"a":[` + strings.TrimSuffix(strings.Repeat("1,", n), ",") + `]}`) }
	if err := checkContract(arr(maxRowsExport), allowKeys); err != nil {
		t.Fatalf("상한까지는 통과해야 합니다: %v", err)
	}
	if err := checkContract(arr(maxRowsExport+1), allowKeys); err == nil {
		t.Fatal("상한을 넘긴 배열이 통과했습니다")
	}
}

// SQL 이 내는 이름은 코드에 적혀 있다 — 식별자 이름을 새로 적으면 여기서 걸린다.
func TestSpaceSQLNamesNoIdentifierExceptSpaceSeq(t *testing.T) {
	for name, sql := range map[string]string{
		"summary": summarySQL, "evidence": evidenceSQL,
		"credits": creditsFactsSQL, "jobs": jobsFactsSQL, "usage": usageFactsSQL,
	} {
		for _, token := range []string{":= p.user_seq", "user_seq :=", "project_seq :=", "title", "credit_seq :=", "history_seq :="} {
			if strings.Contains(sql, token) {
				t.Errorf("%s 의 SQL 이 식별자를 내보냅니다: %s", name, token)
			}
		}
		if !strings.Contains(sql, "{{spaces}}") || !strings.Contains(sql, "{{asof}}") {
			t.Errorf("%s 의 SQL 이 스페이스 목록이나 기준 시각을 안 씁니다", name)
		}
	}
	// 사람은 소금 친 차례 번호(u)로만 나간다.
	if !strings.Contains(usageFactsSQL, "'{{salt}}'") {
		t.Error("사용 구성 SQL 이 사람 번호를 소금 없이 매깁니다")
	}
}

func TestSalesSQLNamesNoIdentifierExceptSpaceSeq(t *testing.T) {
	for _, token := range []string{":= p.user_seq", "user_seq :=", "project_seq :=", "title", "credit_seq :=", "history_seq :=", "plan_seq :=", "owner_seq :="} {
		if strings.Contains(salesSQL, token) {
			t.Errorf("영업 인사이트 SQL 이 식별자를 내보냅니다: %s", token)
		}
	}
	if !strings.Contains(salesSQL, "{{asof}}") {
		t.Errorf("영업 인사이트 SQL 이 기준 시각을 안 씁니다")
	}
}
