// 계약 폼(「Perso 계정 및 플랜」)의 두 조각 — 「플랜명」 추천 목록과 space_seq 칸 아래의 안내 줄 (2026-10-12).
//
// 운영자: 「플랜명 있네. 실제 지금 수주 고객 정보에 해당 플랜 이미 입력돼있어 근데 왜 space_seq는 안적혀?
// 연동을 따로 해야하는거야?」. 그전에는 따로 해야 했습니다 — 플랜명은 글자 칸이고 space_seq 는 사람이 제품에서
// 번호를 찾아 적는 칸이라, 플랜명까지 적어 둔 고객 셋이 목록에서 「미연결」로 서 있었습니다(고객 이름은 적지
// 않습니다 — 이 저장소는 공개입니다). 제품의 엔터프라이즈 플랜은 이름이 「<고객> Biz」 꼴이고 가공 데이터가 스페이스마다
// 그 이름을 들고 있으므로, 같은 이름의 플랜을 쓰는 스페이스를 찾아 보여 줍니다.
//
// **이 파일은 사용 데이터를 쥔 쪽입니다** — `lib/api` 를 import 하지 않고 서버를 부르지 않습니다
// (`tests/test_usage_data_stays_off_server.py` 의 `DATA_SIDE`). 밖으로 내는 것은 하나뿐입니다: **사람이 눌렀을 때**
// `onFill` 로 넘기는 스페이스 번호 글자. 누르는 자리는 둘이고 둘 다 한 번 더 눌러야 서버에 닿습니다 — 편집 폼의
// [채우기](칸에 넣고, 저장은 따로) · 읽기 카드 제목 옆 (!) 의 [채우기](확인 창을 띄우고, 그 창의 확인이 저장). 지금까지 손으로
// 옮겨 적던 바로 그 값입니다. 스스로 채우지 않습니다(효과 훅이 없습니다): 플랜명 칸은 자유 입력이라, 글자가
// 같다는 것만으로 남의 스페이스가 조용히 붙으면 안 됩니다.
import type { UsageSource } from "../../lib/usageSource";
import { RULE, planHint, type PlanHint, type PlanSpaces } from "./usage";
import { usePlanSpaces } from "./useUsage";

/** 「플랜명」 칸이 `list` 로 가리키는 추천 목록의 id. */
export const PLAN_NAMES_ID = "won-plan-names";

/** 안내 줄에 적는 번호의 상한 — 넘으면 「외 N개」. 한 고객이 수십 개를 쓰는 플랜도 복사해 적을 수 있을 만큼. */
const SHOWN = 60;

/** 「플랜명」 추천 목록 — 제품에 실제로 있는 플랜 이름들.
 *
 *  **고르라고 주는 것이지 가두는 것이 아닙니다** (2026-10-12 운영자: 「실제 플랜을 만들기 전에 계약 추가부터
 *  하면 계약 추가가 안되는 문제가 있겠네」). 그래서 `<select>` 가 아니라 `<datalist>` 입니다 — 목록에 없는
 *  이름을 그대로 적을 수 있고, 저장은 이 목록을 보지 않습니다. 플랜이 나중에 만들어지면 그 계약을 열었을 때
 *  아래 안내 줄이 뜹니다. 사용 데이터가 연결되지 않은 곳에서는 목록이 없을 뿐 칸은 예전 그대로입니다. */
export function PlanNameList({ byPlan }: { byPlan: Map<string, PlanSpaces> | null }) {
  if (!byPlan) return null;
  // 순서는 「ko」 로 못박습니다(한글 → 영문). 기본 로캘에 맡기면 브라우저 · CI 마다 순서가 달라집니다.
  const names = [...byPlan.values()].map((plan) => plan.name).sort((a, b) => a.localeCompare(b, "ko"));
  return (
    <datalist id={PLAN_NAMES_ID}>
      {names.map((name) => <option key={name} value={name} />)}
    </datalist>
  );
}

export function PlanNameOptions({ source }: { source: UsageSource | null }) {
  return <PlanNameList byPlan={usePlanSpaces(source)} />;
}

