import type { GeneratedAssetDefinition, InvestigationBootstrapResponse, WorldObjectDTO } from "../api/types";
import { APARTMENT_KIT_ID, transformFor } from "../environments/kitGeometry";
import type { Vec3 } from "./apartment";
import type { AssetEntry, AssetPrimitiveKind, AssetRegistry, CompositeKind, CompositePartDescriptor } from "./assetRegistry";
import { ASSET_REGISTRY, FALLBACK_ASSET } from "./assetRegistry";
import type { AnchorRegistry } from "./anchorRegistry";
import { ANCHOR_REGISTRY } from "./anchorRegistry";
import { buildGeneratedComposite } from "./generatedRenderer";
import { parseInvestigationBootstrap } from "./validation";

/**
 * Pure, deterministic scene model builder (Phase 6 track 2).
 *
 * `buildInvestigationScene` turns a validated InvestigationBootstrapResponse
 * into a plain SCENE MODEL — no Babylon, no DOM, no randomness. It is fully
 * unit-testable as a pure function, and the Babylon glue consumes exactly
 * this model.
 *
 * Contract guarantees:
 *  - The server sends NO coordinates: every position/rotation/scale below
 *    comes from the application-owned anchor/asset registries (or a stable
 *    objectId hash), never from the payload.
 *  - Object identity is ALWAYS the backend objectId — never an array index,
 *    never React render order. The output world object array is sorted by
 *    objectId so any input ordering (or shuffle) yields the same model.
 *  - Unknown asset ids become the neutral fallback primitive, flagged with
 *    `unknownAsset` so the UI can surface a player-visible notice.
 *
 * Phase 11 Track B (environment kits): the bootstrap's `scene.environmentId`
 * selects the kit. The APARTMENT kit keeps the Phase 6 anchor table (its
 * transforms stay byte-identical — golden appearance); every other kit
 * resolves per-object transforms STRICTLY from the kit manifest via
 * {@link transformFor}. Unknown anchors fall back to the stable objectId
 * hash; an unknown kit id falls back to the apartment geometry.
 */

export interface SceneWorldObject {
  objectId: string;
  assetId: string;
  assetType: string;
  subtype: string | null;
  primitiveKind: AssetPrimitiveKind;
  /** Named composite builder (Phase 8_1); null renders the simple primitive. */
  compositeKind: CompositeKind | null;
  /** Invisible pick-hitbox multiplier (see MIN_PICKABLE_EXTENT / pickHitboxExtent). */
  hitboxScale: number;
  color: string;
  scale: Vec3;
  label: string | null;
  position: Vec3;
  rotation: Vec3;
  interaction: string;
  /** True only when the registry asset is interactable AND an interaction was published. */
  interactionWorks: boolean;
  evidenceId: string | null;
  discovered: boolean;
  read: boolean;
  /** True when the asset id was NOT in the application-owned registry. */
  unknownAsset: boolean;
  /**
   * Phase 13: the validated declarative generated definition of a `proc.*`
   * world object. Null for non-procedural assets and for proc.* assets whose
   * block was missing/invalid (those fall back to the neutral primitive).
   */
  generated: GeneratedAssetDefinition | null;
  /**
   * Phase 13: the compiled generated child parts (built by
   * {@link buildGeneratedComposite}, deterministic). The renderer uses this
   * list INSTEAD of catalog/template resolution. Null unless `generated`
   * is valid and non-null.
   */
  generatedParts: readonly CompositePartDescriptor[] | null;
  /** Phase 13: the declared picking extent basis (def.hitbox.scale). */
  generatedHitbox: Vec3 | null;
  /**
   * Phase 12: the frozen logical template of a TEMPLATE-ONLY composite
   * (compositeKind === null). Null for legacy composites and primitives.
   */
  templateId: string | null;
  /**
   * Phase 12: the DEFAULT-variant factory child parts for template-backed
   * objects (null for legacy composites and primitives).
   */
  templateParts: readonly CompositePartDescriptor[] | null;
  /**
   * Phase 12: the template `hitbox` at the default variant scale (null when
   * the template declares none / not a template-backed object).
   */
  templateHitbox: Vec3 | null;
  /** Phase 12: resolved default-variant material token (null = no tint). */
  templateMaterial: string | null;
  /** Phase 12: resolved default-variant state token (pass-through only). */
  templateState: string | null;
  /**
   * Phase 15 Track B: deterministic VISUAL legibility factor for evidence in
   * non-apartment kits. Pure render concern — the objectId, mesh names,
   * anchor/transform and interaction are untouched: only the rendered scale
   * (and ring/pick math that follows it) is affected. Always 1 for the
   * apartment kit (the golden scene stays byte-identical) and for non-evidence
   * objects.
   */
  renderScale: number;
}

