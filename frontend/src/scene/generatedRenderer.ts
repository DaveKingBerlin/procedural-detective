import type { GeneratedAssetDefinition } from "../api/types";
import type { Vec3 } from "./apartment";
import type { CompositePartDescriptor } from "./assetRegistry";
import { FALLBACK_COLOR } from "./assetRegistry";

/**
 * Phase 13 — declarative generated asset renderer (frontend side).
 *
 * The generic companion to the Phase 12 template factory
 * (src/templates/templateRegistry.ts): {@link buildGeneratedComposite} turns a
 * fully validated {@link GeneratedAssetDefinition} (the backend compiler's
 * frozen `to_definition_json()` contract) into the SAME `CompositePartDescriptor`
 * list the scene renderer already knows — a root `pd_obj_<objectId>` mesh with
 * `pd_part_<objectId>_N` children, exactly like the catalog/template machinery.
 *
 * PURE + DETERMINISTIC: this module imports NO Babylon, NO DOM, NO network and
 * NO randomness. The same definition ALWAYS produces the identical parts array;
 * the scene glue (`renderInvestigation.ts`) applies the resulting descriptors
 * verbatim. Only validated definitions ever reach this module from the scene
 * model (the DTO gate is `validateGeneratedDefinition`), so geometry is
 * bounded by construction: |position| ≤ 4, |rotation| ≤ 2π, 0.05 ≤ scale ≤ 2,
 * ≤ 24 parts, #RRGGBB colors, hitbox within 0.15..10.
 *
 * TRANSFORM MODEL (documented):
 *  - every part's `offset` is expressed from the object ROOT in meters;
 *  - a part WITH a `parentId` is positioned RELATIVE to that parent part:
 *    its root offset is the parent part's root offset plus the part's own
 *    declared position, accumulated up the (≤ 2 hop) ancestor chain —
 *    a deterministic TRANSLATION-ONLY inheritance rule;
 *  - each part mesh's `rotation` is applied independently in its own local
 *    frame (rotations are NOT composed down the parent chain), mirroring how
 *    the template factory and legacy composite builders treat rotations;
 *  - `plane` maps to the existing "flat" box primitive: a thin box of the
 *    declared in-plane extent whose thickness along the plane normal (z) is
 *    min(declared scale.z, 0.02 m) — a planar surface is never exploded into
 *    a thick slab, whatever the spec declares.
 *
 * HITBOX: `def.hitbox.scale` is the declared picking extent basis (already
 * bounded 0.15..10 by both the backend derive step and the client gate). The
 * existing MIN_PICKABLE_EXTENT policy (renderInvestigation / assetRegistry)
 * still applies: a small generated object gets the safe invisible pick box.
 *
 * FALLBACK: a null/structurally-absent definition compiles to the same neutral
 * non-interactable 0.4 m box every other unknown asset uses — never a throw,
 * never a ghost object.
 */

/** One compiled generated composite: child parts + the declared hitbox basis. */
export interface GeneratedComposite {
  parts: readonly CompositePartDescriptor[];
  hitbox: Vec3 | null;
}

/** The neutral fallback composite (mirror of the app-wide unknown-asset box). */
const GENERATED_FALLBACK_PARTS: readonly CompositePartDescriptor[] = [
  {
    kind: "box",
    size: { x: 0.4, y: 0.4, z: 0.4 },
    offset: { x: 0, y: 0, z: 0 },
    color: FALLBACK_COLOR,
  },
];

/** Max thickness of a planar surface along its normal (kind "flat" box). */
const PLANE_MAX_THICKNESS = 0.02;

/**
 * Minimal structural guard for the DEFENSIVE fallback path. Real scenes only
 * ever pass fully-validated definitions (the DTO gate); this guard exists so
 * a null / garbage argument degrades to the neutral box instead of throwing.
 */
function isStructurallyValid(def: unknown): def is GeneratedAssetDefinition {
  if (typeof def !== "object" || def === null) return false;
  const record = def as { parts?: unknown; hitbox?: unknown };
  if (!Array.isArray(record.parts) || record.parts.length === 0) return false;
  const hitbox = record.hitbox;
  if (typeof hitbox !== "object" || hitbox === null) return false;
  const scale = (hitbox as { scale?: unknown }).scale;
  if (typeof scale !== "object" || scale === null) return false;
  const vec = scale as { x?: unknown; y?: unknown; z?: unknown };
  return (
    typeof vec.x === "number" &&
    Number.isFinite(vec.x) &&
    typeof vec.y === "number" &&
    Number.isFinite(vec.y) &&
    typeof vec.z === "number" &&
    Number.isFinite(vec.z)
  );
}

/**
 * Build the deterministic composite descriptor list of ONE validated
 * generated definition. Empty/absent definitions compile to the neutral
 * fallback parts (never throws). Part ORDER is the definition order (the
 * backend already emits compiler-ordered part_00..part_0N).
 *
 * @param def a validated GeneratedAssetDefinition (or null/undefined for the
 *            defensive fallback path)
 */
export function buildGeneratedComposite(
  def: GeneratedAssetDefinition | null | undefined,
): GeneratedComposite {
  if (!isStructurallyValid(def)) {
    return { parts: GENERATED_FALLBACK_PARTS, hitbox: null };
  }

  const indexOf = new Map<string, number>();
  def.parts.forEach((part, index) => {
    indexOf.set(part.id, index);
  });

  const absoluteOffsets: Vec3[] = [];
  const parts: CompositePartDescriptor[] = [];
  for (const part of def.parts) {
    // Translation-only parent-relative placement: a parented child's root
    // offset = the parent's absolute root offset + this part's position
    // (parents are guaranteed earlier by validation, so the offset is ready).
    const parentIndex =
      part.parentId !== null && part.parentId !== undefined
        ? indexOf.get(part.parentId)
        : undefined;
    const parentOffset =
      parentIndex !== undefined && parentIndex < absoluteOffsets.length
        ? absoluteOffsets[parentIndex]
        : { x: 0, y: 0, z: 0 };
    const offset: Vec3 = {
      x: part.transform.position.x + parentOffset.x,
      y: part.transform.position.y + parentOffset.y,
      z: part.transform.position.z + parentOffset.z,
    };
    absoluteOffsets.push(offset);

    const kind =
      part.primitive === "cylinder" ? "cylinder" : part.primitive === "sphere" ? "sphere" : "box";
    const size: Vec3 =
      part.primitive === "plane"
        ? {
            x: part.transform.scale.x,
            y: part.transform.scale.y,
            z: Math.min(part.transform.scale.z, PLANE_MAX_THICKNESS),
          }
        : {
            x: part.transform.scale.x,
            y: part.transform.scale.y,
            z: part.transform.scale.z,
          };
    parts.push({
      kind,
      size,
      offset,
      // Always present: the definition always carries a rotation, and the
      // renderer applies it as a local Euler rotation, exactly as declared.
      rotation: {
        x: part.transform.rotation.x,
        y: part.transform.rotation.y,
        z: part.transform.rotation.z,
      },
      color: part.color,
    });
  }

  const hitbox: Vec3 = {
    x: def.hitbox.scale.x,
    y: def.hitbox.scale.y,
    z: def.hitbox.scale.z,
  };
  return { parts, hitbox };
}