import { Link } from "react-router";

/** 404 fallback for unknown routes. */
export default function NotFound() {
  return (
    <section className="page not-found" data-testid="not-found">
      <h2>404 — page not found</h2>
      <p>The route you requested does not exist in this application.</p>
      <p>
        <Link to="/">Back to Home</Link>
      </p>
    </section>
  );
}