export interface InvestigationSceneModel {
  location: { locationId: string; name: string };
  /** Phase 11: the exact environment kit id from the bootstrap (fallback "apartment"). */
  environmentId: string;
  /** Sorted by objectId — stable identity regardless of server ordering. */
  worldObjects: SceneWorldObject[];
}

export function buildInvestigationScene(
  bootstrap: InvestigationBootstrapResponse,
  assetRegistry: AssetRegistry = ASSET_REGISTRY,
  anchorRegistry: AnchorRegistry = ANCHOR_REGISTRY,
): InvestigationSceneModel {
  // Re-validate at build time: the builder is the last safe gate before
  // geometry is derived from server-provided ids. Throws ValidationError on
  // malformed payloads (callers map that to the safe error state).
  const parsed = parseInvestigationBootstrap.validate(bootstrap);
  const scene = parsed.scene;

  const worldObjects = applySharedAnchorSpacing(
    scene.worldObjects.map((dto) => buildSceneWorldObject(dto, assetRegistry, anchorRegistry, scene.environmentId)),
  ).sort(compareByObjectId);

  return {
    location: { locationId: scene.location.locationId, name: scene.location.name },
    environmentId: scene.environmentId,
    worldObjects,
  };
}

/**
 * DEF-072 — deterministic merge of server-confirmed knowledge into a live
 * scene model (PURE, deterministic, offline).
 *
 * The scene model's world-object `discovered`/`read` flags are snapshotted
 * from the bootstrap WorldObjectDTOs at build time. After a live discovery or
 * record read the client's knowledge state (the SAME PlayerKnowledge-derived
 * choice that drives the discovery-summary strip) must flip those flags
 * immediately — otherwise the object-list "· discovered"/"· read" markers and
 * the discovered-only caption overlay stay stale until a reload.
 *
 * Rules:
 *  - the client NEVER fabricates knowledge: each object's flags are derived
 *    EXCLUSIVELY from the server-returned `discoveredEvidenceIds` /
 *    `readEvidenceIds` membership of the object's `evidenceId`;
 *  - an object WITHOUT an `evidenceId` (decorative / no-evidence) is never
 *    touched — its flags stay exactly as built;
 *  - unchanged objects keep their EXACT reference (cheap, no rebuild); only
 *    objects whose flags actually flip are re-created;
 *  - when nothing changed the ORIGINAL model reference is returned (pure,
 *    idempotent — callers can rely on reference stability to skip re-renders).
 */
export function applyKnowledgeToSceneModel(
  model: InvestigationSceneModel,
  knowledge: {
    discoveredEvidenceIds: readonly string[];
    readEvidenceIds: readonly string[];
  },
): InvestigationSceneModel {
  const discoveredSet = new Set(knowledge.discoveredEvidenceIds);
  const readSet = new Set(knowledge.readEvidenceIds);
  let changed = false;
  const worldObjects = model.worldObjects.map((obj) => {
    if (obj.evidenceId === null) return obj;
    const discovered = discoveredSet.has(obj.evidenceId);
    const read = readSet.has(obj.evidenceId);
    if (discovered === obj.discovered && read === obj.read) return obj;
    changed = true;
    return { ...obj, discovered, read };
  });
  if (!changed) return model;
  return { ...model, worldObjects };
}

/**
 * PD-SEC-01 (Phase 20) — server-confirmed post-discovery object binding.
 *
 * The backend now OMITS `evidenceId` from UNDISCOVERED world objects in the
 * pre-reveal DTOs (bootstrap/public-case): before discovery the client has no
 * right to know any evidence id. The ONLY discovery path is a successful
 * POST /objects/{id}/interact, whose response carries the id that is now
 * player-known. This pure, deterministic merge binds that id onto the matching
 * world object so the subsequent knowledge sync ({@link applyKnowledgeToSceneModel})
 * can flip its `discovered`/`read` flags, and so the player-safe derivations
 * (discovery-summary strip, floating captions, detective notebook, object-list
 * markers) can resolve titles from public registry labels.
 *
 * Reference-stable + idempotent: when the object already carries the same id
 * (repeat / already-discovered interactions), the ORIGINAL model reference is
 * returned — no rebuild, no re-render churn.
 */
export function bindEvidenceToSceneModel(
  model: InvestigationSceneModel,
  objectId: string,
  evidenceId: string,
): InvestigationSceneModel {
  if (evidenceId === "") return model;
  let changed = false;
  const worldObjects = model.worldObjects.map((obj) => {
    if (obj.objectId !== objectId) return obj;
    if (obj.evidenceId === evidenceId) return obj;
    changed = true;
    return { ...obj, evidenceId };
  });
  if (!changed) return model;
  return { ...model, worldObjects };
}

