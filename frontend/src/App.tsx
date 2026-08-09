import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Layout from "./components/Layout";
import AccountPage from "./pages/AccountPage";
import LoginPage from "./pages/LoginPage";
import OverviewPage from "./pages/OverviewPage";
import ResetPasswordPage from "./pages/ResetPasswordPage";
import ServerDetailPage from "./pages/ServerDetailPage";
import ServersPage from "./pages/ServersPage";
import SettingsPage from "./pages/SettingsPage";
import ServerMapPage from "./pages/ServerMapPage";
import SharedChartPage from "./pages/SharedChartPage";
import SharedMapPage from "./pages/SharedMapPage";
import UsersPage from "./pages/UsersPage";

function Loading() {
  return (
    <div className="center-screen">
      <div className="spinner" />
      <p>Loading…</p>
    </div>
  );
}

function Protected({ children }: { children: React.ReactNode }) {
  const { loading, authenticated } = useAuth();
  if (loading) return <Loading />;
  if (!authenticated) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

/** Admin-only pages. Shows an explanation rather than bouncing silently -
 *  a redirect with no reason reads as a bug. */
function AdminOnly({ children }: { children: React.ReactNode }) {
  const { loading, authenticated, isAdmin } = useAuth();
  if (loading) return <Loading />;
  if (!authenticated) return <Navigate to="/login" replace />;
  if (!isAdmin) {
    return (
      <section className="card">
        <h2>Not available</h2>
        <p className="muted">
          This page is for administrators. Ask an administrator if you need access.
        </p>
      </section>
    );
  }
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      {/* Public share - no session required */}
      <Route path="/share/c/:token" element={<SharedChartPage />} />
      <Route path="/share/m/:token" element={<SharedMapPage />} />
      {/* Invite and reset links land here before the user has an account */}
      <Route path="/invite/:token" element={<ResetPasswordPage invite />} />
      <Route path="/reset/:token" element={<ResetPasswordPage />} />
      {/* Admin full-page map (same tab; “← Server” to return) */}
      <Route
        path="/server/:serverId/map"
        element={
          <Protected>
            <ServerMapPage />
          </Protected>
        }
      />
      <Route
        path="/"
        element={
          <Protected>
            <Layout />
          </Protected>
        }
      >
        <Route index element={<OverviewPage />} />
        <Route path="server/:serverId" element={<ServerDetailPage />} />
        <Route path="account" element={<AccountPage />} />
        {/* Connection settings and user administration are admin-only. The
            backend enforces this too; these guards just avoid dead pages. */}
        <Route
          path="servers"
          element={
            <AdminOnly>
              <ServersPage />
            </AdminOnly>
          }
        />
        <Route
          path="users"
          element={
            <AdminOnly>
              <UsersPage />
            </AdminOnly>
          }
        />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
