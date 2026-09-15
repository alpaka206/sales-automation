package main

// **화면은 SQL 을 보내지 못한다.** 이름과 인자만 보내고, 무엇이 나갈지는 여기서 정한다.
// 화면에 닿을 수 있는 누구든(권한을 받아낸 악성 페이지 · 같은 PC 의 다른 프로그램) SQL 을
// 보낼 수 있으면 원본을 그대로 빼낼 수 있다.
//
// `Cols` 를 **적어 두는** 이유: 나갈 컬럼이 코드에 보여야 식별자 검사가 눈으로도 읽힌다.
// 적힌 것과 실제로 나온 것이 다르면 `runMetric` 이 거절한다 — SQL 만 고치고 이 줄을 안
// 고치면 그 자리에서 걸린다.

const (
	subs = "read_csv_auto('{{d}}/perso_payment.subscriber.part*.csv', union_by_name=true)"
	plan = "read_csv_auto('{{d}}/perso_payment.plan.csv')"
	hist = "read_csv_auto('{{d}}/perso_payment.subscription_history.csv', union_by_name=true)"
)

type metric struct {
	Name     string
	Label    string
	Cols     []string
	HasMonth bool
	SQL      string
}

var metrics = []metric{
	{
		Name:  "plan_mix",
		Label: "플랜별 구독 수",
		Cols:  []string{"plan", "status", "subscriptions"},
		SQL: `SELECT coalesce(p.name, '(알 수 없음)') AS plan,
		             s.status                        AS status,
		             count(*)                        AS subscriptions
		      FROM ` + subs + ` s
		      LEFT JOIN ` + plan + ` p ON p.seq = s.plan_seq
		      GROUP BY 1, 2 ORDER BY 3 DESC`,
	},
	{
		Name:  "status_mix",
		Label: "상태별 구독 수",
		Cols:  []string{"status", "subscriptions"},
		SQL:   `SELECT status, count(*) AS subscriptions FROM ` + subs + ` GROUP BY 1 ORDER BY 2 DESC`,
	},
	{
		Name:     "monthly_events",
		Label:    "월별 구독 이벤트",
		Cols:     []string{"month", "event_type", "events"},
		HasMonth: true,
		SQL: `SELECT strftime(try_cast(start_date AS TIMESTAMP), '%Y-%m') AS month,
		             event_type                                          AS event_type,
		             count(*)                                            AS events
		      FROM ` + hist + `
		      WHERE try_cast(start_date AS TIMESTAMP) >= (current_date - INTERVAL '{{months}} months')
		      GROUP BY 1, 2 HAVING month IS NOT NULL ORDER BY 1 DESC, 3 DESC`,
	},
}

func metricByName(name string) *metric {
	for i := range metrics {
		if metrics[i].Name == name {
			return &metrics[i]
		}
	}
	return nil
}

func metricNames() []string {
	out := make([]string, 0, len(metrics))
	for _, m := range metrics {
		out = append(out, m.Name)
	}
	return out
}
