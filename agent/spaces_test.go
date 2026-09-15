package main

import (
	"strings"
	"testing"
)

// 스페이스 목록은 SQL 에 그대로 들어간다 — 숫자 말고는 아무것도 지나면 안 된다.
func TestSpaceListAcceptsOnlyPositiveIntegers(t *testing.T) {
	for _, bad := range []string{"", " ", "abc", "1,x", "-1", "0", "1;DROP", "1 OR 1"} {
		if _, err := parseSpaces(bad); err == nil {
			t.Errorf("%q 가 통과했습니다", bad)
		}
	}
	got, err := parseSpaces(" 3, 1 ,3,2 ")
	if err != nil || len(got) != 3 || got[0] != 3 || got[1] != 1 || got[2] != 2 {
		t.Fatalf("정상 입력이 틀렸습니다: %v %v", got, err)
	}
	many := strings.Repeat("1,", maxSpacesPerCall) + "2"
	if _, err := parseSpaces(many); err != nil {
		t.Fatalf("중복은 하나로 세야 합니다: %v", err)
	}
	var distinct []string
	for i := 1; i <= maxSpacesPerCall+1; i++ {
		distinct = append(distinct, strings.Repeat("7", i%5+1)+strings.Repeat("1", i/5+1))
	}
	if _, err := parseSpaces(strings.Join(distinct, ",")); err == nil {
		t.Fatal("상한을 넘긴 목록이 통과했습니다")
	}
}

// 중첩 결과도 같은 계약을 지난다 — 식별자 키·긴 원문·행 폭발.
func TestNestedContractIsEnforced(t *testing.T) {
	allow := map[string]bool{"space_seq": true}
	ok := []byte(`{"spaces":[{"space_seq":1,"used_total":5,"members":[{"rank":1,"jobs":3}]}],"processing":{"p50":1.5}}`)
	if err := checkContract(ok, allow); err != nil {
		t.Fatalf("정상 결과가 거절됐습니다: %v", err)
	}
	// user_seq 가 세 겹 아래에 있어도 걸린다.
	leak := []byte(`{"a":[{"b":{"members":[{"user_seq":7,"jobs":3}]}}]}`)
	if err := checkContract(leak, allow); err == nil {
		t.Fatal("중첩된 user_seq 가 통과했습니다")
	}
	long := []byte(`{"a":{"title":"` + strings.Repeat("가", maxCellChars+1) + `"}}`)
	if err := checkContract(long, allow); err == nil {
		t.Fatal("긴 셀이 통과했습니다")
	}
	big := "[" + strings.Repeat("1,", maxRows) + "1]"
	if err := checkContract([]byte(`{"a":`+big+`}`), allow); err == nil {
		t.Fatal("행 상한을 넘긴 배열이 통과했습니다")
	}
}

// SQL 이 내는 컬럼 이름은 코드에 적혀 있다 — 식별자 이름을 새로 적으면 여기서 걸린다.
func TestSpaceSQLNamesNoIdentifierExceptSpaceSeq(t *testing.T) {
	for name, sql := range spaceMetrics {
		for _, token := range []string{":= p.user_seq", "user_seq :=", "project_seq :=", "title", "credit_seq :=", "history_seq :="} {
			if strings.Contains(sql, token) {
				t.Errorf("%s 의 SQL 이 식별자를 내보냅니다: %s", name, token)
			}
		}
		if !strings.Contains(sql, "{{spaces}}") || !strings.Contains(sql, "{{asof}}") {
			t.Errorf("%s 의 SQL 이 스페이스 목록이나 기준 시각을 안 씁니다", name)
		}
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
