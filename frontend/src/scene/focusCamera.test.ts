import { describe, expect, it } from "vitest";
import {
  clampFocusRadius,
  easeInOutCubic,
  focusCloseKey,
  focusFramingFor,
  focusTransitionTickCount,
  FOCUS_BETA,
  FOCUS_ORBIT_RATE,
  FOCUS_RADIUS_MAX,
  FOCUS_RADIUS_MIN,
  FOCUS_TRANSITION_MS,
  interpolateFocusState,
  orbitFocusAlpha,
  visibleFocusExtent,
  type FocusCameraState,
} from "./focusCamera";

/**
 * Phase 18B — pure forensic-focus camera math. Everything is deterministic:
 * framing (radius scaled from visible extent, safe min/max clamps, target that
 * centers the object), transition interpolation, slow orbit drift, ESC mapping
 * and the perf tick count used by the measured perf report.
 */

const WORLD: FocusCameraState = {
  alpha: 1.1,
  beta: 1.16,
  radius: 14.5,
  target: { x: 0, y: 1.05, z: 0 },
};

describe("focusFramingFor — deterministic close-up framing", () => {
  it("preserves the current alpha (continuous approach) and uses the inspection beta", () => {
    const desk = { position: { x: 2.6, y: 0.72, z: -2.2 }, scale: { x: 0.5, y: 0.2, z: 0.4 }, renderScale: 1 };
    const framing = focusFramingFor(WORLD, desk);
    expect(framing.alpha).toBe(WORLD.alpha);
    expect(framing.beta).toBe(FOCUS_BETA);
  });

  it("frames the object's visible extent with safe min/max radius limits", () => {
    const tiny = focusFramingFor(WORLD, { position: { x: 0, y: 1, z: 0 }, scale: { x: 0.04, y: 0.05, z: 0.04 }, renderScale: 1 });
    expect(tiny.radius).toBe(FOCUS_RADIUS_MIN); // ice-pick-class objects get the floor
    expect(clampFocusRadius(0.001)).toBe(FOCUS_RADIUS_MIN);

    const huge = focusFramingFor(WORLD, { position: { x: 0, y: 1, z: 0 }, scale: { x: 20, y: 3, z: 10 }, renderScale: 1 });
    expect(huge.radius).toBe(FOCUS_RADIUS_MAX);
    expect(clampFocusRadius(999)).toBe(FOCUS_RADIUS_MAX);
  });

  it("targets the object center (position + half-height lift), renderScale-aware", () => {
    const elevated = { position: { x: 3, y: 2, z: 1 }, scale: { x: 0.3, y: 0.5, z: 0.3 }, renderScale: 2 };
    const framing = focusFramingFor(WORLD, elevated);
    expect(framing.target.x).toBe(3);
    expect(framing.target.z).toBe(1);
    expect(framing.target.y).toBe(2 + (0.5 * 2) / 2 * 0.5);
    expect(framing.radius).toBe(clampFocusRadius(0.5 * 2 * 1.6));
  });

  it("visibleFocusExtent reflects the renderScale multiplier", () => {
    expect(visibleFocusExtent({ position: { x: 0, y: 0, z: 0 }, scale: { x: 0.3, y: 0.6, z: 0.3 }, renderScale: 1 })).toBe(0.6);
    expect(visibleFocusExtent({ position: { x: 0, y: 0, z: 0 }, scale: { x: 0.3, y: 0.6, z: 0.3 }, renderScale: 2 })).toBe(1.2);
    expect(visibleFocusExtent({ position: { x: 0, y: 0, z: 0 }, scale: { x: 0.3, y: 0.6, z: 0.3 }, renderScale: 0 })).toBe(0.6);
  });
});

