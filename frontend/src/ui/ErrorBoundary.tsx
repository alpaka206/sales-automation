import { Component, type ErrorInfo, type ReactNode } from "react";

/** 화면 하나가 죽어도 **무슨 일인지 적습니다** (2026-09-09).
 *
 *  이게 없던 동안 렌더 중 예외는 곧 **하얀 화면**이었습니다. 서버 로그에는 200 만 찍히고
 *  (데이터는 멀쩡히 갔으니까), 콘솔 로그도 사람이 F12 를 눌러야 보이며, `/logs` 화면은
 *  서버 쪽 사건만 모읍니다. 그래서 「아무것도 안 뜬다」는 말에서 원인까지 가는 길이
 *  없었습니다 — 2026-09-09 에 티켓 화면이 그렇게 됐고, 그때 이걸 만들었습니다.
 *
 *  **사이드바 안쪽에 겁니다.** 화면 하나가 죽어도 다른 화면으로 갈 수 있어야 합니다 —
 *  통째로 죽으면 콘솔을 벗어날 길이 새로고침뿐이고, 새로고침해도 같은 자리로 돌아옵니다.
 *
 *  **글자를 그대로 보여 줍니다.** 「문제가 발생했습니다」는 아무것도 말하지 않고, 그 화면을
 *  보는 사람은 개발자에게 옮겨 적어 줄 수 있는 유일한 사람입니다. */
type Props = { children: ReactNode };
type State = { error: Error | null; stack: string };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, stack: "" };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 콘솔에도 남깁니다 — 개발자 도구를 이미 열어 둔 사람에게는 이쪽이 빠릅니다.
    console.error("[console] 화면 렌더 실패:", error, info.componentStack);
    this.setState({ stack: info.componentStack || "" });
  }

  render() {
    const { error, stack } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="card" style={{ margin: 16 }}>
        <div className="section-header">
          <div className="section-header__title">이 화면을 그리지 못했습니다</div>
        </div>
        <p className="t-sm">
          왼쪽 메뉴로 다른 화면에는 갈 수 있습니다. 아래 내용을 그대로 개발자에게
          전해 주세요.
        </p>
        <pre
          className="t-xs"
          style={{ whiteSpace: "pre-wrap", overflowX: "auto", marginTop: 8 }}
        >
          {error.name}: {error.message}
          {stack ? `\n${stack.trim()}` : ""}
        </pre>
        <button className="btn btn--sm" style={{ marginTop: 8 }}
                onClick={() => this.setState({ error: null, stack: "" })}>
          다시 그려 보기
        </button>
      </div>
    );
  }
}
