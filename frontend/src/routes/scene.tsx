import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { discoverEvidence, getInvestigation, interactObject, readRecord } from "../api/client";
import { clearPlaythroughCredentials, getPlaythroughId, getPlaythroughToken } from "../api/playthroughToken";
import type { EvidenceReadResultDTO } from "../api/types";
import { handleEvidencePanelKey } from "../evidence/evidenceContent";
import EvidencePanel from "../evidence/evidencePanel";
import { evidencePreviewFor, type EvidencePreviewModel } from "../evidence/evidencePreview";
import type { InvestigationSceneModel } from "../scene/buildInvestigationScene";
import {
  objectiveText,
  summarizeDiscovery,
  summaryFromSession,
} from "../scene/discoverySummary";
import {
  InvestigationSession,
  type InteractionFeedback,
  type InvestigationErrorKind,
  type SessionToast,
} from "../scene/investigationFlow";
import { tooltipForHover, type ObjectTooltipModel } from "../scene/objectTooltip";
import { createInvestigationScene } from "../scene/renderInvestigation";

type PageStatus =
  | { status: "loading" }
  | { status: "no-token" }
  | { status: "ready"; model: InvestigationSceneModel }
  | {
      status: "error";
      kind: InvestigationErrorKind;
      message: string;
      tokenInvalid: boolean;
      retryable: boolean;
    };

/**
 * "/scene" — the browser investigation page.
 *
 * Reads the playthrough credential from localStorage, fetches the frozen
 * investigation bootstrap, builds the pure scene model and hands it to the
 * Babylon glue. Server state is authoritative: discovered/read flags come
 * only from the backend, and interactions only learn from the backend's
 * responses. Client state here is limited to camera/UI state, the selected
 * object, cached player-safe DTOs and loading/error state (Phase 6 N).
 */
