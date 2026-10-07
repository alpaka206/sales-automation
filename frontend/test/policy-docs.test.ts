import { expect, it } from "vitest";
import { HUMAN_ONLY, initialPlacement, placementGroups } from "../src/screens/PolicyDocs";

// 서버의 고르개(`policy_docs.PLACEMENTS`)와 같은 모양 — 「문의별 참고」(knowledge)는 2026-09-10 에 빠졌습니다.
const PLACEMENTS = [
  { key: "rules_all", label: "모든 회신에 적용" },
  { key: "rules_first", label: "첫 회신에만" },
  { key: "rules_followup", label: "그 이후 회신에" },
];

it("a new document starts on the first placement the server accepts", () => {
  // `"knowledge"` 가 박혀 있던 동안 고르개를 안 건드린 새 문서는 400 「모르는 값입니다」였습니다.
  expect(initialPlacement(null, PLACEMENTS)).toBe("rules_all");
  expect(initialPlacement(null, [])).toBe("");
});

it("an existing document keeps its placement even when the picker does not offer it", () => {
  // 바꾸면 그 문서를 연 순간 「바뀜」이 되어, 제목만 고친 저장이 문서를 옮기거나 400 이 됩니다.
  expect(initialPlacement({ placement: "rules_first" }, PLACEMENTS)).toBe("rules_first");
  expect(initialPlacement({ placement: HUMAN_ONLY }, PLACEMENTS)).toBe(HUMAN_ONLY);
  expect(initialPlacement({ placement: "" }, PLACEMENTS)).toBe("");
});

it("every row lands in exactly one group — people-only and unknown placements stay visible", () => {
  const rows = [
    { id: 1, placement: "rules_all" },
    { id: 2, placement: HUMAN_ONLY },
    { id: 3, placement: "" },
    { id: 4, placement: "knowledge_first" }, // 서버가 모르는 이름을 줘도 사라지지 않습니다
    { id: 5, placement: "rules_all" },
  ];
  const groups = placementGroups(PLACEMENTS, rows);

  expect(groups.map((group) => [group.key, group.rows.map((row) => row.id)])).toEqual([
    ["rules_all", [1, 5]],
    [HUMAN_ONLY, [2]],
    ["", [3, 4]],
  ]);
  expect(groups.flatMap((group) => group.rows)).toHaveLength(rows.length);
});
