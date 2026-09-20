/**
 * Phase 18B — Forensic Focus Mode: pure, deterministic camera math.
 *
 * This module contains NO Babylon, NO DOM and NO randomness: the framing of a
 * focused evidence object, the transition interpolation and the slow-orbit
 * drift are all pure functions of their inputs, so the handling code in
 * renderInvestigation.ts can apply them to the live ArcRotateCamera and every
 * test can drive them deterministically.
 *
 * Framing rules (presentation-only, never authoritative world mutation):
 *  - `focusFramingFor` keeps the CURRENT horizontal alpha (continuity — the
 *    camera flies toward the object from the direction the player was
 *    already looking), flattens beta to a deterministic inspection angle and
 *    frames the object's visible extent with a safe min/max radius clamp;
 *  - `interpolateFocusState` smoothsteps each component between the SAVED
 *    world state and the focus framing over {@link FOCUS_TRANSITION_MS};
 *  - `orbitFocusAlpha` provides the optional slow turntable drift (only ever
 *    used when the OS prefers-reduced-motion is OFF);
 *  - `focusCloseKey` is the pure ESC-wiring shared with the route.
 */

/** One camera snapshot (the values the renderer reads/writes on the camera). */
export interface FocusCameraState {
  alpha: number;
  beta: number;
  radius: number;
  target: { x: number; y: number; z: number };
}

/** The minimal world-object surface the framing math needs (structural). */
export interface CameraTargetSource {
  position: { x: number; y: number; z: number };
  scale: { x: number; y: number; z: number };
  renderScale: number;
}

/** Safe focus-radius floor: tiny evidence never gets a clipping close-up. */
export const FOCUS_RADIUS_MIN = 1.5;

/** Safe focus-radius ceiling: a huge prop never over-zooms. */
export const FOCUS_RADIUS_MAX = 10;

/** Framing factor: camera distance = visible extent x this (1.6 frustums). */
export const FOCUS_RADIUS_FACTOR = 1.6;

/**
 * Deterministic inspection beta (~75°, i.e. looking down at ~15° from level):
 * shallow enough to read the silhouette of a small object on a desk, stable
 * for every object so repeated focus sessions are byte-identical.
 */
export const FOCUS_BETA = 1.3;

/** How high above the object's base the camera target sits (0.5 = centre). */
export const FOCUS_TARGET_LIFT = 0.5;

/** Focus-transition duration in milliseconds. */
export const FOCUS_TRANSITION_MS = 900;

/** Slow auto-orbit rate during inspection (radians/second). */
export const FOCUS_ORBIT_RATE = 0.22;

/**
 * Background de-emphasis scale applied to the key + hemi light intensities
 * while a focus session is active (safely restored to the exact original on
 * exit — no global per-frame cost).
 */
export const FOCUS_BG_LIGHT_SCALE = 0.62;

/** Clamp a computed focus radius into the safe [min, max] framing window. */
export function clampFocusRadius(radius: number): number {
  if (!Number.isFinite(radius)) return FOCUS_RADIUS_MIN;
  return Math.max(FOCUS_RADIUS_MIN, Math.min(FOCUS_RADIUS_MAX, radius));
}

/** The rendered visible extent (largest axis, renderScale-aware). */
export function visibleFocusExtent(obj: CameraTargetSource): number {
  const scaleFactor = Number.isFinite(obj.renderScale) && obj.renderScale > 0 ? obj.renderScale : 1;
  return Math.max(obj.scale.x, obj.scale.y, obj.scale.z) * scaleFactor;
}

/**
 * Deterministic close-up framing for one evidence object, derived from the
 * CURRENT camera state (alpha is preserved so the approach is continuous)
 * and the object's visible extent.
 */
export function focusFramingFor(current: FocusCameraState, obj: CameraTargetSource): FocusCameraState {
  const scaleFactor = Number.isFinite(obj.renderScale) && obj.renderScale > 0 ? obj.renderScale : 1;
  const halfHeight = (obj.scale.y * scaleFactor) / 2;
  const extent = Math.max(obj.scale.x, obj.scale.y, obj.scale.z) * scaleFactor;
  return {
    alpha: current.alpha,
    beta: FOCUS_BETA,
    radius: clampFocusRadius(extent * FOCUS_RADIUS_FACTOR),
    target: {
      x: obj.position.x,
      y: obj.position.y + halfHeight * FOCUS_TARGET_LIFT,
      z: obj.position.z,
    },
  };
}

/** Clamp a progress value into [0, 1] (deterministic, no NaN leaks). */
function clampUnit(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(1, value));
}

/** Ease-in-out cubic: gentle exit/entry, fully deterministic. */
export function easeInOutCubic(t: number): number {
  const x = clampUnit(t);
  return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
}

/** Interpolate every camera component from `from` toward `to` by progress. */
export function interpolateFocusState(
  from: FocusCameraState,
  to: FocusCameraState,
  progress: number,
): FocusCameraState {
  const t = easeInOutCubic(clampUnit(progress));
  return {
    alpha: from.alpha + (to.alpha - from.alpha) * t,
    beta: from.beta + (to.beta - from.beta) * t,
    radius: from.radius + (to.radius - from.radius) * t,
    target: {
      x: from.target.x + (to.target.x - from.target.x) * t,
      y: from.target.y + (to.target.y - from.target.y) * t,
      z: from.target.z + (to.target.z - from.target.z) * t,
    },
  };
}

/** Advance the alpha angle by the slow turntable drift (rad/sec x seconds). */
export function orbitFocusAlpha(alpha: number, deltaMs: number): number {
  return alpha + FOCUS_ORBIT_RATE * (Math.max(0, deltaMs) / 1000);
}

/**
 * How many fixed-size ticks a transition takes to complete (perf probe). The
 * transition remains TIME-based (real elapsed render time); this is the exact
 * tick count a fixed-step simulation uses, so tests can report the duration.
 */
export function focusTransitionTickCount(stepMs: number): number {
  const step = Number.isFinite(stepMs) && stepMs > 0 ? stepMs : FOCUS_TRANSITION_MS;
  return Math.ceil(FOCUS_TRANSITION_MS / step);
}

/** Pure ESC handling for the inspection surface (shared with the route). */
export function focusCloseKey(key: string): "close" | null {
  return key === "Escape" ? "close" : null;
}