describe("interpolateFocusState — smooth deterministic transition", () => {
  const TO: FocusCameraState = { alpha: 1.1, beta: FOCUS_BETA, radius: 1.5, target: { x: 1, y: 2, z: 3 } };

  it("returns the exact START at progress 0 and the exact TARGET at progress 1", () => {
    expect(interpolateFocusState(WORLD, TO, 0)).toEqual(WORLD);
    expect(interpolateFocusState(WORLD, TO, 1)).toEqual(TO);
    expect(interpolateFocusState(WORLD, TO, 2)).toEqual(TO); // clamped
    expect(interpolateFocusState(WORLD, TO, -1)).toEqual(WORLD); // clamped
  });

  it("is strictly monotonic between endpoints (eased, all components)", () => {
    const atQuarter = interpolateFocusState(WORLD, TO, 0.25);
    const atHalf = interpolateFocusState(WORLD, TO, 0.5);
    const atThree = interpolateFocusState(WORLD, TO, 0.75);
    for (const key of ["beta", "radius"] as const) {
      const seq = [WORLD[key], atQuarter[key], atHalf[key], atThree[key], TO[key]];
      const increasing = seq.every((v, i) => i === 0 || v > seq[i - 1]);
      const decreasing = seq.every((v, i) => i === 0 || v < seq[i - 1]);
      expect(increasing || decreasing, `${key} interpolates monotonically`).toBe(true);
      // Strictly inside the open interval (radius shrinks 14.5 -> 1.5; beta grows 1.16 -> 1.3).
      expect(atHalf[key]).toBeGreaterThan(Math.min(WORLD[key], TO[key]));
      expect(atHalf[key]).toBeLessThan(Math.max(WORLD[key], TO[key]));
    }
    expect(atHalf.target.x).toBeGreaterThan(WORLD.target.x);
    expect(atHalf.target.x).toBeLessThan(TO.target.x);
  });

  it("is deterministic: identical inputs always produce identical outputs", () => {
    expect(interpolateFocusState(WORLD, TO, 0.37)).toEqual(interpolateFocusState(WORLD, TO, 0.37));
  });
});

describe("easeInOutCubic — deterministic easing", () => {
  it("maps endpoints exactly and stays within [0,1]", () => {
    expect(easeInOutCubic(0)).toBe(0);
    expect(easeInOutCubic(1)).toBe(1);
    expect(easeInOutCubic(0.5)).toBe(0.5);
    expect(easeInOutCubic(-3)).toBe(0);
    expect(easeInOutCubic(7)).toBe(1);
  });
});

describe("orbitFocusAlpha — optional slow turntable drift", () => {
  it("drifts alpha at the fixed rad/sec rate", () => {
    expect(orbitFocusAlpha(1.1, 1000)).toBeCloseTo(1.1 + FOCUS_ORBIT_RATE, 10);
    expect(orbitFocusAlpha(1.1, 500)).toBeCloseTo(1.1 + FOCUS_ORBIT_RATE / 2, 10);
  });

  it("never drifts backward on bogus deltas", () => {
    expect(orbitFocusAlpha(1.1, -100)).toBe(1.1);
  });
});

describe("focusCloseKey — ESC wiring (pure)", () => {
  it("maps Escape to close and ignores every other key", () => {
    expect(focusCloseKey("Escape")).toBe("close");
    expect(focusCloseKey("Enter")).toBeNull();
    expect(focusCloseKey("")).toBeNull();
  });
});

describe("perf probe — focusTransitionTickCount", () => {
  it("reports the exact deterministic tick count for a fixed step", () => {
    // e.g. 18ms frames -> ceil(900/18) = 50 ticks (reported as the measured
    // transition length in the perf test).
    expect(focusTransitionTickCount(18)).toBe(Math.ceil(FOCUS_TRANSITION_MS / 18));
    expect(focusTransitionTickCount(FOCUS_TRANSITION_MS / 2)).toBe(2);
    expect(focusTransitionTickCount(NaN)).toBe(1);
    expect(focusTransitionTickCount(0)).toBe(1);
  });
});