import { NavLink, Outlet, useLocation } from "react-router-dom";
import { Icon } from "../ui/Icon";

// The sidebar, one definition. Same order and wording as partials/nav.html — 전체
// 대시보드 outside every section, 고객 관리 above 인사이트 — because this replaces that
// file, it does not get to disagree with it.
// `mobile` marks the four destinations the phone bar keeps; `mobileLabel` is the
// shorter word that fits under a 17px icon.
type Entry = { to: string; label: string; icon: string; end?: boolean; mobileLabel?: string };
type Section = { title: string; icon: string; items: Entry[] };

const SECTIONS: Section[] = [
  {
    title: "인바운드 리드",
    icon: "inbound",
    items: [
      { to: "/", label: "문의 대시보드", icon: "dashboard", end: true, mobileLabel: "인바운드" },
      { to: "/messages", label: "회신 및 검토", icon: "messages" },
      { to: "/email-templates", label: "이메일 템플릿", icon: "mail" },
    ],
  },
  {
    title: "고객 관리",
    icon: "users",
    // 손이 가는 순서입니다: 이미 수주한 곳 → 지나간 리드 전부.
    // 맨 아래 것은 지나간 리드까지 전부 담은 목록이라 매일 여는 화면이 아닙니다.
    items: [
      { to: "/won-customers", label: "수주 고객", icon: "globe" },
      { to: "/customers", label: "리드 히스토리", icon: "users", end: true, mobileLabel: "고객" },
    ],
  },
  {
    title: "인사이트",
    icon: "bolt",
    // 문의 수·국가별 막대를 그리던 항목이 하나 더 있었는데 안 보는 화면이라 지웠습니다.
    // 남은 것은 손이 가야 하는 목록뿐이고, 그래서 그 페이지가 곧 고객 인사이트입니다.
    items: [
      { to: "/operations", label: "고객 인사이트", icon: "bell", mobileLabel: "인사이트" },
    ],
  },
  {
    title: "시스템",
    icon: "settings",
    items: [
      { to: "/settings/users", label: "접근 승인", icon: "shield" },
      { to: "/logs", label: "운영 로그", icon: "file" },
    ],
  },
];

function NavItem({ entry, pending }: { entry: Entry; pending?: number }) {
  const location = useLocation();
  // 활성 판정을 NavLink 에 맡기지 않고 여기서 만드는 이유는 하위 경로입니다.
  //
  // 하위 경로도 같은 화면입니다: `/won-customers/2102` 에서 상세를 보고 있어도 왼쪽 nav 는
  // 수주 고객을 가리켜야 합니다. 정확 일치만 보면 상세로 들어가는 순간 사이드바가 아무
  // 데도 강조하지 않아서, 내가 어느 화면에 있는지가 사라집니다.
  //
  // `/` 를 따로 예외 처리하지 않습니다: 접두사를 `path + "/"` 로 보므로 루트는 `"//"` 가
  // 되어 어디에도 안 걸리고, 결국 정확 일치로만 켜집니다. 형제 경로도 안전합니다 —
  // `/won-customers` 는 `/customers/` 로 시작하지 않습니다.
  const path = entry.to;
  const active =
    location.pathname === path || location.pathname.startsWith(path + "/");
  return (
    <NavLink
      to={entry.to}
      className={`nav-item ${entry.mobileLabel ? "nav-item--mobile-primary" : "nav-item--secondary"}${
        active ? " is-active" : ""
      }`}
    >
      <Icon name={entry.icon} size={17} />
      <span className="nav-item__label">{entry.label}</span>
      {entry.mobileLabel && <span className="nav-item__mobile-label">{entry.mobileLabel}</span>}
      {pending ? <span className="nav-item__badge tnum">{pending}</span> : null}
    </NavLink>
  );
}

export function Shell({ pending }: { pending?: number }) {
  const location = useLocation();
  return (
    <>
      <a href="#main" className="skip-link">본문으로 건너뛰기</a>
      <div className="app-shell">
        <aside className="sidebar">
          <div className="sidebar__top">
            <button
              type="button"
              className="sidebar-toggle"
              aria-label="메뉴 접기/펼치기"
              title="메뉴 접기/펼치기"
              onClick={() => {
                const collapsed = document.documentElement.classList.toggle("nav-collapsed");
                try {
                  localStorage.setItem("perso.nav", collapsed ? "collapsed" : "open");
                } catch {
                  /* storage blocked — the toggle still works for this page */
                }
              }}
            >
              <Icon name="panel" size={17} />
            </button>
          </div>
          {/* 섹션 밖에 링크가 하나 더 있었습니다 — 각 화면의 숫자를 모아 보여 주기만 하는
              자리라 안 보게 되었고, `/overview` 와 함께 지웠습니다. */}
          <nav className="sidebar__nav" aria-label="주요 메뉴">
            {SECTIONS.map((section) => {
              const current = section.items.some((item) =>
                location.pathname.startsWith(item.to.replace(/^\/$/, "/@never")),
              );
              return (
                <section key={section.title} className={`nav-section${current ? " is-current" : ""}`}>
                  <div className="nav-section__title">
                    <Icon name={section.icon} size={17} />
                    <span>{section.title}</span>
                  </div>
                  {section.items.map((item) => (
                    <NavItem
                      key={item.to}
                      entry={item}
                      pending={item.to === "/messages" || item.end ? pending : undefined}
                    />
                  ))}
                </section>
              );
            })}
          </nav>
        </aside>
        <div className="content">
          {/* main--fit is the dashboard's scroll model: the board fills exactly the space
              the queue leaves instead of running past the fold. It was a Jinja block
              (`main_class`); here the screens that want it say so.
              티켓 세부 내역도 같은 모델입니다 — 초안과 요약이 각자 스크롤하려면 둘을 감싼
              틀이 화면 높이에 묶여 있어야 합니다. */}
          <main
            className={`main${
              location.pathname === "/" ||
              /^\/(messages|tickets)\/\d+$/.test(location.pathname)
                ? " main--fit"
                : ""
            }`}
            id="main"
            tabIndex={-1}
          >
            <Outlet />
          </main>
        </div>
      </div>
    </>
  );
}
