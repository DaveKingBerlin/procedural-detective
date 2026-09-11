import { Link, NavLink, Outlet } from "react-router";
import type { BackendStatus } from "./hooks/useBackendStatus";
import { useBackendStatus } from "./hooks/useBackendStatus";

/** Application shell: header, navigation, backend status indicator, routed content. */
export default function App() {
  const status = useBackendStatus();

  return (
    <div className="app">
      <header className="app-header">
        <Link to="/" className="app-brand">
          Procedural Detective
        </Link>
        <nav className="app-nav" aria-label="Primary">
          <NavLink
            to="/"
            end
            className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
          >
            Home
          </NavLink>
          <NavLink
            to="/scene"
            className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
          >
            Scene
          </NavLink>
        </nav>
        <BackendStatusIndicator {...status} />
      </header>
      <main className="app-main">
        <Outlet context={status} />
      </main>
    </div>
  );
}

function BackendStatusIndicator({ state, message }: BackendStatus) {
  return (
    <div
      className={`backend-status backend-status--${state}`}
      data-testid="backend-status"
      title={`Backend: ${state}`}
    >
      <span className="backend-status-dot" aria-hidden="true" />
      <span data-testid="backend-status-message">{message}</span>
    </div>
  );
}