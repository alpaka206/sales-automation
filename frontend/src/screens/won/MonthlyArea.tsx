import { useEffect, useRef, useState } from "react";

/** 월별 영역 차트 — SVG 하나. 차트 라이브러리를 안 쓰는 이유는 「없어서」가 아닙니다.
 *
 *  Recharts·Chart.js 는 gzip 90~110KB 이고 지금 번들이 127KB 입니다. 차트 하나에 번들을
 *  두 배로 만드는 값인지가 기준인데, 여기 필요한 것은 면 둘·0선·눈금·툴팁이 전부라
 *  라이브러리가 하는 일의 대부분을 안 씁니다. 게다가 이 화면의 색·글꼴·모서리는 `won.css`
 *  토큰에서 오고, 라이브러리는 자기 기본값을 들고 옵니다 — 맞추는 작업이 그리는 작업보다
 *  큽니다. 애니메이션·확대·여러 차트 종류가 필요해지면 그때가 라이브러리를 들일 때입니다.
 *
 *  **막대에서 면으로 바뀌었습니다** (2026-09-02 운영자 지시). 막대는 달마다 따로 선
 *  값이라 「달과 달 사이」를 말하지 않는데, MRR 은 추세로 읽는 값입니다. 면은 이어져
 *  있어서 오르내림이 한눈에 들어오고, 무엇보다 **쌓을 수 있습니다** — New 를 위에 얹는
 *  것이 이 차트의 요구사항입니다.
 *
 *  색은 눈으로 고르지 않았습니다. `#2A9D8F`(--teal-500)와 `#B42318`(--red-fg)은 이미 이
 *  저장소의 토큰이고, 색맹 분리도·명도대·채도·대비 검사를 통과한 쌍입니다(deutan ΔE 16.1,
 *  정상시야 30.6). 양수와 음수는 **극성**이라 색을 가르는 것이 맞고, 음수는 중도 해지
 *  정산이라 경고색을 씁니다 — 다만 색만으로 말하지 않습니다: 0선 아래로 자라고,
 *  툴팁이 「해지 정산」이라고 씁니다.
 *
 *  **New 는 다른 색이 아니라 같은 색의 진한 쪽입니다**(`--teal-700` #0F766E). 전체의
 *  일부이지 다른 종류가 아니므로 색상(hue)을 바꾸면 「두 지표」로 읽힙니다 — 부분·전체는
 *  명도로 가르는 것이 맞고, 명도차는 색각과 무관하게 남습니다.
 */
const POSITIVE = "#2A9D8F";
const NEGATIVE = "#B42318";
const NEW = "#0F766E";

// 세로만 고정입니다. 가로는 카드 폭을 **재서** 그 값을 viewBox 로 씁니다 — 좌표 한 칸이
// 화면 1px 이 되어야 글자가 안 늘어납니다. 예전에는 720 짜리 그림을 `preserveAspectRatio
// ="none"` 으로 카드 폭에 맞춰 늘였는데, 그러면 넓은 화면에서 눈금·월 글자까지 가로로
// 같이 늘어나 살짝 뚱뚱해 보였습니다(좁은 화면에서는 반대로 눌렸습니다).
const H = 158;
// 오른쪽이 막대 때보다 넓습니다. 면은 **끝에서 끝까지** 그리므로 마지막 달의 글자가
// 축 바깥으로 반쯤 나가는데, 그만큼의 자리를 여기서 냅니다.
const PAD = { top: 14, right: 24, bottom: 22, left: 52 };

/** 눈금은 딱 떨어지는 수로. 0 / 500만 / 1,000만 처럼 읽히지 않으면 눈금이 아니라 잡음입니다. */
function niceStep(span: number): number {
  if (span <= 0) return 1;
  const raw = span / 2;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  for (const factor of [1, 2, 2.5, 5, 10]) {
    if (raw <= magnitude * factor) return magnitude * factor;
  }
  return magnitude * 10;
}

