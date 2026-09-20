import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { AccusationResponse, RevealResponse } from "../api/types";
import {
  CANNED_REVEAL_CRIME_TIME,
  makeAccusationResponse,
  makeCandidates,
  makeRevealResponse,
  TEST_TOKEN,
} from "../scene/testFixtures";
import {
  AccusationFlow,
  type AccusationCallbacks,
  type AccusationServices,
  type ConfirmOutcome,
} from "./accusationFlow";
import {
  parseAccusationResponse,
  validateAccusationForm,
  isValidCrimeTime,
} from "./accusationValidation";

/**
 * Phase 7 O frontend coverage at the controller level:
 * candidate order preservation, selection state, missing-field validation,
 * submission, double-submit prevention, 409 conflict handling, expired
 * credential handling, and the no-pre-reveal-correctness guarantee. All
 * deterministic and fully offline (services are mocked).
 */

const PT_ID = "PT-test-0001";

function makeServices(overrides: Partial<AccusationServices> = {}): AccusationServices {
  return {
    submitAccusation: vi.fn(async () => makeAccusationResponse()),
    getReveal: vi.fn(async () => makeRevealResponse()),
    ...overrides,
  };
}

function makeFlow(
  services: AccusationServices,
  callbacks: AccusationCallbacks = {},
): AccusationFlow {
  return new AccusationFlow(services, TEST_TOKEN, { playthroughId: PT_ID }, makeCandidates(), callbacks);
}

/** Fill every dimension of an editing flow so validation passes. */
function selectAll(flow: AccusationFlow): void {
  flow.selectSuspect("suspect_alpha");
  flow.selectMotive("motive_alpha");
  flow.selectWeapon("weapon_alpha");
  flow.setCrimeTime("21:45");
}

describe("accusation candidates (player-safe, unmarked, ordered)", () => {
  it("preserves the exact candidate order the server returned (no client re-ordering or winner marking)", () => {
    const flow = makeFlow(makeServices());
    expect(flow.candidatesSnapshot.suspects.map((entry) => entry.id)).toEqual([
      "suspect_alpha",
      "suspect_beta",
      "suspect_gamma",
    ]);
    expect(flow.candidatesSnapshot.motives.map((entry) => entry.id)).toEqual([
      "motive_alpha",
      "motive_beta",
      "motive_gamma",
    ]);
    expect(flow.candidatesSnapshot.weapons.map((entry) => entry.id)).toEqual([
      "weapon_alpha",
      "weapon_beta",
      "weapon_gamma",
    ]);
  });

  it("no pre-reveal correctness indicator: candidates carry no correct/winner field and the flow never derives one", () => {
    const flow = makeFlow(makeServices());
    for (const suspect of flow.candidatesSnapshot.suspects) {
      expect(Object.keys(suspect).sort()).toEqual(["id", "name"]);
      expect("correct" in suspect).toBe(false);
      expect("winner" in suspect).toBe(false);
    }
    for (const motive of flow.candidatesSnapshot.motives) {
      expect(Object.keys(motive).sort()).toEqual(["id", "label"]);
      expect("correct" in motive).toBe(false);
      expect("winner" in motive).toBe(false);
    }
    for (const weapon of flow.candidatesSnapshot.weapons) {
      expect(Object.keys(weapon).sort()).toEqual(["assetId", "id", "name"]);
      expect("correct" in weapon).toBe(false);
      expect("winner" in weapon).toBe(false);
    }
    // The controller exposes no correctness anywhere in its public state.
    const snapshot = JSON.stringify({ selection: flow.currentSelection, candidates: flow.candidatesSnapshot });
    expect(snapshot).not.toMatch(/correct/i);
    expect(snapshot).not.toMatch(/\bwinner\b/i);
  });
});

describe("selection state", () => {
  it("updates each dimension independently and reflects the exact chosen ids", () => {
    const flow = makeFlow(makeServices());
    expect(flow.currentSelection).toEqual({ murdererId: null, motiveId: null, weaponId: null, crimeTime: null });

    flow.selectSuspect("suspect_beta");
    flow.selectMotive("motive_gamma");
    flow.selectWeapon("weapon_beta");
    flow.setCrimeTime("19:00");

    expect(flow.currentSelection).toEqual({
      murdererId: "suspect_beta",
      motiveId: "motive_gamma",
      weaponId: "weapon_beta",
      crimeTime: "19:00",
    });
  });
});

