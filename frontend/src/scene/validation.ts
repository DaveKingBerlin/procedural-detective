import type {
  InvestigationBootstrapResponse,
  InvestigationSceneLocationDTO,
  PlayerKnowledgeDTO,
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

/** Validates the full 200 bootstrap payload of GET .../investigation. */
export const parseInvestigationBootstrap: Validator<InvestigationBootstrapResponse> = {
  validate(raw: unknown): InvestigationBootstrapResponse {
    if (!isRecord(raw)) {
      throw new ValidationError("Investigation bootstrap must be an object.");
    }
    const playthroughId = requireString(raw, "playthroughId", "bootstrap");
    const caseId = requireString(raw, "caseId", "bootstrap");
    const caseVersion = requireNonNegativeInteger(raw, "caseVersion", "bootstrap");
    if (raw.state !== "PLAYING") {
      throw new ValidationError('bootstrap.state must be "PLAYING".');
    }
    const playerKnowledge = validatePlayerKnowledge.validate(raw.playerKnowledge);
    const scene = validateWorldGraph.validate(raw.scene);
    return {
      playthroughId,
      caseId,
      caseVersion,
      state: "PLAYING",
      playerKnowledge,
      scene,
    };
  },
};