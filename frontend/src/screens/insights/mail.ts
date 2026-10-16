// 영업 인사이트의 메일 초안 문구 — 목업(`영업인사이트.html` 의 P.*.mail) 그대로 (2026-10-16 운영자 확인 ④: 「이번에는
// 목업 문구 그대로 코드에」 — 고치려면 배포가 필요하다. 「정책 · 템플릿」 화면에서 고치게 하는 것은 다음 일이다).
//
// **메일은 보내지 않습니다 — 초안을 만들고 복사만 합니다** (운영자 조건 3: 「메일은 절대 보내지 마. 초안 만들고
// 복사만」). 이 파일은 글자를 만들기만 하고, 복사는 창(`MailDraft.tsx`)이 브라우저 클립보드로 합니다. 받는 사람에게
// 가는 길은 사람이 복사해 자기 메일 프로그램에 붙여 넣는 것 하나입니다.
//
// 문구에 사용량 숫자는 넣지 않습니다 — 「한도에 가깝다」 · 「여유 있다」처럼 말로만. 확인 못 한 것 하나: 업셀링 문구의
// Business 플랜 혜택(월 크레딧이 더 크고 · 우선 처리 · 전담 매니저)이 지금 요금제와 맞는지.
import type { Rules } from "../won/usage";
import type { ChurnRow, ContractRow, GrowRow, Tab, UpRow } from "./judge";

export type Lang = "ko" | "en";
export type Draft = { subject: string; body: string };
/** 받는 곳 이름이 들어갈 자리 — 여러 곳 초안은 복사할 때 곳마다 바뀝니다. */
export const NAME_SLOT = "{고객명}";

const SUBJECT: Record<Tab, Record<Lang, string>> = {
  up: { ko: "[Perso AI] Business 플랜 안내드립니다", en: "Getting more out of Perso AI Dubbing" },
  churn: { ko: "[Perso AI] 더빙 이용 현황 안내드립니다", en: "[Perso AI] Checking in on your dubbing usage" },
  grow: { ko: "[Perso AI] 크레딧 추가·계약 갱신 안내", en: "[Perso AI] Additional credits and renewal options" },
};

function upBody(row: UpRow | null, lang: Lang): string {
  const near = !!row;
  if (lang === "ko") {
    return `안녕하세요,\nPerso AI 더빙 팀입니다.\n\n${near
      ? "최근 PRO 플랜의 월 한도에 가깝게 이용하고 계신 것 같아 연락드립니다."
      : "최근 Perso AI 더빙을 활발히 이용하고 계신 것 같아 연락드립니다."}\n\n이 흐름이 계속된다면 Business 플랜이 더 잘 맞을 수 있습니다. 월 크레딧이 더 크고, 우선 처리와 전담 매니저가 제공됩니다. 간단한 온라인 미팅으로 업무 흐름에 맞는지 함께 살펴볼 수 있을까요?\n\n감사합니다.\nPerso AI 팀 드림`;
  }
  return `Hi,\n\nThank you for using Perso AI Dubbing.${near
    ? " It looks like your workspace has been running close to the monthly limit of the PRO plan recently."
    : " It looks like your workspace has been using Perso AI Dubbing heavily recently."}\n\nIf you expect this pace to continue, our Business plan may be a better fit: larger monthly credits, priority processing and a dedicated manager. Would you be open to a short call to see whether it suits your workflow?\n\nBest regards,\nPerso AI Team`;
}

const CHURN_LINE: Record<"idle" | "under" | "gen", Record<Lang, string>> = {
  idle: {
    ko: "최근 한 달 넘게 더빙 이용이 없으신 것 같아 연락드립니다. 사용 중 불편한 점이나 막히는 부분이 있으셨는지 궁금합니다.",
    en: "We noticed there hasn't been any dubbing activity in your workspace for over a month. We'd love to know if anything got in the way or if there's something we can help with.",
  },
  under: {
    ko: "계약 기간에 비해 크레딧 사용이 여유 있으신 것 같아 연락드립니다. 남은 크레딧을 잘 활용하실 수 있도록 도와드리고 싶습니다.",
    en: "It looks like you still have a good amount of contract credits available relative to the contract period. We'd be glad to help you make the most of them.",
  },
  gen: {
    ko: "최근 이용 현황을 확인하다가 연락드립니다. 사용 중 불편한 점이나 도움이 필요한 부분이 있으시면 편하게 말씀해 주세요.",
    en: "We were reviewing recent usage and wanted to check in. If there's anything we can help with, please let us know.",
  },
};