export function MonthlyArea({
  months, valueAt, newAt, format, formatTick, now, negativeNote, newLabel, onHover, uid, caption,
}: {
  months: string[];
  valueAt: (month: string) => number;
  /** 그 달의 **신규 고객 몫** — 그 달에 처음 잡힌 고객이 번 돈(`won.first_revenue_month` ·
   *  `won.first_cash_month`). 총액보다 클 수 있습니다: 총액은 중도 해지 정산까지 반영한
   *  순액이라, 신규가 온 달에 누가 해지하면 순액이 더 작습니다. 그리는 쪽에서만 0 에서
   *  바닥을 칩니다 — 자르는 것은 그림의 사정이고, 값의 사정이 아닙니다. */
  newAt: (month: string) => number;
  /** 값을 사람이 읽는 글자로 — 통화 기호까지. 툴팁과 범례가 씁니다. */
  format: (value: number) => string;
  /** 축 눈금 — 통화 기호 없이. 기호는 큰 숫자와 고르개가 이미 말하고, 눈금마다 반복하면
   *  격자보다 시끄러워집니다. 단위(만·억)는 축 전체가 하나여야 눈금끼리 비교됩니다. */
  formatTick: (value: number) => string;
  /** 이번 달 — 눈금 글자만 굵어집니다. 색을 바꾸지 않는 이유: 색은 값의 성격을 말하지
   *  순서를 말하지 않습니다. 「이번 달」은 순서입니다. */
  now: string;
  /** 음수가 뜻하는 것. 툴팁이 색 대신 말로 설명합니다. */
  negativeNote: string;
  /** 범례에 적을 New 의 이름 (New MRR · New 매출). */
  newLabel: string;
  /** 짚고 있는 달을 카드에 알려 줍니다 — 카드 위의 큰 숫자가 그 달 값으로 바뀝니다.
   *  손을 떼면 `null` 이고, 그때 카드는 이번 달로 돌아갑니다. */
  onHover?: (month: string | null) => void;
  /** clipPath 의 id 는 문서 전체에서 유일해야 합니다. 카드가 둘이라 고정 문자열을 쓰면
   *  둘째 카드가 첫째의 clip 을 물고, 음수 면이 엉뚱한 자리에서 잘립니다. */
  uid: string;
  /** 이 숫자들이 무엇인가 — 「VAT 포함」·「입금 기준」. 범례와 한 줄, 카드 왼쪽 아래. */
  caption?: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const box = useRef<HTMLElement | null>(null);
  // 재기 전 한 프레임의 값. 카드 폭과 비슷하면 첫 그림이 튀지 않습니다.
  const [W, setW] = useState(720);

  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      setW(Math.max(320, Math.round(entry.contentRect.width)));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  function move(index: number | null) {
    setHover(index);
    onHover?.(index === null ? null : months[index]);
  }

  const values = months.map(valueAt);
  // **그리는 값만 0 에서 바닥을 칩니다.** 신규 고객이 온 달에 다른 고객이 중도 해지하면
  // 총액이 신규분보다 작거나 음수일 수 있습니다(해지 정산은 음수입니다) — 그때 New 는
  // 총액보다 크고, 그건 데이터가 틀린 것이 아니라 두 값이 **다른 것을 세기 때문**입니다:
  // New 는 신규 고객이 번 돈, 총액은 해지까지 반영한 순액입니다.
  //
  // 그래서 카드 위의 큰 숫자는 자르지 않습니다(그 달의 진짜 New 를 적습니다). 자르는 것은
  // 그림뿐인데, 0선 위로만 쌓는 그림에서 음수 조각은 그릴 자리가 없기 때문입니다. 넘치는
  // 달에는 그 달의 양수 면이 통째로 진해집니다 — 「이 달 매출은 다 신규분이다」로 읽히고,
  // 실제로 그렇습니다.
  const news = months.map((month) => Math.max(0, newAt(month)));

  const top = Math.max(0, ...values);
  const bottom = Math.min(0, ...values);
  const step = niceStep(Math.max(top - bottom, 1));
  const hi = Math.ceil(top / step) * step || step;
  const lo = Math.floor(bottom / step) * step;

  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  // **끝에서 끝까지** 놓습니다. 막대 시절에는 칸 가운데였는데, 면으로 바꾸면 양끝에
  // 반 칸씩(6개월이면 한 칸이 100px 넘습니다) 빈자리가 남아 카드가 헐거워 보입니다.
  const span = months.length > 1 ? plotW / (months.length - 1) : 0;
  const x = (index: number) => PAD.left + span * index;
  const y = (value: number) => PAD.top + plotH * (1 - (value - lo) / (hi - lo));
  const zero = y(0);

  // **눈금 값을 반올림하지 않습니다.** 반올림한 값으로 y 를 계산하던 시절에는, 계열이
  // 전부 0 인 카드(고객이 없는 담당부서 칩)에서 step 이 0.5 가 되어 0.5 가 1 로 올라가고
  // 그 눈금이 `y(1) = -108`, 즉 **카드 위쪽 바깥**에 실선 한 줄과 「1.0」으로 그려졌습니다
  // (svg 가 `overflow:visible` 입니다). 자릿수를 접는 것은 `formatTick` 의 일입니다.
  const ticks: number[] = [];
  for (let t = lo; t <= hi + 1e-9; t += step) ticks.push(t);

  /** 값 배열을 지나는 선. 곡선을 안 쓰는 이유: 보간은 없는 값을 그려 넣습니다 — 두 달
   *  사이에 실제로 있던 적 없는 최고점이 생깁니다. */
  const line = (at: (index: number) => number) =>
    months.map((_, index) => `${index ? "L" : "M"}${x(index)} ${y(at(index))}`).join(" ");
  /** 두 선 사이를 채운 면. 위 선을 따라 갔다가 아래 선을 거꾸로 돌아옵니다. */
  const bandPath = (upper: (i: number) => number, lower: (i: number) => number) =>
    `${line(upper)} ${months
      .map((_, index) => {
        const back = months.length - 1 - index;
        return `L${x(back)} ${y(lower(back))}`;
      })
      .join(" ")} Z`;

  /** 그 달의 손닿는 사각형 `[x, width]` — 플롯 안으로 자릅니다. */
  const hit = (index: number): [number, number] => {
    const left = Math.max(PAD.left, x(index) - (span || plotW) / 2);
    const right = Math.min(W - PAD.right, x(index) + (span || plotW) / 2);
    return [left, Math.max(1, right - left)];
  };

  const total = (index: number) => values[index];
  const rest = (index: number) => values[index] - news[index];
  const area = bandPath(total, () => 0);
  const newArea = bandPath(total, rest);
  const hasNew = news.some((value) => value > 0);
  const hasNegative = values.some((value) => value < 0);

  return (
    <figure className="marea" ref={box}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
           aria-label={`${months[0]}부터 ${months[months.length - 1]}까지 월별 값`}
           onMouseLeave={() => move(null)}>
        <defs>
          {/* 같은 면을 두 번 그리고 0선 위·아래로 각각 잘라 냅니다. 면 하나에 색 둘을
              칠할 방법이 없고, 부호마다 경로를 따로 만들면 교차점을 손으로 풀어야
              합니다 — 자르는 쪽이 짧고, 잘린 자리가 정확히 0선입니다. */}
          <clipPath id={`${uid}-pos`}>
            <rect x={0} y={0} width={W} height={zero} />
          </clipPath>
          <clipPath id={`${uid}-neg`}>
            <rect x={0} y={zero} width={W} height={H - zero} />
          </clipPath>
        </defs>

        {ticks.map((tick) => (
          <g key={tick}>
            {/* 격자는 실선 1px, 배경에서 한 단계만 떨어진 회색. 점선은 데이터보다 시끄럽습니다. */}
            <line x1={PAD.left} x2={W - PAD.right} y1={y(tick)} y2={y(tick)}
                  className={tick === 0 ? "marea__zero" : "marea__grid"} />
            <text x={PAD.left - 8} y={y(tick) + 3.5} className="marea__tick">{formatTick(tick)}</text>
          </g>
        ))}

        <path d={area} fill={POSITIVE} opacity={0.16} clipPath={`url(#${uid}-pos)`} />
        <path d={area} fill={NEGATIVE} opacity={0.16} clipPath={`url(#${uid}-neg)`} />
        {/* 신규 몫은 총액 선 **바로 아래**에 얹힙니다 — 위로 자란 것이 곧 그 달의 신규분
            입니다. 진한 면이라 아래 옅은 면과 겹쳐도 경계가 남습니다. */}
        {hasNew && <path d={newArea} fill={NEW} opacity={0.62} clipPath={`url(#${uid}-pos)`} />}
        <path d={line(total)} fill="none" stroke={POSITIVE} strokeWidth={1.75}
              strokeLinejoin="round" clipPath={`url(#${uid}-pos)`} />
        <path d={line(total)} fill="none" stroke={NEGATIVE} strokeWidth={1.75}
              strokeLinejoin="round" clipPath={`url(#${uid}-neg)`} />

        {months.map((month, index) => (
          <g key={month}>
            <text x={x(index)} y={H - 6}
                  className={`marea__month${month === now ? " is-now" : ""}`}>
              {month.slice(5)}
            </text>
            {/* 손이 닿는 자리는 점보다 넓습니다 — 선 위의 한 점을 정확히 짚으라고 할 수
                없습니다. 그 달 둘레의 반 칸씩이 그 달의 버튼이되, **플롯 밖으로는 안
                나갑니다**: 첫 달과 마지막 달은 축 눈금 자리와 카드 여백까지 자기 버튼으로
                삼고 있었습니다. */}
            <rect x={hit(index)[0]} y={PAD.top} width={hit(index)[1]} height={plotH}
                  fill="transparent" onMouseEnter={() => move(index)}>
              <title>{`${month} · ${format(values[index])}${
                values[index] < 0 ? ` (${negativeNote})` : ""}`}</title>
            </rect>
          </g>
        ))}

        {hover !== null && (
          <g className="marea__cursor">
            <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + plotH} />
            {news[hover] > 0 && (
              <circle cx={x(hover)} cy={y(rest(hover))} r={3} fill="#fff" stroke={NEW} strokeWidth={1.75} />
            )}
            <circle cx={x(hover)} cy={y(values[hover])} r={3.5} fill="#fff"
                    stroke={values[hover] < 0 ? NEGATIVE : POSITIVE} strokeWidth={2} />
          </g>
        )}
      </svg>

      {/* 표로도 읽힙니다 — 색과 길이는 화면을 보는 사람의 채널이고, 값 자체는 누구에게나
          있어야 합니다. */}
      <table className="sr-only">
        <caption>월별 값</caption>
        <thead><tr><th scope="col">달</th><th scope="col">전체</th><th scope="col">{newLabel}</th></tr></thead>
        <tbody>
          {months.map((month, index) => (
            <tr key={month}>
              <th scope="row">{month}</th>
              <td>{format(values[index])}</td>
              <td>{format(newAt(months[index]))}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* 바닥 왼쪽 한 줄에 단서와 범례가 같이 섭니다 (2026-09-02 운영자 지시). 「VAT
          포함」은 제목 옆 괄호에 담당부서와 함께 들어 있었는데, 거기서는 무엇이 지표
          이름이고 무엇이 단서인지 흐렸습니다 — 그리고 그것은 이 그림의 숫자가 무엇인지
          말하는 문장이라 `figcaption` 이 제자리입니다. */}
      <figcaption className="marea__legend">
        {caption && <span className="cap">{caption}</span>}
        <span><i style={{ background: POSITIVE, opacity: 0.45 }} />전체</span>
        <span><i style={{ background: NEW, opacity: 0.75 }} />{newLabel}</span>
        {hasNegative && <span className="is-neg"><i style={{ background: NEGATIVE }} />{negativeNote}</span>}
      </figcaption>
    </figure>
  );
}
