import { describe, expect, it } from "vitest";
import type { EvidenceReadResultDTO } from "../api/types";
import {
  buildVictimProfileView,
  isForensicRecord,
  isVictimRelatedRecord,
  isVictimSceneObject,
} from "./victimModel";
import {
  makeHostileVictimRecord,
  makeVictimBodyRecord,
  makeVictimForensicRecord,
  makeVictimProfile,
  makeWorldObject,
  VICTIM_BODY_OBJECT_ID,
  VICTIM_DISCOVERY_EVIDENCE_ID,
} from "./testFixtures";

/**
 * Phase 47 — victim profile view model (pure, deterministic).
 *
 * Covers: identity/discovery derivation, neutral fallbacks, progressive
 * disclosure from READ records only, and the no-hidden-truth boundary.
 */

describe("buildVictimProfileView — identity, discovery & header", () => {
  it("derives a complete identity + discovery view from the profile", () => {
    const view = buildVictimProfileView(makeVictimProfile(), []);
    expect(view.headerTitle).toBe("Victim — Body found in the kitchen at 22:18");
    expect(view.identity).toEqual({
      name: "Sarah Miller",
      gender: "Female",
      age: 41,
      occupation: "Architect",
    });
    expect(view.background).toContain("architect");
    expect(view.discovery).toEqual({
      foundAt: "Miller Apartment - Kitchen",
      discoveredAt: "22:18",
      observedAtCanonical: "2026-09-11T22:18:31+02:00",
    });
  });

  it("falls back to the bare 'Victim' header when the discovery title is empty", () => {
    const victim = makeVictimProfile({
      discovery: {
        evidenceId: VICTIM_DISCOVERY_EVIDENCE_ID,
        locationId: "kitchen",
        locationName: "Kitchen",
        observedAt: "2026-09-11T22:18:31+02:00",
        title: "   ",
      },
    });
    expect(buildVictimProfileView(victim, []).headerTitle).toBe("Victim");
  });

  it("renders no discovery block when discovery is absent (never broken values)", () => {
    const view = buildVictimProfileView(makeVictimProfile({ discovery: null }), []);
    expect(view.discovery).toBeNull();
    expect(view.headerTitle).toBe("Victim");
  });

  it("degrades a null/absent gender and age to null (no NaN / undefined)", () => {
    const view = buildVictimProfileView(
      makeVictimProfile({ gender: null, age: null, occupation: null, background: null }),
      [],
    );
    expect(view.identity.gender).toBeNull();
    expect(view.identity.age).toBeNull();
    expect(view.identity.occupation).toBeNull();
    expect(JSON.stringify(view)).not.toContain("undefined");
    expect(JSON.stringify(view)).not.toContain("NaN");
  });
});

describe("buildVictimProfileView — progressive disclosure", () => {
  it("shows NO known facts / forensic findings / timeline before any record is read", () => {
    const view = buildVictimProfileView(makeVictimProfile(), []);
    expect(view.knownFacts).toEqual([]);
    expect(view.forensicFindings).toEqual([]);
    expect(view.timeline).toEqual([]);
  });

  it("derives known facts + timeline ONLY from victim-related READ records", () => {
    const unrelated: EvidenceReadResultDTO = {
      ...makeVictimBodyRecord({ evidenceId: "unrelated_01" }),
      content: { renderType: "TIMELINE", victimRelated: false, summary: "Unrelated." },
    };
    const view = buildVictimProfileView(makeVictimProfile(), [
      makeVictimBodyRecord(),
      unrelated,
    ]);
    expect(view.knownFacts).toHaveLength(1);
    expect(view.knownFacts[0].evidenceId).toBe(VICTIM_DISCOVERY_EVIDENCE_ID);
    expect(view.timeline).toHaveLength(1);
    expect(view.timeline[0].displayTime).toBe("22:18");
    expect(view.timeline[0].text).toBe("Body found in the kitchen at 22:18");
  });

  it("moves a FORENSIC_COMPARISON record into forensic findings once it is read", () => {
    const before = buildVictimProfileView(makeVictimProfile(), [makeVictimBodyRecord()]);
    expect(before.forensicFindings).toEqual([]);

    const after = buildVictimProfileView(makeVictimProfile(), [
      makeVictimBodyRecord(),
      makeVictimForensicRecord(),
    ]);
    expect(after.forensicFindings).toHaveLength(1);
    expect(after.forensicFindings[0].detail).toContain("blunt-force trauma");
  });

  it("classifies victim-related and forensic records safely", () => {
    expect(isVictimRelatedRecord(makeVictimBodyRecord())).toBe(true);
    expect(isVictimRelatedRecord(makeVictimForensicRecord())).toBe(true);
    expect(isVictimRelatedRecord({ ...makeVictimBodyRecord(), content: {} })).toBe(false);
    expect(isForensicRecord(makeVictimForensicRecord())).toBe(true);
    expect(isForensicRecord(makeVictimBodyRecord())).toBe(false);
  });
});

