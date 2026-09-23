import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { getInvestigation, interactObject, readRecord } from "../api/client";
import { clearPlaythroughCredentials, getPlaythroughId, getPlaythroughToken } from "../api/playthroughToken";
import type { EvidenceReadResultDTO } from "../api/types";
import { getCatalogError } from "../catalog/assetCatalog";
import { getKit, hasKit, isKitCatalogHealthy } from "../environments/kitCatalog";
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
import {
  discoveredCaptionsForWorld,
  type ObjectCaptionModel,
} from "../scene/objectCaption";
import { focusCloseKey } from "../scene/focusCamera";
import { evidenceLabelFor, focusBadgesFor } from "../scene/objectLabel";
import FocusInspection from "../scene/FocusInspection";
import { tooltipForHover, type ObjectTooltipModel } from "../scene/objectTooltip";
import { createInvestigationScene } from "../scene/renderInvestigation";
import type { InvestigationSceneHandle } from "../scene/renderInvestigation";
import { loadHypothesis, saveHypothesis, type HypothesisPins } from "../notebook/hypothesisStore";
import { buildNotebookModel } from "../notebook/notebookModel";
import NotebookPanel from "../notebook/NotebookPanel";

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
  const sceneHandleRef = useRef<InvestigationSceneHandle | null>(null);
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
  /**
   * Phase 15 Track B — the currently selected world object (the one whose
   * evidence panel is open): its 3D emissive/ring persists until the panel
   * closes, and the object-list button shows the focus state.
   */
  const [selectedObjectId, setSelectedObjectId] = useState<string | null>(null);
  /**
   * Projected caption positions for DISCOVERED evidence (fractions of the
   * canvas, from the live Babylon camera via projectObjectPoint). At most one
   * setState per poll when positions actually moved.
   */
  const [captionPos, setCaptionPos] = useState<Readonly<Record<string, { x: number; y: number }>>>({});
  /**
   * Phase 18C — Detective Notebook drawer state. `notebookOpen` is pure UI;
   * `notebookRev` bumps when the lazy read-record hydration lands so the
   * notebook re-derives from the now-populated record cache; `pins` are the
   * PLAYER-NOTES-ONLY hypothesis pins (namespaced localStorage, never sent).
   */
  const [notebookOpen, setNotebookOpen] = useState(true);
  const [notebookRev, setNotebookRev] = useState(0);
  const [pins, setPins] = useState<HypothesisPins>(() => loadHypothesis(getPlaythroughId() ?? ""));

  const retry = () => {
    setRunId((n) => n + 1);
  };

  const resetCredential = () => {
    clearPlaythroughCredentials();
    sessionRef.current = null;
    sceneHandleRef.current = null;
    setToast(null);
    setRecordPanel(null);
    setPanelContext(null);
    setTooltip(null);
    setInteractionError(null);
    setHasInteracted(false);
    setSelectedObjectId(null);
    setCaptionPos({});
    setStatus({ status: "no-token" });
  };

  const applyFeedback = (feedback: InteractionFeedback) => {
    if (feedback.toast !== null && feedback.error === null) {
      setHasInteracted(true);
    }
    // DEF-072: after every server-confirmed interaction (discovery OR read) the
    // session has merged the returned knowledge (discoveredEvidenceIds /
    // readEvidenceIds) into its scene model — re-sync it into React state so
    // the object-list markers and the discovered-only caption overlay flip
    // IMMEDIATELY (no reload needed). The flags stay server-authoritative:
    // knowledge only ever grows with ids the server returned, and the reference
    // stability of the merge makes no-change cases (409 errors, decorative
    // objects, already-discovered repeats) cheap no-ops.
    const session = sessionRef.current;
    const model = session?.sceneModel ?? null;
    if (model !== null) {
      // Phase 19C §4: re-sync by REFERENCE STABILITY, not by error status. The
      // DEF-072 merge returns the SAME model reference when nothing changed
      // (cheap no-op) and a NEW reference when a server-confirmed discovery or
      // read flipped flags. Gating the old code on `error === null` left the
      // object-list "· discovered" markers and the discovery captions stale
      // whenever a discovery landed but its record read FAILED: the flags are
      // the server-confirmed knowledge and must flip immediately regardless of
      // the record-read outcome.
      setStatus((prev) =>
        prev.status === "ready" && model !== prev.model
          ? { ...prev, model }
          : prev,
      );
    }
    setToast(feedback.toast);
    setRecordPanel(feedback.record);
    setInteractionError(feedback.error?.message ?? null);
    // When the panel opens from an object interaction, carry the PUBLIC
    // object context (registry label + color) so the panel can identify the
    // selected object and show its small-evidence preview (Phase 8_1 D).
    if (feedback.record !== null) {
      setPanelContext(evidencePreviewFor(session?.sceneModel ?? null, feedback.objectId) ?? null);
      // Phase 15: persistent selected-object focus (3D emissive/ring + list).
      setSelectedObjectId(feedback.objectId);
      sceneHandleRef.current?.setObjectSelected(feedback.objectId);
    }
  };

  /** Close the evidence panel and release the selected-object focus. */
  const closeRecordPanel = () => {
    setRecordPanel(null);
    setPanelContext(null);
    setSelectedObjectId(null);
    sceneHandleRef.current?.setObjectSelected(null);
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
    setSelectedObjectId(null);
    setCaptionPos({});
    sceneHandleRef.current = null;
    // Phase 18C: the notebook reloads THIS playthrough's player pins (the
    // localStorage namespace is per-playthrough, so a different playthrough
    // cannot bleed pins in).
    setPins(loadHypothesis(playthroughId));
    setNotebookOpen(true);

    // PD-SEC-01: interactObject is the ONLY discovery entry (the direct
    // evidence discover route is removed server-side).
    const services = { getInvestigation, interactObject, readRecord };
    const session = new InvestigationSession(
      services,
      token,
      (sceneCanvas, model) => {
        const result = createInvestigationScene(sceneCanvas, model, {
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
        });
        if (result.ok) sceneHandleRef.current = result as InvestigationSceneHandle;
        return result;
      },
      { playthroughId },
    );

    void session.start(canvas).then((outcome) => {
      if (cancelled) return;
      if (outcome.ok) {
        sessionRef.current = session;
        setStatus({ status: "ready", model: outcome.model });
        setSceneStatus("ready");
        // Phase 18C: after a reload the read-record cache is empty; lazily
        // hydrate ONLY ids the server confirmed READ (the notebook re-derives
        // from world-object labels until the records land — never blocks).
        // DEF-095: record reads are a PLAYING-only gameplay action. The
        // backend's frozen "gameplay ends at accusation" gate answers
        // GET /records/* with 409 NOT_PLAYING once the playthrough left
        // PLAYING, so an ACCUSED/REVEALED reload must NOT dispatch the
        // hydration at all (the scene is only restored as the player-visible
        // world; the reveal uses its own DTO). The session enforces the same
        // gate inside hydrateNotebookRecords (the authoritative source).
        if (
          session.bootstrapState === "PLAYING" &&
          session.knowledgeSnapshot &&
          session.knowledgeSnapshot.readEvidenceIds.length > 0
        ) {
          void session.hydrateNotebookRecords().then(() => {
            if (!cancelled) setNotebookRev((n) => n + 1);
          });
        }
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
      sceneHandleRef.current = null;
    };
  }, [runId]);

  // Escape closes the evidence panel (Phase 6 L).
  useEffect(() => {
    if (recordPanel === null) return;
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (handleEvidencePanelKey(event.key) === "close") {
        event.preventDefault();
        closeRecordPanel();
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

  // Phase 18C: the Detective Notebook model. Derived fresh on every render
  // from the session's server-authoritative knowledge + read-record cache +
  // world objects. `notebookRev` is consumed here so a lazy read-record
  // hydration forces a re-derivation (groups catch up without a reload).
  const session = sessionRef.current;
  void notebookRev; // re-derive when the lazy record hydration lands
  const notebookModel =
    status.status === "ready" && session !== null
      ? buildNotebookModel({
          discoveredEvidenceIds: session.discoveredEvidenceIdsSnapshot(),
          readEvidenceIds: session.readEvidenceIdsSnapshot(),
          worldObjects: status.model.worldObjects,
          records: session.recordCacheSnapshot(),
        })
      : null;

  /** Persist player-authored pins ONLY (namespaced localStorage per playthrough). */
  const handlePinsChanged = (next: HypothesisPins) => {
    setPins(next);
    const playthroughId = getPlaythroughId();
    if (playthroughId) saveHypothesis(playthroughId, next);
  };

  // Phase 15: floating captions for DISCOVERED evidence only. The text comes
  // from what the player already saw (record titles / public registry labels).
  const discoveredTitles = new Map((summary?.entries ?? []).map((entry) => [entry.evidenceId, entry.title]));
  const captions: ObjectCaptionModel[] =
    status.status === "ready"
      ? discoveredCaptionsForWorld(status.model.worldObjects, discoveredTitles)
      : [];
  const discoveredKey = captions.map((caption) => caption.objectId).join("|");

  // Follow the camera: re-project caption anchors at a low rate (positions
  // only change while orbiting), and at once when the caption set changes.
  useEffect(() => {
    if (status.status !== "ready") return;
    const handle = sceneHandleRef.current;
    if (!handle) return;
    const project = () => {
      const next: Record<string, { x: number; y: number }> = {};
      for (const caption of captions) {
        const point = handle.projectObjectPoint(caption.objectId, { x: 0, y: 0.3, z: 0 });
        if (point !== null) next[caption.objectId] = point;
      }
      setCaptionPos((previous) => (sameCaptionMap(previous, next) ? previous : next));
    };
    project();
    const timer = window.setInterval(project, 400);
    return () => window.clearInterval(timer);
    // Dependency note: `captions` is a fresh array every render, but only the
    // discovery ID set (discoveredKey) changes its projected positions.
  }, [status.status, runId, discoveredKey]);

  // Phase 18B: the currently focused evidence object (derived from the
  // selection — the same "which panel is open" state that drives the 3D ring).
  const focusedWorldObject =
    selectedObjectId === null || status.status !== "ready"
      ? null
      : status.model.worldObjects.find((o) => o.objectId === selectedObjectId && o.interactionWorks) ?? null;

  // Forensic focus follows the selection. The glue's setObjectFocus is
  // idempotent (same id = no-op) and presentation-only: it never re-dispatches
  // a pick/interact, so no discovery is ever double-triggered.
  useEffect(() => {
    const handle = sceneHandleRef.current;
    if (!handle) return;
    handle.setObjectFocus(focusedWorldObject ? focusedWorldObject.objectId : null);
  }, [selectedObjectId, runId]);

  // ESC closes the inspection surface through the SAME restore path as the
  // accessible Close button (closeRecordPanel -> setObjectFocus(null)).
  useEffect(() => {
    if (focusedWorldObject === null) return;
    const onKeyDown = (event: globalThis.KeyboardEvent) => {
      if (focusCloseKey(event.key) === "close") {
        event.preventDefault();
        closeRecordPanel();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [focusedWorldObject]);

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
          {/* Phase 15: floating captions over DISCOVERED evidence only — app-authored
              plain text (read-record titles / public registry labels). Position is
              re-projected from the live camera; never interactive. */}
          {captions.map((caption) => {
            const pos = captionPos[caption.objectId];
            if (pos === undefined) return null;
            return (
              <div
                key={caption.objectId}
                className="object-caption"
                data-testid={`object-caption-${caption.objectId}`}
                style={{ left: `${pos.x * 100}%`, top: `${pos.y * 100}%` }}
              >
                {caption.text}
              </div>
            );
          })}
          {/* Phase 18B: forensic focus inspection surface (present only while an
              evidence object is focused). The label/badges are public-safe
              (evidenceLabelFor/focusBadgesFor); closing restores the world state. */}
          {focusedWorldObject && (
            <FocusInspection
              label={evidenceLabelFor(focusedWorldObject)}
              badges={focusBadgesFor(focusedWorldObject)}
              onClose={closeRecordPanel}
            />
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

          {/* Phase 18C: the Detective Notebook — a compact drawer listing ONLY
              discovered, player-safe information, plus the player's private
              hypothesis pins (localStorage only, never sent to the server). */}
          {notebookModel !== null && (
            <NotebookPanel
              model={notebookModel}
              candidates={sessionRef.current?.candidatesSnapshot ?? null}
              pins={pins}
              open={notebookOpen}
              onToggle={() => setNotebookOpen((open) => !open)}
              onPinsChanged={handlePinsChanged}
            />
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
                      className={
                        obj.objectId === selectedObjectId
                          ? "scene-object-button scene-object-button--selected"
                          : "scene-object-button"
                      }
                      aria-pressed={obj.objectId === selectedObjectId}
                      onClick={() => handleObjectAction(obj.objectId)}
                    >
                      {/* Phase 19E: the object list shows the SEMANTIC human
                          label (registry label / humanized proc canonicalName),
                          never the raw objectId/assetId/proc.* token. */}
                      {evidenceLabelFor(obj)}
                    </button>
                    <span
                      className="object-label visually-hidden"
                      data-testid={`object-label-${obj.objectId}`}
                    >
                      {evidenceLabelFor(obj)}
                    </span>
                    {obj.discovered && (
                      <span className="object-discovered" data-testid={`object-discovered-${obj.objectId}`}>
                        {" "}
                        · discovered
                      </span>
                    )}
                    {obj.discovered && obj.read && (
                      <span className="object-discovered" data-testid={`object-read-${obj.objectId}`}>
                        {" "}
                        · read
                      </span>
                    )}
                    {obj.discovered && obj.evidenceId !== null && discoveredTitles.has(obj.evidenceId) && (
                      <span
                        className="object-label-discovered"
                        data-testid={`object-discovered-label-${obj.objectId}`}
                      >
                        {" "}
                        — {discoveredTitles.get(obj.evidenceId)}
                      </span>
                    )}
                  </li>
                ) : (
                  <li key={obj.objectId}>
                    {/* Phase 19E: decorative/label-less objects also render the
                        semantic human label, never the raw objectId/assetId. */}
                    <span data-testid={`object-label-${obj.objectId}`}>{evidenceLabelFor(obj)}</span>
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

          {getCatalogError() !== null && (
            <p className="assets-notice" data-testid="catalog-error" role="alert">
              The bundled asset catalog could not be validated — every object is shown as a
              neutral placeholder.
            </p>
          )}

          {/* Phase 11 Track B: environment-kit identity + safe degradation. */}
          {!isKitCatalogHealthy() && (
            <p className="assets-notice" data-testid="kit-catalog-error" role="alert">
              The bundled environment kits could not be validated — the scene is shown in the
              standard room.
            </p>
          )}
          {status.model.environmentId !== "apartment" && (
            <p className="environment-notice" data-testid="environment-notice">
              Environment: {environmentName(status.model.environmentId)}
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
          onClose={closeRecordPanel}
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

/** Shallow content-equality for the caption-position map (avoids re-render churn). */
function sameCaptionMap(
  a: Readonly<Record<string, { x: number; y: number }>>,
  b: Readonly<Record<string, { x: number; y: number }>>,
): boolean {
  const aKeys = Object.keys(a);
  const bKeys = Object.keys(b);
  if (aKeys.length !== bKeys.length) return false;
  for (const key of aKeys) {
    const pa = a[key];
    const pb = b[key];
    if (pb === undefined || pa.x !== pb.x || pa.y !== pb.y) return false;
  }
  return true;
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

/**
 * Player-safe environment identity label (Phase 11 Track B). Only the kit's
 * own canonical name is ever shown; an unknown/legacy id degrades to a fixed
 * neutral string (the raw id is never echoed — server strings stay off the
 * page unless the app itself authored them).
 */
function environmentName(environmentId: string): string {
  if (hasKit(environmentId)) {
    const kit = getKit(environmentId);
    if (kit !== undefined) return kit.canonicalName;
  }
  return "standard room";
}

/** Player-safe label for the accusation entry action (no truth values involved). */
function accusationActionLabel(state: string | null | undefined): string {
  if (state === "ACCUSED") return "Your accusation is on file — reveal it";
  if (state === "REVEALED") return "View the case reveal";
  return "Make your accusation";
}