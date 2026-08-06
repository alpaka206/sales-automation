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
    items: [
      { to: "/customers?stage=negotiation", label: "협상중 고객", icon: "flame" },
      { to: "/customers", label: "리드 히스토리", icon: "users", end: true, mobileLabel: "고객" },
      { to: "/won-customers", label: "수주 고객", icon: "globe" },
    ],
  },
  {
    title: "인사이트",
    icon: "bolt",
    items: [
      { to: "/operations", label: "리드 추이", icon: "globe", mobileLabel: "인사이트" },
      { to: "/operations#updates", label: "고객 인사이트", icon: "bell" },
    ],
  },
  {
    title: "활용 툴",
    icon: "briefcase",
    items: [
      { to: "/tools/quote-calculator", label: "견적 계산기", icon: "sliders" },
      { to: "/tools/quotation", label: "견적서", icon: "file" },
      { to: "/tools/contract", label: "계약서", icon: "file" },
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
  // 협상중 고객 and 리드 히스토리 are the same path; only the query separates them, and
  // NavLink compares paths alone — so the active test is made here.
  const [path, query] = entry.to.split("?");
  const active =
    location.pathname === path &&
    (query ? location.search.includes(query) : !location.search.includes("stage=negotiation"));
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
          <nav className="sidebar__nav" aria-label="주요 메뉴">
            <NavLink
              to="/overview"
              className={({ isActive }) =>
                `nav-item nav-item--mobile-primary${isActive ? " is-active" : ""}`
              }
            >
              <Icon name="dashboard" size={17} />
              <span className="nav-item__label">전체 대시보드</span>
              <span className="nav-item__mobile-label">전체</span>
            </NavLink>
            {SECTIONS.map((section) => {
              const current = section.items.some((item) =>
                location.pathname.startsWith(item.to.split("?")[0].replace(/^\/$/, "/@never")),
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
              (`main_class`); here the one screen that wants it says so. */}
          <main
            className={`main${location.pathname === "/" ? " main--fit" : ""}`}
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