describe("buildVictimProfileView — no hidden-truth leak", () => {
  it("never surfaces non-allowlisted truth keys from a read record", () => {
    const view = buildVictimProfileView(makeVictimProfile(), [makeHostileVictimRecord()]);
    const serialized = JSON.stringify(view);
    for (const marker of [
      "HIDDEN_SUSPECT_MARKER",
      "HIDDEN_WEAPON_MARKER",
      "HIDDEN_MOTIVE_MARKER",
      "HIDDEN_TRUTH_MARKER",
      "HIDDEN_SOLUTION_MARKER",
    ]) {
      expect(serialized, `leaked ${marker}`).not.toContain(marker);
    }
    // The allowlisted public summary IS shown (it is a legitimate known fact).
    expect(view.knownFacts[0].detail).toContain("nothing unusual");
  });
});

describe("isVictimSceneObject — victim body detection", () => {
  const body = makeWorldObject({
    objectId: VICTIM_BODY_OBJECT_ID,
    assetType: "victim_body",
    subtype: "victim_body",
    evidenceId: null,
  });

  it("matches the published sceneObjectId", () => {
    expect(isVictimSceneObject(body, makeVictimProfile())).toBe(true);
  });

  it("matches the object whose evidence link equals the discovery evidenceId", () => {
    const victim = makeVictimProfile({ sceneObjectId: null });
    const linked = makeWorldObject({
      objectId: "some_body_object",
      assetType: "prop",
      subtype: null,
      evidenceId: VICTIM_DISCOVERY_EVIDENCE_ID,
    });
    expect(isVictimSceneObject(linked, victim)).toBe(true);
  });

  it("falls back to the victim_body asset semantics (server omitted sceneObjectId)", () => {
    const victim = makeVictimProfile({ sceneObjectId: null, discovery: null });
    expect(isVictimSceneObject(body, victim)).toBe(true);
  });

  it("never matches a witness person object or a null victim", () => {
    const witnessPerson = makeWorldObject({
      objectId: "witness_emily_reed",
      assetType: "character",
      subtype: "witness",
      evidenceId: null,
    });
    expect(isVictimSceneObject(witnessPerson, makeVictimProfile())).toBe(false);
    expect(isVictimSceneObject(body, null)).toBe(false);
  });

  it("does not treat an unrelated evidence-linked object as the victim", () => {
    const knife = makeWorldObject({
      objectId: "kitchen_knife",
      assetType: "sharp_weapon",
      subtype: "sharp_weapon",
      evidenceId: "forensic_knife_match_01",
    });
    expect(isVictimSceneObject(knife, makeVictimProfile())).toBe(false);
  });

  it("DEF-121 — a set sceneObjectId is authoritative (evidenceId/assetType cannot widen it)", () => {
    // makeVictimProfile() publishes sceneObjectId = victim_body_placeholder.
    const victim = makeVictimProfile();

    // A DIFFERENT object that shares the discovery evidenceId must NOT match.
    const evidenceTwin = makeWorldObject({
      objectId: "decoy_body",
      assetType: "prop",
      subtype: null,
      evidenceId: VICTIM_DISCOVERY_EVIDENCE_ID,
    });
    expect(isVictimSceneObject(evidenceTwin, victim)).toBe(false);

    // A DIFFERENT object that shares the victim_body asset semantics must NOT
    // match either.
    const bodyTwin = makeWorldObject({
      objectId: "decoy_victim_body",
      assetType: "victim_body",
      subtype: "victim_body",
      evidenceId: null,
    });
    expect(isVictimSceneObject(bodyTwin, victim)).toBe(false);

    // Only the authoritative object id matches.
    expect(isVictimSceneObject(body, victim)).toBe(true);
  });

  it("DEF-121 — an empty sceneObjectId still falls back to evidenceId/assetType", () => {
    const emptyScene = makeVictimProfile({ sceneObjectId: "" });
    const evidenceLinked = makeWorldObject({
      objectId: "some_body_object",
      assetType: "prop",
      subtype: null,
      evidenceId: VICTIM_DISCOVERY_EVIDENCE_ID,
    });
    expect(isVictimSceneObject(evidenceLinked, emptyScene)).toBe(true);
    expect(isVictimSceneObject(body, emptyScene)).toBe(true);
  });
});
