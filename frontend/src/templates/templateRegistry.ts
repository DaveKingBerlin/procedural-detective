import type { Vec3 } from "../scene/apartment";

/**
 * Phase 12 Track B — frozen template vocabulary + generic template→composite
 * factory (frontend side).
 *
 * THE BACKEND FROZEN CONTRACT (`backend/app/assets/catalog.py`):
 *
 *   TEMPLATE_VOCABULARY: exactly 59 logical templates. A composite catalog
 *   asset references ONE via `templateId`; the frontend compiles it into
 *   local primitives. These three vocabularies + `PRIMARY_COLOR_KEYS` are the
 *   SINGLE frontend copy, PINNED to the backend values. A frontend test pins
 *   them against the real manifest (see assetCatalog.test.ts / the Phase 12
 *   lockstep suite), so the frontend and the manifest can never drift: every
 *   `templateId` used by the bundled catalog must be a member, and every
 *   material/state token the manifest emits must be a member of the frozen
 *   literal vocabularies.
 *
 * THE FACTORY:
 *   {@link buildTemplateComposite} is the SINGLE generic, deterministic
 *   template→primitive-composite factory used by buildInvestigationScene /
 *   renderInvestigation: it turns a `templateId` + resolved variant colors +
 *   variant scale into `{ parts, hitbox, bounds }`. The same template +
 *   params ALWAYS yields the identical parts array (pure math over the frozen
 *   data tables below). All geometry is finite and bounded: every part scale
 *   and offset keeps |value| <= 1.9 in template space, so the maximum variant
 *   scale (2.0) can never push a single part beyond 3.8 world meters.
 *
 * SAFETY: this module contains ONLY declarative numbers + hex color lookups
 * (no URLs, no paths, no scripts, no shaders, no DOM/engine). Unknown
 * template ids resolve to {@link TEMPLATE_FALLBACK} — a neutral, non-
 * interactable 0.4 m cube — so an unknown/absent template can never crash the
 * scene or produce a ghost (invisible) object.
 */

/** The four primitive kinds a template part is compiled to. */
export type TemplatePrimitiveKind = "box" | "cylinder" | "sphere" | "flat";

/** One frozen template part — a primitive shape in template-local space. */
export interface TemplatePartSpec {
  primitiveKind: TemplatePrimitiveKind;
  /** Part size in template meters (x=width, y=height, z=depth; sphere uses max axis as diameter). */
  scale: Vec3;
  /** Local offset from the object ROOT in template meters. */
  offset: Vec3;
  /** Optional local Euler rotation in radians. */
  rotation?: Vec3;
  /**
   * Logical color key resolved against the asset's (variant-merged) `colors`
   * map at build time. A missing key falls back to the asset's primary tone;
   * never a server string.
   */
  colorKey: string;
}

/** Optional explicit whole-object picking box (template-local meters). */
export interface TemplateHitboxSpec {
  scale: Vec3;
}

/** One frozen template — the full silhouette data table entry. */
export interface TemplateSpec {
  id: string;
  /** 2–6 parts (the fallback template is the 1-part neutral exception). */
  parts: readonly TemplatePartSpec[];
  /** Optional explicit logical picking box (scaled with the variant scale). */
  hitbox?: TemplateHitboxSpec;
}

/*
 * ======================================================================
 * FROZEN VOCABULARIES (pinned verbatim from backend/app/assets/catalog.py —
 * TEMPLATE_VOCABULARY order is the backend's order; do not reorder).
 * ==================================================================== */

/** The frozen 59-template logical vocabulary (backend mirror). */
export const TEMPLATE_VOCABULARY: readonly string[] = [
  "blade_chef",
  "blade_bread",
  "blade_letter",
  "tool_screwdriver",
  "tool_hammer",
  "tool_wrench",
  "tool_bat",
  "blades_scissor",
  "bottle_glass",
  "rope_coil",
  "key_small",
  "usb_stick",
  "wallet_flat",
  "watch_round",
  "bottle_med",
  "glove_flat",
  "marker_flat",
  "phone_body",
  "camera_body",
  "box_jewelry",
  "table_form",
  "chair_frame",
  "sofa_form",
  "bed_form",
  "nightstand",
  "shelf_rack",
  "cabinet_box",
  "locker_box",
  "crate_box",
  "monitor_stand",
  "keyboard_slab",
  "tablet_flat",
  "cctv_cam",
  "router_box",
  "reader_panel",
  "printer_box",
  "tv_screen",
  "docs_flat",
  "folder_flat",
  "card_flat",
  "notebook_doc",
  "lamp_profile",
  "picture_frame",
  "plant_pot",
  "cup_form",
  "plate_flat",
  "clock_round",
  "pen_stick",
  "handbag_form",
  "coat_hang",
  "safe_box",
  "switch_panel",
  "trash_bin",
  "sink_bowl",
  "counter_top",
  "storage_box",
  "window_flat",
  "wall_panel",
  "door_slab",
];

/** Frozen material literal vocabulary (backend mirror). */
export const MATERIAL_VOCABULARY: readonly string[] = [
  "wood.dark",
  "wood.light",
  "metal.brass",
  "metal.steel",
  "plastic",
  "fabric",
  "leather",
  "ceramic",
];

