// Timestamps are stored UTC-naive and the operator is in Korea, so everything renders in
// KST — the port of the `kst` Jinja filter. Intl does the conversion; no date library.
const FORMATS: Record<string, Intl.DateTimeFormatOptions> = {
  full: { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" },
  "md-hm": { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" },
  date: { year: "numeric", month: "2-digit", day: "2-digit" },
};

export function kst(value: string | null | undefined, shape: keyof typeof FORMATS = "full") {
  if (!value) return "";
  // The API sends naive datetimes (no offset). They are UTC — say so, or the browser
  // reads them as local time and every timestamp shifts by the local offset.
  const iso = /(Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`;
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  return new Intl.DateTimeFormat("ko-KR", { ...FORMATS[shape], timeZone: "Asia/Seoul", hour12: false })
    .format(at)
    .replace(/\.$/, "");
}

/** 인용된 지난 메일을 잘라 냅니다 — **이 메일이 새로 한 말만** 남깁니다.
 *
 *  메일 클라이언트는 답장마다 그 전의 대화를 통째로 아래에 붙입니다. 이 목록에서는 그게
 *  그대로 중복입니다: 인용된 그 문의가 **바로 위에 자기 줄로** 이미 서 있고, 답장이
 *  쌓일수록 같은 글이 줄마다 반복됩니다 (2026-09-07 운영자 지적 —
 *  「문의 회신 전에 다른 곳에서 있던 게 반복해서 나온다」).
 *
 *  **저장한 값은 안 건드립니다.** 그리는 자리에서만 자릅니다 — 잘라서 저장하면 잘못
 *  잘린 메일의 원본을 되찾을 길이 없고, 이미 쌓인 수천 줄에 백필이 필요해집니다.
 *  화면에서 자르면 옛 줄도 그 자리에서 같이 정리됩니다.
 *
 *  자를 곳이 없거나 자르고 나서 남는 것이 없으면(=인용문이 본문의 전부인 전달 메일)
 *  원문을 그대로 돌려줍니다. 아무것도 안 보이는 줄보다는 중복이 낫습니다.
 */
const QUOTE_HEADERS = [
  /^\s*>/,                                                    // 인용 부호로 시작하는 줄
  /\bwrote:\s*$/i,                                            // On <날짜>, <사람> wrote:
  /님이 작성:\s*$/,                                            // 한국어 클라이언트
  /^\s*-{2,}\s*(original|forwarded) message\s*-{2,}/i,        // 아웃룩·전달
  /^\s*_{10,}\s*$/,                                           // 아웃룩 구분선
];

export function stripQuotedReply(body: string): string {
  const lines = body.split("\n");
  const cut = lines.findIndex((line) => QUOTE_HEADERS.some((re) => re.test(line)));
  if (cut < 0) return body;
  const kept = lines.slice(0, cut).join("\n").trim();
  return kept || body;
}