export default function ScenePage() {
  const navigate = useNavigate();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const sessionRef = useRef<InvestigationSession | null>(null);
  const [status, setStatus] = useState<PageStatus>(() =>
    hasStoredCredential() ? { status: "loading" } : { status: "no-token" },
  );
  const [runId, setRunId] = useState(0);
  const [toast, setToast] = useState<SessionToast | null>(null);
  const [recordPanel, setRecordPanel] = useState<EvidenceReadResultDTO | null>(null);
  const [panelContext, setPanelContext] = useState<EvidencePreviewModel | null>(null);
  const [tooltip, setTooltip] = useState<(ObjectTooltipModel & { x: number; y: number }) | null>(null);
  const [interactionError, setInteractionError] = useState<string | null>(null);
  const [sceneStatus, setSceneStatus] = useState<"idle" | "ready" | "failed">("idle");
  const [hintsHidden, setHintsHidden] = useState(false);
  const [hasInteracted, setHasInteracted] = useState(false);

  const retry = () => {
    setRunId((n) => n + 1);
  };

  const resetCredential = () => {
    clearPlaythroughCredentials();
    sessionRef.current = null;
    setToast(null);
    setRecordPanel(null);
    setPanelContext(null);
    setTooltip(null);
    setInteractionError(null);
    setHasInteracted(false);
    setStatus({ status: "no-token" });
  };

  const applyFeedback = (feedback: InteractionFeedback) => {
    if (feedback.toast !== null && feedback.error === null) {
      setHasInteracted(true);
    }
    setToast(feedback.toast);
    setRecordPanel(feedback.record);
    setInteractionError(feedback.error?.message ?? null);
    // When the panel opens from an object interaction, carry the PUBLIC
    // object context (registry label + color) so the panel can identify the
    // selected object and show its small-evidence preview (Phase 8_1 D).
    if (feedback.record !== null) {
      setPanelContext(evidencePreviewFor(sessionRef.current?.sceneModel ?? null, feedback.objectId) ?? null);
    }
  };

  useEffect(() => {
    const token = getPlaythroughToken();
    const playthroughId = getPlaythroughId();
    if (!token || !playthroughId) {
      setStatus({ status: "no-token" });
      return;
    }
    const canvas = canvasRef.current;
    if (!canvas) {
      setStatus({
        status: "error",
        kind: "scene",
        message: "The 3D view is not available on this page.",
        tokenInvalid: false,
        retryable: true,
      });
      return;
    }

    let cancelled = false;
    setStatus({ status: "loading" });
    setToast(null);
    setRecordPanel(null);
    setPanelContext(null);
    setTooltip(null);
    setInteractionError(null);
    setSceneStatus("idle");

    const services = { getInvestigation, interactObject, discoverEvidence, readRecord };
    const session = new InvestigationSession(
      services,
      token,
      (sceneCanvas, model) =>
        createInvestigationScene(sceneCanvas, model, {
          onPick: (objectId) => {
            void session.interact(objectId).then((feedback) => {
              if (!cancelled) applyFeedback(feedback);
            });
          },
          onHoverStart: (objectId, origin) => {
            const next = tooltipForHover(model, objectId);
            setTooltip(next ? { ...next, x: origin?.x ?? 0, y: origin?.y ?? 0 } : null);
          },
          onHoverEnd: () => setTooltip(null),
        }),
      { playthroughId },
    );

    void session.start(canvas).then((outcome) => {
      if (cancelled) return;
      if (outcome.ok) {
        sessionRef.current = session;
        setStatus({ status: "ready", model: outcome.model });
        setSceneStatus("ready");
      } else {
        sessionRef.current = null;
        setStatus({
          status: "error",
          kind: outcome.kind,
          message: outcome.message,
          tokenInvalid: outcome.tokenInvalid,
          retryable: outcome.retryable,
        });
        if (outcome.kind === "scene") setSceneStatus("failed");
      }
    });

    return () => {
      cancelled = true;
      session.disposeScene();
      sessionRef.current = null;
    };
  }, [runId]);

  // Escape closes the evidence panel (Phase 6 L).
  useEffect(() => {
    if (recordPanel === null) return;
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (handleEvidencePanelKey(event.key) === "close") {
        event.preventDefault();
        setRecordPanel(null);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [recordPanel]);

  // Discovery toast auto-dismisses after a few seconds.
  useEffect(() => {
    if (toast === null) return;
    const timer = window.setTimeout(() => setToast(null), 4500);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const handleObjectAction = (objectId: string) => {
    const session = sessionRef.current;
    if (!session) return;
    void session.interact(objectId).then((feedback) => applyFeedback(feedback));
  };

  const dismissToast = () => {
    setToast(null);
    sessionRef.current?.dismissToast();
  };

  const knowledge = sessionRef.current?.knowledgeSnapshot;
  const summary = status.status === "ready" ? summaryFromSession(sessionRef.current, status.model) : null;
  const interacted =
    hasInteracted || (knowledge != null && knowledge.discoveredEvidenceIds.length > 0);
  const lifecycle = sessionRef.current?.bootstrapState;

  return (
    <section className="page scene">
      <h2>Investigation</h2>

      {status.status === "no-token" && <NoTokenState />}

      {status.status !== "no-token" && (
        <div className="scene-canvas-shell" data-testid="scene-canvas">
          <canvas
            ref={canvasRef}
            width={800}
            height={480}
            className="scene-canvas"
            aria-label="3D investigation scene: the location from the current playthrough. Click interactive objects in the scene to inspect them."
          />
          {tooltip && (
            <div
              className="object-tooltip"
              data-testid="object-tooltip"
              role="tooltip"
              style={{ left: tooltip.x, top: tooltip.y }}
            >
              {tooltip.label}
            </div>
          )}
        </div>
      )}

      {status.status === "loading" && (
        <p className="investigation-loading" data-testid="investigation-loading" role="status">
          Loading the investigation scene…
        </p>
      )}

      {status.status === "error" && (
        <div className="investigation-error" data-testid="investigation-error" role="alert">
          <p className="investigation-error-message">{status.message}</p>
          {status.tokenInvalid && (
            <button
              type="button"
              data-testid="reset-playthrough-token"
              onClick={resetCredential}
            >
              Reset playthrough access
            </button>
          )}
          {status.retryable && !status.tokenInvalid && (
            <button type="button" data-testid="retry-investigation" onClick={retry}>
              Try again
            </button>
          )}
        </div>
      )}

      {status.status === "ready" && (
        <div className="investigation-ready">
          <p className="objective-text" data-testid="objective-text">
            {objectiveText(summary ?? emptySummary(), interacted)}
          </p>

          {!hintsHidden && (
            <div className="controls-hint" data-testid="controls-hint">
              <span>
                Click objects in the 3D scene to inspect them — the list below is an
                accessibility fallback. Drag to orbit, scroll to zoom; Enter activates.
              </span>
              <button
                type="button"
                className="controls-hint-dismiss"
                data-testid="controls-hint-dismiss"
                onClick={() => setHintsHidden(true)}
                aria-label="Dismiss controls hint"
              >
                Hide
              </button>
            </div>
          )}

          <div className="accusation-actions" data-testid="accusation-callout">
            {lifecycle === "ACCUSED" && (
              <p className="accusation-callout-text" data-testid="accusation-callout-text">
                Your accusation is on file for this case.
              </p>
            )}
            <button
              type="button"
              data-testid="accusation-open"
              onClick={() => void navigate("/accuse")}
            >
              {accusationActionLabel(lifecycle)}
            </button>
            {lifecycle === "REVEALED" && (
              <button
                type="button"
                data-testid="reveal-view-truth"
                onClick={() => void navigate("/reveal")}
              >
                View the truth
              </button>
            )}
            {lifecycle === "ACCUSED" && (
              <button
                type="button"
                data-testid="reveal-case"
                onClick={() => void navigate("/reveal")}
              >
                Reveal the case
              </button>
            )}
          </div>

          {summary !== null && (
            <div className="discovered-summary" data-testid="discovered-summary">
              <h3>Discovered evidence</h3>
              {summary.entries.length === 0 ? (
                <p className="discovered-summary-empty">
                  Nothing discovered yet — look around and click objects to inspect them.
                </p>
              ) : (
                <ul>
                  {summary.entries.map((entry) => (
                    <li key={entry.evidenceId} data-testid={`discovered-entry-${entry.evidenceId}`}>
                      <span data-testid={`discovered-title-${entry.evidenceId}`}>{entry.title}</span>
                      {entry.read && <span className="object-discovered"> · read</span>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <div className="scene-objects" data-testid="scene-objects">
            <h3>Objects in this room</h3>
            <p className="scene-objects-fallback-note">
              Accessibility fallback — the 3D scene above is the primary way to inspect
              objects: click them directly.
            </p>
            <ul>
              {status.model.worldObjects.map((obj) =>
                obj.interactionWorks ? (
                  <li key={obj.objectId}>
                    <button
                      type="button"
                      data-testid={`object-${obj.objectId}`}
                      onClick={() => handleObjectAction(obj.objectId)}
                    >
                      {obj.label ?? obj.objectId}
                    </button>
                    <span
                      className="object-label visually-hidden"
                      data-testid={`object-label-${obj.objectId}`}
                    >
                      {obj.label ?? obj.objectId}
                    </span>
                    {obj.discovered && <span className="object-discovered"> · discovered</span>}
                  </li>
                ) : (
                  <li key={obj.objectId}>
                    <span data-testid={`object-label-${obj.objectId}`}>{obj.label ?? obj.objectId}</span>
                  </li>
                ),
              )}
            </ul>
          </div>

          {status.model.worldObjects.some((obj) => obj.unknownAsset) && (
            <p className="assets-notice" data-testid="assets-notice">
              One object in this room uses an unknown asset type and is shown as a neutral
              placeholder.
            </p>
          )}

          {sceneStatus === "ready" && (
            <p className="scene-ready" data-testid="scene-ready">
              Scene ready — click objects in the scene to inspect them; drag to orbit, scroll to zoom.
            </p>
          )}
          {sceneStatus === "failed" && (
            <p className="scene-error" data-testid="scene-error" role="alert">
              Scene could not initialize.
            </p>
          )}

          {interactionError && (
            <p className="interaction-error" data-testid="interaction-error" role="alert">
              {interactionError}
            </p>
          )}
        </div>
      )}

      {recordPanel && (
        <EvidencePanel
          record={recordPanel}
          onClose={() => setRecordPanel(null)}
          objectLabel={panelContext?.label ?? null}
          preview={panelContext}
        />
      )}

      {toast && (
        <div className="discovery-toast" data-testid="discovery-toast" role="status">
          <span data-testid="discovery-toast-text">{toast.text}</span>
          <button
            type="button"
            className="discovery-toast-dismiss"
            data-testid="discovery-toast-dismiss"
            onClick={dismissToast}
            aria-label="Dismiss notification"
          >
            Dismiss
          </button>
        </div>
      )}
    </section>
  );
}

function NoTokenState() {
  return (
    <div className="investigation-no-token" data-testid="investigation-no-token">
      <p>No playthrough access is configured on this device.</p>
      <p>
        Start a new investigation from the <Link to="/">landing page</Link> — the demo
        flow configures everything for you.
      </p>
    </div>
  );
}

/** Empty summary fallback used before the session starts (never rendered). */
function emptySummary() {
  return summarizeDiscovery([], [], [], new Map());
}

function hasStoredCredential(): boolean {
  const token = getPlaythroughToken();
  const playthroughId = getPlaythroughId();
  return token !== null && token !== "" && playthroughId !== null && playthroughId !== "";
}

/** Player-safe label for the accusation entry action (no truth values involved). */
function accusationActionLabel(state: string | null | undefined): string {
  if (state === "ACCUSED") return "Your accusation is on file — reveal it";
  if (state === "REVEALED") return "View the case reveal";
  return "Make accusation";
}