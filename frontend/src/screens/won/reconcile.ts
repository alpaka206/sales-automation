// 스냅샷 근거로 우리 회차를 **자동으로 완료 처리**한다 — 2026-09-15 운영자 결정
// (「결제날인데 결제가 된 게 들어왔어… 그럼 바뀌는 건 상관없지, 완전 자동으로」).
//
// **이 파일이 스냅샷에서 우리 기록으로 건너가는 유일한 자리다.** 건너가는 것은 회차의
// 완료 여부와 그 날짜뿐이고(`done` · `paid_on` · `granted_by` · `memo`), 스냅샷 원본은 여전히
// 이 PC 를 안 떠난다. `tests/test_agent_stays_local.py` 가 이 파일이 부르는 경로와 보내는
// 칸을 그 목록으로 고정한다 — 다른 무엇을 보내기 시작하면 빨개진다.
//
// **예정일 2일 전부터 본다** (`RULE.leadDays`, 2026-09-15 운영자). 지난 회차는 전부, 그보다
// 먼 회차는 안 본다 — 다음 달 회차의 근거를 지금 찾을 이유가 없다.
//
// 「자동」의 뜻: **수주 고객 화면을 에이전트가 켜진 PC 에서 열면** 그때 맞대어 적용된다.
// 서버는 스냅샷을 볼 수 없으므로(그게 원칙이다) 서버 쪽 스케줄은 없다. 매일 아침 누군가
// 목록을 여는 것으로 충분하다 — 스냅샷도 하루 한 번이다.
import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { postForm } from "../../lib/api";
import type { Contract, Row } from "./shared";
import { mergeEvidence, planReconcile, type ReconcilePlan, type SpaceEvidence } from "./usage";
import type { EvidenceIndex } from "./useUsage";
import { parseSpaceSeqs } from "./usage";

export const AUTO_BY = "스냅샷 자동";

/** 계획 하나를 실제로 쓴다. 회차마다 한 요청 — 실패해도 나머지는 계속한다. */
export async function applyReconcile(plan: ReconcilePlan, contract: Contract, snapshotAt: string): Promise<number> {
  let applied = 0;
  for (const g of plan.grants) {
    const grant = contract.credit_grants.find((x) => x.id === g.id);
    // 운영자가 적어 둔 비고는 남긴다 — 근거를 앞에 붙인다.
    const memo = [`소진 시작 ${g.firstUse} (스냅샷 ${snapshotAt}${g.n > 1 ? ` · 묶음 ${g.n}` : ""})`, grant?.memo]
      .filter(Boolean).join(" · ");
    try {
      await postForm(`/won-customers/credits/${g.id}`, { done: "true", granted_by: AUTO_BY, memo });
      applied += 1;
    } catch { /* 다음 회차 */ }
  }
  for (const p of plan.payments) {
    // 자동으로 닫힌 회차와 사람이 닫은 회차가 화면에서 같아 보이면 안 된다 — 비고에 남긴다.
    const note = [`스냅샷 결제 확인 ${p.paidOn} (스냅샷 ${snapshotAt})`, p.note].filter(Boolean).join(" · ");
    try {
      await postForm(`/won-customers/payments/${p.id}`, { done: "true", paid_on: p.paidOn, note });
      applied += 1;
    } catch { /* 다음 회차 */ }
  }
  return applied;
}

export function planFor(contract: Contract, index: EvidenceIndex): ReconcilePlan {
  const rows = parseSpaceSeqs(contract.space_seq)
    .map((s) => index.bySpace.get(s)).filter((x): x is SpaceEvidence => !!x);
  if (!rows.length) return { grants: [], payments: [] };
  return planReconcile(contract, mergeEvidence(rows), index.snapshotAt);
}

/**
 * 목록·상세가 부르는 훅. 근거가 오면 계약마다 계획을 세워 **한 번** 적용하고 화면을
 * 다시 읽는다. 같은 스냅샷에 대해 두 번 쓰지 않도록 이 세션에서 적용한 (계약, 스냅샷)
 * 쌍을 기억한다 — 서버 쪽은 어차피 `done` 이 이미 참이면 같은 값이라 되풀이해도 무해하다.
 */
const applied = new Set<string>();

export function useAutoReconcile(contracts: Contract[], index: EvidenceIndex | null) {
  const queryClient = useQueryClient();
  const running = useRef(false);
  useEffect(() => {
    if (!index || running.current) return;
    const todo = contracts
      .filter((c) => !applied.has(`${c.id}:${index.snapshotAt}`))
      .map((c) => ({ c, plan: planFor(c, index) }))
      .filter(({ plan }) => plan.grants.length || plan.payments.length);
    for (const c of contracts) applied.add(`${c.id}:${index.snapshotAt}`);
    if (!todo.length) return;
    running.current = true;
    (async () => {
      let n = 0;
      for (const { c, plan } of todo) n += await applyReconcile(plan, c, index.snapshotAt);
      running.current = false;
      if (n) await queryClient.invalidateQueries({ queryKey: ["won-customers"] });
      if (n) await queryClient.invalidateQueries({ queryKey: ["won-customer"] });
    })();
  }, [contracts, index, queryClient]);
}

/** 목록 행에서 계약을 꺼낸다 — 목록 payload 에는 활성 계약 하나만 실려 있다. */
export function contractsOfRows(rows: Row[] | undefined): Contract[] {
  return (rows ?? []).map((r) => r.active).filter((c): c is Contract => !!c);
}