/**
 * Deterministic de-cramping of co-located world objects (Phase 8 E).
 *
 * The backend may place several objects at the same semantic anchor (the
 * golden case puts the vase ON the dining table — both use "dining_table").
 * A shared anchor resolves to a single position, which would stack objects
 * perfectly on top of each other. This pure helper separates such groups:
 *
 *  - the TALLEST object in the group keeps the anchor position exactly (it
 *    is the "surface");
 *  - every other object is lifted to sit ON that surface (deterministic y)
 *    and fanned out horizontally around it (deterministic x offset from the
 *    sorted objectId order).
 *
 * Everything is a pure function of the anchor transform + object scales/ids,
 * never of array order or server payload fields, so determinism and the
 * "registry-only geometry" rule both hold.
 */
export function applySharedAnchorSpacing(objects: SceneWorldObject[]): SceneWorldObject[] {
  const groups = new Map<string, SceneWorldObject[]>();
  for (const obj of objects) {
    const key = `${obj.position.x}|${obj.position.y}|${obj.position.z}`;
    const group = groups.get(key);
    if (group) {
      group.push(obj);
    } else {
      groups.set(key, [obj]);
    }
  }
  if (groups.size === objects.length) {
    return objects; // Nothing shares a spot — no spacing needed.
  }
  const spaced: SceneWorldObject[] = [];
  for (const group of groups.values()) {
    if (group.length === 1) {
      spaced.push(group[0]);
      continue;
    }
    const ordered = [...group].sort(compareByObjectId);
    let tallestIndex = 0;
    for (let i = 1; i < ordered.length; i++) {
      if (ordered[i].scale.y > ordered[tallestIndex].scale.y) tallestIndex = i;
    }
    const surface = ordered[tallestIndex];
    const surfaceTop = surface.position.y + surface.scale.y / 2;
    const others = ordered.filter((obj) => obj !== surface);
    spaced.push(surface);
    const count = others.length;
    others.forEach((obj, index) => {
      const spread = (index - (count - 1) / 2) * 0.55;
      spaced.push({
        ...obj,
        position: {
          x: surface.position.x + spread,
          y: Math.max(surfaceTop + obj.scale.y / 2, surface.position.y),
          z: surface.position.z,
        },
      });
    });
  }
  return spaced;
}

function compareByObjectId(a: SceneWorldObject, b: SceneWorldObject): number {
  if (a.objectId < b.objectId) return -1;
  if (a.objectId > b.objectId) return 1;
  return 0;
}

/* ======================================================================
 * Phase 15 Track B — evidence legibility in the larger environment kits.
 *
 * The golden apartment stays byte-identical (factor 1). In the four bigger
 * kits (office / hotel_suite / warehouse / mansion) tiny evidence objects
 * (knife, letter opener, wristwatch, medication bottle…) are easy to miss
 * entirely from the default camera, so DISCOVERABLE, interactable evidence
 * below a legible extent gets a deterministic uniform visual scale-up. This
 * is a spot-fix, primitive-only and purely visual:
 *   - objectIds / mesh names / anchors / interactions are untouched;
 *   - the same (kit, object) pair ALWAYS yields the same factor;
 *   - the apartment kit and all non-evidence objects always render at 1;
 *   - picking boxes scale with the mesh, so direct 3D clicking keeps working
 *     (it only gets EASIER for the previously underwhelming targets).
 * ==================================================================== */

/** Minimum legible evidence extent in world meters (Phase 15 visibility floor). */
export const EVIDENCE_LEGIBLE_EXTENT = 0.45;

/** Hard cap so an absurdly small item never becomes a room-scale prop. */
export const MAX_EVIDENCE_RENDER_SCALE = 3;

/**
 * Deterministic evidence legibility factor for a world object (pure function
 * of the kit id, the published evidence link, the interaction affordance and
 * the catalog-derived scale). Apartment = 1; evidence that already spans at
 * least {@link EVIDENCE_LEGIBLE_EXTENT} in its largest axis stays at 1.
 */
export function kitEvidenceRenderScale(
  environmentId: string,
  evidenceId: string | null,
  interactionWorks: boolean,
  scale: Vec3,
): number {
  if (environmentId === APARTMENT_KIT_ID) return 1;
  if (evidenceId === null || !interactionWorks) return 1;
  const extent = Math.max(scale.x, scale.y, scale.z);
  if (!Number.isFinite(extent) || extent <= 0 || extent >= EVIDENCE_LEGIBLE_EXTENT) return 1;
  return Math.min(MAX_EVIDENCE_RENDER_SCALE, EVIDENCE_LEGIBLE_EXTENT / extent);
}

