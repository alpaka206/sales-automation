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