function churnBody(row: ChurnRow | null, lang: Lang, rules: Pick<Rules, "churnIdleDays">): string {
  // 목업은 「한 달」을 30일로 박아 두었다 — 여기는 「위험 높음 = 무활동 N일」 기준과 같은 값을 본다.
  const kind = !row ? "gen" : row.daysIdle === null || row.daysIdle >= rules.churnIdleDays ? "idle" : "under";
  const name = row?.company ?? NAME_SLOT;
  if (lang === "ko") {
    return `안녕하세요, ${name} 담당자님.\nPerso AI 더빙 팀입니다.\n\n${CHURN_LINE[kind].ko}\n\n필요하시면 활용 방법 안내나 간단한 온라인 미팅도 가능합니다. 편하신 시간을 알려주시면 연락드리겠습니다.\n\n감사합니다.\nPerso AI 팀 드림`;
  }
  return `Hi ${name} team,\n\nThis is the Perso AI Dubbing team.\n\n${CHURN_LINE[kind].en}\n\nWe're happy to walk you through best practices or set up a short call. Just let us know a time that works for you.\n\nBest regards,\nPerso AI Team`;
}

const GROW_LINE: Record<"short" | "renew" | "gen", Record<Lang, string>> = {
  short: {
    ko: "최근 이용량이 늘어 계약 크레딧이 예정보다 일찍 소진될 것으로 보입니다. 작업이 중단되지 않도록 추가 크레딧이나 계약 조정 방안을 미리 안내드리고자 합니다.",
    en: "Your usage has grown recently, and it looks like your contract credits may run out earlier than planned. To avoid any interruption, we'd like to share options for additional credits or a contract adjustment.",
  },
  renew: {
    ko: "계약 종료일이 다가오고 있어 갱신 조건을 미리 안내드리고자 합니다. 다음 기간에 필요한 분량이 달라졌다면 함께 조정해 드리겠습니다.",
    en: "Your contract end date is approaching, so we'd like to share renewal options ahead of time. If your needs for the next period have changed, we can adjust accordingly.",
  },
  gen: {
    ko: "계약 현황을 확인하다가 연락드립니다. 추가 크레딧이나 갱신 조건을 안내드리고자 합니다.",
    en: "We were reviewing your contract and wanted to share options for additional credits or renewal.",
  },
};

function growBody(row: GrowRow | null, lang: Lang, rules: Pick<Rules, "renewalDays">): string {
  const kind = !row ? "gen"
    : row.forecast === "부족 예상" || row.level === "초과사용" ? "short"
    : row.daysLeft !== null && row.daysLeft >= 0 && row.daysLeft <= rules.renewalDays ? "renew" : "gen";
  const name = row?.company ?? NAME_SLOT;
  if (lang === "ko") {
    return `안녕하세요, ${name} 담당자님.\nPerso AI 더빙 팀입니다.\n\n${GROW_LINE[kind].ko}\n\n편하신 시간을 알려주시면 연락드리겠습니다.\n\n감사합니다.\nPerso AI 팀 드림`;
  }
  return `Hi ${name} team,\n\nThis is the Perso AI Dubbing team.\n\n${GROW_LINE[kind].en}\n\nPlease let us know a time that works for you and we'll follow up.\n\nBest regards,\nPerso AI Team`;
}

/** 그 카드의 초안 — 줄을 주면 그 곳의 근거에 맞춘 초안, null 이면 그 카드의 기본 문구(카드의 「메일 초안」 · 여러 곳). */
export function draftFor(tab: Tab, row: UpRow | ContractRow | null, lang: Lang, rules: Rules): Draft {
  const body = tab === "up" ? upBody(row as UpRow | null, lang)
    : tab === "churn" ? churnBody(row as ChurnRow | null, lang, rules)
    : growBody(row as GrowRow | null, lang, rules);
  return { subject: SUBJECT[tab][lang], body };
}

/** 여러 곳 초안의 곳 이름 — 목업의 name_(업셀링은 「Space N」, 수주 고객은 회사 이름). */
export const placeName = (row: UpRow | ContractRow) => ("space_seq" in row ? `Space ${row.space_seq}` : row.company);