/** Frozen state literal vocabulary (backend mirror). */
export const STATE_VOCABULARY: readonly string[] = ["clean", "weathered", "damaged", "open", "closed", "on", "off"];

/**
 * Deterministic primary-tone preference shared by the asset registry, the
 * variant renderers and the template factory: the most descriptive visible
 * color key of a descriptor (shade/base/blade/blades/top/body) wins; anything
 * else falls back to the first color entry (manifest order), then to the
 * neutral hex. The variant color merge ({@link renderColors}) and the factory
 * color-key fallback both key off this list.
 */
export const PRIMARY_COLOR_KEYS: readonly string[] = ["shade", "base", "blade", "blades", "top", "body"];

/** RGB hex grammar used by the factory color lookup. */
const COLOR_PATTERN = /^#[0-9A-Fa-f]{6}$/;

/** Neutral hex used when no catalog color matches a part color key. */
const PART_COLOR_FALLBACK_HEX = "#8d8d93";

/** Deterministic primary tone of a colors map (never a server string). */
export function primaryHex(colors: Readonly<Record<string, string>>, fallbackHex: string = PART_COLOR_FALLBACK_HEX): string {
  for (const key of PRIMARY_COLOR_KEYS) {
    const value = colors[key];
    if (typeof value === "string" && COLOR_PATTERN.test(value)) return value;
  }
  for (const value of Object.values(colors)) {
    if (typeof value === "string" && COLOR_PATTERN.test(value)) return value;
  }
  return fallbackHex;
}

/* ======================================================================
 * Frozen 59-template DATA TABLE.
 *
 * Silhouette differentiation (Phase 12 / REQUIREMENTS 2.4): the backend
 * asserts distinctness for the critical pairs below, and the template tests
 * re-assert materially different dimension signatures on the BUILDS:
 *  - chef (0.24 long pale blade, 17:1 sliver) vs bread (0.32 longer blade +
 *    a serration layer, 26:1) vs letter opener (short wide spatulate blade,
 *    7:1 — never a knife);
 *  - screwdriver (thin vertical cylinder shaft, 14:1) vs hammer (blocky head
 *    + grip, ~5:1);
 *  - key (flat y-profile: bow + shaft + teeth, ~4:1) vs USB stick (one long
 *    thin slab + cap, ~7:1);
 *  - phone (thin slab + screen, 30:1) vs camera (chunky body + lens + grip,
 *    ~1.5:1).
 * ==================================================================== */

interface PartTuple {
  p: TemplatePrimitiveKind;
  s: Vec3;
  o: Vec3;
  r?: Vec3;
  c: string;
}

type Row = [string, PartTuple[], Vec3?];

/** Compact tuple -> frozen TemplatePartSpec rows (pure data). */
function row([id, parts, hitbox]: Row): TemplateSpec {
  return {
    id,
    parts: parts.map(({ p, s, o, r, c }) => ({
      primitiveKind: p,
      scale: { x: s.x, y: s.y, z: s.z },
      offset: { x: o.x, y: o.y, z: o.z },
      ...(r !== undefined ? { rotation: { x: r.x, y: r.y, z: r.z } } : {}),
      colorKey: c,
    })),
    ...(hitbox !== undefined ? { hitbox: { scale: { x: hitbox.x, y: hitbox.y, z: hitbox.z } } } : {}),
  };
}

const B = (s: Vec3, o: Vec3, c: string, r?: Vec3): PartTuple => ({ p: "box", s, o, c, r });
const C = (s: Vec3, o: Vec3, c: string, r?: Vec3): PartTuple => ({ p: "cylinder", s, o, c, r });
const S = (s: Vec3, o: Vec3, c: string, r?: Vec3): PartTuple => ({ p: "sphere", s, o, c, r });

