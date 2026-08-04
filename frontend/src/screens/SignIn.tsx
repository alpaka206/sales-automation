import { useEffect, useState } from "react";
import { Icon } from "../ui/Icon";

/** The sign-in and awaiting-approval screens.
 *
 * These render before there is a session, so they are NOT inside the router: /auth/* is
 * the one prefix the auth middleware lets through unauthenticated, and keeping the URL
 * exactly as it was means nothing about the gate had to change. main.tsx picks this
 * component by pathname and never mounts the console.
 */
type AuthState = {
  domain: string;
  configured: boolean;
  email?: string;
  error?: string;
};

export function SignIn({ pending }: { pending: boolean }) {
  const [state, setState] = useState<AuthState | null>(null);

  useEffect(() => {
    fetch("/auth/state", { credentials: "same-origin" })
      .then((response) => response.json())
      .then(setState)
      .catch(() => setState({ domain: "", configured: false }));
  }, []);

  const error = new URLSearchParams(location.search).get("error") || state?.error;

  return (
    <div style={{ minHeight: "100dvh", display: "grid", placeItems: "center", padding: 24, background: "var(--bg)" }}>
      <div className="card" style={{ width: "100%", maxWidth: 400, textAlign: "center", padding: 32 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 11, marginBottom: 20 }}>
          <img src="/static/logo.png" alt="" className="brand__logo" />
          <span className="brand__name">PERSO</span>
        </div>

        {pending ? (
          <>
            <h1 style={{ fontSize: 18, fontWeight: 800, letterSpacing: "-.02em", margin: "0 0 6px" }}>
              승인 대기 중
            </h1>
            <p className="t-sm t-muted" style={{ margin: "0 0 22px" }}>
              {state?.email ? <strong>{state.email}</strong> : "이 계정"} 은 아직 접근이 승인되지
              않았습니다. 관리자가 접근 승인 화면에서 주소를 추가하면 바로 들어올 수 있습니다.
            </p>
            <a href="/auth/logout" className="btn btn--subtle" style={{ width: "100%" }}>
              다른 계정으로 로그인
            </a>
          </>
        ) : (
          <>
            <h1 style={{ fontSize: 18, fontWeight: 800, letterSpacing: "-.02em", margin: "0 0 6px" }}>
              로그인
            </h1>
            <p className="t-sm t-muted" style={{ margin: "0 0 22px" }}>
              <strong>@{state?.domain ?? "…"}</strong> Google 계정 전용
            </p>

            {error && (
              <div className="banner banner--danger" style={{ textAlign: "left", marginBottom: 18 }}>
                <span className="banner__icon"><Icon name="warn" size={18} /></span>
                <div className="banner__body" style={{ color: "var(--text)" }}>{error}</div>
              </div>
            )}

            {state?.configured === false ? (
              <div className="banner banner--warn" style={{ textAlign: "left" }}>
                <span className="banner__icon"><Icon name="warn" size={18} /></span>
                <div className="banner__body" style={{ color: "var(--text)" }}>
                  Google OAuth가 아직 설정되지 않았습니다. 관리자가 자격증명을 등록해야 합니다.
                </div>
              </div>
            ) : (
              <a href="/auth/google" className="btn btn--primary btn--lg" style={{ width: "100%" }}>
                <Icon name="user" size={17} /> Google로 로그인
              </a>
            )}
          </>
        )}
      </div>
    </div>
  );
}
