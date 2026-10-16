// 영업 인사이트의 메일 초안 — 서버에 저장된 기본 문구로 **조립만** 합니다 (2026-10-16 운영자: 「메일 초안 문구를 영업
// 인사이트 화면에서 고칠 수 있게」 — 그 전에는 문구가 이 파일에 박혀 있었고 고치려면 배포가 필요했다).
//
// 문구는 서버의 `src/db/insight_drafts.py`(기본 문구 · 칸의 목록)와 표 `insight_drafts`(고친 칸)에 있고, 화면은
// `GET /api/ui/insight-drafts` 로 받습니다(바깥 겹이 받아 내려 준다). 이 파일은 글을 들고 있지 않습니다 — **어느 근거
// 문단을 고를지**와 **자리표시를 채우는 것**만 합니다:
//
// - 카드마다 제목 · 공통 문단(인사 · `{근거}` · 맺음말의 틀) · 근거별 문단. 그 곳의 상황으로 근거 문단 하나를 골라
//   `{근거}` 자리에 넣습니다(`reasonFor`).
// - `{고객명}` — 이탈 방지 · 추가 계약은 회사 이름, 업셀링은 「Space 번호」. 카드 공통 · 여러 곳 초안은 글자 그대로 두고,
//   여러 곳 초안은 복사할 때 곳마다 바꿉니다(`MailDraft.tsx`).
//
// **메일은 보내지 않습니다 — 초안을 만들고 복사만 합니다** (운영자 조건 3). 받는 사람에게 가는 길은 사람이 복사해 자기 메일
// 프로그램에 붙여 넣는 것 하나입니다.
import type { Rules } from "../won/usage";
import type { ContractRow, GrowRow, Tab, UpRow } from "./judge";

export type Lang = "ko" | "en";
export type Draft = { subject: string; body: string };
/** 받는 곳 이름이 들어갈 자리. */
export const NAME_SLOT = "{고객명}";
/** 근거 문단이 들어갈 자리 — 공통 문단에 한 번(서버가 저장할 때 검사한다). */
export const REASON_SLOT = "{근거}";

/** 서버가 준 문구의 한 칸(`lib/insightDrafts.ts` 의 DraftPart 중 조립에 쓰는 칸). */
export type TextPart = { tab: Tab; lang: Lang; part: string; value: string };
/** `<카드>.<언어>.<부분>` → 지금 문구. */
export type Templates = Record<string, string>;

export function templatesFrom(parts: TextPart[] | null | undefined): Templates | null {
  if (!parts?.length) return null;
  return Object.fromEntries(parts.map((p) => [`${p.tab}.${p.lang}.${p.part}`, p.value]));
}

/** 어느 근거 문단을 쓸까 — 줄이 없으면(카드 공통 · 여러 곳) 언제나 `gen`.
 *  - 업셀링: 한 곳이면 `near`(PRO 한도에 가깝게).
 *  - 이탈 방지: 무활동이 「위험 높음 = 무활동 N일」 기준 이상이거나 기록이 없으면 `idle`, 아니면 `under`(크레딧 여유).
 *    목업은 「한 달」을 30일로 박아 두었다 — 여기는 그 기준과 같은 값을 본다.
 *  - 추가 계약: 부족 예상 · 초과사용이면 `short`, 계약 종료가 기준 이내면 `renew`, 아니면 `gen`. */
export function reasonFor(tab: Tab, row: UpRow | ContractRow | null, rules: Pick<Rules, "churnIdleDays" | "renewalDays">): string {
  if (!row) return "gen";
  if (tab === "up") return "near";
  const c = row as ContractRow;
  if (tab === "churn") return c.daysIdle === null || c.daysIdle >= rules.churnIdleDays ? "idle" : "under";
  const g = row as GrowRow;
  if (g.forecast === "부족 예상" || g.level === "초과사용") return "short";
  return g.daysLeft !== null && g.daysLeft >= 0 && g.daysLeft <= rules.renewalDays ? "renew" : "gen";
}

/** 여러 곳 초안의 곳 이름 — 목업의 name_(업셀링은 「Space N」, 수주 고객은 회사 이름). */
export const placeName = (row: UpRow | ContractRow) => ("space_seq" in row ? `Space ${row.space_seq}` : row.company);

/** 이름 자리를 채운다 — 줄이 없으면 그대로 둔다(여러 곳 초안은 복사할 때 곳마다 채운다). */
export const fillName = (text: string, row: UpRow | ContractRow | null) =>
  (row ? text.split(NAME_SLOT).join(placeName(row)) : text);

/** 그 카드 · 언어의 초안 — 줄을 주면 그 곳의 근거에 맞춘 초안, null 이면 그 카드의 공통 초안. 문구가 없으면 null. */
export function draftFor(tab: Tab, row: UpRow | ContractRow | null, lang: Lang, rules: Rules,
                         templates: Templates | null): Draft | null {
  const text = (part: string) => templates?.[`${tab}.${lang}.${part}`];
  const subject = text("subject");
  const body = text("body");
  const reason = text(`reason.${reasonFor(tab, row, rules)}`);
  if (subject === undefined || body === undefined || reason === undefined) return null;
  return { subject: fillName(subject, row), body: fillName(body.split(REASON_SLOT).join(reason), row) };
}
