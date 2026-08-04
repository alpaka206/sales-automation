import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Modal } from "../ui/Modal";
import { getJSON, postForm } from "../lib/api";
import { Icon } from "../ui/Icon";
import { kst } from "../lib/format";

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

  // Admin-only, and the gate lives on the server. A 403 is the answer, not a bug.
  if (error) {
    return (
      <div className="card" style={{ maxWidth: 520 }}>
        <div className="empty">
          <div className="empty__icon"><Icon name="shield" size={24} /></div>
          <div className="empty__text">관리자만 접근할 수 있습니다.</div>
        </div>
      </div>
    );
  }
  if (isPending || !data) return <div className="skeleton" style={{ height: 200 }} />;

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

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">접근 승인</h1>
          <p className="page-sub">
            여기에 주소를 추가하는 것이 곧 권한 부여입니다 — 신청 대기열은 없습니다.
            {data.domain && ` 허용 도메인: ${data.domain}`}
          </p>
        </div>
      </div>

      <section className="card mb-gap" style={{ maxWidth: 720 }}>
        <div className="section-label" style={{ marginBottom: 12 }}>사용자 추가</div>
        <form className="row" style={{ gap: 10 }} onSubmit={(event) => void submit(event, "/settings/users/add")}>
          <input className="input" name="username" placeholder="이름" />
          <input className="input" name="email" placeholder="이메일" required />
          <select className="select" name="role" style={{ width: 140 }}>
            <option value="member">member</option>
            <option value="admin">admin</option>
          </select>
          <button className="btn btn--primary" type="submit"><Icon name="plus" size={15} /> 추가</button>
        </form>
      </section>

      <div className="card card--flush">
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th scope="col">이메일</th><th scope="col">이름</th><th scope="col">권한</th>
                  <th scope="col">마지막 로그인</th><th scope="col" /></tr>
            </thead>
            <tbody>
              {data.approved_users.length === 0 ? (
                <tr><td colSpan={5}><div className="empty"><div className="empty__text">승인된 사용자가 없습니다.</div></div></td></tr>
              ) : (
                data.approved_users.map((user) => (
                  <tr key={user.email}>
                    <td className="mono">{user.email}{user.email === data.me_email && <span className="tag" style={{ marginLeft: 6 }}>나</span>}</td>
                    <td>{user.name || "-"}</td>
                    <td><span className="tag">{user.role}</span></td>
                    <td className="tnum td-subtle">{user.last_login_at ? kst(user.last_login_at) : "—"}</td>
                    <td>
                      {/* Never for your own row: the server refuses it anyway (an admin
                          cannot lock themselves out), so offering the button would only
                          be an error message waiting to happen. */}
                      {user.email !== data.me_email && (
                        <div className="row" style={{ gap: 6 }}>
                          {user.role !== "admin" && (
                            <button type="button" className="btn btn--subtle btn--sm"
                                    onClick={() => void act(user.email, "make_admin")}>운영자로</button>
                          )}
                          {user.role !== "viewer" && (
                            <button type="button" className="btn btn--subtle btn--sm"
                                    onClick={() => void act(user.email, "make_viewer")}>조회 전용으로</button>
                          )}
                          <button type="button" className="btn btn--ghost btn--sm"
                                  onClick={() => setRemoving(user.email)}>삭제</button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Removal is not a role change: the row leaves the list entirely. */}
      {removing && (
        <Modal
          title="접근 권한을 삭제합니다"
          description={`${removing} 의 접근 권한을 삭제하시겠습니까? 목록에서 완전히 제거됩니다.`}
          onClose={() => setRemoving(null)}
          actions={
            <button type="button" className="btn btn--danger"
                    onClick={() => { void act(removing, "revoke"); setRemoving(null); }}>
              삭제
            </button>
          }
        />
      )}
    </>
  );
}
