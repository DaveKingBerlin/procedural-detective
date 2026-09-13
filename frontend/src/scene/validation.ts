import type {
  AccusationCandidatesDTO,
  InvestigationBootstrapResponse,
  InvestigationSceneLocationDTO,
  MotiveCandidateDTO,
  PlaythroughLifecycleState,
  PlayerKnowledgeDTO,
  SuspectCandidateDTO,
  WeaponCandidateDTO,
  WorldObjectDTO,
} from "../api/types";

/**
 * Strict, deterministic validation for the frozen Phase 6 investigation DTOs.
 *
 * Every payload crossing the trust boundary (the /investigation bootstrap,
 * and anything inside its `scene` — the player-safe WorldGraph) is parsed
 * through these tiny validator objects: parse, never trust. A malformed
 * payload throws ValidationError and is never silently coerced; callers map
 * that error to the player-safe "malformed world graph" state so the page
 * degrades instead of white-screening.
 *
 * Deterministic by construction: no randomness, no dates, no mutation of
 * inputs; the same raw payload always produces the same result or the same
 * error.
 */

export class ValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ValidationError";
  }
}

/** The shape of every tiny deterministic validator in this module. */
export interface Validator<T> {
  validate(raw: unknown): T;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function requireString(owner: Record<string, unknown>, field: string, where: string): string {
  const value = owner[field];
  if (!isNonEmptyString(value)) {
    throw new ValidationError(`${where}.${field} must be a non-empty string.`);
  }
  return value;
}

function requireNullableString(owner: Record<string, unknown>, field: string, where: string): string | null {
  const value = owner[field];
  if (value === null) return null;
  if (typeof value !== "string") {
    throw new ValidationError(`${where}.${field} must be a string or null.`);
  }
  return value;
}

function requireBoolean(owner: Record<string, unknown>, field: string, where: string): boolean {
  const value = owner[field];
  if (typeof value !== "boolean") {
    throw new ValidationError(`${where}.${field} must be a boolean.`);
  }
  return value;
}

function requireStringArray(owner: Record<string, unknown>, field: string, where: string): string[] {
  const value = owner[field];
  if (!Array.isArray(value) || !value.every(isNonEmptyString)) {
    throw new ValidationError(`${where}.${field} must be an array of non-empty strings.`);
  }
  return [...value];
}

function requireNonNegativeInteger(owner: Record<string, unknown>, field: string, where: string): number {
  const value = owner[field];
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new ValidationError(`${where}.${field} must be a non-negative integer.`);
  }
  return value;
}

/** Validator for the PlayerKnowledge portion of the bootstrap. */
export const validatePlayerKnowledge: Validator<PlayerKnowledgeDTO> = {
  validate(raw: unknown): PlayerKnowledgeDTO {
    if (!isRecord(raw)) {
      throw new ValidationError("playerKnowledge must be an object.");
    }
    return {
      discoveredEvidenceIds: requireStringArray(raw, "discoveredEvidenceIds", "playerKnowledge"),
      readEvidenceIds: requireStringArray(raw, "readEvidenceIds", "playerKnowledge"),
      visitedLocationIds: requireStringArray(raw, "visitedLocationIds", "playerKnowledge"),
    };
  },
};

/** Validator for one WorldObjectDTO entry. */
export const validateWorldObject: Validator<WorldObjectDTO> = {
  validate(raw: unknown): WorldObjectDTO {
    if (!isRecord(raw)) {
      throw new ValidationError("world object must be an object.");
    }
    return {
      objectId: requireString(raw, "objectId", "worldObject"),
      assetId: requireString(raw, "assetId", "worldObject"),
      assetType: requireString(raw, "assetType", "worldObject"),
      subtype: requireNullableString(raw, "subtype", "worldObject"),
      locationId: requireString(raw, "locationId", "worldObject"),
      anchor: requireString(raw, "anchor", "worldObject"),
      interaction: requireString(raw, "interaction", "worldObject"),
      evidenceId: requireNullableString(raw, "evidenceId", "worldObject"),
      discovered: requireBoolean(raw, "discovered", "worldObject"),
      read: requireBoolean(raw, "read", "worldObject"),
    };
  },
};

/**
 * The player-safe WorldGraph: bootstrap's `scene` object
 * ({location:{locationId,name}, worldObjects:[WorldObjectDTO]}).
 */
export interface WorldGraphDTO {
  location: InvestigationSceneLocationDTO;
  worldObjects: WorldObjectDTO[];
}

/**
 * Validates the player-safe WorldGraph shape. Exported separately so the
 * frontend test suite can exercise WorldGraph validation in isolation.
 */
