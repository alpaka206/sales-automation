import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Modal } from "../ui/Modal";
import { getJSON, postForm, HttpError } from "../lib/api";
import { Icon } from "../ui/Icon";
import { kst } from "../lib/format";
import { DataTable } from "../ui/DataTable";
import { ActionButton, useAction } from "../ui/ActionButton";
import { Loading } from "../ui/Loading";

type User = { email: string; name: string; role: string; approved: boolean; last_login_at: string | null };
type Data = { approved_users: User[]; me_email: string; domain: string };

export function SettingsUsers() {
  const queryClient = useQueryClient();
  const [removing, setRemoving] = useState<string | null>(null);
  const { data, isPending, error } = useQuery({
    queryKey: ["settings-users"],
    queryFn: () => getJSON<Data>("/api/ui/settings/users"),
    retry: false,
  });

  // Admin-only, and the gate lives on the server. 403 is the answer, not a bug —
  // 하지만 그 외의 실패까지 같은 문장으로 그리면, 서버가 터졌을 때 관리자에게 권한이
  // 없다고 말하게 됩니다. 실제로 그랬습니다: 이 화면은 500 을 내면서 "관리자만 접근할 수
  // 있습니다" 를 띄우고 있었고, 그래서 아무도 원인을 못 봤습니다.
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
  if (isPending || !data) return <Loading columns={5} />;

  async function act(email: string, action: string) {
    await postForm(`/settings/users/${encodeURIComponent(email)}`, { action });
    await queryClient.invalidateQueries({ queryKey: ["settings-users"] });
  }

  async function submit(event: React.FormEvent<HTMLFormElement>, path: string) {
    event.preventDefault();
    const form = event.currentTarget;
    await postForm(path, Object.fromEntries(new FormData(form) as never) as Record<string, string>);
    form.reset();
    await queryClient.invalidateQueries({ queryKey: ["settings-users"] });
  }

  const [addUser, adding] = useAction((event: React.FormEvent<HTMLFormElement>) =>
    submit(event, "/settings/users/add"));

  return (
    <>
      <div className="page-header">
        <div><h1 className="page-title">접근 승인</h1></div>
      </div>

      <section className="card mb-gap" style={{ maxWidth: 720 }}>
        <div className="section-label" style={{ marginBottom: 12 }}>사용자 추가</div>
        <form className="row" style={{ gap: 10 }} onSubmit={addUser}>
          {/* 도메인은 고정입니다 — 서버가 붙입니다. 칸을 둘로 두면 "이름" 이 실제로는
              메일 아이디였고, 도메인은 화면 위 안내문에만 적혀 있었습니다. */}
          <div className="row" style={{ gap: 4, flex: 1, minWidth: 0 }}>
            <input className="input mono" name="username" placeholder="메일 아이디"
                   required style={{ flex: 1, minWidth: 0 }} />
            {data.domain && (
              <span className="t-subtle mono" style={{ whiteSpace: "nowrap" }}>@{data.domain}</span>
            )}
          </div>
          {/* 권한은 둘뿐입니다. "member" 를 고르면 normalize_role 이 admin 으로 풀어서,
              조회 전용을 주려던 사람에게 전체 접근이 나갔습니다. 아래 버튼과 같은 말로. */}
          <select className="select" name="role" style={{ width: 140 }}>
            <option value="admin">운영자</option>
            <option value="viewer">조회 전용</option>
          </select>
          <button className="btn btn--primary" type="submit" disabled={adding} aria-busy={adding || undefined}>
            {adding ? <><span className="spinner" role="status" /> 추가 중</>
                    : <><Icon name="plus" size={15} /> 추가</>}
          </button>
        </form>
      </section>

      <div className="card card--flush">
        <DataTable
          columns={[
            {
              label: "이메일",
              width: "34%",
              className: "mono",
              cell: (user) => (
                <>
                  {user.email}
                  {user.email === data.me_email && <span className="tag" style={{ marginLeft: 6 }}>나</span>}
                </>
              ),
            },
            { label: "이름", width: "18%", cell: (user) => user.name || "-" },
            { label: "권한", width: "12%", cell: (user) => <span className="tag">{user.role}</span> },
            { label: "마지막 로그인", width: "16%", className: "tnum td-subtle",
              cell: (user) => (user.last_login_at ? kst(user.last_login_at) : "—") },
            {
              width: "20%",
              // Never for your own row: the server refuses it anyway (an admin cannot
              // lock themselves out), so offering the button would only be an error
              // message waiting to happen.
              cell: (user) => user.email === data.me_email ? null : (
                <div className="row" style={{ gap: 6 }}>
                  {user.role !== "admin" && (
                    <ActionButton className="btn btn--subtle btn--sm" pending="바꾸는 중"
                                  onClick={() => act(user.email, "make_admin")}>운영자로</ActionButton>
                  )}
                  {user.role !== "viewer" && (
                    <ActionButton className="btn btn--subtle btn--sm" pending="바꾸는 중"
                                  onClick={() => act(user.email, "make_viewer")}>조회 전용으로</ActionButton>
                  )}
                  <button type="button" className="btn btn--ghost btn--sm"
                          onClick={() => setRemoving(user.email)}>삭제</button>
                </div>
              ),
            },
          ]}
          rows={data.approved_users}
          rowKey={(user) => user.email}
          empty="승인된 사용자가 없습니다."
        />
      </div>

      {/* Removal is not a role change: the row leaves the list entirely. */}
      {removing && (
        <Modal
          title="접근 권한을 삭제합니다"
          description={`${removing} 의 접근 권한을 삭제하시겠습니까? 목록에서 완전히 제거됩니다.`}
          onClose={() => setRemoving(null)}
          actions={
            // 모달은 끝난 뒤에 닫습니다. 먼저 닫으면 "삭제 중" 을 볼 자리가 사라지고,
            // 실패해도 목록만 그대로인 채 아무 말이 없습니다.
            <ActionButton className="btn btn--danger" pending="삭제 중"
                          onClick={() => act(removing, "revoke").then(() => setRemoving(null))}>
              삭제
            </ActionButton>
          }
        />
      )}
    </>
  );
}