/* eslint-disable max-len -- frozen data table (each row is one template). */
const TEMPLATE_ROWS: readonly Row[] = [
  // --- blades (critical distinctness: chef vs bread vs letter) ---
  ["blade_chef", [
    B({ x: 0.045, y: 0.014, z: 0.24 }, { x: 0, y: 0, z: 0.065 }, "blade"),
    B({ x: 0.035, y: 0.024, z: 0.09 }, { x: 0, y: 0, z: -0.155 }, "handle"),
  ], { x: 0.05, y: 0.026, z: 0.26 }],
  ["blade_bread", [
    B({ x: 0.045, y: 0.012, z: 0.32 }, { x: 0, y: 0, z: 0.07 }, "blade"),
    B({ x: 0.04, y: 0.006, z: 0.3 }, { x: 0, y: -0.008, z: 0.075 }, "blade"),
    B({ x: 0.04, y: 0.028, z: 0.1 }, { x: 0, y: 0, z: -0.2 }, "handle"),
  ], { x: 0.05, y: 0.03, z: 0.36 }],
  ["blade_letter", [
    B({ x: 0.09, y: 0.018, z: 0.13 }, { x: 0, y: 0, z: 0.075 }, "blade"),
    B({ x: 0.05, y: 0.028, z: 0.07 }, { x: 0, y: 0, z: -0.135 }, "handle"),
  ], { x: 0.1, y: 0.03, z: 0.16 }],

  // --- tools (screwdriver vs hammer vs wrench vs bat) ---
  ["tool_screwdriver", [
    C({ x: 0.014, y: 0.2, z: 0.014 }, { x: 0, y: 0.07, z: 0 }, "shaft"),
    C({ x: 0.03, y: 0.09, z: 0.03 }, { x: 0, y: -0.06, z: 0 }, "handle"),
  ], { x: 0.035, y: 0.28, z: 0.035 }],
  ["tool_hammer", [
    B({ x: 0.18, y: 0.07, z: 0.06 }, { x: 0, y: 0.05, z: 0 }, "head"),
    B({ x: 0.045, y: 0.22, z: 0.045 }, { x: 0, y: -0.09, z: 0 }, "handle"),
  ], { x: 0.2, y: 0.28, z: 0.08 }],
  ["tool_wrench", [
    B({ x: 0.24, y: 0.04, z: 0.05 }, { x: 0, y: 0, z: 0 }, "body"),
    B({ x: 0.1, y: 0.035, z: 0.045 }, { x: 0.13, y: 0, z: 0 }, "grip"),
  ], { x: 0.26, y: 0.05, z: 0.06 }],
  ["tool_bat", [
    C({ x: 0.05, y: 0.6, z: 0.05 }, { x: 0, y: 0.03, z: 0 }, "barrel"),
    C({ x: 0.052, y: 0.16, z: 0.052 }, { x: 0, y: -0.27, z: 0 }, "grip"),
  ], { x: 0.06, y: 0.72, z: 0.06 }],

  // --- sharp / small evidence ---
  ["blades_scissor", [
    B({ x: 0.022, y: 0.012, z: 0.1 }, { x: 0, y: 0, z: 0.05 }, "blades", { x: 0, y: 0.45, z: 0 }),
    B({ x: 0.022, y: 0.012, z: 0.1 }, { x: 0, y: 0, z: 0.05 }, "blades", { x: 0, y: -0.45, z: 0 }),
    C({ x: 0.03, y: 0.018, z: 0.03 }, { x: 0, y: 0, z: 0 }, "pivot"),
  ], { x: 0.06, y: 0.03, z: 0.12 }],
  ["bottle_glass", [
    C({ x: 0.07, y: 0.22, z: 0.07 }, { x: 0, y: 0, z: 0 }, "glass"),
    C({ x: 0.032, y: 0.05, z: 0.032 }, { x: 0, y: 0.14, z: 0 }, "glass"),
    C({ x: 0.036, y: 0.018, z: 0.036 }, { x: 0, y: 0.185, z: 0 }, "cap"),
  ], { x: 0.08, y: 0.28, z: 0.08 }],
  ["rope_coil", [
    C({ x: 0.26, y: 0.05, z: 0.26 }, { x: 0, y: 0, z: 0 }, "rope"),
    B({ x: 0.2, y: 0.035, z: 0.22 }, { x: 0, y: 0.045, z: 0 }, "rope"),
  ], { x: 0.3, y: 0.1, z: 0.3 }],
  ["key_small", [
    C({ x: 0.022, y: 0.012, z: 0.022 }, { x: 0, y: 0, z: -0.024 }, "metal"),
    B({ x: 0.01, y: 0.008, z: 0.03 }, { x: 0, y: 0, z: 0.01 }, "metal"),
    B({ x: 0.022, y: 0.008, z: 0.01 }, { x: 0, y: 0, z: 0.03 }, "metal"),
  ], { x: 0.07, y: 0.02, z: 0.05 }],
  ["usb_stick", [
    B({ x: 0.045, y: 0.006, z: 0.014 }, { x: 0, y: 0, z: 0 }, "shell"),
    B({ x: 0.012, y: 0.008, z: 0.016 }, { x: 0.03, y: 0, z: -0.001 }, "cap"),
  ], { x: 0.06, y: 0.015, z: 0.05 }],
  ["wallet_flat", [
    B({ x: 0.1, y: 0.012, z: 0.075 }, { x: 0, y: 0, z: 0 }, "leather"),
    B({ x: 0.1, y: 0.01, z: 0.055 }, { x: 0, y: 0.012, z: 0.012 }, "leather"),
  ], { x: 0.11, y: 0.03, z: 0.09 }],
  ["watch_round", [
    C({ x: 0.028, y: 0.012, z: 0.028 }, { x: 0, y: 0.006, z: 0 }, "case"),
    B({ x: 0.006, y: 0.012, z: 0.032 }, { x: 0, y: 0.006, z: 0.028 }, "strap"),
    B({ x: 0.006, y: 0.012, z: 0.032 }, { x: 0, y: 0.006, z: -0.028 }, "strap"),
  ], { x: 0.06, y: 0.03, z: 0.09 }],
  ["bottle_med", [
    C({ x: 0.018, y: 0.05, z: 0.018 }, { x: 0, y: 0, z: 0 }, "body"),
    C({ x: 0.011, y: 0.012, z: 0.011 }, { x: 0, y: 0.032, z: 0 }, "cap"),
  ], { x: 0.04, y: 0.09, z: 0.04 }],
  ["glove_flat", [
    B({ x: 0.09, y: 0.018, z: 0.05 }, { x: 0, y: 0, z: -0.01 }, "leather"),
    B({ x: 0.045, y: 0.014, z: 0.025 }, { x: 0, y: 0, z: -0.055 }, "leather"),
    B({ x: 0.025, y: 0.016, z: 0.02 }, { x: 0.032, y: 0, z: -0.03 }, "leather"),
  ], { x: 0.11, y: 0.04, z: 0.075 }],
  ["marker_flat", [
    B({ x: 0.14, y: 0.02, z: 0.045 }, { x: 0, y: 0, z: 0 }, "marker"),
    B({ x: 0.05, y: 0.004, z: 0.02 }, { x: 0, y: 0.012, z: 0 }, "number"),
  ], { x: 0.16, y: 0.04, z: 0.06 }],
  ["phone_body", [
    B({ x: 0.16, y: 0.007, z: 0.075 }, { x: 0, y: 0, z: 0 }, "body"),
    B({ x: 0.12, y: 0.004, z: 0.06 }, { x: 0, y: 0.005, z: 0 }, "screen"),
  ], { x: 0.17, y: 0.02, z: 0.09 }],
  ["camera_body", [
    B({ x: 0.12, y: 0.08, z: 0.1 }, { x: 0, y: 0, z: 0 }, "body"),
    C({ x: 0.028, y: 0.03, z: 0.04 }, { x: 0, y: 0.025, z: 0.06 }, "lens"),
    B({ x: 0.045, y: 0.07, z: 0.03 }, { x: 0.05, y: 0, z: 0.04 }, "body"),
  ], { x: 0.14, y: 0.11, z: 0.14 }],
  ["box_jewelry", [
    B({ x: 0.22, y: 0.035, z: 0.16 }, { x: 0, y: 0.035, z: 0 }, "lid"),
    B({ x: 0.2, y: 0.04, z: 0.14 }, { x: 0, y: -0.012, z: 0 }, "body"),
  ], { x: 0.24, y: 0.09, z: 0.18 }],

  // --- furniture ---
  ["table_form", [
    B({ x: 1.9, y: 0.08, z: 1.1 }, { x: 0, y: 0, z: 0 }, "top"),
    B({ x: 0.07, y: 0.6, z: 0.07 }, { x: -0.85, y: -0.32, z: -0.45 }, "legs"),
    B({ x: 0.07, y: 0.6, z: 0.07 }, { x: 0.85, y: -0.32, z: -0.45 }, "legs"),
    B({ x: 0.07, y: 0.6, z: 0.07 }, { x: -0.85, y: -0.32, z: 0.45 }, "legs"),
    B({ x: 0.07, y: 0.6, z: 0.07 }, { x: 0.85, y: -0.32, z: 0.45 }, "legs"),
  ], { x: 1.9, y: 0.72, z: 1.1 }],
  ["chair_frame", [
    B({ x: 0.45, y: 0.06, z: 0.45 }, { x: 0, y: 0, z: 0 }, "seat"),
    B({ x: 0.45, y: 0.5, z: 0.06 }, { x: 0, y: 0.28, z: -0.22 }, "frame"),
    B({ x: 0.04, y: 0.45, z: 0.04 }, { x: -0.18, y: -0.24, z: -0.18 }, "frame"),
    B({ x: 0.04, y: 0.45, z: 0.04 }, { x: 0.18, y: -0.24, z: -0.18 }, "frame"),
    B({ x: 0.04, y: 0.45, z: 0.04 }, { x: -0.18, y: -0.24, z: 0.18 }, "frame"),
    B({ x: 0.04, y: 0.45, z: 0.04 }, { x: 0.18, y: -0.24, z: 0.18 }, "frame"),
  ], { x: 0.5, y: 0.9, z: 0.5 }],
  ["sofa_form", [
    B({ x: 1.9, y: 0.22, z: 0.95 }, { x: 0, y: 0, z: 0 }, "base"),
    B({ x: 1.9, y: 0.5, z: 0.3 }, { x: 0, y: 0.32, z: -0.4 }, "base"),
    B({ x: 1.8, y: 0.16, z: 0.8 }, { x: 0, y: 0.25, z: 0 }, "cushions"),
  ], { x: 1.9, y: 0.75, z: 0.95 }],
  ["bed_form", [
    B({ x: 1.9, y: 0.35, z: 1.45 }, { x: 0, y: 0, z: 0 }, "frame"),
    B({ x: 1.8, y: 0.18, z: 1.35 }, { x: 0, y: 0.27, z: 0 }, "mattress"),
  ], { x: 1.9, y: 0.55, z: 1.45 }],
  ["nightstand", [
    B({ x: 0.5, y: 0.05, z: 0.45 }, { x: 0, y: 0, z: 0 }, "top"),
    B({ x: 0.46, y: 0.42, z: 0.41 }, { x: 0, y: -0.25, z: 0 }, "body"),
    B({ x: 0.44, y: 0.05, z: 0.39 }, { x: 0, y: -0.49, z: 0 }, "body"),
  ], { x: 0.5, y: 0.55, z: 0.45 }],
  ["shelf_rack", [
    B({ x: 0.05, y: 1.7, z: 0.3 }, { x: -0.42, y: 0, z: 0 }, "frame"),
    B({ x: 0.05, y: 1.7, z: 0.3 }, { x: 0.42, y: 0, z: 0 }, "frame"),
    B({ x: 0.9, y: 0.04, z: 0.3 }, { x: 0, y: 0.1, z: 0 }, "shelf"),
    B({ x: 0.9, y: 0.04, z: 0.3 }, { x: 0, y: -0.1, z: 0 }, "shelf"),
  ], { x: 0.9, y: 1.9, z: 0.35 }],
  ["cabinet_box", [
    B({ x: 1, y: 2, z: 0.55 }, { x: 0, y: 0, z: 0 }, "frame"),
    B({ x: 0.92, y: 1.8, z: 0.05 }, { x: 0, y: 0.02, z: 0.28 }, "doors"),
    B({ x: 0.03, y: 0.3, z: 0.03 }, { x: 0.32, y: 0.02, z: 0.31 }, "doors"),
  ], { x: 1, y: 2, z: 0.55 }],
  ["locker_box", [
    B({ x: 0.6, y: 1.9, z: 0.55 }, { x: 0, y: 0, z: 0 }, "body"),
    B({ x: 0.52, y: 1.7, z: 0.04 }, { x: 0, y: 0.02, z: 0.28 }, "door"),
    B({ x: 0.24, y: 0.4, z: 0.02 }, { x: 0, y: 0.3, z: 0.3 }, "door"),
  ], { x: 0.6, y: 1.9, z: 0.55 }],
  ["crate_box", [
    B({ x: 0.7, y: 0.55, z: 0.7 }, { x: 0, y: 0, z: 0 }, "wood"),
    B({ x: 0.72, y: 0.07, z: 0.72 }, { x: 0, y: 0.2, z: 0 }, "band"),
    B({ x: 0.72, y: 0.07, z: 0.72 }, { x: 0, y: -0.2, z: 0 }, "band"),
  ], { x: 0.7, y: 0.55, z: 0.7 }],

  // --- electronics ---
  ["monitor_stand", [
    B({ x: 0.52, y: 0.34, z: 0.015 }, { x: 0, y: 0, z: 0 }, "bezel"),
    B({ x: 0.46, y: 0.3, z: 0.012 }, { x: 0, y: 0, z: 0.015 }, "screen"),
    B({ x: 0.06, y: 0.1, z: 0.06 }, { x: 0, y: -0.2, z: 0 }, "bezel"),
    B({ x: 0.2, y: 0.02, z: 0.18 }, { x: 0, y: -0.25, z: 0 }, "bezel"),
  ], { x: 0.54, y: 0.4, z: 0.2 }],
  ["keyboard_slab", [
    B({ x: 0.45, y: 0.02, z: 0.15 }, { x: 0, y: 0, z: 0 }, "base"),
    B({ x: 0.4, y: 0.01, z: 0.1 }, { x: 0, y: 0.015, z: 0 }, "keys"),
  ], { x: 0.45, y: 0.03, z: 0.15 }],
  ["tablet_flat", [
    B({ x: 0.25, y: 0.006, z: 0.17 }, { x: 0, y: 0, z: 0 }, "body"),
    B({ x: 0.22, y: 0.004, z: 0.14 }, { x: 0, y: 0.004, z: 0 }, "screen"),
  ], { x: 0.26, y: 0.02, z: 0.18 }],
  ["cctv_cam", [
    B({ x: 0.15, y: 0.08, z: 0.18 }, { x: 0, y: 0.01, z: 0 }, "housing"),
    C({ x: 0.02, y: 0.02, z: 0.03 }, { x: 0, y: 0.04, z: 0.09 }, "lens"),
    B({ x: 0.03, y: 0.12, z: 0.06 }, { x: 0, y: -0.05, z: -0.05 }, "housing"),
  ], { x: 0.16, y: 0.16, z: 0.2 }],
  ["router_box", [
    B({ x: 0.2, y: 0.03, z: 0.14 }, { x: 0, y: 0, z: 0 }, "body"),
    B({ x: 0.015, y: 0.05, z: 0.015 }, { x: -0.08, y: 0.035, z: 0 }, "antenna"),
    B({ x: 0.015, y: 0.05, z: 0.015 }, { x: 0.08, y: 0.035, z: 0 }, "antenna"),
  ], { x: 0.21, y: 0.12, z: 0.15 }],
  ["reader_panel", [
    B({ x: 0.08, y: 0.12, z: 0.02 }, { x: 0, y: 0, z: 0 }, "panel"),
    C({ x: 0.008, y: 0.004, z: 0.008 }, { x: 0, y: 0.02, z: 0.012 }, "led"),
  ], { x: 0.08, y: 0.12, z: 0.02 }],
  ["printer_box", [
    B({ x: 0.45, y: 0.16, z: 0.35 }, { x: 0, y: 0, z: 0 }, "body"),
    B({ x: 0.3, y: 0.03, z: 0.2 }, { x: 0, y: -0.06, z: -0.1 }, "tray"),
  ], { x: 0.45, y: 0.22, z: 0.35 }],
  ["tv_screen", [
    B({ x: 1.1, y: 0.62, z: 0.03 }, { x: 0, y: 0, z: 0 }, "screen"),
    B({ x: 1.2, y: 0.02, z: 0.04 }, { x: 0, y: 0.33, z: 0 }, "bezel"),
    B({ x: 1.2, y: 0.02, z: 0.04 }, { x: 0, y: -0.33, z: 0 }, "bezel"),
    B({ x: 0.4, y: 0.02, z: 0.25 }, { x: 0, y: -0.36, z: 0 }, "bezel"),
  ], { x: 1.2, y: 0.74, z: 0.06 }],

  // --- documents / records ---
  ["docs_flat", [
    B({ x: 0.29, y: 0.004, z: 0.21 }, { x: 0, y: 0, z: 0 }, "paper"),
    B({ x: 0.2, y: 0.001, z: 0.14 }, { x: 0, y: 0.002, z: 0 }, "text"),
  ], { x: 0.29, y: 0.01, z: 0.21 }],
  ["folder_flat", [
    B({ x: 0.32, y: 0.018, z: 0.24 }, { x: 0, y: 0, z: 0 }, "cover"),
    B({ x: 0.06, y: 0.012, z: 0.03 }, { x: 0, y: 0.012, z: 0.1 }, "tab"),
  ], { x: 0.33, y: 0.03, z: 0.25 }],
  ["card_flat", [
    B({ x: 0.086, y: 0.002, z: 0.054 }, { x: 0, y: 0, z: 0 }, "card"),
    B({ x: 0.008, y: 0.001, z: 0.04 }, { x: 0, y: 0.001, z: 0.005 }, "strip"),
  ], { x: 0.09, y: 0.006, z: 0.06 }],
  ["notebook_doc", [
    B({ x: 0.21, y: 0.015, z: 0.29 }, { x: 0, y: 0, z: 0 }, "cover"),
    B({ x: 0.19, y: 0.008, z: 0.27 }, { x: 0, y: 0.012, z: 0 }, "pages"),
  ], { x: 0.22, y: 0.03, z: 0.3 }],

  // --- decor / everyday ---
  ["lamp_profile", [
    C({ x: 0.16, y: 0.03, z: 0.16 }, { x: 0, y: -0.13, z: 0 }, "base"),
    C({ x: 0.022, y: 0.24, z: 0.022 }, { x: 0, y: 0.02, z: 0 }, "base"),
    C({ x: 0.17, y: 0.1, z: 0.17 }, { x: 0, y: 0.17, z: 0 }, "shade"),
  ], { x: 0.2, y: 0.4, z: 0.2 }],
  ["picture_frame", [
    B({ x: 0.35, y: 0.45, z: 0.012 }, { x: 0, y: 0, z: 0 }, "frame"),
    B({ x: 0.28, y: 0.38, z: 0.006 }, { x: 0, y: 0, z: 0.009 }, "glass"),
  ], { x: 0.36, y: 0.46, z: 0.02 }],
  ["plant_pot", [
    C({ x: 0.28, y: 0.26, z: 0.28 }, { x: 0, y: -0.08, z: 0 }, "pot"),
    C({ x: 0.24, y: 0.04, z: 0.24 }, { x: 0, y: 0.07, z: 0 }, "pot"),
    S({ x: 0.24, y: 0.24, z: 0.24 }, { x: 0, y: 0.2, z: 0 }, "foliage"),
  ], { x: 0.32, y: 0.5, z: 0.32 }],
  ["cup_form", [
    C({ x: 0.065, y: 0.08, z: 0.065 }, { x: 0, y: 0, z: 0 }, "body"),
    B({ x: 0.012, y: 0.035, z: 0.05 }, { x: 0.045, y: 0, z: 0 }, "handle"),
  ], { x: 0.09, y: 0.1, z: 0.08 }],
  ["plate_flat", [
    C({ x: 0.24, y: 0.012, z: 0.24 }, { x: 0, y: 0, z: 0 }, "plate"),
    C({ x: 0.18, y: 0.005, z: 0.18 }, { x: 0, y: 0.008, z: 0 }, "rim"),
  ], { x: 0.25, y: 0.03, z: 0.25 }],
  ["clock_round", [
    C({ x: 0.32, y: 0.02, z: 0.32 }, { x: 0, y: 0, z: 0 }, "face"),
    C({ x: 0.36, y: 0.035, z: 0.36 }, { x: 0, y: 0, z: 0 }, "frame"),
    B({ x: 0.24, y: 0.005, z: 0.02 }, { x: 0, y: 0.015, z: 0 }, "frame"),
  ], { x: 0.37, y: 0.04, z: 0.37 }],
  ["pen_stick", [
    C({ x: 0.014, y: 0.12, z: 0.014 }, { x: 0, y: 0, z: 0 }, "body"),
    B({ x: 0.004, y: 0.045, z: 0.012 }, { x: 0.009, y: 0.02, z: 0 }, "clip"),
  ], { x: 0.02, y: 0.15, z: 0.02 }],
  ["handbag_form", [
    B({ x: 0.3, y: 0.18, z: 0.12 }, { x: 0, y: -0.02, z: 0 }, "body"),
    B({ x: 0.06, y: 0.02, z: 0.07 }, { x: 0, y: 0.11, z: 0 }, "handle"),
  ], { x: 0.32, y: 0.24, z: 0.14 }],
  ["coat_hang", [
    B({ x: 0.4, y: 0.9, z: 0.16 }, { x: 0, y: -0.12, z: 0 }, "coat"),
    B({ x: 0.2, y: 0.1, z: 0.06 }, { x: 0, y: 0.38, z: 0.05 }, "collar"),
    B({ x: 0.42, y: 0.02, z: 0.02 }, { x: 0, y: 0.52, z: 0 }, "coat"),
  ], { x: 0.5, y: 1.1, z: 0.2 }],

  // --- structural / utility ---
  ["safe_box", [
    B({ x: 0.5, y: 0.4, z: 0.35 }, { x: 0, y: 0, z: 0 }, "body"),
    B({ x: 0.44, y: 0.36, z: 0.04 }, { x: 0, y: 0.02, z: 0.17 }, "door"),
  ], { x: 0.5, y: 0.4, z: 0.35 }],
  ["switch_panel", [
    B({ x: 0.08, y: 0.12, z: 0.015 }, { x: 0, y: 0, z: 0 }, "plate"),
    B({ x: 0.014, y: 0.05, z: 0.008 }, { x: 0, y: 0, z: 0.012 }, "toggle"),
  ], { x: 0.08, y: 0.12, z: 0.02 }],
  ["trash_bin", [
    C({ x: 0.4, y: 0.6, z: 0.4 }, { x: 0, y: 0, z: 0 }, "body"),
    C({ x: 0.42, y: 0.05, z: 0.42 }, { x: 0, y: 0.32, z: 0 }, "rim"),
  ], { x: 0.42, y: 0.65, z: 0.42 }],
  ["sink_bowl", [
    C({ x: 0.5, y: 0.18, z: 0.5 }, { x: 0, y: 0, z: 0 }, "bowl"),
    C({ x: 0.68, y: 0.05, z: 0.68 }, { x: 0, y: 0.11, z: 0 }, "rim"),
  ], { x: 0.7, y: 0.25, z: 0.7 }],
  ["counter_top", [
    B({ x: 1.9, y: 0.08, z: 0.62 }, { x: 0, y: 0, z: 0 }, "top"),
    B({ x: 1.85, y: 0.6, z: 0.55 }, { x: 0, y: -0.34, z: 0 }, "base"),
  ], { x: 1.9, y: 0.75, z: 0.62 }],
  ["storage_box", [
    B({ x: 0.6, y: 0.36, z: 0.4 }, { x: 0, y: 0, z: 0 }, "body"),
    B({ x: 0.58, y: 0.05, z: 0.38 }, { x: 0, y: 0.21, z: 0 }, "lid"),
  ], { x: 0.6, y: 0.42, z: 0.4 }],
  ["window_flat", [
    B({ x: 1.4, y: 1.2, z: 0.03 }, { x: 0, y: 0, z: 0 }, "glass"),
    B({ x: 1.46, y: 0.06, z: 0.05 }, { x: 0, y: 0.62, z: 0 }, "frame"),
    B({ x: 1.46, y: 0.06, z: 0.05 }, { x: 0, y: -0.62, z: 0 }, "frame"),
    B({ x: 0.05, y: 1.2, z: 0.05 }, { x: 0, y: 0, z: 0 }, "frame"),
  ], { x: 1.4, y: 1.2, z: 0.06 }],
  ["wall_panel", [
    B({ x: 1, y: 1.9, z: 0.12 }, { x: 0, y: 0, z: 0 }, "panel"),
    B({ x: 1.06, y: 0.06, z: 0.14 }, { x: 0, y: 0.95, z: 0 }, "trim"),
    B({ x: 1.06, y: 0.06, z: 0.14 }, { x: 0, y: -0.95, z: 0 }, "trim"),
  ], { x: 1, y: 1.96, z: 0.12 }],
  ["door_slab", [
    B({ x: 0.9, y: 1.9, z: 0.08 }, { x: 0, y: 0, z: 0 }, "slab"),
    B({ x: 0.6, y: 1.3, z: 0.02 }, { x: 0, y: 0.1, z: 0.05 }, "slab"),
    B({ x: 0.02, y: 0.12, z: 0.03 }, { x: 0.4, y: 0, z: 0.06 }, "handle"),
  ], { x: 0.9, y: 1.9, z: 0.1 }],
];
/* eslint-enable max-len */

