import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getJSON, postForm, HttpError } from "../lib/api";
import { Link } from "react-router-dom";
import { Icon } from "../ui/Icon";
import { kst } from "../lib/format";
import { Loading } from "../ui/Loading";
import { DeleteDialog } from "../ui/DeleteDialog";
import { ActionButton, SubmitButton, useAction } from "../ui/ActionButton";

type Account = {
  email: string;
  enabled: boolean;
  /** 지금 수집 대상인가 — 켜져 있고 끊기지 않았을 때만 참입니다. **서버가 판단합니다**:
   *  화면이 두 조건을 조합하면 수집기가 보는 목록과 화면이 그리는 목록이 언젠가 갈립니다. */
  ready: boolean;
  last_error: string | null;
  connected_by: string | null;
  connected_at: string;
  last_polled_at: string | null;
};
type PendingLink = {
  external_id: string; mailbox: string; contact: string; contact_email: string;
  subject: string; preview: string; happened_at: string;
  conversation_id: number; ticket_id: string | null; ticket_subject: string;
};
type Data = {
  /** 「연결할까요?」 — 후보 티켓은 **가장 최근 것 하나**입니다(운영자 지시). 연락처는
   *  아는데 티켓이 없는 메일은 여기 안 뜹니다: 그건 이미 리드 히스토리의 한 줄입니다. */
  pending_links: PendingLink[];
  configured: boolean;
  /** 주소만으로 추가할 수 있나 — **그 사람이 로그인할 필요가 없는 길**입니다
   *  (도메인 전체 위임). 서비스 계정 열쇠가 있을 때만 참입니다. */
  delegation: boolean;
  scope: string;
  accounts: Account[];
};

/** 메일함 연결 — 사서함마다 한 번 동의를 받아 둡니다 (2026-09-07 운영자 지시).
 *
 *  **토큰은 화면에 안 옵니다.** 그 사람이 구글에 로그인하고 동의를 누르면 서버가 콜백에서
 *  받아 암호화해 담습니다 — 값을 복사해 붙이는 단계가 없습니다.
 */
