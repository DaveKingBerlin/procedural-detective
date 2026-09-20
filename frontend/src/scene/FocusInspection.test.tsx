// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import FocusInspection from "./FocusInspection";
import { FALLBACK_EVIDENCE_LABEL, PROCEDURAL_ARTIFACT_BADGE, VALIDATED_GEOMETRY_BADGE } from "./objectLabel";

// React 19 act() support in the jsdom test environment.
declare global {
  /** Enabled by test harnesses to activate React's act() support. */
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Phase 18B — inspection surface (presentational, accessible).
 *
 * Headless coverage (react-dom/server, same style as evidencePanel.test.tsx):
 *  - human-readable name is the primary player-facing text; safe fallback;
 *  - badges appear ONLY for the badge flags given (and match the honest text);
 *  - an accessible "Close inspection" button exists;
 *  - the rendered DOM never contains proc.* tokens / truth / accusation-reveal
 *    triggers.
 *
 * jsdom coverage (same style as routes/new.examples.test.tsx):
 *  - the Close button ACTIVATES the provided onClose handler.
 */

const PROC_BADGES = { procedural: true, validatedGeometry: true };
const CATALOG_BADGES = { procedural: false, validatedGeometry: false };

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
});

afterEach(() => {
  act(() => {
    root?.unmount();
  });
  container.remove();
});

function mount(label: string, badges = PROC_BADGES, onClose: () => void = () => {}): void {
  act(() => {
    root = createRoot(container);
    root.render(<FocusInspection label={label} badges={badges} onClose={onClose} />);
  });
}

function closeButton(): HTMLButtonElement {
  const button = container.querySelector<HTMLButtonElement>('[data-testid="focus-close"]');
  if (!button) throw new Error("expected focus-close button");
  return button;
}

describe("FocusInspection — headless HTML (react-dom/server)", () => {
  it("renders the human-readable object name as the primary text", () => {
    const html = renderToStaticMarkup(
      <FocusInspection label="Bronze Ceremonial Ice Pick" badges={PROC_BADGES} onClose={() => {}} />,
    );
    expect(html).toContain("Bronze Ceremonial Ice Pick");
    expect(html).toContain('data-testid="focus-inspection-name"');
    expect(html).toContain('data-testid="focus-inspection"');
  });

  it("falls back to the safe 'Evidence Object' string when the label is blank", () => {
    const html = renderToStaticMarkup(<FocusInspection label="   " badges={CATALOG_BADGES} onClose={() => {}} />);
    expect(html).toContain(FALLBACK_EVIDENCE_LABEL);
    expect(html).not.toContain("undefined");
  });

  it("renders BOTH badges for a procedural artifact and NEITHER for a catalog object", () => {
    const procHtml = renderToStaticMarkup(
      <FocusInspection label="Bronze Ceremonial Ice Pick" badges={PROC_BADGES} onClose={() => {}} />,
    );
    expect(procHtml).toContain(PROCEDURAL_ARTIFACT_BADGE);
    expect(procHtml).toContain(VALIDATED_GEOMETRY_BADGE);
    expect(procHtml).toContain('data-testid="focus-badge-procedural"');
    expect(procHtml).toContain('data-testid="focus-badge-validated"');

    const catalogHtml = renderToStaticMarkup(
      <FocusInspection label="Kitchen knife" badges={CATALOG_BADGES} onClose={() => {}} />,
    );
    expect(catalogHtml).not.toContain(PROCEDURAL_ARTIFACT_BADGE);
    expect(catalogHtml).not.toContain(VALIDATED_GEOMETRY_BADGE);
    expect(catalogHtml).not.toContain("focus-badge-procedural");
    expect(catalogHtml).not.toContain("focus-badge-validated");
  });

  it("exposes the accessible Close inspection button", () => {
    const html = renderToStaticMarkup(
      <FocusInspection label="Bronze Ceremonial Ice Pick" badges={PROC_BADGES} onClose={() => {}} />,
    );
    expect(html).toContain('aria-label="Close inspection"');
    expect(html).toContain('data-testid="focus-close"');
    expect(html).toContain("autofocus"); // keyboard focus moves into the surface
  });

  it("renders NO proc.* / assetId / hash / truth / accusation-reveal tokens", () => {
    const html = renderToStaticMarkup(
      <FocusInspection label="Bronze Ceremonial Ice Pick" badges={PROC_BADGES} onClose={() => {}} />,
    );
    for (const token of [
      "proc.",
      "decor.",
      "a1b2c3d4e5f60718",
      "truth",
      "winner",
      "correct",
      "murderer",
      "accusation",
      "reveal",
      "Becker",
    ]) {
      expect(html.toLowerCase(), `no ${token}`).not.toContain(token.toLowerCase());
    }
  });
});

describe("FocusInspection — interaction (jsdom)", () => {
  it("the Close inspection button calls onClose exactly once", () => {
    const onClose = vi.fn();
    mount("Bronze Ceremonial Ice Pick", CATALOG_BADGES, onClose);
    act(() => {
      closeButton().dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("keyboard focus lands on the native Close button (no mouse-only dependency)", () => {
    mount("Bronze Ceremonial Ice Pick", PROC_BADGES);
    const button = closeButton();
    // autoFocus moves keyboard/screen-reader focus into the inspection surface...
    expect(document.activeElement).toBe(button);
    // ...and it is a REAL native button, so browsers provide Enter/Space
    // activation natively (the click test above proves the handler wiring).
    expect(button.tagName).toBe("BUTTON");
    expect(button.type).toBe("button");
    expect(button.getAttribute("aria-label")).toBe("Close inspection");
  });

  it("the mounted DOM still contains the semantic label + badges (no proc.* leak)", () => {
    mount("Bronze Ceremonial Ice Pick", PROC_BADGES);
    expect(container.textContent).toContain("Bronze Ceremonial Ice Pick");
    expect(container.textContent).toContain(PROCEDURAL_ARTIFACT_BADGE);
    expect(container.textContent).toContain(VALIDATED_GEOMETRY_BADGE);
    expect(container.textContent).not.toContain("proc.");
  });
});