function buildSceneWorldObject(
  dto: WorldObjectDTO,
  assetRegistry: AssetRegistry,
  anchorRegistry: AnchorRegistry,
  environmentId: string,
): SceneWorldObject {
  // Phase 13 Track B: a `proc.*` asset with a VALID generated block renders
  // from the declarative definition — the catalog is BYPASSED entirely (proc.*
  // ids are never catalog ids). A proc.* asset whose block is missing/invalid
  // has generated === null (the validator gate) and falls back to the neutral
  // primitive; a non-proc asset ALWAYS ignores the field.
  const generated = dto.generated ?? null;
  const isGenerated = generated !== null;
  const knownEntry = isGenerated ? undefined : assetRegistry.get(dto.assetId);
  const entry: AssetEntry = knownEntry ?? FALLBACK_ASSET;
  const unknownAsset = knownEntry === undefined && !isGenerated;
  // Phase 11 Track B: the per-object transform comes from the kit's anchor
  // registry — the apartment kit keeps the Phase 6 table (byte-identical),
  // every other kit resolves strictly from the manifest; unknown anchors use
  // the stable objectId-hash slot (see ../environments/kitGeometry.ts).
  const transform = transformFor(environmentId, dto.anchor, dto.objectId, anchorRegistry);
  const rotation = transform.rotation ?? { x: 0, y: 0, z: 0 };

  // Phase 13: compile the generated definition into child parts + the declared
  // picking extent ONCE at model build time (pure, deterministic — the same
  // load always yields the same parts array).
  let generatedParts: readonly CompositePartDescriptor[] | null = null;
  let generatedHitbox: Vec3 | null = null;
  if (generated !== null) {
    const built = buildGeneratedComposite(generated);
    generatedParts = built.parts;
    generatedHitbox = built.hitbox;
  }

  // Phase 12 Track B: TEMPLATE-ONLY composites (a templateId but NO legacy
  // compositeKind) are sized from the factory's absolute bounds (the child
  // parts were already compiled at the DEFAULT variant in the registry), so
  // shared-anchor spacing, the highlight ring and the pick-hitbox policy all
  // see the REAL rendered footprint. Legacy composites and primitives keep
  // the catalog `dimensions` scale exactly as before (byte-identical golden).
  const templateId = entry.templateId ?? null;
  const templateParts = entry.templateParts ?? null;
  const templateFaceBounds = entry.templateFaceBounds ?? null;
  const templateHitbox = entry.templateHitbox ?? null;
  const templateMaterial = entry.templateMaterial ?? null;
  const templateState = entry.templateState ?? null;
  const templateBacked =
    !isGenerated &&
    entry.compositeKind === null &&
    templateId !== null &&
    templateParts !== null &&
    templateFaceBounds !== null;
  // Phase 13: a generated object's footprint IS its declared picking extent
  // (already ≥ the visual bounds per axis, clamped pickable by the compiler),
  // so spacing/ring/hitbox math all see the real rendered footprint.
  const scale = generatedHitbox !== null
    ? generatedHitbox
    : templateBacked
      ? { x: templateFaceBounds.x, y: templateFaceBounds.y, z: templateFaceBounds.z }
      : { x: entry.scale.x, y: entry.scale.y, z: entry.scale.z };

  return {
    objectId: dto.objectId,
    assetId: dto.assetId,
    assetType: dto.assetType,
    subtype: dto.subtype,
    primitiveKind: entry.primitiveKind,
    compositeKind: entry.compositeKind ?? null,
    hitboxScale: entry.hitboxScale ?? 1,
    color: entry.color,
    scale,
    // Unknown assets get no label: a neutral placeholder must never claim a
    // name. Generated objects also keep label null — a server-provided
    // canonicalName is never echoed onto the page (app-authored text only).
    label: unknownAsset ? null : entry.label,
    position: { x: transform.position.x, y: transform.position.y, z: transform.position.z },
    rotation,
    interaction: dto.interaction,
    // DEF-062 (ADV-144, MED): interactionWorks is PAYLOAD-DRIVEN — the
    // affordance of an ALREADY-PUBLISHED case must never change when a newer
    // catalog is bundled later. The published DTO's non-empty `interaction`
    // string is the ONLY gate: an object is clickable/hoverable exactly when
    // the backend published an interaction for it. The catalog `interactable`
    // flag is NOT consulted here — it remains only the DEFAULT affordance for
    // NEW scene authoring (future), never a per-published-case override.
    interactionWorks: dto.interaction !== "",
    evidenceId: dto.evidenceId,
    discovered: dto.discovered,
    read: dto.read,
    unknownAsset,
    generated,
    generatedParts,
    generatedHitbox,
    templateId: templateBacked ? templateId : null,
    templateParts: templateBacked ? templateParts : null,
    templateHitbox: templateBacked ? templateHitbox : null,
    templateMaterial: templateBacked ? templateMaterial : null,
    templateState: templateBacked ? templateState : null,
    // Phase 15 Track B: visual legibility factor for non-apartment kit evidence.
    renderScale: kitEvidenceRenderScale(environmentId, dto.evidenceId, dto.interaction !== "", scale),
  };
}