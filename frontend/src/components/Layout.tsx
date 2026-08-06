import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth";

export default function Layout() {
  const { logout } = useAuth();

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">RM</span>
          <div>
            <div className="brand-title">RCON Server Manager</div>
            <div className="brand-sub">Query · RCON · Stats</div>
          </div>
        </div>
        <nav className="nav">
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          <NavLink to="/servers">Servers</NavLink>
          <NavLink to="/settings">Settings</NavLink>
          <button className="btn ghost" onClick={() => logout()}>
            Logout
          </button>
        </nav>
      </header>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