describe("missing-field validation (client-side)", () => {
  it("blocks the confirmation step when any dimension is missing", () => {
    const services = makeServices();
    const flow = makeFlow(services);
    flow.selectMotive("motive_alpha");
    flow.selectWeapon("weapon_alpha");

    expect(flow.openConfirmation()).toBe("invalid");
    expect(flow.phase).toBe("editing");

    const errors = flow.lastFieldErrors;
    expect(errors.murdererId).toMatch(/Choose one of the options/);
    expect(errors.motiveId).toBeUndefined();
    expect(errors.weaponId).toBeUndefined();
    expect(errors.crimeTime).toMatch(/24-hour time/);
    expect(services.submitAccusation).not.toHaveBeenCalled();
  });

  it("rejects a missing crime time even when all three ids are chosen", () => {
    const flow = makeFlow(makeServices());
    flow.selectSuspect("suspect_alpha");
    flow.selectMotive("motive_alpha");
    flow.selectWeapon("weapon_alpha");
    flow.setCrimeTime("");

    expect(flow.openConfirmation()).toBe("invalid");
    expect(flow.lastFieldErrors.crimeTime).toMatch(/24-hour time/);
  });

  it("rejects out-of-universe ids and malformed times in the pure validator", () => {
    const candidates = makeCandidates();
    const errors = validateAccusationForm(
      {
        murdererId: "not_a_suspect",
        motiveId: "motive_alpha",
        weaponId: "weapon_alpha",
        crimeTime: "25:99",
      },
      candidates,
    );
    expect(errors.murdererId).toMatch(/Choose a suspect/);
    expect(errors.crimeTime).toMatch(/24-hour time/);
    expect(errors.motiveId).toBeUndefined();
    expect(errors.weaponId).toBeUndefined();
  });

  it("isValidCrimeTime only accepts real 24h HH:MM values", () => {
    expect(isValidCrimeTime("00:00")).toBe(true);
    expect(isValidCrimeTime("23:59")).toBe(true);
    expect(isValidCrimeTime("09:15")).toBe(true);
    expect(isValidCrimeTime("24:00")).toBe(false);
    expect(isValidCrimeTime("12:60")).toBe(false);
    expect(isValidCrimeTime("7:30")).toBe(false);
    expect(isValidCrimeTime("21:45:00")).toBe(false); // the UI collects HH:MM only
    expect(isValidCrimeTime(null)).toBe(false);
    expect(isValidCrimeTime("")).toBe(false);
    expect(isValidCrimeTime("2026-09-11T21:45:00+02:00")).toBe(false);
  });
});

describe("submission", () => {
  it("sends the exact frozen request body with crimeTime as HH:MM:00 (no date)", async () => {
    const services = makeServices();
    const flow = makeFlow(services);
    selectAll(flow);

    expect(flow.openConfirmation()).toBe("confirmed");
    const outcome = (await flow.confirmAccusation()) as ConfirmOutcome;
    expect(outcome.outcome).toBe("accepted");

    expect(services.submitAccusation).toHaveBeenCalledTimes(1);
    expect(services.submitAccusation).toHaveBeenCalledWith(
      PT_ID,
      { murdererId: "suspect_alpha", motiveId: "motive_alpha", weaponId: "weapon_alpha", crimeTime: "21:45:00" },
      TEST_TOKEN,
    );
    expect(flow.phase).toBe("accepted");
    expect(flow.acceptedAccusation?.accusation.crimeTime).toBe("21:45:00");
  });

  it("a 200 response is parsed into the typed allowlist (truth never present)", async () => {
    const services = makeServices();
    const flow = makeFlow(services);
    selectAll(flow);
    flow.openConfirmation();
    expect((await flow.confirmAccusation()).outcome).toBe("accepted");
    expect(flow.acceptedAccusation).toMatchObject({
      playthroughId: PT_ID,
      status: "ACCUSED",
      accusation: { murdererId: "suspect_alpha", crimeTime: "21:45:00" },
    });
    // The accepted state never holds or fetches truth.
    expect(JSON.stringify(flow.acceptedAccusation)).not.toMatch(/murdererName|motiveLabel|weaponName|timeline|explanation/);
    expect(services.getReveal).not.toHaveBeenCalled();
  });
});

