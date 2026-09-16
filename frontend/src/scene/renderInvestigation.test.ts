import { describe, expect, it, vi } from "vitest";
import { NullEngine } from "@babylonjs/core/Engines";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import type { Mesh } from "@babylonjs/core/Meshes/mesh";
import { Scene } from "@babylonjs/core/scene";
import { Ray } from "@babylonjs/core/Culling/ray";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import type { DiscoveryResultDTO, InteractionResultDTO } from "../api/types";
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
import { makeBootstrap, makeEmailRecord, TEST_TOKEN } from "./testFixtures";

/**
 * Babylon glue tests. All engine work uses the deterministic NullEngine —
 * no GPU, no DOM, no network. Real Babylon init failures are simulated by an
 * injected engine factory that throws.
 */

const EMPTY_MODEL: InvestigationSceneModel = {
  location: { locationId: "miller_apartment_kitchen", name: "Miller Apartment - Kitchen" },
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

    // Interactable objects get a hidden ring under the object.
    for (const objectId of ["kitchen_knife", "apartment_laptop", "vase_01"]) {
      const ring = result.scene.getNodeByName(`pd_ring_${objectId}`);
      expect(ring, `expected a ring for ${objectId}`).not.toBeNull();
      expect(ring!.isVisible).toBe(false);
    }

    // The non-interactable victim body gets NO ring.
    expect(result.scene.getNodeByName("pd_ring_victim_body_placeholder")).toBeNull();
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

    const root = result.scene.getNodeByName("pd_obj_vase_01");
    result.scene.onPointerDown?.(pointerMove(0, 0) as never, pickInfo(root) as never, POINTER_TYPES);
    expect(picked).toEqual(["vase_01"]);
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
    expect(feedback.toast?.text).toBe("Interacted with Kitchen knife");
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