export const validateWorldGraph: Validator<WorldGraphDTO> = {
  validate(raw: unknown): WorldGraphDTO {
    if (!isRecord(raw)) {
      throw new ValidationError("scene must be an object.");
    }
    const locationRaw = raw.location;
    if (!isRecord(locationRaw)) {
      throw new ValidationError("scene.location must be an object.");
    }
    const worldObjectsRaw = raw.worldObjects;
    if (!Array.isArray(worldObjectsRaw)) {
      throw new ValidationError("scene.worldObjects must be an array.");
    }
    return {
      location: {
        locationId: requireString(locationRaw, "locationId", "scene.location"),
        name: requireString(locationRaw, "name", "scene.location"),
      },
      worldObjects: worldObjectsRaw.map((entry) => validateWorldObject.validate(entry)),
    };
  },
};

/** The frozen lifecycle states the bootstrap may honestly publish. */
const LIFECYCLE_STATES = new Set<PlaythroughLifecycleState>(["PLAYING", "ACCUSED", "REVEALED"]);

/** Validator for one suspect candidate ({id,name}); unknown fields dropped. */
export const validateSuspectCandidate: Validator<SuspectCandidateDTO> = {
  validate(raw: unknown): SuspectCandidateDTO {
    if (!isRecord(raw)) {
      throw new ValidationError("candidates.suspects entry must be an object.");
    }
    return {
      id: requireString(raw, "id", "candidates.suspects"),
      name: requireString(raw, "name", "candidates.suspects"),
    };
  },
};

/** Validator for one motive candidate ({id,label}); unknown fields dropped. */
export const validateMotiveCandidate: Validator<MotiveCandidateDTO> = {
  validate(raw: unknown): MotiveCandidateDTO {
    if (!isRecord(raw)) {
      throw new ValidationError("candidates.motives entry must be an object.");
    }
    return {
      id: requireString(raw, "id", "candidates.motives"),
      label: requireString(raw, "label", "candidates.motives"),
    };
  },
};

/** Validator for one weapon candidate ({id,assetId,name}); unknown fields dropped. */
export const validateWeaponCandidate: Validator<WeaponCandidateDTO> = {
  validate(raw: unknown): WeaponCandidateDTO {
    if (!isRecord(raw)) {
      throw new ValidationError("candidates.weapons entry must be an object.");
    }
    return {
      id: requireString(raw, "id", "candidates.weapons"),
      assetId: requireString(raw, "assetId", "candidates.weapons"),
      name: requireString(raw, "name", "candidates.weapons"),
    };
  },
};

/**
 * Validates the player-safe `candidates` block of the bootstrap.
 *
 * Player-safe guarantees:
 *  - only {id,name} / {id,label} / {id,assetId,name} survive; every other
 *    field (e.g. a malformed/hostile "correct"/"winner" marker) is DROPPED,
 *    never copied into the typed DTO;
 *  - the ARRAY ORDER is preserved exactly — the client renders candidates in
 *    the server's alphabetical order and never re-orders them.
 */
export const validateAccusationCandidates: Validator<AccusationCandidatesDTO> = {
  validate(raw: unknown): AccusationCandidatesDTO {
    if (!isRecord(raw)) {
      throw new ValidationError("candidates must be an object.");
    }
    const suspectsRaw = raw.suspects;
    const motivesRaw = raw.motives;
    const weaponsRaw = raw.weapons;
    if (!Array.isArray(suspectsRaw)) {
      throw new ValidationError("candidates.suspects must be an array.");
    }
    if (!Array.isArray(motivesRaw)) {
      throw new ValidationError("candidates.motives must be an array.");
    }
    if (!Array.isArray(weaponsRaw)) {
      throw new ValidationError("candidates.weapons must be an array.");
    }
    return {
      suspects: suspectsRaw.map((entry) => validateSuspectCandidate.validate(entry)),
      motives: motivesRaw.map((entry) => validateMotiveCandidate.validate(entry)),
      weapons: weaponsRaw.map((entry) => validateWeaponCandidate.validate(entry)),
    };
  },
};

/** Validates the full 200 bootstrap payload of GET .../investigation. */
export const parseInvestigationBootstrap: Validator<InvestigationBootstrapResponse> = {
  validate(raw: unknown): InvestigationBootstrapResponse {
    if (!isRecord(raw)) {
      throw new ValidationError("Investigation bootstrap must be an object.");
    }
    const playthroughId = requireString(raw, "playthroughId", "bootstrap");
    const caseId = requireString(raw, "caseId", "bootstrap");
    const caseVersion = requireNonNegativeInteger(raw, "caseVersion", "bootstrap");
    if (typeof raw.state !== "string" || !LIFECYCLE_STATES.has(raw.state as PlaythroughLifecycleState)) {
      throw new ValidationError('bootstrap.state must be one of "PLAYING", "ACCUSED" or "REVEALED".');
    }
    const state = raw.state as PlaythroughLifecycleState;
    const playerKnowledge = validatePlayerKnowledge.validate(raw.playerKnowledge);
    const scene = validateWorldGraph.validate(raw.scene);
    const candidates = validateAccusationCandidates.validate(raw.candidates);
    return {
      playthroughId,
      caseId,
      caseVersion,
      state,
      playerKnowledge,
      scene,
      candidates,
    };
  },
};