export function SettingsMailboxes() {
  const queryClient = useQueryClient();
  const [removing, setRemoving] = useState<string | null>(null);
  const { data, isPending, error } = useQuery({
    queryKey: ["mailboxes"],
    queryFn: () => getJSON<Data>("/api/ui/mailboxes"),
    retry: false,
  });

  // 연결·해제는 서버가 이 화면으로 되돌려 보냅니다(303). 돌아온 주소에 결과가 실려 있어서
  // 화면이 그걸 읽고 한 줄 적습니다 — 새 창을 띄우지 않는 이유는 구글 로그인이 팝업
  // 차단에 걸리기 때문입니다.
  const params = new URLSearchParams(window.location.search);
  const outcome = params.get("mailbox");
  const detail = params.get("detail") || "";
  const connected = params.get("email") || "";

  async function act(path: string, body: Record<string, string>) {
    await postForm(path, body);
    await queryClient.invalidateQueries({ queryKey: ["mailboxes"] });
  }

  // 훅은 early return 보다 **위**에 있어야 합니다 — 아래에 두면 로딩 렌더에서 건너뛰고
  // 데이터가 온 렌더에서 부르게 되어 React 가 훅 수가 달라졌다고 터집니다(#310).
  const [addMailbox, addingMailbox] = useAction(
    async (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      const form = event.currentTarget;
      await act("/integrations/mailboxes/add",
                Object.fromEntries(new FormData(form) as never) as Record<string, string>);
      form.reset();
    },
  );

  if (error) {
    const denied = error instanceof HttpError && error.status === 403;
    return (
      <div className="card" style={{ maxWidth: 520 }}>
        <div className="empty">
          <div className="empty__icon"><Icon name="shield" size={24} /></div>
          <div className="empty__text">
            {denied ? "관리자만 접근할 수 있습니다." : "목록을 불러오지 못했습니다."}
          </div>
          {!denied && <div className="t-xs t-subtle">{error.message}</div>}
        </div>
      </div>
    );
  }
  if (isPending || !data) return <Loading columns={4} />;

  return (
    <>
      <div className="row-between" style={{ marginBottom: 16 }}>
        <h1 className="page-title">메일함 연결</h1>
        {data.configured && (
          <a className="btn btn--primary btn--sm" href="/integrations/mailboxes/connect">
            <Icon name="plus" size={14} /> 메일함 추가
          </a>
        )}
      </div>

      {outcome === "added" && (
        <div className="card" style={{ marginBottom: 12 }}>
          <strong>{connected}</strong> 을(를) 추가했습니다. 그 사람의 동의는 필요 없습니다 —
          서비스 계정이 대신 엽니다. <strong>다만 관리자가 도메인 전체 위임을 등록해 두지
          않았으면 첫 수집에서 실패하고</strong>, 그 이유가 아래 줄에 적힙니다.
        </div>
      )}
      {outcome === "connected" && (
        <div className="card" style={{ marginBottom: 12 }}>
          {/* **저장된 주소를 크게 적습니다.** 관리자가 자기 계정으로 로그인해 둔
              브라우저에서는 구글이 계정을 안 묻고 조용히 그 계정으로 동의해 버릴 수
              있습니다 — 「untae 연결」을 눌렀는데 관리자 사서함이 붙습니다. 계정 고르개를
              띄우도록 요청하지만(`select_account`) 그래도 틀릴 수 있고, 그때 알아챌
              유일한 자리가 여기입니다. */}
          <strong>{connected}</strong> 사서함을 연결했습니다. 의도한 계정이 맞는지 확인해
          주세요 — 다르면 해제하고 다시 연결하면 됩니다.
        </div>
      )}
      {(outcome === "error" || outcome === "setup_required") && (
        <div className="card" style={{ marginBottom: 12 }}>
          <span className="t-danger">연결하지 못했습니다.</span>{" "}
          <span className="t-xs t-subtle">{detail}</span>
        </div>
      )}

      {/* **개인함으로 온 메일 한 통에 한 번 묻습니다** (2026-09-08 운영자 지시).
          붙이는 코드는 원래 있었고 안 돌던 이유가 하나였습니다 — 그 연락처에 티켓이
          여럿이면 어느 대화의 것인지 코드가 알 수 없고, **잘못 붙으면 남의 티켓에 남의
          메일이 서는데 아무도 그걸 모릅니다.** 그 한 번의 판단을 사람이 합니다. */}
      {data.pending_links.length > 0 && (
        <div className="card" style={{ marginBottom: 12 }}>
          <div className="section-label" style={{ marginBottom: 10 }}>
            티켓에 연결할까요? <span className="tnum">{data.pending_links.length}건</span>
          </div>
          <div className="history-list">
            {data.pending_links.map((item) => (
              <div key={item.external_id} className="row-between"
                   style={{ padding: "10px 0", borderTop: "1px solid var(--border)", gap: 12 }}>
                <div style={{ minWidth: 0 }}>
                  <div className="t-xs t-subtle">
                    {item.mailbox} · {kst(item.happened_at)}
                  </div>
                  <strong className="truncate">{item.subject || "(제목 없음)"}</strong>
                  <div className="t-xs t-subtle truncate">
                    {item.contact || item.contact_email} — {item.preview}
                  </div>
                  <div className="t-xs">
                    →{" "}
                    <Link to={`/tickets/${item.conversation_id}`}>
                      {item.ticket_subject || `티켓 ${item.ticket_id ?? item.conversation_id}`}
                    </Link>
                  </div>
                </div>
                <div className="row" style={{ gap: 6, flexShrink: 0 }}>
                  {/* 「연결」은 우리 줄에 티켓을 채우고 **허브스팟 티켓에도 노트를
                      남깁니다**. 「아니요」도 저장합니다 — 안 적으면 회차마다 다시
                      물어봅니다. */}
                  <ActionButton className="btn btn--subtle btn--sm" pending="연결 중"
                                onClick={() => act("/integrations/mailboxes/link",
                                                   { external_id: item.external_id,
                                                     conversation_id: String(item.conversation_id) })}>
                    연결
                  </ActionButton>
                  <ActionButton className="btn btn--ghost btn--sm" pending="처리 중"
                                onClick={() => act("/integrations/mailboxes/link",
                                                   { external_id: item.external_id,
                                                     conversation_id: "" })}>
                    아니요
                  </ActionButton>
                </div>
              </div>
            ))}
          </div>
          <p className="t-xs t-subtle" style={{ marginBottom: 0 }}>
            연결하면 이 티켓의 기록에 서고 허브스팟 티켓에도 노트로 남습니다.
            「아니요」를 눌러도 그 메일은 리드 히스토리에 그대로 있습니다 — 티켓에만 안
            붙습니다.
          </p>
        </div>
      )}

      {!data.configured && (
        <div className="card" style={{ marginBottom: 12 }}>
          Google OAuth 클라이언트가 설정되지 않아 연결할 수 없습니다
          (<code>GOOGLE_OAUTH_CLIENT_ID</code> · <code>GOOGLE_OAUTH_CLIENT_SECRET</code>).
        </div>
      )}

      {/* **주소만으로 추가** — 그 사람이 로그인할 필요가 없는 길입니다 (2026-09-07 운영자
          요구: 「운태가 로그인 하지 않아도 운태의 기록을 불러올 수 있도록」). 서비스 계정이
          그 사람을 가장하고, 사람마다 받던 동의를 Workspace 슈퍼관리자가 한 번 대신
          해 둡니다. 보관할 refresh token 이 없어서 비밀번호 변경으로 죽지도 않습니다. */}
      {data.delegation && (
        <div className="card" style={{ marginBottom: 12 }}>
          <div className="section-label" style={{ marginBottom: 8 }}>주소로 추가</div>
          <form className="row" style={{ gap: 8 }} onSubmit={addMailbox}>
            <input className="input" name="email" type="email" required
                   placeholder="untae@estsoft.com" style={{ flex: 1 }} />
            <SubmitButton busy={addingMailbox}>추가</SubmitButton>
          </form>
          <p className="t-xs t-subtle" style={{ marginBottom: 0 }}>
            본인 로그인이 필요 없습니다. <strong>Workspace 슈퍼관리자가 Admin console →
            보안 → API 제어 → 도메인 전체 위임</strong>에 이 서비스 계정의 client ID 와{" "}
            <code>{data.scope}</code> 를 등록해 두어야 열립니다 — 등록 전에 추가해 두어도
            되고, 안 되어 있으면 아래 줄에 그렇게 적힙니다.
          </p>
        </div>
      )}

      <div className="card">
        <p className="t-xs t-subtle" style={{ marginTop: 0 }}>
          도메인 전체 위임을 못 쓰는 계정(개인 <code>@gmail.com</code> 등)은 「메일함
          추가」로 붙입니다 — 구글 로그인 화면이 뜨고 <strong>그 사서함의 주인이 자기
          계정으로 로그인해 동의</strong>해야 합니다. 요청하는 권한은 읽기 전용
          (<code>{data.scope}</code>)이라 어느 쪽이든 이 연결로 메일이 나가지 않습니다.
        </p>

        {data.accounts.length === 0 ? (
          <div className="empty"><div className="empty__text">연결된 메일함이 없습니다.</div></div>
        ) : (
          <div className="history-list" style={{ marginTop: 12 }}>
            {data.accounts.map((account) => (
              <div key={account.email} className="row-between"
                   style={{ padding: "10px 0", borderTop: "1px solid var(--border)", gap: 12 }}>
                <div style={{ minWidth: 0 }}>
                  <div className="row" style={{ gap: 6 }}>
                    <strong className="truncate">{account.email}</strong>
                    {account.ready
                      ? <span className="pill pill--sm">수집 중</span>
                      : account.last_error
                        ? <span className="pill pill--danger pill--sm">재연결 필요</span>
                        : <span className="pill pill--sm">꺼둠</span>}
                  </div>
                  {/* **끊긴 이유를 적습니다.** Gmail 토큰은 다른 스코프와 달리 비밀번호를
                      바꾸면 죽습니다 — 로그에만 남기면 수집이 멈춘 줄 아무도 모릅니다. */}
                  {account.last_error && (
                    <div className="t-xs t-danger">{account.last_error}</div>
                  )}
                  <div className="t-xs t-subtle">
                    연결 {kst(account.connected_at)}
                    {account.connected_by ? ` · ${account.connected_by}` : ""}
                    {/* 「연결은 됐는데 아무것도 안 오는」 것과 「도는데 새 메일이 없는」
                        것은 다른 이야기입니다. */}
                    {account.last_polled_at
                      ? ` · 마지막 수집 ${kst(account.last_polled_at)}`
                      : " · 아직 수집한 적 없음"}
                  </div>
                </div>
                <div className="row" style={{ gap: 6, flexShrink: 0 }}>
                  {/* 끄기는 **연결을 지우지 않습니다** — 다시 켜는 데 재동의가 필요 없어야
                      합니다. `enabled` 를 빈 문자열이 아니라 "0"/"1" 로 보내는 이유:
                      빈 폼 값은 중간에서 사라져 「켜기」가 조용히 「끄기」가 됩니다. */}
                  <ActionButton className="btn btn--subtle btn--sm"
                                pending={account.enabled ? "끄는 중" : "켜는 중"}
                                onClick={() => act("/integrations/mailboxes/toggle",
                                                   { email: account.email,
                                                     enabled: account.enabled ? "0" : "1" })}>
                    {account.enabled ? "끄기" : "켜기"}
                  </ActionButton>
                  {account.last_error && (
                    <a className="btn btn--subtle btn--sm"
                       href={`/integrations/mailboxes/connect?email=${encodeURIComponent(account.email)}`}>
                      재연결
                    </a>
                  )}
                  <button type="button" className="btn btn--subtle btn--sm"
                          onClick={() => setRemoving(account.email)}>
                    <Icon name="trash" size={14} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {removing && (
        <DeleteDialog
          name={removing}
          warning="토큰을 지웁니다. 다시 쓰려면 그 계정으로 한 번 더 동의해야 합니다. 구글 계정 쪽 권한은 그대로 남습니다."
          onCancel={() => setRemoving(null)}
          onConfirm={async () => {
            await act("/integrations/mailboxes/disconnect", { email: removing });
            setRemoving(null);
          }}
        />
      )}
    </>
  );
}
