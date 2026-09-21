import { describe, expect, it, vi } from "vitest";
import { NullEngine } from "@babylonjs/core/Engines";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import type { ArcRotateCamera } from "@babylonjs/core/Cameras/arcRotateCamera";
import type { Mesh } from "@babylonjs/core/Meshes/mesh";
import { Scene } from "@babylonjs/core/scene";
import { Ray } from "@babylonjs/core/Culling/ray";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import type { DiscoveryResultDTO, InteractionResultDTO } from "../api/types";
import { cameraProfileFor } from "../environments/kitGeometry";
import { FOCUS_BG_LIGHT_SCALE, FOCUS_TRANSITION_MS, focusTransitionTickCount } from "./focusCamera";
import type { InvestigationSceneModel } from "./buildInvestigationScene";
import { buildInvestigationScene } from "./buildInvestigationScene";
import { MIN_PICKABLE_EXTENT, needsPickHitbox, pickHitboxExtent, resolveAsset } from "./assetRegistry";
import { InvestigationSession, type InvestigationServices } from "./investigationFlow";
import {
  createInvestigationScene,
  meshNameFor,
  objectIdFromMeshName,
  objectIdFromPickedMesh,
  type RenderOptions,
} from "./renderInvestigation";
import {
  makeBootstrap,
  makeEmailRecord,
  makeIcePickDefinition,
  makeHotelSuiteBootstrap,
  makeOfficeBootstrap,
  makeProcWorldObject,
  makeTrophyDefinition,
  makeWorldObject,
  TEST_TOKEN,
} from "./testFixtures";
import type { GeneratedAssetDefinition } from "../api/types";

/**
 * Babylon glue tests. All engine work uses the deterministic NullEngine —
 * no GPU, no DOM, no network. Real Babylon init failures are simulated by an
 * injected engine factory that throws.
 */

const EMPTY_MODEL: InvestigationSceneModel = {
  location: { locationId: "miller_apartment_kitchen", name: "Miller Apartment - Kitchen" },
  environmentId: "apartment",
  worldObjects: [],
};

const NOOP_CANVAS = {} as HTMLCanvasElement;

function nullEngineOptions(
  overrides: {
    onPick?: (objectId: string) => void;
    onHoverStart?: (objectId: string, origin?: { x: number; y: number }) => void;
    onHoverEnd?: (objectId: string | null) => void;
  } = {},
): RenderOptions {
  return {
    createEngine: () => new NullEngine(),
    cameraControl: false,
    startRenderLoop: () => {},
    ...overrides,
  };
}

/** A picking-info shaped stand-in: only pickedMesh is ever read. */
function pickInfo(mesh: unknown): { pickedMesh: unknown } {
  return { pickedMesh: mesh };
}

/** IPointerEvent-shaped stand-in with the coordinates the hover handler reads. */
function pointerMove(x: number, y: number): { clientX: number; clientY: number } {
  return { clientX: x, clientY: y };
}

/** The pointer-event type argument the Babylon handlers require (unused by us). */
const POINTER_TYPES = 0 as never;

function knifeModel(): InvestigationSceneModel {
  return buildInvestigationScene(makeBootstrap());
}

describe("mesh naming: picking maps a mesh back to the object id", () => {
  it("encodes and decodes object ids deterministically", () => {
    expect(meshNameFor("kitchen_knife")).toBe("pd_obj_kitchen_knife");
    expect(objectIdFromMeshName(meshNameFor("kitchen_knife"))).toBe("kitchen_knife");
    expect(objectIdFromMeshName("pd_obj_apartment_laptop")).toBe("apartment_laptop");
  });

  it("never treats shell meshes or garbage as world objects", () => {
    expect(objectIdFromMeshName("floor_01")).toBeNull();
    expect(objectIdFromMeshName("pd_obj_")).toBeNull();
    expect(objectIdFromMeshName(null)).toBeNull();
    expect(objectIdFromMeshName(undefined)).toBeNull();
    expect(objectIdFromMeshName("")).toBeNull();
  });

  it("is identical for identical ids (object identity never from array index)", () => {
    expect(meshNameFor("kitchen_knife")).toBe(meshNameFor("kitchen_knife"));
    expect(objectIdFromMeshName(meshNameFor("a"))).not.toBe(objectIdFromMeshName(meshNameFor("b")));
  });
});