describe("double-submit prevention", () => {
  it("two rapid confirmations produce exactly one network request", async () => {
    let release!: (value: AccusationResponse) => void;
    const gate = new Promise<AccusationResponse>((resolve) => {
      release = resolve;
    });
    const services = makeServices({ submitAccusation: vi.fn(() => gate) });
    const flow = makeFlow(services);
    selectAll(flow);
    flow.openConfirmation();

    const first = flow.confirmAccusation();
    const second = flow.confirmAccusation();

    expect(services.submitAccusation).toHaveBeenCalledTimes(1);
    expect(await second).toEqual({ outcome: "busy" });
    expect(flow.phase).toBe("submitting");

    release(makeAccusationResponse());
    expect(await first).toEqual({ outcome: "accepted" });
  });
});

describe("409 conflict handling (already submitted -> reveal)", () => {
  it("shows the already-submitted state and fetches the now-available reveal", async () => {
    const onRevealAvailable = vi.fn();
    const reveal = makeRevealResponse();
    const services = makeServices({
      submitAccusation: vi.fn(async () => {
        throw new ApiError(409, "CASE_ALREADY_SUBMITTED", "this playthrough already has an accusation", null);
      }),
      getReveal: vi.fn(async () => reveal),
    });
    const flow = makeFlow(services, { onRevealAvailable });
    selectAll(flow);
    flow.openConfirmation();

    const outcome = await flow.confirmAccusation();

    expect(outcome.outcome).toBe("already-submitted");
    expect(flow.phase).toBe("already-submitted");
    expect(flow.serverError).toContain("already been submitted");
    await vi.waitFor(() => {
      expect(services.getReveal).toHaveBeenCalledWith(PT_ID, TEST_TOKEN);
      expect(onRevealAvailable).toHaveBeenCalledWith(reveal);
    });
  });

  it("keeps the already-submitted state intact when the conflict reveal prefetch fails", async () => {
    const services = makeServices({
      submitAccusation: vi.fn(async () => {
        throw new ApiError(409, "CASE_ALREADY_SUBMITTED", "already there", null);
      }),
      getReveal: vi.fn(async () => {
        throw new ApiError(500, "INTERNAL", "boom", null);
      }),
    });
    const flow = makeFlow(services);
    selectAll(flow);
    flow.openConfirmation();

    const outcome = await flow.confirmAccusation();
    expect(outcome.outcome).toBe("already-submitted");
    await vi.waitFor(() => {
      expect(services.getReveal).toHaveBeenCalledTimes(1);
    });
    expect(flow.phase).toBe("already-submitted");
  });
});

describe("expired credential handling", () => {
  it("401 -> auth-failed outcome, tokenInvalid state and a reset affordance message", async () => {
    const services = makeServices({
      submitAccusation: vi.fn(async () => {
        throw new ApiError(401, "UNAUTHORIZED", "bearer token expired", null);
      }),
    });
    const flow = makeFlow(services);
    selectAll(flow);
    flow.openConfirmation();

    const outcome = await flow.confirmAccusation();

    expect(outcome.outcome).toBe("auth-failed");
    expect(flow.tokenInvalid).toBe(true);
    expect(flow.phase).toBe("confirming");
    expect(flow.serverError).toMatch(/no longer valid/);
  });

  it("403 with a non-conflict code is treated as an expired credential too", async () => {
    const services = makeServices({
      submitAccusation: vi.fn(async () => {
        throw new ApiError(403, "FORBIDDEN", "token state conflict", null);
      }),
    });
    const flow = makeFlow(services);
    selectAll(flow);
    flow.openConfirmation();
    expect((await flow.confirmAccusation()).outcome).toBe("auth-failed");
  });
});

