import { useState } from "react";

/** 월별 막대 — SVG 하나. 차트 라이브러리를 안 쓰는 이유는 「없어서」가 아닙니다.
 *
 *  Recharts·Chart.js 는 gzip 90~110KB 이고 지금 번들이 127KB 입니다. 차트 하나에 번들을
 *  두 배로 만드는 값인지가 기준인데, 여기 필요한 것은 막대 열두 개·0선·눈금·툴팁이 전부라
 *  라이브러리가 하는 일의 대부분을 안 씁니다. 게다가 이 화면의 색·글꼴·모서리는 `won.css`
 *  토큰에서 오고, 라이브러리는 자기 기본값을 들고 옵니다 — 맞추는 작업이 그리는 작업보다
 *  큽니다. 애니메이션·확대·여러 차트 종류가 필요해지면 그때가 라이브러리를 들일 때입니다.
 *
 *  색은 눈으로 고르지 않았습니다. `#2A9D8F`(--teal-500)와 `#B42318`(--red-fg)은 이미 이
 *  저장소의 토큰이고, 색맹 분리도·명도대·채도·대비 검사를 통과한 쌍입니다(deutan ΔE 16.1,
 *  정상시야 30.6). 양수와 음수는 **극성**이라 색을 가르는 것이 맞고, 음수는 중도 해지
 *  정산이라 경고색을 씁니다 — 다만 색만으로 말하지 않습니다: 0선 아래로 자라고, 그 달에
 *  값이 직접 적히고, 툴팁이 「해지 정산」이라고 씁니다.
 */
const POSITIVE = "#2A9D8F";
const NEGATIVE = "#B42318";

const W = 720;
const H = 158;
const PAD = { top: 14, right: 10, bottom: 22, left: 52 };

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

export function MonthlyBars({ months, valueAt, format, formatTick, now, negativeNote }: {
  months: string[];
  valueAt: (month: string) => number;
  /** 값을 사람이 읽는 글자로 — 통화 기호까지. 툴팁과 직접 라벨이 씁니다. */
  format: (value: number) => string;
  /** 축 눈금 — 통화 기호 없이. 기호는 큰 숫자와 고르개가 이미 말하고, 눈금마다 반복하면
   *  격자보다 시끄러워집니다. 단위(만·억)는 축 전체가 하나여야 눈금끼리 비교됩니다. */
  formatTick: (value: number) => string;
  /** 이번 달 — 눈금 글자만 굵어집니다. 색을 바꾸지 않는 이유: 색은 값의 성격을 말하지
   *  순서를 말하지 않습니다. 「이번 달」은 순서입니다. */
  now: string;
  /** 음수가 뜻하는 것. 툴팁이 색 대신 말로 설명합니다. */
  negativeNote: string;
}) {
  const [hover, setHover] = useState<number | null>(null);

  const values = months.map(valueAt);
  const top = Math.max(0, ...values);
  const bottom = Math.min(0, ...values);
  const step = niceStep(Math.max(top - bottom, 1));
  const hi = Math.ceil(top / step) * step || step;
  const lo = Math.floor(bottom / step) * step;

  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const band = plotW / months.length;
  // 막대는 칸을 다 채우지 않습니다 — 남는 자리가 곧 막대를 가르는 공백입니다. 테두리를
  // 그리지 않는 이유이기도 합니다: 선은 데이터가 아닌 잉크입니다.
  const barW = Math.min(24, band - 10);
  const y = (value: number) => PAD.top + plotH * (1 - (value - lo) / (hi - lo));
  const zero = y(0);

  const ticks: number[] = [];
  for (let t = lo; t <= hi + 1e-9; t += step) ticks.push(Math.round(t));

  // 이야기가 있는 막대 하나에만 값을 답니다. 열두 개에 다 적으면 아무것도 안 읽힙니다 —
  // 음수가 있으면 그것이(해지 정산), 없으면 아무 데도 안 답니다. 나머지는 눈금과 툴팁이.
  const worst = values.reduce(
    (acc, value, index) => (value < 0 && value < values[acc] ? index : acc),
    -1 as number,
  );
  const labelled = values.some((v) => v < 0) ? (worst >= 0 ? worst : values.indexOf(Math.min(...values))) : -1;

  return (
    <figure className="mbars">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img"
           aria-label={`${months[0]}부터 ${months[months.length - 1]}까지 월별 값`}
           onMouseLeave={() => setHover(null)}>
        {ticks.map((tick) => (
          <g key={tick}>
            {/* 격자는 실선 1px, 배경에서 한 단계만 떨어진 회색. 점선은 데이터보다 시끄럽습니다. */}
            <line x1={PAD.left} x2={W - PAD.right} y1={y(tick)} y2={y(tick)}
                  className={tick === 0 ? "mbars__zero" : "mbars__grid"} />
            <text x={PAD.left - 8} y={y(tick) + 3.5} className="mbars__tick">{formatTick(tick)}</text>
          </g>
        ))}

        {months.map((month, index) => {
          const value = values[index];
          const x = PAD.left + band * index + (band - barW) / 2;
          const height = Math.max(2, Math.abs(y(value) - zero));
          const negative = value < 0;
          const radius = Math.min(4, barW / 2, height);
          // 데이터가 끝나는 쪽만 둥글고 0선 쪽은 각집니다 — 막대는 0에서 자라납니다.
          const path = negative
            ? `M${x} ${zero} h${barW} v${height - radius} a${radius} ${radius} 0 0 1 ${-radius} ${radius} h${-(barW - radius * 2)} a${radius} ${radius} 0 0 1 ${-radius} ${-radius} z`
            : `M${x} ${zero} h${barW} v${-(height - radius)} a${radius} ${radius} 0 0 0 ${-radius} ${-radius} h${-(barW - radius * 2)} a${radius} ${radius} 0 0 0 ${-radius} ${radius} z`;
          return (
            <g key={month}>
              <path d={path} fill={negative ? NEGATIVE : POSITIVE}
                    opacity={hover === null || hover === index ? 1 : 0.45} />
              {index === labelled && value !== 0 && (
                <text x={x + barW / 2} y={negative ? y(value) + 13 : y(value) - 5}
                      className="mbars__callout">{format(value)}</text>
              )}
              <text x={PAD.left + band * index + band / 2} y={H - 6}
                    className={`mbars__month${month === now ? " is-now" : ""}`}>
                {month.slice(5)}
              </text>
              {/* 손이 닿는 자리는 막대보다 넓습니다 — 2px 짜리 막대를 정확히 짚으라고 할 수
                  없습니다. 칸 전체가 그 달의 버튼입니다. */}
              <rect x={PAD.left + band * index} y={PAD.top} width={band} height={plotH}
                    fill="transparent" onMouseEnter={() => setHover(index)}>
                <title>{`${month} · ${format(value)}${value < 0 ? ` (${negativeNote})` : ""}`}</title>
              </rect>
            </g>
          );
        })}
      </svg>

      {hover !== null && (
        <figcaption className="mbars__tip" aria-live="polite">
          <b>{months[hover]}</b> {format(values[hover])}
          {values[hover] < 0 && <em> · {negativeNote}</em>}
        </figcaption>
      )}

      {/* 표로도 읽힙니다 — 색과 길이는 화면을 보는 사람의 채널이고, 값 자체는 누구에게나
          있어야 합니다. */}
      <table className="sr-only">
        <caption>월별 값</caption>
        <tbody>
          {months.map((month, index) => (
            <tr key={month}><th scope="row">{month}</th><td>{format(values[index])}</td></tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