const TEMPLATES: ReadonlyMap<string, TemplateSpec> = new Map(TEMPLATE_ROWS.map(row).map((t) => [t.id, t] as const));

/**
 * The neutral fallback template: ONE non-interactable 0.4 m box. Used for
 * unknown/absent template ids so the scene never crashes and never ghosts.
 */
export const TEMPLATE_FALLBACK: TemplateSpec = {
  id: "fallback",
  parts: [{ primitiveKind: "box", scale: { x: 0.4, y: 0.4, z: 0.4 }, offset: { x: 0, y: 0, z: 0 }, colorKey: "body" }],
};

/** Exact template lookup; unknown/absent ids resolve to the neutral fallback. */
export function getTemplate(templateId: string): TemplateSpec {
  const template = TEMPLATES.get(templateId);
  return template !== undefined ? template : TEMPLATE_FALLBACK;
}

/** True only for ids in the frozen 59-template vocabulary (never the fallback). */
export function hasTemplate(templateId: string): boolean {
  return TEMPLATES.has(templateId);
}

/* ======================================================================
 * The generic factory.
 * ==================================================================== */

/** A resolved factory part — structurally a CompositePartDescriptor (no "flat"). */
export interface TemplateCompositePart {
  kind: "box" | "cylinder" | "sphere";
  size: Vec3;
  offset: Vec3;
  rotation?: Vec3;
  color: string;
}