describe("server-side validation error (422)", () => {
  it("maps a 422 envelope to the server's message and returns to the confirmation step", async () => {
    const services = makeServices({
      submitAccusation: vi.fn(async () => {
        throw new ApiError(422, "VALIDATION_ERROR", "weaponId is not a known candidate for this case version", null);
      }),
    });
    const flow = makeFlow(services);
    selectAll(flow);
    flow.openConfirmation();

    const outcome = await flow.confirmAccusation();

    expect(outcome.outcome).toBe("failed");
    expect(flow.phase).toBe("confirming");
    expect(flow.serverError).toContain("weaponId");
    expect(flow.tokenInvalid).toBe(false);
  });
});

describe("parseAccusationResponse (trust boundary)", () => {
  it("drops unknown fields (including smuggled truth) and keeps the frozen allowlist", () => {
    const raw = {
      playthroughId: "PT-test-0001",
      caseId: "CASE-test-01",
      caseVersion: 1,
      status: "ACCUSED",
      accusation: { murdererId: "suspect_alpha", motiveId: "motive_alpha", weaponId: "weapon_alpha", crimeTime: "21:45:00" },
    };
    const withSmuggledTruth = {
      ...raw,
      murdererName: "Ada Marsh",
      truth: { murdererId: "suspect_alpha", motiveLabel: "secret" },
      proof: "solution-proof-internal",
    };
    expect(parseAccusationResponse(withSmuggledTruth)).toEqual(raw);
  });

  it("rejects a body that is not ACCUSED or that lacks required fields", () => {
    expect(() => parseAccusationResponse({ status: "REVEALED" })).toThrow(/status/);
    expect(() => parseAccusationResponse({ ...makeAccusationResponse(), accusation: null })).toThrow(/accusation/);
    expect(() => parseAccusationResponse("nope")).toThrow(/object/);
  });
});

describe("confirmation lifecycle", () => {
  it("requires a confirmation step: submission is impossible from editing", async () => {
    const services = makeServices();
    const flow = makeFlow(services);

    const outcome = await flow.confirmAccusation();

    expect(outcome.outcome).toBe("invalid");
    expect(services.submitAccusation).not.toHaveBeenCalled();
  });

  it("cancel returns to editing without submitting and preserves the selection", () => {
    const services = makeServices();
    const flow = makeFlow(services);
    selectAll(flow);
    flow.openConfirmation();
    expect(flow.phase).toBe("confirming");

    flow.cancelConfirmation();

    expect(flow.phase).toBe("editing");
    expect(flow.currentSelection.murdererId).toBe("suspect_alpha");
    expect(services.submitAccusation).not.toHaveBeenCalled();
  });
});