describe("createInvestigationScene", () => {
  it("returns ok:false with the message when the engine constructor throws", () => {
    const result = createInvestigationScene(NOOP_CANVAS, EMPTY_MODEL, {
      createEngine: () => {
        throw new Error("no gpu available");
      },
    });
    expect(result.ok).toBe(false);
    if (result.ok) throw new Error("expected failure");
    expect(result.error).toBe("no gpu available");
  });

  it("builds an empty scene model on the NullEngine headlessly", () => {
    const result = createInvestigationScene(NOOP_CANVAS, EMPTY_MODEL, nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    result.dispose();
  });

  it("instantiates one mesh per world object with registry geometry and colors", () => {
    const model = buildInvestigationScene(makeBootstrap());
    const result = createInvestigationScene(NOOP_CANVAS, model, nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    expect(result.scene.getNodeByName("pd_obj_kitchen_knife")).not.toBeNull();
    expect(result.scene.getNodeByName("pd_obj_apartment_laptop")).not.toBeNull();
    expect(result.scene.getNodeByName("pd_obj_vase_01")).not.toBeNull();
    expect(result.scene.getNodeByName("pd_obj_victim_body_placeholder")).not.toBeNull();
    // Shell meshes are named from the manifest, but never carry the object prefix.
    expect(result.scene.getNodeByName("pd_obj_floor_01")).toBeNull();

    // World objects are re-locatable through the deterministic id mapping.
    expect(objectIdFromMeshName(result.scene.getNodeByName("pd_obj_vase_01")!.name)).toBe("vase_01");
    result.dispose();
  });

  it("supports deterministic highlight toggling per object id", () => {
    const result = createInvestigationScene(
      NOOP_CANVAS,
      buildInvestigationScene(makeBootstrap()),
      nullEngineOptions(),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    expect(result.isObjectHighlighted("kitchen_knife")).toBe(false);
    result.setObjectHighlight("kitchen_knife", true);
    expect(result.isObjectHighlighted("kitchen_knife")).toBe(true);
    expect(result.isObjectHighlighted("vase_01")).toBe(false);
    result.setObjectHighlight("kitchen_knife", false);
    expect(result.isObjectHighlighted("kitchen_knife")).toBe(false);
    result.dispose();
  });

  it("dispose is idempotent-safe and never throws", () => {
    const result = createInvestigationScene(NOOP_CANVAS, EMPTY_MODEL, nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    result.dispose();
    result.dispose();
  });
});

describe("Phase 8 hover affordance rings", () => {
  it("creates hidden highlight rings only for interactable world objects", () => {
    const result = createInvestigationScene(
      NOOP_CANVAS,
      buildInvestigationScene(makeBootstrap()),
      nullEngineOptions(),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    // Interactable objects (catalog interactable:true) get a hidden ring.
    for (const objectId of ["kitchen_knife", "apartment_laptop", "letter_opener", "scissors"]) {
      const ring = result.scene.getNodeByName(`pd_ring_${objectId}`);
      expect(ring, `expected a ring for ${objectId}`).not.toBeNull();
      expect(ring!.isVisible).toBe(false);
    }

    // Phase 10 Track B: the v1 manifest declares table/door/lamp/vase/victim
    // non-interactable, so they get NO ring (like the victim always did).
    for (const objectId of ["vase_01", "apartment_table", "apartment_door", "apartment_lamp", "victim_body_placeholder"]) {
      expect(result.scene.getNodeByName(`pd_ring_${objectId}`), `no ring for ${objectId}`).toBeNull();
    }
    result.dispose();
  });
});

/* ======================================================================
 * Phase 8_1 — direct 3D interaction: composite picking, hitboxes, hover
 * callbacks and the mesh-click -> session dispatch chain.
 * ==================================================================== */

describe("objectIdFromPickedMesh — parent-walking picking (Phase 8_1 A1)", () => {
  it("resolves a COMPOSITE CHILD back to the objectId by walking the parent chain", () => {
    const child = {
      name: "pd_part_kitchen_knife_0",
      parent: { name: meshNameFor("kitchen_knife"), parent: null },
    };
    expect(objectIdFromPickedMesh(child)).toBe("kitchen_knife");
  });

  it("resolves the ROOT mesh directly (objectIdFromMeshName still works for roots)", () => {
    expect(objectIdFromPickedMesh({ name: meshNameFor("vase_01"), parent: null })).toBe("vase_01");
    expect(objectIdFromMeshName(meshNameFor("vase_01"))).toBe("vase_01");
  });

  it("walks arbitrarily deep parent chains before finding the root", () => {
    const grandchild = {
      name: "pd_sub_child",
      parent: { name: "pd_part_x", parent: { name: meshNameFor("apartment_laptop"), parent: null } },
    };
    expect(objectIdFromPickedMesh(grandchild)).toBe("apartment_laptop");
  });

  it("returns null for non-object meshes and missing roots", () => {
    expect(objectIdFromPickedMesh({ name: "floor_01", parent: null })).toBeNull();
    expect(objectIdFromPickedMesh({ name: "pd_ring_vase_01", parent: null })).toBeNull();
    expect(objectIdFromPickedMesh({ name: "pd_part_orphan", parent: null })).toBeNull();
    expect(objectIdFromPickedMesh(null)).toBeNull();
    expect(objectIdFromPickedMesh(undefined)).toBeNull();
  });
});

describe("composite roots + invisible pick hitboxes (Phase 8_1 A1/A2)", () => {
  it("keeps the EXACT pd_obj_<objectId> root names for composites (stability)", () => {
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    for (const objectId of ["kitchen_knife", "apartment_laptop", "apartment_table", "victim_body_placeholder"]) {
      const root = result.scene.getNodeByName(meshNameFor(objectId)) as Mesh | null;
      expect(root, `expected root for ${objectId}`).not.toBeNull();
      expect(root!.name, `root name for ${objectId}`).toBe(meshNameFor(objectId));
    }
    // Child parts are named OUTSIDE the object prefix so parent-walking never
    // mistakes a part for a root.
    expect(result.scene.getNodeByName("pd_part_kitchen_knife_0")).not.toBeNull();
    expect(result.scene.getNodeByName("pd_part_kitchen_knife_1")).not.toBeNull();
    result.dispose();
  });

  it("gives the small knife an invisible hitbox at the safe minimum pickable extent", () => {
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    const hit = result.scene.getNodeByName("pd_hit_kitchen_knife") as Mesh | null;
    expect(hit, "knife needs an invisible hitbox (0.04m min dimension < 0.4m)").not.toBeNull();
    expect(hit!.material instanceof StandardMaterial).toBe(true);
    if (hit!.material instanceof StandardMaterial) {
      expect(hit!.material.alpha, "hitbox material must be fully transparent").toBe(0);
    }
    expect(hit!.isPickable, "hitbox must remain pickable").toBe(true);
    // Pure policy: the hitbox extent auto-compensates to the minimum.
    expect(pickHitboxExtent({ scale: resolveAsset("PROP_KITCHEN_KNIFE_01").scale }).x).toBe(
      MIN_PICKABLE_EXTENT,
    );
    expect(needsPickHitbox(resolveAsset("PROP_KITCHEN_KNIFE_01"))).toBe(true);
    result.dispose();
  });

  it("leaves large objects (table) without an unnecessary hitbox", () => {
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    expect(result.scene.getNodeByName("pd_hit_apartment_table")).toBeNull();
    expect(needsPickHitbox(resolveAsset("PROP_TABLE_01"))).toBe(false);
    result.dispose();
  });
});

describe("mesh click dispatch (Phase 8_1 A1/A4 + E)", () => {
  it("clicking a composite CHILD calls onPick with the backend objectId", () => {
    const picked: string[] = [];
    const result = createInvestigationScene(
      NOOP_CANVAS,
      knifeModel(),
      nullEngineOptions({ onPick: (id) => picked.push(id) }),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    const blade = result.scene.getNodeByName("pd_part_kitchen_knife_0");
    result.scene.onPointerDown?.(pointerMove(0, 0) as never, pickInfo(blade) as never, POINTER_TYPES);
    expect(picked).toEqual(["kitchen_knife"]);

    result.dispose();
  });

  it("clicking a composite ROOT also resolves through the id mapping", () => {
    const picked: string[] = [];
    const result = createInvestigationScene(
      NOOP_CANVAS,
      knifeModel(),
      nullEngineOptions({ onPick: (id) => picked.push(id) }),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    const root = result.scene.getNodeByName("pd_obj_kitchen_knife");
    result.scene.onPointerDown?.(pointerMove(0, 0) as never, pickInfo(root) as never, POINTER_TYPES);
    expect(picked).toEqual(["kitchen_knife"]);
    result.dispose();
  });

  it("a NON-interactable click (victim) does NOT call onPick", () => {
    const onPick = vi.fn();
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions({ onPick }));
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    const victimRoot = result.scene.getNodeByName("pd_obj_victim_body_placeholder");
    result.scene.onPointerDown?.(pointerMove(0, 0) as never, pickInfo(victimRoot) as never, POINTER_TYPES);
    expect(onPick).not.toHaveBeenCalled();
    result.dispose();
  });

  it("clicking a non-object mesh (shell/floor) does NOT call onPick", () => {
    const onPick = vi.fn();
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions({ onPick }));
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    result.scene.onPointerDown?.(
      pointerMove(0, 0) as never,
      pickInfo({ name: "floor_01", parent: null }) as never,
      POINTER_TYPES,
    );
    expect(onPick).not.toHaveBeenCalled();
    result.dispose();
  });

  it("the mesh-click path dispatches the SAME server interaction as the list path", async () => {
    const picked: string[] = [];
    const result = createInvestigationScene(
      NOOP_CANVAS,
      knifeModel(),
      nullEngineOptions({ onPick: (id) => picked.push(id) }),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    // 1) Simulate a direct mesh click on a knife child part.
    const blade = result.scene.getNodeByName("pd_part_kitchen_knife_0");
    result.scene.onPointerDown?.(pointerMove(0, 0) as never, pickInfo(blade) as never, POINTER_TYPES);
    expect(picked).toEqual(["kitchen_knife"]);
    result.dispose();

    // 2) The resolved objectId drives the SAME InvestigationSession.interact
    //    call the DOM list button would make (identical server contract).
    const services = makeTestServices();
    const session = new InvestigationSession(services, TEST_TOKEN, null, { playthroughId: "PT-test-0001" });
    await session.start(null);
    const feedback = await session.interact(picked[0]);

    expect(services.interactObject).toHaveBeenCalledWith(
      "PT-test-0001",
      "kitchen_knife",
      "inspect",
      TEST_TOKEN,
    );
    expect(feedback.error).toBeNull();
    expect(feedback.toast?.text).toBe("Nothing relevant was found on the Kitchen knife.");
  });
});

/* ======================================================================
 * Phase 15 Track B — judging polish: persistent selection focus, evidence
 * legibility scaling and the projected caption hook (NullEngine).
 * ==================================================================== */

describe("Phase 15 — persistent selected-object focus", () => {
  it("setObjectSelected / getSelectedObjectId track the singleton selection", () => {
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    expect(result.getSelectedObjectId()).toBeNull();
    result.setObjectSelected("kitchen_knife");
    expect(result.getSelectedObjectId()).toBe("kitchen_knife");
    // Move selection to another interactable, then clear.
    result.setObjectSelected("apartment_laptop");
    expect(result.getSelectedObjectId()).toBe("apartment_laptop");
    result.setObjectSelected(null);
    expect(result.getSelectedObjectId()).toBeNull();
    result.dispose();
  });

  it("the selection keeps the object's ring visible after hover ends", () => {
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    result.setObjectSelected("kitchen_knife");
    const ring = result.scene.getNodeByName("pd_ring_kitchen_knife");
    expect(ring).not.toBeNull();
    expect(ring!.isVisible, "selection ring visible immediately").toBe(true);
    result.dispose();
  });

  it("selection never touches non-interactable objects (no ring)", () => {
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    result.setObjectSelected("vase_01");
    expect(result.getSelectedObjectId()).toBe("vase_01");
    expect(result.scene.getNodeByName("pd_ring_vase_01")).toBeNull();
    result.dispose();
  });
});

describe("Phase 15 — evidence legibility scaling (renderer)", () => {
  it("apartment golden objects render at scaling 1 (byte-identical)", () => {
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    for (const objectId of ["kitchen_knife", "apartment_laptop", "apartment_table"]) {
      const root = result.scene.getNodeByName(meshNameFor(objectId)) as Mesh | null;
      expect(root, objectId).not.toBeNull();
      expect({ x: root!.scaling.x, y: root!.scaling.y, z: root!.scaling.z }).toEqual({ x: 1, y: 1, z: 1 });
    }
    result.dispose();
  });

  it("office evidence roots carry the deterministic legibility scaling", () => {
    const model = buildInvestigationScene(makeOfficeBootstrap());
    const knife = model.worldObjects.find((o) => o.objectId === "office_desk_knife");
    expect(knife!.renderScale).toBeGreaterThan(1);
    const result = createInvestigationScene(NOOP_CANVAS, model, nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const root = result.scene.getNodeByName("pd_obj_office_desk_knife") as Mesh | null;
    expect(root).not.toBeNull();
    expect(root!.scaling.x).toBe(knife!.renderScale);
    expect(root!.scaling.y).toBe(knife!.renderScale);
    expect(root!.scaling.z).toBe(knife!.renderScale);
    result.dispose();
  });
});

describe("Phase 15 — projected caption points (fractions of the viewport)", () => {
  it("returns null for unknown / non-existent objects", () => {
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    expect(result.projectObjectPoint("no_such_object", { x: 0, y: 0, z: 0 })).toBeNull();
    result.dispose();
  });

  it("projects a known object's top into finite 0..1 fractions (or null in a headless view)", () => {
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const point = result.projectObjectPoint("kitchen_knife", { x: 0, y: 0.2, z: 0 });
    if (point === null) {
      // A headless scene may legitimately fail to produce a view matrix —
      // the caption then simply stays hidden (safe degrade, never a crash).
      return;
    }
    expect(Number.isFinite(point.x)).toBe(true);
    expect(Number.isFinite(point.y)).toBe(true);
    expect(point.x).toBeGreaterThanOrEqual(0);
    expect(point.x).toBeLessThanOrEqual(1);
    expect(point.y).toBeGreaterThanOrEqual(0);
    expect(point.y).toBeLessThanOrEqual(1);
    result.dispose();
  });
});

describe("hover callbacks + tooltip data flow (Phase 8_1 A3/B1 + E)", () => {
  it("hover only fires for interactables and carries the pointer origin", () => {
    const onHoverStart = vi.fn();
    const onHoverEnd = vi.fn();
    const result = createInvestigationScene(
      NOOP_CANVAS,
      knifeModel(),
      nullEngineOptions({ onHoverStart, onHoverEnd }),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    // Interactable knife part -> onHoverStart('kitchen_knife', {x,y}).
    const knifeChild = result.scene.getNodeByName("pd_part_kitchen_knife_0");
    result.scene.onPointerMove?.(pointerMove(50, 60) as never, pickInfo(knifeChild) as never, POINTER_TYPES);
    expect(onHoverStart).toHaveBeenCalledTimes(1);
    expect(onHoverStart).toHaveBeenCalledWith("kitchen_knife", { x: 50, y: 60 });
    expect(onHoverEnd).not.toHaveBeenCalled();

    // Moving away clears the hover.
    result.scene.onPointerMove?.(
      pointerMove(10, 10) as never,
      pickInfo({ name: "floor_01", parent: null }) as never,
      POINTER_TYPES,
    );
    expect(onHoverEnd).toHaveBeenCalledTimes(1);
    expect(onHoverEnd).toHaveBeenCalledWith("kitchen_knife");

    // Firing the same object twice does not re-fire onHoverStart.
    result.scene.onPointerMove?.(pointerMove(50, 60) as never, pickInfo(knifeChild) as never, POINTER_TYPES);
    result.scene.onPointerMove?.(pointerMove(51, 61) as never, pickInfo(knifeChild) as never, POINTER_TYPES);
    expect(onHoverStart).toHaveBeenCalledTimes(2);
    result.dispose();
  });

  it("hovering the NON-interactable victim never fires onHoverStart", () => {
    const onHoverStart = vi.fn();
    const onHoverEnd = vi.fn();
    const result = createInvestigationScene(
      NOOP_CANVAS,
      knifeModel(),
      nullEngineOptions({ onHoverStart, onHoverEnd }),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    const victimChild = result.scene.getNodeByName("pd_part_victim_body_placeholder_0");
    result.scene.onPointerMove?.(
      pointerMove(20, 20) as never,
      pickInfo(victimChild) as never,
      POINTER_TYPES,
    );
    expect(onHoverStart).not.toHaveBeenCalled();
    expect(onHoverEnd).not.toHaveBeenCalled();
    result.dispose();
  });
});

describe("DEF-056 — the scene glue module pulls in the real Ray picking (production-build guard)", () => {
  it("importing renderInvestigation installs the REAL Scene.prototype.pick, not the stub", () => {
    // renderInvestigation.ts starts with `import "@babylonjs/core/Culling/ray"`,
    // whose evaluation monkey-patches Scene.prototype.pick. If that side-effect
    // import is ever removed (or tree-shaken away), the production bundle ships
    // the warn-and-return-dummy stub and direct 3D interaction dies in the real
    // browser — this test would fail the moment the stub resurfaces.
    const pick = Scene.prototype.pick;
    const source = Function.prototype.toString.call(pick);
    expect(source).toContain("Pick(this");
    expect(source).not.toContain("_WarnImport");
  });

  it("the REAL ray path picks a composite part and walks it back to the objectId", () => {
    // DEF-056 (still open after the ray import): Babylon 8.56 does not populate
    // pick-ready world matrices/bounding infos for meshes parented after
    // creation, so a REAL scene.pick could never hit a pd_obj_* child even at
    // the exact pixel. This runs the TRUE picking math (ray vs mesh
    // bounding/triangle code — no fake pickInfo) on a NullEngine scene:
    // after the glue's own refresh pass, a world-space ray aimed at the knife
    // blade must resolve back to "kitchen_knife".
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const scene = result.scene;

    const blade = scene.getNodeByName("pd_part_kitchen_knife_0") as Mesh | null;
    expect(blade).not.toBeNull();
    const wm = blade!.getWorldMatrix();
    const aim = new Vector3(wm.m[12], wm.m[13], wm.m[14]);
    // Short ray from SOUTH of the knife aiming north through the blade/hitbox.
    // Any ray through the whole room would cross the apartment walls first
    // (closest-hit wins), so we keep it short and pointed at the knife.
    const origin = new Vector3(aim.x, aim.y, aim.z - 3);
    const direction = new Vector3(0, 0, 1);
    const ray = new Ray(origin, direction, 6);

    const pickResult = scene.pickWithRay(ray, undefined, false);
    expect(pickResult?.pickedMesh, "real ray must hit the knife region").not.toBeNull();
    const objectId = objectIdFromPickedMesh(pickResult?.pickedMesh);
    expect(objectId).toBe("kitchen_knife");
    result.dispose();
  });

  it("interactable parts+hitboxes are admitted by the move-pick predicate; the victim is not", () => {
    // DEF-056 (hover path): the InputManager's default pointerMovePredicate
    // only admits meshes with enablePointerMoveEvents (or an action manager).
    // Without this, the engine's own move-picking always returned an empty
    // pickInfo and the hover tooltip could never appear.
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const scene = result.scene;

    for (const name of ["pd_part_kitchen_knife_0", "pd_hit_kitchen_knife", "pd_part_apartment_laptop_0"]) {
      const m = scene.getNodeByName(name) as Mesh | null;
      expect(m, `${name} exists`).not.toBeNull();
      expect(m!.enablePointerMoveEvents, `${name} admitted on hover`).toBe(true);
    }
    // Non-interactable composites never get hover admission.
    const victimPart = scene.getNodeByName("pd_part_victim_body_placeholder_0") as Mesh | null;
    expect(victimPart).not.toBeNull();
    expect(victimPart!.enablePointerMoveEvents).toBe(false);
    result.dispose();
  });

  it("our pointer predicates admit ONLY world-object meshes (walls never shadow clicks)", () => {
    // DEF-056 (click path): the apartment shell (walls/floors...) is the
    // CLOSEST mesh along many pick rays, so Babylon's default down predicate
    // resolved clicks to a wall even when the user pointed at the knife.
    // We install our own move/down predicates admitting ONLY pd_* meshes.
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const scene = result.scene;

    expect(scene.pointerMovePredicate, "move predicate installed by the glue").not.toBeNull();
    expect(scene.pointerDownPredicate, "down predicate installed by the glue").not.toBeNull();

    const admit = (name: string): boolean => {
      const mesh = scene.getNodeByName(name) as Mesh | null;
      expect(mesh, `${name} exists`).not.toBeNull();
      const move = scene.pointerMovePredicate!(mesh!);
      const down = scene.pointerDownPredicate!(mesh!);
      expect(move, `${name} move admission`).toBe(true);
      expect(down, `${name} down admission`).toBe(true);
      return true;
    };
    // Interactable world-object meshes are admitted...
    for (const name of ["pd_obj_kitchen_knife", "pd_part_kitchen_knife_0", "pd_hit_kitchen_knife", "pd_part_apartment_table_0"]) {
      admit(name);
    }
    // ...and the apartment shell + rings never shadow them.
    for (const name of ["wall_back", "floor_01", "pd_ring_kitchen_knife", "table_01", "art_01"]) {
      const mesh = scene.getNodeByName(name) as Mesh | null;
      expect(mesh, `${name} exists`).not.toBeNull();
      expect(scene.pointerMovePredicate!(mesh!), `${name} must not be a move candidate`).toBe(false);
      expect(scene.pointerDownPredicate!(mesh!), `${name} must not be a down candidate`).toBe(false);
    }
    result.dispose();
  });
});

function makeTestServices(): InvestigationServices {
  return {
    getInvestigation: vi.fn(async () => makeBootstrap()),
    interactObject: vi.fn(
      async (): Promise<InteractionResultDTO> => ({
        objectId: "kitchen_knife",
        interaction: "inspect",
        evidenceId: null,
        discovery: null,
        result: "interacted",
      }),
    ),
    discoverEvidence: vi.fn(
      async (): Promise<DiscoveryResultDTO> => ({
        evidenceId: "x",
        kind: "object",
        title: "x",
        interaction: "inspect",
        state: "discovered",
      }),
    ),
    readRecord: vi.fn(async () => makeEmailRecord()),
  };
}

/* ======================================================================
 * Phase 11 Track B — environment kit shell rendering (NullEngine)
 * ==================================================================== */

/** A warehouse-flavored model: same golden objects, warehouse kit identity. */
function warehouseModel(): InvestigationSceneModel {
  const bootstrap = makeOfficeBootstrap();
  bootstrap.scene.environmentId = "warehouse";
  bootstrap.scene.location = { locationId: "warehouse_floor_main", name: "Warehouse - Main Floor" };
  return buildInvestigationScene(bootstrap);
}

interface LightProbe {
  intensity?: number;
}

describe("Phase 11 Track B — kit shell, camera and lighting rendering", () => {
  it("renders the kit SHELL meshes for a non-apartment model (office room)", () => {
    const result = createInvestigationScene(
      NOOP_CANVAS,
      buildInvestigationScene(makeOfficeBootstrap()),
      nullEngineOptions(),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    // kitGeometry shell primitives (never pd_*-prefixed world objects).
    for (const name of ["floor_01", "wall_north", "wall_south", "wall_east", "wall_west", "door_1", "window_1", "window_2", "lamp_01"]) {
      expect(result.scene.getNodeByName(name), `${name} shell mesh`).not.toBeNull();
    }
    expect(result.scene.getNodeByName("pd_obj_floor_01")).toBeNull();
    result.dispose();
  });

  it("keeps the EXACT golden apartment shell for apartment models (byte-identical)", () => {
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    // Apartment manifest primitives (Phase 2), not the kit shell. The
    // apartment's "light" primitive renders a PointLight named light_01_point.
    for (const name of ["floor_01", "wall_back", "wall_left", "wall_right", "table_01", "light_01_point", "rug_01"]) {
      expect(result.scene.getNodeByName(name), `${name} golden shell mesh`).not.toBeNull();
    }
    // Kit-shell-only primitives must NOT exist in the apartment scene.
    expect(result.scene.getNodeByName("door_1")).toBeNull();
    expect(result.scene.getNodeByName("window_1")).toBeNull();
    result.dispose();
  });

  it("an unknown environment renders the apartment shell (fallback) without errors", () => {
    const mars = buildInvestigationScene(makeBootstrap());
    mars.environmentId = "planet_mars";
    const result = createInvestigationScene(NOOP_CANVAS, mars, nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    expect(result.scene.getNodeByName("table_01")).not.toBeNull();
    result.dispose();
  });

  it("targets the kit camera at the manifest spawn with the derived orbit distance", () => {
    const result = createInvestigationScene(NOOP_CANVAS, knifeModelOffice(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const camera = result.scene.getNodeByName("investigation_camera") as ArcRotateCamera | null;
    expect(camera).not.toBeNull();
    const profile = cameraProfileFor("office");
    expect(profile).not.toBeNull();
    expect(camera!.radius).toBe(profile!.distance);
    expect({ x: camera!.target.x, y: camera!.target.y, z: camera!.target.z }).toEqual({
      x: 0.8,
      y: 1.0,
      z: 2.0,
    });
    result.dispose();
  });

  it("applies the kit lighting profile (key/hemi intensities) for non-apartment kits", () => {
    const result = createInvestigationScene(NOOP_CANVAS, warehouseModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const key = result.scene.getNodeByName("investigation_key") as LightProbe | null;
    const hemi = result.scene.getNodeByName("investigation_hemi") as LightProbe | null;
    expect(key, "key light").not.toBeNull();
    expect(hemi, "hemi light").not.toBeNull();
    // warehouse manifest lighting: cool_dim, key 0.55 / hemi 0.25.
    expect(key!.intensity).toBe(0.55);
    expect(hemi!.intensity).toBe(0.25);
    result.dispose();
  });

  it("the apartment kit keeps its golden lighting values (never overridden)", () => {
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const key = result.scene.getNodeByName("investigation_key") as LightProbe | null;
    const hemi = result.scene.getNodeByName("investigation_hemi") as LightProbe | null;
    expect(key!.intensity).toBe(0.85);
    expect(hemi!.intensity).toBe(0.5);
    result.dispose();
  });
});

/* ======================================================================
 * Phase 12 Track B — template-backed composites render via the generic
 * factory (NullEngine): ROOT `pd_obj_<objectId>` + `pd_part_<objectId>_N`
 * children, catalog colors only.
 * ==================================================================== */

describe("Phase 12 — template-backed composite rendering (generic factory)", () => {
  /** A one-object scene: the PROP_HAMMER_01 (tool_hammer) evidence tool. */
  function hammerModel(): InvestigationSceneModel {
    const bootstrap = makeBootstrap();
    bootstrap.scene.worldObjects = [
      makeWorldObject({
        objectId: "hammer",
        assetId: "PROP_HAMMER_01",
        assetType: "tool",
        subtype: "tool",
        locationId: "miller_apartment_kitchen",
        anchor: "kitchen_counter",
        interaction: "",
        evidenceId: null,
      }),
    ];
    return buildInvestigationScene(bootstrap);
  }

  it("renders the ROOT pd_obj_hammer plus pd_part_hammer_* children on the NullEngine", () => {
    const model = hammerModel();
    const hammer = model.worldObjects[0];
    expect(hammer.templateParts).not.toBeNull();

    const result = createInvestigationScene(NOOP_CANVAS, model, nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    expect(result.scene.getNodeByName("pd_obj_hammer")).not.toBeNull();
    for (let index = 0; index < hammer.templateParts!.length; index++) {
      const part = result.scene.getNodeByName(`pd_part_hammer_${index}`) as Mesh | null;
      expect(part, `pd_part_hammer_${index}`).not.toBeNull();
      expect(part!.material instanceof StandardMaterial).toBe(true);
    }
    result.dispose();
  });

  it("template parts use ONLY catalog-derived colors (hex #RRGGBB, no server strings)", () => {
    const model = hammerModel();
    const parts = model.worldObjects[0].templateParts!;
    expect(parts.length).toBeGreaterThan(0);
    for (const part of parts) {
      expect(part.color).toMatch(/^#[0-9a-fA-F]{6}$/);
    }
  });

  it("the small template composite still gets the safe MIN_PICKABLE_EXTENT hitbox", () => {
    const model = hammerModel();
    const hammer = model.worldObjects[0];
    // hammer template hitbox min dimension < 0.4 -> the invisible hitbox policy
    // applies to template-backed composites exactly like legacy evidence.
    const result = createInvestigationScene(NOOP_CANVAS, model, nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const hit = result.scene.getNodeByName("pd_hit_hammer") as Mesh | null;
    expect(hit).not.toBeNull();
    result.dispose();
    expect(needsPickHitbox({ scale: hammer.templateHitbox! })).toBe(true);
  });
});

function knifeModelOffice(): InvestigationSceneModel {
  return buildInvestigationScene(makeOfficeBootstrap());
}

/* ======================================================================
 * Phase 13 Track B — declarative generated assets render on the NullEngine
 * as ordinary pd_obj_<id> roots with pd_part_<id>_N children. Picking,
 * hover and hitbox policies are byte-identical to catalog/template objects.
 * ==================================================================== */

/** Golden nine + one proc.* trophy with a valid generated definition. */
function trophyModel(): InvestigationSceneModel {
  const bootstrap = makeBootstrap();
  bootstrap.scene.worldObjects = [...bootstrap.scene.worldObjects, makeProcWorldObject()];
  return buildInvestigationScene(bootstrap);
}

describe("Phase 13 — generated definition rendering (NullEngine)", () => {
  it("renders the ROOT pd_obj_custom_trophy plus pd_part_* children with finite transforms", () => {
    const result = createInvestigationScene(NOOP_CANVAS, trophyModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    const root = result.scene.getNodeByName("pd_obj_custom_trophy") as Mesh | null;
    expect(root, "root mesh").not.toBeNull();
    expect(root!.position.x).toBe(3.4); // office_desk_01 anchor — placement via anchor registry
    expect(Number.isFinite(root!.position.y)).toBe(true);
    expect(Number.isFinite(root!.position.z)).toBe(true);
    for (let index = 0; index < 3; index++) {
      const part = result.scene.getNodeByName(`pd_part_custom_trophy_${index}`) as Mesh | null;
      expect(part, `pd_part_custom_trophy_${index}`).not.toBeNull();
      expect(part!.material instanceof StandardMaterial).toBe(true);
      expect(Number.isFinite(part!.position.x)).toBe(true);
      expect(Number.isFinite(part!.position.y)).toBe(true);
      expect(Number.isFinite(part!.position.z)).toBe(true);
    }
    result.dispose();
  });

  it("generated parts resolve through the parent chain and get a hidden/real pick catch", () => {
    const result = createInvestigationScene(NOOP_CANVAS, trophyModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const scene = result.scene;

    // A generated child resolves back to the backend objectId like any other part.
    const child = scene.getNodeByName("pd_part_custom_trophy_1");
    expect(objectIdFromPickedMesh(child)).toBe("custom_trophy");
    expect(objectIdFromMeshName("pd_obj_custom_trophy")).toBe("custom_trophy");

    // The small trophy (min extent 0.3 < 0.4) gets the invisible pick hitbox —
    // the MIN_PICKABLE_EXTENT policy applies to generated assets identically.
    const hit = scene.getNodeByName("pd_hit_custom_trophy") as Mesh | null;
    expect(hit, "generated object needs the safe minimum pickable hitbox").not.toBeNull();
    result.dispose();
  });

  it("prod-generated meshes are the ONLY picking predicate candidates (walls never shadow)", () => {
    const result = createInvestigationScene(NOOP_CANVAS, trophyModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const scene = result.scene;

    for (const name of ["pd_obj_custom_trophy", "pd_part_custom_trophy_0", "pd_hit_custom_trophy"]) {
      const mesh = scene.getNodeByName(name) as Mesh | null;
      expect(mesh, `${name} exists`).not.toBeNull();
      expect(scene.pointerMovePredicate!(mesh!), `${name} move admission`).toBe(true);
      expect(scene.pointerDownPredicate!(mesh!), `${name} down admission`).toBe(true);
    }
    // Shell/wall meshes still never admit picking.
    const wall = scene.getNodeByName("wall_back") as Mesh | null;
    expect(wall).not.toBeNull();
    expect(scene.pointerMovePredicate!(wall!)).toBe(false);
    expect(scene.pointerDownPredicate!(wall!)).toBe(false);
    result.dispose();
  });

  it("interaction remains payload-driven: a silent generated object has no ring and no onPick", () => {
    const onPick = vi.fn();
    const result = createInvestigationScene(
      NOOP_CANVAS,
      trophyModel(),
      nullEngineOptions({ onPick }),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const scene = result.scene;

    // makeProcWorldObject publishes interaction:"" -> no affordance -> no ring.
    expect(scene.getNodeByName("pd_ring_custom_trophy")).toBeNull();
    const root = scene.getNodeByName("pd_obj_custom_trophy");
    scene.onPointerDown?.(pointerMove(0, 0) as never, pickInfo(root) as never, POINTER_TYPES);
    expect(onPick).not.toHaveBeenCalled();
    result.dispose();
  });

  it("an INVALID generated object renders as the neutral fallback while neighbors still render", () => {
    const bootstrap = makeBootstrap();
    const broken = makeProcWorldObject();
    const tampered: GeneratedAssetDefinition = makeTrophyDefinition();
    tampered.parts[0].primitive = "capsule" as never;
    broken.generated = tampered;
    bootstrap.scene.worldObjects = [...bootstrap.scene.worldObjects, broken];
    const result = createInvestigationScene(NOOP_CANVAS, buildInvestigationScene(bootstrap), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const scene = result.scene;

    // The invalid trophy STILL has its identity root, but renders as the
    // neutral single-part primitive (no generated child parts 0..2 in order)
    // while the golden neighbors are untouched.
    expect(scene.getNodeByName("pd_obj_custom_trophy")).not.toBeNull();
    expect(scene.getNodeByName("pd_part_custom_trophy_1")).toBeNull();
    expect(scene.getNodeByName("pd_part_custom_trophy_2")).toBeNull();
    const fallbackPart = scene.getNodeByName("pd_part_custom_trophy_0") as Mesh | null;
    expect(fallbackPart).not.toBeNull();
    expect(objectIdFromPickedMesh(fallbackPart)).toBe("custom_trophy");
    // Neighbors still render (knife root + children intact).
    expect(scene.getNodeByName("pd_obj_kitchen_knife")).not.toBeNull();
    expect(scene.getNodeByName("pd_part_kitchen_knife_0")).not.toBeNull();
    result.dispose();
  });
});

/* ======================================================================
 * Phase 18B — forensic focus mode (NullEngine).
 *
 * Covers requirements A/C/E/F: selecting evidence opens focus; the SAME
 * ArcRotateCamera saves + restores state exactly; transition + safe radius
 * limits + reduced-motion jump; background de-emphasis + object dominance
 * with full cleanup; slow-orbit stop on interaction; no re-discovery; direct
 * picking and reload stay byte-identical; repeated open/close cycles are
 * deterministic and memory-stable; the hard-case ice pick is visible,
 * pickable, distinguishable and proc.*-leak-free.
 * ==================================================================== */

function cameraOf(result: { scene: Scene }): ArcRotateCamera {
  const camera = result.scene.getNodeByName("investigation_camera") as ArcRotateCamera | null;
  expect(camera, "investigation_camera exists").not.toBeNull();
  return camera!;
}

/** grayscale vector of a live camera state (equality-safe snapshot reader). */
function cameraVec(state: { alpha: number; beta: number; radius: number; target: { x: number; y: number; z: number } }) {
  return {
    alpha: state.alpha,
    beta: state.beta,
    radius: state.radius,
    tx: state.target.x,
    ty: state.target.y,
    tz: state.target.z,
  };
}

/** Office fixture + the bronze ceremonial ice pick (Phase 18B hard case). */
function icePickOfficeModel(): InvestigationSceneModel {
  const bootstrap = makeOfficeBootstrap();
  bootstrap.scene.worldObjects.push(
    makeWorldObject({
      objectId: "bronze_ceremonial_ice_pick",
      assetId: "proc.decor.4551660f4a46b2eb",
      assetType: "sharp_weapon",
      subtype: "sharp_weapon",
      locationId: "office_mainroom",
      anchor: "office_desk_a",
      interaction: "inspect",
      evidenceId: "forensic_ice_pick_01",
      generated: makeIcePickDefinition(),
    }),
  );
  return buildInvestigationScene(bootstrap);
}

describe("Phase 18B — forensic focus mode (camera + state machine)", () => {
  it("selecting an evidence object opens focus and focuses THE clicked object", () => {
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    expect(result.isObjectFocused()).toBe(false);
    expect(result.getFocusedObjectId()).toBeNull();
    result.setObjectFocus("kitchen_knife");
    expect(result.isObjectFocused()).toBe(true);
    expect(result.getFocusedObjectId()).toBe("kitchen_knife");
    // Idempotent: re-selecting the same object is a no-op (no re-entry).
    const snapshot = result.getFocusSnapshot();
    const before = result.getFocusSnapshot()!.saved;
    result.setObjectFocus("kitchen_knife");
    expect(result.getFocusedObjectId()).toBe("kitchen_knife");
    expect(result.getFocusSnapshot()!.saved).toEqual(before);
    expect(snapshot).not.toBeNull();
    result.dispose();
  });

  it("a focus request for a NON-interactable object is refused (no session)", () => {
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    result.setObjectFocus("vase_01"); // non-interactable -> no focus session
    expect(result.isObjectFocused()).toBe(false);
    result.dispose();
  });

  it("transition animates the SAME camera to the focus framing with safe radius limits", () => {
    const model = icePickOfficeModel();
    const pick = model.worldObjects.find((o) => o.objectId === "bronze_ceremonial_ice_pick")!;
    const result = createInvestigationScene(NOOP_CANVAS, model, nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const camera = cameraOf(result);
    const initialRadius = camera.radius;

    result.setObjectFocus("bronze_ceremonial_ice_pick");
    const snapshot = result.getFocusSnapshot()!;
    // Framing radius scaled DOWN from the object's visible extent, but floored
    // at the safe minimum (never a clipping close-up).
    expect(snapshot.framing.radius).toBeGreaterThan(0);
    expect(snapshot.framing.radius).toBeLessThan(initialRadius);
    expect(snapshot.framing.radius).toBeGreaterThanOrEqual(1.5);

    // Frame 0 = saved world state; after the full transition the camera state
    // is EXACTLY the computed framing (same camera instance throughout).
    expect(cameraVec(result.getFocusSnapshot()!.live)).toEqual(cameraVec(snapshot.saved));
    for (let step = 0; step < focusTransitionTickCount(18); step += 1) result.tickFocus(18);
    expect(result.getFocusSnapshot()!.transitionDone).toBe(true);
    expect(cameraVec(result.getFocusSnapshot()!.live)).toEqual(cameraVec(snapshot.framing));
    // Target centered on the object (its model position), not the room center.
    expect(Math.abs(camera.target.x - pick.position.x)).toBeLessThan(1);
    expect(Math.abs(camera.target.z - pick.position.z)).toBeLessThan(1);
    result.dispose();
  });

  it("the saved world camera state is restored EXACTLY on close (alpha/beta/radius/target)", () => {
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const camera = cameraOf(result);

    result.setObjectFocus("kitchen_knife");
    const saved = cameraVec(result.getFocusSnapshot()!.saved);
    for (let step = 0; step < focusTransitionTickCount(18); step += 1) result.tickFocus(18);
    // The player is free to orbit during inspection before closing.
    camera.alpha += 2.3;
    camera.beta += 0.4;

    result.setObjectFocus(null);
    expect(result.isObjectFocused()).toBe(false);
    expect(cameraVec({ alpha: camera.alpha, beta: camera.beta, radius: camera.radius, target: camera.target })).toEqual(saved);
    result.dispose();
  });

  it("close restores lights + emissive + ring byte-exactly (background de-emphasis cleaned)", () => {
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const key = result.scene.getNodeByName("investigation_key") as LightProbe | null;
    const hemi = result.scene.getNodeByName("investigation_hemi") as LightProbe | null;
    const knifePart = result.scene.getNodeByName("pd_part_kitchen_knife_0") as Mesh | null;
    expect(key).not.toBeNull();
    expect(hemi).not.toBeNull();
    expect(knifePart).not.toBeNull();

    const normalKey = key!.intensity ?? 0;
    const normalHemi = hemi!.intensity ?? 0;
    const restEmissive = (knifePart!.material as StandardMaterial).emissiveColor.clone();

    result.setObjectFocus("kitchen_knife");
    // Background de-emphasis: deterministic key/hemi dip.
    expect(key!.intensity).toBeCloseTo(normalKey * FOCUS_BG_LIGHT_SCALE, 10);
    expect(hemi!.intensity).toBeCloseTo(normalHemi * FOCUS_BG_LIGHT_SCALE, 10);
    // Object dominance: dedicated focus emissive outshines the rest state.
    const focused = (knifePart!.material as StandardMaterial).emissiveColor;
    expect(focused.r).toBeCloseTo(0.78, 5);
    expect(focused.g).toBeCloseTo(0.7, 5);
    expect(focused.b).toBeCloseTo(0.38, 5);
    const ring = result.scene.getNodeByName("pd_ring_kitchen_knife");
    expect(ring!.isVisible).toBe(true);

    result.setObjectFocus(null);
    expect(key!.intensity).toBe(normalKey);
    expect(hemi!.intensity).toBe(normalHemi);
    const restored = (knifePart!.material as StandardMaterial).emissiveColor;
    expect(restored.r).toBeCloseTo(restEmissive.r, 10);
    expect(restored.g).toBeCloseTo(restEmissive.g, 10);
    expect(restored.b).toBeCloseTo(restEmissive.b, 10);
    expect(ring!.isVisible).toBe(false); // no hover, no selection -> hidden again
    result.dispose();
  });

  it("repeated open/close cycles are deterministic (same final camera state, no leaks)", async () => {
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const camera = cameraOf(result);
    const meshesBefore = result.scene.meshes.length;
    const lightsBefore = result.scene.lights.length;
    const observers = () => (result.scene.onBeforeRenderObservable as unknown as { observers: unknown[] }).observers.length;
    // Babylon 8 defers observer removal to the next macrotask (setTimeout 0),
    // so we settle before counting: no accumulation after the async removal.
    const flushObservers = () => new Promise<void>((resolve) => setTimeout(resolve, 0));
    expect(observers()).toBe(0);

    result.setObjectFocus("kitchen_knife");
    const world = cameraVec(result.getFocusSnapshot()!.saved);
    result.setObjectFocus(null);
    await flushObservers();
    expect(observers()).toBe(0);

    let restored: ReturnType<typeof cameraVec> | null = null;
    for (let cycle = 0; cycle < 25; cycle += 1) {
      result.setObjectFocus("kitchen_knife");
      expect(observers()).toBe(1); // exactly one beforeRender observer during focus
      for (let step = 0; step < focusTransitionTickCount(18); step += 1) result.tickFocus(18);
      // The SAVED world state is identical on every cycle (entered from the
      // same restored camera state), so the restore target is deterministic.
      expect(cameraVec(result.getFocusSnapshot()!.saved)).toEqual(world);
      result.setObjectFocus(null);
      await flushObservers();
      expect(observers()).toBe(0); // observer fully unregistered after exit
      const live = cameraVec({ alpha: camera.alpha, beta: camera.beta, radius: camera.radius, target: camera.target });
      if (restored === null) restored = live;
      expect(live).toEqual(restored); // every cycle restores to the identical state
      expect(live).toEqual(world);
    }
    // Zero accumulation across 25 cycles: no meshes, lights or camera clones.
    expect(result.scene.meshes.length).toBe(meshesBefore);
    expect(result.scene.lights.length).toBe(lightsBefore);
    expect(cameraOf(result)).toBe(camera);
    result.dispose();
  });

  it("no evidence double-trigger: entering/leaving focus never calls onPick again", () => {
    const onPick = vi.fn();
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions({ onPick }));
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    result.setObjectFocus("kitchen_knife");
    for (let step = 0; step < focusTransitionTickCount(18); step += 1) result.tickFocus(18);
    result.setObjectFocus(null);
    result.setObjectFocus("kitchen_knife");
    result.setObjectFocus(null);
    expect(onPick).not.toHaveBeenCalled();
    result.dispose();
  });

  it("direct mesh picking is unchanged: normal dispatch when NOT in focus, and preserved after focus", () => {
    const picked: string[] = [];
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions({ onPick: (id) => picked.push(id) }));
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const scene = result.scene;

    // Not in focus: a click dispatches exactly as before.
    scene.onPointerDown?.(pointerMove(0, 0) as never, pickInfo(scene.getNodeByName("pd_hit_kitchen_knife")) as never, POINTER_TYPES);
    expect(picked).toEqual(["kitchen_knife"]);

    // Run a full focus session...
    result.setObjectFocus("kitchen_knife");
    for (let step = 0; step < focusTransitionTickCount(18); step += 1) result.tickFocus(18);
    result.setObjectFocus(null);

    // ...and direct picking still dispatches identically (no handler replaced).
    picked.length = 0;
    scene.onPointerDown?.(pointerMove(0, 0) as never, pickInfo(scene.getNodeByName("pd_part_kitchen_knife_0")) as never, POINTER_TYPES);
    expect(picked).toEqual(["kitchen_knife"]);
    result.dispose();
  });

  it("any pointer interaction stops the slow auto-orbit (must stop on interaction)", () => {
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const camera = cameraOf(result);

    result.setObjectFocus("kitchen_knife");
    for (let step = 0; step < focusTransitionTickCount(18); step += 1) result.tickFocus(18);
    expect(result.getFocusSnapshot()!.orbitEnabled).toBe(true);
    const alphaBefore = camera.alpha;
    result.tickFocus(1000); // drift while nothing has happened
    expect(camera.alpha).toBeGreaterThan(alphaBefore);

    const alphaAtPointer = camera.alpha;
    // A pointer-down on ANY mesh (even the floor) halts the turntable.
    result.scene.onPointerDown?.(pointerMove(1, 1) as never, pickInfo({ name: "floor_01", parent: null }) as never, POINTER_TYPES);
    expect(result.getFocusSnapshot()!.orbitEnabled).toBe(false);
    result.tickFocus(2000);
    expect(camera.alpha).toBe(alphaAtPointer);
    result.dispose();
  });

  it("reduced-motion: no animation frames, straight to framing, NO auto-orbit", () => {
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), {
      ...nullEngineOptions(),
      prefersReducedMotion: true,
    });
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const camera = cameraOf(result);

    result.setObjectFocus("kitchen_knife");
    const snapshot = result.getFocusSnapshot()!;
    expect(snapshot.reducedMotion).toBe(true);
    expect(snapshot.transitionDone).toBe(true); // jumped, not animated
    expect(snapshot.orbitEnabled).toBe(false); // no turntable
    expect(cameraVec(snapshot.live)).toEqual(cameraVec(snapshot.framing)); // framing still correct
    // No beforeRender observer was ever registered -> no animation frame.
    const observers = (result.scene.onBeforeRenderObservable as unknown as { observers: unknown[] }).observers.length;
    expect(observers).toBe(0);
    // Ticks after the jump move nothing (no drift, no re-entry).
    const before = { alpha: camera.alpha, radius: camera.radius };
    result.tickFocus(5000);
    expect(camera.alpha).toBe(before.alpha);
    expect(camera.radius).toBe(before.radius);
    result.dispose();
  });

  it("reload unchanged: focus adds NO persistence and never mutates the scene model", () => {
    const model = icePickOfficeModel();
    const captured = model;

    const first = createInvestigationScene(NOOP_CANVAS, model, nullEngineOptions());
    expect(first.ok).toBe(true);
    if (!first.ok) throw new Error("expected ok");
    const initialState = cameraVec(cameraOf(first));
    first.setObjectFocus("bronze_ceremonial_ice_pick");
    for (let step = 0; step < focusTransitionTickCount(18); step += 1) first.tickFocus(18);
    first.setObjectFocus(null);
    first.dispose();

    // A deterministically-rebuilt scene starts from the SAME camera (no
    // persisted focus state) and the model reference is untouched.
    const second = createInvestigationScene(NOOP_CANVAS, model, nullEngineOptions());
    expect(second.ok).toBe(true);
    if (!second.ok) throw new Error("expected ok");
    expect(cameraVec(cameraOf(second))).toEqual(initialState);
    expect(model).toBe(captured);
    expect(second.getFocusedObjectId()).toBeNull();
    second.dispose();
  });
});

describe("Phase 18B — hard-case acceptance (bronze ceremonial ice pick, office)", () => {
  it("renders the ice pick visibly with DISTINCT materials (no fallback gray)", () => {
    const result = createInvestigationScene(NOOP_CANVAS, icePickOfficeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const scene = result.scene;

    // Object visible with its own identity + geometry.
    const root = scene.getNodeByName("pd_obj_bronze_ceremonial_ice_pick") as Mesh | null;
    expect(root, "ice pick root visible").not.toBeNull();
    expect(root!.isVisible).toBe(true);
    const handle = scene.getNodeByName("pd_part_bronze_ceremonial_ice_pick_0") as Mesh | null;
    const blade = scene.getNodeByName("pd_part_bronze_ceremonial_ice_pick_1") as Mesh | null;
    expect(handle, "handle part").not.toBeNull();
    expect(blade, "blade part").not.toBeNull();

    // Materials distinguishable and NOT the neutral fallback gray.
    const assertColor = (mesh: Mesh | null, expectedHex: string): void => {
      expect(mesh).not.toBeNull();
      const mat = mesh!.material as StandardMaterial;
      const got = `#${[mat.diffuseColor.r, mat.diffuseColor.g, mat.diffuseColor.b]
        .map((c) => Math.round(c * 255).toString(16).padStart(2, "0"))
        .join("")}`;
      expect(got).toBe(expectedHex);
      expect(got).not.toBe("#8d8d93");
    };
    assertColor(handle, "#8a5a2b");
    assertColor(blade, "#c9a227");
    result.dispose();
  });

  it("direct picking works on the ice pick (mesh click -> canonical object)", () => {
    const picked: string[] = [];
    const result = createInvestigationScene(
      NOOP_CANVAS,
      icePickOfficeModel(),
      nullEngineOptions({ onPick: (id) => picked.push(id) }),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const scene = result.scene;

    scene.onPointerDown?.(
      pointerMove(0, 0) as never,
      pickInfo(scene.getNodeByName("pd_part_bronze_ceremonial_ice_pick_1")) as never,
      POINTER_TYPES,
    );
    expect(picked).toEqual(["bronze_ceremonial_ice_pick"]);
    result.dispose();
  });

  it("focus shows geometry clearly and restores the exact world camera", () => {
    const result = createInvestigationScene(NOOP_CANVAS, icePickOfficeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const camera = cameraOf(result);

    result.setObjectFocus("bronze_ceremonial_ice_pick");
    const snapshot = result.getFocusSnapshot()!;
    expect(snapshot.framing.radius).toBe(1.5); // small evidence -> safe floor close-up
    for (let step = 0; step < focusTransitionTickCount(18); step += 1) result.tickFocus(18);
    // After the full transition the LIVE camera equals the computed framing.
    const live = result.getFocusSnapshot()!.live;
    expect(cameraVec(live)).toEqual(cameraVec(snapshot.framing));
    // The inspected object is centered (target near its world position).
    const pick = icePickOfficeModel().worldObjects.find((o) => o.objectId === "bronze_ceremonial_ice_pick")!;
    expect(Math.abs(camera.target.x - pick.position.x)).toBeLessThan(0.6);
    expect(Math.abs(camera.target.z - pick.position.z)).toBeLessThan(0.6);

    const saved = cameraVec(snapshot.saved);
    result.setObjectFocus(null);
    expect(cameraVec({ alpha: camera.alpha, beta: camera.beta, radius: camera.radius, target: camera.target })).toEqual(saved);
    result.dispose();
  });

  it("no proc.* leak: scene node names and focus snapshot never carry the proc token", () => {
    const result = createInvestigationScene(NOOP_CANVAS, icePickOfficeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const scene = result.scene;
    for (const mesh of scene.meshes) {
      expect(mesh.name.toLowerCase()).not.toContain("proc.");
      expect(mesh.name).not.toContain("decor.");
    }
    result.setObjectFocus("bronze_ceremonial_ice_pick");
    const snapshot = JSON.stringify(result.getFocusSnapshot());
    expect(snapshot).not.toContain("proc.");
    expect(snapshot).not.toContain("truth");
    expect(snapshot).not.toContain("murderer");
    result.dispose();
  });

  it("the semantic label + badge path used by the UI is deterministic from the same model", () => {
    // (Model-identity determinism edge: two loads of the same fixture focus
    // the same object to the same framing — F15 reload guarantee.)
    const first = createInvestigationScene(NOOP_CANVAS, icePickOfficeModel(), nullEngineOptions());
    const second = createInvestigationScene(NOOP_CANVAS, icePickOfficeModel(), nullEngineOptions());
    expect(first.ok && second.ok).toBe(true);
    if (!first.ok || !second.ok) throw new Error("expected ok");
    first.setObjectFocus("bronze_ceremonial_ice_pick");
    second.setObjectFocus("bronze_ceremonial_ice_pick");
    for (let step = 0; step < focusTransitionTickCount(18); step += 1) {
      first.tickFocus(18);
      second.tickFocus(18);
    }
    expect(cameraVec(first.getFocusSnapshot()!.framing)).toEqual(cameraVec(second.getFocusSnapshot()!.framing));
    first.dispose();
    second.dispose();
  });
});

/* ======================================================================
 * Phase 18B — performance probe (E).
 *
 * Same probe approach as the rest of the scene suite (deterministic
 * NullEngine + injected 18ms frame steps — no heavy instrumentation): the
 * transition length is MEASURED in frame-ticks, the exit restore is O(1),
 * and repeated open/close cycles leave the mesh/light/observer counts stable
 * (covered in the memory test above).
 * ==================================================================== */

describe("Phase 18B — performance probe (transition + memory numbers)", () => {
  it("measures the focus transition: 900ms / 50 fixed frames entry, 0-tick restore", () => {
    expect(FOCUS_TRANSITION_MS).toBe(900);
    expect(focusTransitionTickCount(18)).toBe(50);

    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");

    result.setObjectFocus("kitchen_knife");
    let ticksToComplete = 0;
    for (let step = 1; step <= 100 && !result.getFocusSnapshot()!.transitionDone; step += 1) {
      result.tickFocus(18);
      ticksToComplete = step;
    }
    // Measured: entry transition = exactly 50 x 18ms = 900ms of sim time.
    expect(ticksToComplete).toBe(focusTransitionTickCount(18));
    expect(result.getFocusSnapshot()!.transitionDone).toBe(true);

    // Measured: exit restore is instantaneous (session torn down, O(1) apply).
    result.setObjectFocus(null);
    expect(result.getFocusSnapshot()).toBeNull();
    result.dispose();
  });

  it("a focus session adds zero render-visible objects (no camera/material/mesh re-creation)", () => {
    const result = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const meshes = result.scene.meshes.length;
    const lights = result.scene.lights.length;
    const materials = result.scene.materials.length;

    result.setObjectFocus("kitchen_knife");
    for (let step = 0; step < focusTransitionTickCount(18); step += 1) result.tickFocus(18);
    expect(result.scene.meshes.length).toBe(meshes);
    expect(result.scene.lights.length).toBe(lights);
    expect(result.scene.materials.length).toBe(materials);
    result.setObjectFocus(null);
    result.dispose();
  });
});

/* ======================================================================
 * Phase 18D — showcase art direction (renderer): decor props, warm desk
 * pool, ambient-fill identity, evidence coexistence and bounded complexity.
 * ==================================================================== */

/** A hotel-suite model for the Phase 18D showcase extension. */
function hotelSuiteModel(): InvestigationSceneModel {
  return buildInvestigationScene(makeHotelSuiteBootstrap());
}

interface LightColorProbe {
  diffuse?: { r: number; g: number; b: number };
  intensity?: number;
}

describe("Phase 18D — decor props render as plain NON-interactable shell meshes", () => {
  it("office decor meshes exist, carry no pd_ identity, rings or hover admission", () => {
    const result = createInvestigationScene(NOOP_CANVAS, buildInvestigationScene(makeOfficeBootstrap()), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const scene = result.scene;

    for (const name of ["decor_desk_01", "decor_monitor_01", "decor_bookshelf_01", "decor_desklamp_01"]) {
      const mesh = scene.getNodeByName(name) as Mesh | null;
      expect(mesh, `${name} shell mesh`).not.toBeNull();
      expect(mesh!.name.startsWith("pd_"), `${name} is NOT a world object`).toBe(false);
      // Decorative by construction: no hover ring and never a pick candidate.
      expect(scene.getNodeByName(`pd_ring_${name}`), `${name} has no ring`).toBeNull();
      expect(scene.pointerMovePredicate!(mesh!), `${name} not admitted on hover`).toBe(false);
      expect(scene.pointerDownPredicate!(mesh!), `${name} not admitted on click`).toBe(false);
      // No emissive affordance: the rest state carries no interactive glow.
      const mat = mesh!.material as StandardMaterial;
      expect(mat.emissiveColor.r).toBe(0);
      expect(mat.emissiveColor.g).toBe(0);
      expect(mat.emissiveColor.b).toBe(0);
    }
    // No world object with a decor-prefixed id either.
    expect(objectIdFromMeshName("decor_desk_01")).toBeNull();
    result.dispose();
  });

  it("hotel decor meshes render (bed + lounge composition) without interaction", () => {
    const result = createInvestigationScene(NOOP_CANVAS, hotelSuiteModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const scene = result.scene;
    for (const name of ["decor_bed_01", "decor_sofa_01", "decor_coffeetable_01", "decor_tablelamp_01", "decor_armchair_01", "decor_handbag_01"]) {
      const mesh = scene.getNodeByName(name) as Mesh | null;
      expect(mesh, `${name} hotel shell mesh`).not.toBeNull();
      expect(scene.pointerMovePredicate!(mesh!), `${name} decorative (no hover)`).toBe(false);
    }
    result.dispose();
  });
});

describe("Phase 18D — warm desk pool + ambient-fill identity", () => {
  it("office renders the warm desk-pool PointLight + emissive marker", () => {
    const result = createInvestigationScene(NOOP_CANVAS, buildInvestigationScene(makeOfficeBootstrap()), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const scene = result.scene;
    const pool = scene.getNodeByName("light_desk_pool_point") as LightColorProbe | null;
    expect(pool, "desk pool point light").not.toBeNull();
    expect(pool!.intensity).toBe(0.6);
    // Warm pool color (#f2c48a -> r > b).
    expect(pool!.diffuse!.r).toBeGreaterThan(pool!.diffuse!.b);
    const marker = scene.getNodeByName("light_desk_pool_marker");
    expect(marker, "desk pool emissive marker").not.toBeNull();
    result.dispose();
  });

  it("hemi ambient fill follows the kit accent: cool office, warm hotel, golden apartment", () => {
    // Office: cool accent (#b8ccd8) -> the fill reads cooler (b > r).
    const office = createInvestigationScene(NOOP_CANVAS, buildInvestigationScene(makeOfficeBootstrap()), nullEngineOptions());
    expect(office.ok).toBe(true);
    if (!office.ok) throw new Error("expected ok");
    const officeHemi = office.scene.getNodeByName("investigation_hemi") as LightColorProbe | null;
    expect(officeHemi).not.toBeNull();
    expect(officeHemi!.diffuse!.b).toBeGreaterThan(officeHemi!.diffuse!.r);

    // Hotel: warm accent (#f0c080) -> the fill reads warm (r > b).
    const hotel = createInvestigationScene(NOOP_CANVAS, hotelSuiteModel(), nullEngineOptions());
    expect(hotel.ok).toBe(true);
    if (!hotel.ok) throw new Error("expected ok");
    const hotelHemi = hotel.scene.getNodeByName("investigation_hemi") as LightColorProbe | null;
    expect(hotelHemi).not.toBeNull();
    expect(hotelHemi!.diffuse!.r).toBeGreaterThan(hotelHemi!.diffuse!.b);

    // Apartment golden: the hard-coded warm fill is byte-identical.
    const apartment = createInvestigationScene(NOOP_CANVAS, knifeModel(), nullEngineOptions());
    expect(apartment.ok).toBe(true);
    if (!apartment.ok) throw new Error("expected ok");
    const apartmentHemi = apartment.scene.getNodeByName("investigation_hemi") as LightColorProbe | null;
    expect(apartmentHemi).not.toBeNull();
    expect(apartmentHemi!.diffuse!.r).toBeCloseTo(1.0, 6);
    expect(apartmentHemi!.diffuse!.g).toBeCloseTo(0.93, 6);
    expect(apartmentHemi!.diffuse!.b).toBeCloseTo(0.84, 6);
    office.dispose();
    hotel.dispose();
    apartment.dispose();
  });

  it("focus mode works in the hotel suite (Phase 18D showcase extension)", () => {
    const result = createInvestigationScene(NOOP_CANVAS, hotelSuiteModel(), nullEngineOptions());
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const camera = cameraOf(result);
    const initialRadius = camera.radius;

    result.setObjectFocus("hotel_desk_knife");
    const snapshot = result.getFocusSnapshot()!;
    // Small knife evidence -> safe min close-up, tighter than the world camera.
    expect(snapshot.framing.radius).toBe(1.5);
    expect(snapshot.framing.radius).toBeLessThan(initialRadius);
    for (let step = 0; step < focusTransitionTickCount(18); step += 1) result.tickFocus(18);
    expect(cameraVec(result.getFocusSnapshot()!.live)).toEqual(cameraVec(snapshot.framing));

    // The inspected hotel knife is centered near its desk anchor (4, 10).
    const knife = hotelSuiteModel().worldObjects.find((o) => o.objectId === "hotel_desk_knife")!;
    expect(Math.abs(camera.target.x - knife.position.x)).toBeLessThan(0.6);
    expect(Math.abs(camera.target.z - knife.position.z)).toBeLessThan(0.6);
    const saved = cameraVec(snapshot.saved);
    result.setObjectFocus(null);
    expect(cameraVec({ alpha: camera.alpha, beta: camera.beta, radius: camera.radius, target: camera.target })).toEqual(saved);
    result.dispose();
  });
});

describe("Phase 18D — evidence coexistence + bounded scene complexity", () => {
  it("the hard-case ice pick stays visible and pickable WITH the office decor present", () => {
    const picked: string[] = [];
    const result = createInvestigationScene(
      NOOP_CANVAS,
      icePickOfficeModel(),
      nullEngineOptions({ onPick: (id) => picked.push(id) }),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("expected ok");
    const scene = result.scene;
    // Both the decor shell and the procedural evidence coexist in one scene.
    expect(scene.getNodeByName("decor_desk_01")).not.toBeNull();
    expect(scene.getNodeByName("decor_bookshelf_01")).not.toBeNull();
    const pickRoot = scene.getNodeByName("pd_obj_bronze_ceremonial_ice_pick") as Mesh | null;
    expect(pickRoot, "ice pick still visible").not.toBeNull();
    expect(pickRoot!.isVisible).toBe(true);

    result.scene.onPointerDown?.(
      pointerMove(0, 0) as never,
      pickInfo(scene.getNodeByName("pd_part_bronze_ceremonial_ice_pick_1")) as never,
      POINTER_TYPES,
    );
    expect(picked).toEqual(["bronze_ceremonial_ice_pick"]);
    result.dispose();
  });

  it("office + hotel scenes stay within the documented mesh/material/light budget", () => {
    for (const makeModel of [() => buildInvestigationScene(makeOfficeBootstrap()), hotelSuiteModel]) {
      const result = createInvestigationScene(NOOP_CANVAS, makeModel(), nullEngineOptions());
      expect(result.ok).toBe(true);
      if (!result.ok) throw new Error("expected ok");
      // Phase 18D documented budget (bounded decor + one extra point light):
      //  - meshes/materials <= 64 (the v1 kit shell + decor + golden objects)
      //  - lights <= 4 (key + hemi + overhead + desk pool)
      expect(result.scene.meshes.length, "meshes bounded").toBeLessThanOrEqual(64);
      expect(result.scene.materials.length, "materials bounded").toBeLessThanOrEqual(64);
      expect(result.scene.lights.length, "lights bounded").toBeLessThanOrEqual(4);
      expect(result.scene.lights.length, "at least the two lights").toBeGreaterThanOrEqual(3);
      result.dispose();
    }
  });

  it("rebuilding the same showcase scene is deterministic and leak-free across cycles", () => {
    const counts: number[] = [];
    for (let cycle = 0; cycle < 3; cycle += 1) {
      const result = createInvestigationScene(NOOP_CANVAS, hotelSuiteModel(), nullEngineOptions());
      expect(result.ok).toBe(true);
      if (!result.ok) throw new Error("expected ok");
      if (cycle > 0) {
        expect(result.scene.meshes.length).toBe(counts[0]);
        expect(result.scene.materials.length).toBe(counts[1]);
        expect(result.scene.lights.length).toBe(counts[2]);
      } else {
        counts.push(result.scene.meshes.length, result.scene.materials.length, result.scene.lights.length);
      }
      result.dispose();
    }
  });
});