/** Factory parameters: the (variant-merged) color map + the variant scale. */
export interface TemplateCompositeParams {
  colors: Readonly<Record<string, string>>;
  /** Variant scale multiplier; defensively clamped into [0.5, 2.0]. */
  scale: number;
}

/** The compiled result: child parts + optional hitbox + absolute bounds. */
export interface TemplateCompositeResult {
  parts: readonly TemplateCompositePart[];
  /** Template `hitbox` scaled by the variant scale, or null when undeclared. */
  hitbox: Vec3 | null;
  /** Absolute bounding box of the whole object (parts ∪ hitbox), meters. */
  bounds: Vec3;
}

/** Category-safe scale clamp (dual of variantParams.clampScale). */
const SCALE_MIN = 0.5;
const SCALE_MAX = 2.0;

function clampScale(value: number): number {
  if (!Number.isFinite(value)) return 1;
  return Math.max(SCALE_MIN, Math.min(SCALE_MAX, value));
}

function resolvePartHex(colors: Readonly<Record<string, string>>, colorKey: string): string {
  const direct = colors[colorKey];
  if (typeof direct === "string" && COLOR_PATTERN.test(direct)) return direct;
  return primaryHex(colors);
}

/**
 * Compute the absolute axis-aligned bounds: per axis the full span of every
 * part (|offset| + size/2) and the hitbox, doubled around the root.
 */
