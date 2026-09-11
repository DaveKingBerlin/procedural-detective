import { Link, useOutletContext } from "react-router";
import type { BackendStatus } from "../hooks/useBackendStatus";

/** "/" — welcome text plus the backend status surfaced by the shell. */
export default function Home() {
  const { state, message, readiness } = useOutletContext<BackendStatus>();

  return (
    <section className="page home">
      <h2>Welcome</h2>
      <p>
        Procedural Detective turns a short natural-language prompt into a complete, logically
        consistent 3D investigation. This app hosts the application routes and the 3D scene
        bootstrap.
      </p>

      <h3>Backend status</h3>
      <p>
        State:{" "}
        <span className={`status-text status-text--${state}`} data-testid="home-backend-status">
          {state}
        </span>
        <br />
        Message: <code data-testid="home-backend-message">{message}</code>
        {readiness && (
          <>
            <br />
            <span data-testid="home-readiness">
              Readiness: database {readiness.database} · migrations {readiness.migrations}
            </span>
          </>
        )}
      </p>

      <p>
        Try the <Link to="/scene">Scene</Link> route to walk through the placeholder apartment
        built from local 3D shapes.
      </p>
    </section>
  );
}