describe("crime time contract (WHEN)", () => {
  it("submits exactly 'HH:MM:00' — the collected HH:MM extended by :00, never a date", async () => {
    const services = makeServices();
    const flow = makeFlow(services);
    flow.selectSuspect("suspect_alpha");
    flow.selectMotive("motive_alpha");
    flow.selectWeapon("weapon_alpha");
    flow.setCrimeTime("06:30");

    flow.openConfirmation();
    const bodySent = (services.submitAccusation as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(bodySent).toBeUndefined(); // nothing sent before confirming
    await flow.confirmAccusation();
    const sent = (services.submitAccusation as ReturnType<typeof vi.fn>).mock.calls[0][1] as {
      crimeTime: string;
    };
    expect(sent.crimeTime).toBe("06:30:00");
    expect(sent.crimeTime).not.toMatch(/T/); // no date component
  });

  it("the reveal DTO's crime time parses to a bare HH:MM display from its ISO (fixture sanity)", () => {
    // Contract: truth.crimeTime is a full ISO whose local time-of-day is the
    // crime time; the reveal screen shows "HH:MM" from that ISO.
    expect(CANNED_REVEAL_CRIME_TIME).toMatch(/^2026-09-11T\d{2}:\d{2}:\d{2}/);
    const reveal: RevealResponse = makeRevealResponse();
    expect(reveal.truth.crimeTime).toBe(CANNED_REVEAL_CRIME_TIME);
  });
});

describe("Phase 18C — applyHypothesis (player notes never affect the solver or contract)", () => {
  it("fills the form ONLY from explicit pins; unpinned dimensions stay empty (no implicit fill)", async () => {
    const services = makeServices();
    const flow = makeFlow(services);
    // Only a suspect + time pin exists:
    flow.applyHypothesis({ suspect: "suspect_beta", motive: null, weapon: null, time: "19:00" });

    expect(flow.currentSelection).toEqual({
      murdererId: "suspect_beta",
      motiveId: null,
      weaponId: null,
      crimeTime: "19:00",
    });
    // Missing dimensions block confirmation — the player must still choose them
    // explicitly; NO request fires.
    expect(flow.openConfirmation()).toBe("invalid");
    expect(services.submitAccusation).not.toHaveBeenCalled();
  });

  it("pins trigger NO network work by themselves (no request fired for applying)", () => {
    const services = makeServices();
    const flow = makeFlow(services);
    flow.applyHypothesis({ suspect: "suspect_alpha", motive: "motive_alpha", weapon: "weapon_alpha", time: "21:45" });
    expect(services.submitAccusation).not.toHaveBeenCalled();
    expect(services.getReveal).not.toHaveBeenCalled();
  });

  it("submitting after applying pins sends EXACTLY the pinned values and nothing more", async () => {
    const services = makeServices();
    const flow = makeFlow(services);
    flow.applyHypothesis({ suspect: "suspect_beta", motive: "motive_gamma", weapon: "weapon_beta", time: "19:00" });

    expect(flow.openConfirmation()).toBe("confirmed");
    const outcome = await flow.confirmAccusation();
    expect(outcome.outcome).toBe("accepted");

    expect(services.submitAccusation).toHaveBeenCalledTimes(1);
    expect(services.submitAccusation).toHaveBeenCalledWith(
      PT_ID,
      { murdererId: "suspect_beta", motiveId: "motive_gamma", weaponId: "weapon_beta", crimeTime: "19:00:00" },
      TEST_TOKEN,
    );
    // The accepted echo carries ONLY the four submitted dimensions.
    expect(JSON.stringify(flow.acceptedAccusation)).not.toMatch(/murdererName|motiveLabel|weaponName|timeline|explanation/);
  });

  it("never alters the candidate universe, scoring or solver inputs", () => {
    const services = makeServices();
    const flow = makeFlow(services);
    const before = flow.candidatesSnapshot;
    flow.applyHypothesis({ suspect: "suspect_alpha", motive: "motive_alpha", weapon: "weapon_alpha", time: "21:45" });
    // Same object identity/value — the pins only moved the selection.
    expect(flow.candidatesSnapshot).toBe(before);
    expect(flow.requestBody).toEqual({
      murdererId: "suspect_alpha",
      motiveId: "motive_alpha",
      weaponId: "weapon_alpha",
      crimeTime: "21:45:00",
    });
  });

  it("a stale pin outside the current universe cannot be submitted (membership re-check)", async () => {
    const services = makeServices();
    const flow = makeFlow(services);
    // From an older playthrough's universe:
    flow.applyHypothesis({ suspect: "old_universe_suspect", motive: "motive_alpha", weapon: "weapon_alpha", time: "21:45" });
    expect(flow.openConfirmation()).toBe("invalid");
    expect(flow.lastFieldErrors.murdererId).toMatch(/Choose a suspect/);
    expect(services.submitAccusation).not.toHaveBeenCalled();
  });

  it("is a no-op once the accusation is past editing (submitted state is immutable)", async () => {
    const services = makeServices();
    const flow = makeFlow(services);
    selectAll(flow);
    flow.openConfirmation();
    expect(flow.phase).toBe("confirming");

    flow.applyHypothesis({ suspect: "suspect_gamma", motive: "motive_gamma", weapon: "weapon_gamma", time: "06:00" });
    expect(flow.currentSelection.murdererId).toBe("suspect_alpha"); // unchanged
    expect(flow.phase).toBe("confirming");
  });
});