/** space_seq 칸 아래의 한 줄. 무엇을 적을지는 `usage.planHint` 가 정하고 여기는 그리기만 합니다. */
export function PlanSpaceLine({ hint, onFill }: { hint: PlanHint; onFill: (spaceSeq: string) => void }) {
  if (hint.kind === "none") return null;
  const shown = hint.spaces.slice(0, SHOWN);
  const rest = hint.spaces.length - shown.length;
  const numbers = shown.join(", ");
  if (hint.kind === "fill") {
    return (
      <div className="plan-hint">
        <span>제품에서 「{hint.plan}」 플랜을 쓰는 스페이스: <b>{numbers}</b></span>
        <button type="button" className="btn btn-sm" onClick={() => onFill(numbers)}>채우기</button>
      </div>
    );
  }
  return (
    <div className="plan-hint">
      <span>
        제품에서 「{hint.plan}」 플랜을 쓰는 스페이스 {hint.spaces.length}개: <b>{numbers}</b>
        {rest > 0 && ` 외 ${rest}개`}
      </span>
      {/* 운영자: 「여러 고객이 같이 쓰는 플랜은 번호를 보여주는데, 중복이라 채우기 안된다고 설명도 넣어줘」.
          「일 수 있어」인 이유 — 어느 고객의 스페이스인지는 가공 데이터에 없어서 개수로 잽니다(`usage.planHint`). */}
      <span className="plan-hint__why">
        스페이스가 {RULE.fillSpaces}개를 넘는 플랜은 여러 고객이 같이 쓰는 플랜일 수 있어 [채우기]를 막았습니다 —
        그대로 채우면 다른 고객의 사용량이 이 계약에 섞입니다. 이 고객의 번호만 골라 직접 적어 주세요.
      </span>
    </div>
  );
}

export function PlanSpaceHint({ source, planName, spaceSeq, onFill }: {
  source: UsageSource | null; planName: string; spaceSeq: string; onFill: (spaceSeq: string) => void;
}) {
  return <PlanSpaceLine hint={planHint(planName, spaceSeq, usePlanSpaces(source))} onFill={onFill} />;
}

/** 읽기 카드(「Perso 계정 및 플랜」) 제목 옆의 **(!)** — 채울 수 있을 때만 섭니다. 편집을 열어 보기 전에는 채울
 *  것이 있는지 알 길이 없었습니다(2026-10-12 운영자: 「지금은 … 편집을 눌러야만 "채우기"가 뜨는데」).
 *
 *  **모양이 두 번 바뀌었습니다** (같은 날). 처음은 머리 오른쪽 끝의 「space_seq 채우기」 칩이었는데 회색이라 꺼진
 *  버튼처럼 보였고(「칩 안보이는데」), 청록 알약으로 바꾸자 바로 위의 계약 고르개(`.sel-pill`)와 같은 옷이 됐습니다 —
 *  「색이 상단 계약 토글이랑 겹쳐서 눈에 잘 안보여. 혹시 Perso 계정 및 플랜 오른쪽에 (!) 같은 칩 추가하고, 그거
 *  누르면 채우기 버튼 뜨는걸로 하는거 어때」. 그래서 주황 (!) 입니다: 이 화면에서 주황은 「손이 가야 한다」
 *  (세팅중 · 일부 스냅샷에 없음)이고 청록은 「고르는 것」입니다.
 *
 *  누르면 열리는 것은 `<details>` 입니다 — 여닫는 데 상태도 효과 훅도 없습니다(이 모듈은 스스로 도는 것이 없어야
 *  합니다). 말풍선의 [채우기]는 번호를 `onFill` 로 넘길 뿐이고, 받는 쪽(`WonCustomerDetail` 의 `PlanSection`)이
 *  확인 창을 띄워 그 창의 확인이 저장합니다. 여러 고객이 같이 쓸 수 있는 플랜(`shared`)에는 표시가 없습니다:
 *  고를 번호가 있는 일이라 편집 폼의 안내 줄에서 사람이 적습니다. */
export function PlanSpaceAlertView({ hint, onFill }: { hint: PlanHint; onFill: (spaceSeq: string) => void }) {
  if (hint.kind !== "fill") return null;
  const numbers = hint.spaces.join(", ");
  return (
    <details className="plan-alert">
      <summary className="plan-alert__i" aria-label="space_seq 를 채울 수 있습니다" title="space_seq 를 채울 수 있습니다">!</summary>
      <div className="plan-alert__pop">
        <b>space_seq 가 비어 있습니다</b>
        <span>제품에서 「{hint.plan}」 플랜을 쓰는 스페이스: <b>{numbers}</b></span>
        <button type="button" className="btn btn-sm btn-primary" onClick={() => onFill(numbers)}>채우기</button>
      </div>
    </details>
  );
}

export function PlanSpaceAlert({ source, planName, spaceSeq, onFill }: {
  source: UsageSource | null; planName: string; spaceSeq: string; onFill: (spaceSeq: string) => void;
}) {
  return <PlanSpaceAlertView hint={planHint(planName, spaceSeq, usePlanSpaces(source))} onFill={onFill} />;
}