function computeBounds(parts: readonly TemplateCompositePart[], hitbox: Vec3 | null): Vec3 {
  const half = { x: 0.0, y: 0.0, z: 0.0 };
  const absorb = (size: Vec3, offset: Vec3): void => {
    half.x = Math.max(half.x, Math.abs(offset.x) + size.x / 2);
    half.y = Math.max(half.y, Math.abs(offset.y) + size.y / 2);
    half.z = Math.max(half.z, Math.abs(offset.z) + size.z / 2);
  };
  for (const part of parts) absorb(part.size, part.offset);
  if (hitbox !== null) absorb(hitbox, { x: 0, y: 0, z: 0 });
  // Guard against a degenerate empty result (never leaves a zero extent).
  return {
    x: Math.max(half.x * 2, 0.001),
    y: Math.max(half.y * 2, 0.001),
    z: Math.max(half.z * 2, 0.001),
  };
}

/**
 * THE single generic template→composite factory. Deterministic: identical
 * (templateId, params) always yields deep-equal { parts, hitbox, bounds }.
 * Unknown/absent template ids compile from {@link TEMPLATE_FALLBACK} (a
 * neutral non-interactable cube) — never a crash, never a ghost object.
 */
export function buildTemplateComposite(templateId: string, params: TemplateCompositeParams): TemplateCompositeResult {
  const spec = getTemplate(templateId);
  const s = clampScale(params.scale);
  const colors = params.colors;
  const parts: TemplateCompositePart[] = spec.parts.map((part) => {
    const kind =
      part.primitiveKind === "cylinder" ? "cylinder" : part.primitiveKind === "sphere" ? "sphere" : "box";
    const size = { x: part.scale.x * s, y: part.scale.y * s, z: part.scale.z * s };
    const offset = { x: part.offset.x * s, y: part.offset.y * s, z: part.offset.z * s };
    return {
      kind,
      size,
      offset,
      ...(part.rotation !== undefined ? { rotation: { x: part.rotation.x, y: part.rotation.y, z: part.rotation.z } } : {}),
      color: resolvePartHex(colors, part.colorKey),
    };
  });
  const hitbox = spec.hitbox !== undefined ? { x: spec.hitbox.scale.x * s, y: spec.hitbox.scale.y * s, z: spec.hitbox.scale.z * s } : null;
  return { parts, hitbox, bounds: computeBounds(parts, hitbox) };
}