// DEF-056 (HIGH): Babylon v8 defines the REAL Scene.prototype.pick as a
// SIDE-EFFECT of evaluating @babylonjs/core/Culling/ray — the scene.js we
// bundle contains only the feature-gated stub that warns and returns a dummy
// PickingInfo. With tree-shaken subpath imports that module is never pulled
// in, so direct 3D interaction (hover tooltips, mesh clicks -> onPick) was
// dead in the production build despite green unit tests (tests feed fake
// pickInfo objects). Importing the module here (before any scene/picking
// use) makes the real ray/picking implementation ship in the bundle.
// NOTE: this import is intentionally unused — it MUST stay a side-effect
// import. Do not "clean it up".
import "@babylonjs/core/Culling/ray";
import { ArcRotateCamera } from "@babylonjs/core/Cameras/arcRotateCamera";
import { Engine } from "@babylonjs/core/Engines/engine";
import { DirectionalLight } from "@babylonjs/core/Lights/directionalLight";
import { HemisphericLight } from "@babylonjs/core/Lights/hemisphericLight";
import { Color3, Color4 } from "@babylonjs/core/Maths/math.color";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { MeshBuilder } from "@babylonjs/core/Meshes/meshBuilder";
import { Mesh } from "@babylonjs/core/Meshes/mesh";
import { Scene } from "@babylonjs/core/scene";
import type { ScenePrimitive } from "./apartment";
import type { InvestigationSceneModel, SceneWorldObject } from "./buildInvestigationScene";
import type { CompositePartDescriptor } from "./assetRegistry";
import { buildObjectComposite, needsPickHitbox, pickHitboxExtent } from "./assetRegistry";
import { buildKitShell, cameraProfileFor, lightingFor, APARTMENT_KIT_ID } from "../environments/kitGeometry";
import { applyMaterialTint, materialTintFor, type MaterialTint } from "../templates/variantParams";
import { instantiatePrimitive } from "./render";

/**
 * Babylon.js glue for the investigation scene (Phase 6 F/G + Phase 8 E).
 *
 * Separation of concerns:
 *  - geometry, colors, labels and interactability arrive in the PURE scene
 *    MODEL built by buildInvestigationScene (registry-derived, validated);
 *  - this module only translates that model into local Babylon primitives.
 *
 * Safety rules honored here:
 *  - NO network asset loads, NO arbitrary material/shader/script strings:
 *    colors/scales/mesh kinds are application-owned registry values only;
 *  - the engine is injected through {@link RenderOptions.createEngine} so
 *    tests can pass the NullEngine and initialization failures are testable;
 *  - picking maps a picked Mesh back to the backend objectId through the
 *    deterministic mesh-name encoding (see meshNameFor/objectIdFromMeshName);
 *  - any engine/init throw is captured and returned as {ok:false, error},
 *    so a Babylon failure becomes a visible degrade state, never a crash.
 *
 * Phase 8 visual polish (all deterministic, all primitives, no remote
 * assets, no executable scene code):
 *  - warmer key + ambient lighting (a directional key light and a warm
 *    hemisphere fill),
 *  - cast materials (dim specular -> matte, subtle warm emissive on
 *    interactables),
 *  - a slightly wider default camera framing the whole apartment,
 *  - pointer hover: a subtle highlight ring + brighter emissive + pointer
 *    cursor on interactable objects only.
 *
 * Phase 8_1 direct 3D interaction (all deterministic, security unchanged):
 *  - every world object renders as a named ROOT mesh (`pd_obj_<objectId>`)
 *    holding CHILD geometry meshes (composites from buildObjectComposite, or
 *    the plain single-part primitive). Picking walks the parent chain, so a
 *    click on ANY child part resolves back to the backend objectId.
 *  - objects below {@link MIN_PICKABLE_EXTENT} (0.4m) get an invisible
 *    parent hitbox sized to a safe minimum pickable extent, so small evidence
 *    (knife, letter opener) is reliably clickable without visible decoys.
 *  - hover exposes onHoverStart/onHoverEnd DOM callbacks for the tooltip.
 *
 * DEF-056 diagnostic hook: when the page is opened with `?pd-debug-pick=1`
 * (or in `vite dev`), the built scene/engine/camera/model are exposed on
 * `window.__pdDebugScene` for read-only browser probes. In the NORMAL flow
 * (production build without the query) this hook is a no-op and adds nothing.
 */

/** Subtle warm emissive accent applied to interactable world objects. */
const INTERACTABLE_EMISSIVE = new Color3(0.16, 0.13, 0.05);

/** Brighter emissive used while the pointer hovers an interactable object. */
const HOVER_EMISSIVE = new Color3(0.34, 0.3, 0.2);

/** Dim specular color — keeps every material looking cast/matte, not glossy. */
const MATTE_SPECULAR = new Color3(0.1, 0.1, 0.1);

const MESH_NAME_PREFIX = "pd_obj_";
const RING_NAME_PREFIX = "pd_ring_";
/** Child geometry parts of a composite (never carries the object prefix). */
const PART_NAME_PREFIX = "pd_part_";
/** Invisible pick hitboxes (never carries the object prefix). */
const HITBOX_NAME_PREFIX = "pd_hit_";

/**
 * Deterministic mapping between a backend world object id and the Babylon
 * mesh name used for picking. Object identity is NEVER an array index.
 */
export function meshNameFor(objectId: string): string {
  return `${MESH_NAME_PREFIX}${objectId}`;
}

/** Reverse of {@link meshNameFor}; returns null for any non-object mesh. */
export function objectIdFromMeshName(meshName: string | null | undefined): string | null {
  if (typeof meshName !== "string") return null;
  if (!meshName.startsWith(MESH_NAME_PREFIX)) return null;
  const candidate = meshName.slice(MESH_NAME_PREFIX.length);
  return candidate.length > 0 ? candidate : null;
}

/**
 * Parent-walking picking (Phase 8_1 A1): composite geometry lives in CHILD
 * meshes, so a picked child must resolve back to the objectId by walking the
 * `parent` chain until a `pd_obj_<objectId>` ROOT is found. Non-object meshes
 * (shell primitives, parts without a root) resolve to null. Structurally
 * typed so tests can pass plain fake meshes without a Babylon instance.
 */
export function objectIdFromPickedMesh(
  mesh: { name?: unknown; parent?: unknown } | null | undefined,
): string | null {
  let node: { name?: unknown; parent?: unknown } | null | undefined = mesh;
  while (node) {
    const id = objectIdFromMeshName(typeof node.name === "string" ? node.name : null);
    if (id !== null) return id;
    node = node.parent as { name?: unknown; parent?: unknown } | null | undefined;
  }
  return null;
}

export interface InvestigationSceneHandle {
  engine: Engine;
  scene: Scene;
  dispose: () => void;
  setObjectHighlight(objectId: string, on: boolean): void;
  isObjectHighlighted(objectId: string): boolean;
}

export type CreateInvestigationSceneResult =
  | ({ ok: true } & InvestigationSceneHandle)
  | { ok: false; error: string };

export interface RenderOptions {
  /** Defaults to a real Engine over the canvas; tests inject the NullEngine. */
  createEngine?: (canvas: HTMLCanvasElement) => Engine;
  /** Defaults to engine.runRenderLoop; tests inject a no-op. */
  startRenderLoop?: (engine: Engine, scene: Scene) => void;
  /** Camera orbit attach on the canvas; default true (disabled in DOM-less tests). */
  cameraControl?: boolean;
  /** Apartment shell override (defaults to the Phase 2 manifest). */
  manifest?: ScenePrimitive[];
  /** Called whenever the pointer picks an interactable object mesh. */
  onPick?: (objectId: string) => void;
  /** Called when the pointer starts hovering an interactable (tooltip wiring). */
  onHoverStart?: (objectId: string, origin?: { x: number; y: number }) => void;
  /** Called when the pointer stops hovering an interactable. */
  onHoverEnd?: (objectId: string | null) => void;
}

export function createInvestigationScene(
  canvas: HTMLCanvasElement,
  model: InvestigationSceneModel,
  options: RenderOptions = {},
): CreateInvestigationSceneResult {
  let resizeListener: (() => void) | null = null;
  const highlights = new Set<string>();
  const interactables = new Map<string, { mesh: Mesh; ring: Mesh | null }>();
  let hoveredId: string | null = null;

  try {
    const engine = options.createEngine
      ? options.createEngine(canvas)
      : new Engine(canvas, true, { preserveDrawingBuffer: true, stencil: true });
    const scene = new Scene(engine);
    scene.clearColor = new Color4(0.075, 0.085, 0.12, 1);

    // Phase 11 Track B: the scene model's environmentId selects the SHELL and
    // the camera framing/lighting. The apartment kit (and any unknown kit id)
    // keeps the exact golden camera/shell/lighting; non-apartment kits use
    // the kit manifest geometry (shell, spawn camera target, lighting profile).
    const environmentId = model.environmentId ?? APARTMENT_KIT_ID;
    const cameraProfile = cameraProfileFor(environmentId);

    const camera = new ArcRotateCamera(
      "investigation_camera",
      1.1,
      1.16,
      cameraProfile !== null ? cameraProfile.distance : 14.5,
      cameraProfile !== null
        ? new Vector3(cameraProfile.target.x, cameraProfile.target.y, cameraProfile.target.z)
        : new Vector3(0, 1.05, 0),
      scene,
    );
    if (options.cameraControl !== false && typeof canvas.addEventListener === "function") {
      camera.attachControl(canvas, true);
    }

    // Kit lighting profile (key/hemi intensity + normalized accent tint) for
    // non-apartment kits; the apartment kit keeps its golden warm lights.
    const kitLighting = lightingFor(environmentId);
    const keyDiffuse =
      kitLighting !== null ? accentToneOf(kitLighting.accentColor) : new Color3(1, 0.86, 0.7);
    const keyIntensity = kitLighting !== null ? kitLighting.keyIntensity : 0.85;
    const hemiIntensity = kitLighting !== null ? kitLighting.hemiIntensity : 0.5;

    // Warm key light from above-right, plus a soft hemisphere ambient fill.
    const key = new DirectionalLight("investigation_key", new Vector3(-0.7, -1, -0.35), scene);
    key.diffuse = keyDiffuse;
    key.intensity = keyIntensity;

    const hemi = new HemisphericLight("investigation_hemi", new Vector3(0.35, 1, -0.25), scene);
    hemi.diffuse = new Color3(1, 0.93, 0.84);
    hemi.intensity = hemiIntensity;

    // DEF-056 (click path, real-browser proof): Babylon's DEFAULT pointer
    // predicates admit EVERY mesh, so `scene.pick` on a pointer-down (or on a
    // pointer-up) resolves to the CLOSEST mesh along the ray. With the
    // evidence placed against the apartment walls, that closest mesh is often
    // a WALL — the ray hits `wall_front_left` before the knife hiding behind
    // it (verified: hover = pd_hit_kitchen_knife, down/null = wall_front_left
    // at the SAME pixel). The hover path only worked because Babylon's default
    // move predicate ALSO requires an action manager / enablePointerMoveEvents,
    // which walls do not have.
    // Fix (the documented robust approach): install OUR OWN pointer
    // predicates — only world-object meshes (`pd_obj_` / `pd_part_` /
    // `pd_hit_` roots, parts and hitboxes) are admissible candidates, so both
    // hover and click resolve to the interactable the user actually points at.
    const isWorldObjectMesh = (mesh: Mesh): boolean => {
      const name = mesh.name ?? "";
      if (
        !name.startsWith(MESH_NAME_PREFIX) &&
        !name.startsWith(PART_NAME_PREFIX) &&
        !name.startsWith(HITBOX_NAME_PREFIX)
      ) {
        return false;
      }
      return mesh.isPickable && mesh.isVisible && mesh.isEnabled() && mesh.isReady();
    };
    scene.pointerMovePredicate = (mesh) => isWorldObjectMesh(mesh as Mesh);
    scene.pointerDownPredicate = (mesh) => isWorldObjectMesh(mesh as Mesh);

    // Shell from the environment kit (the apartment kit keeps the Phase 2
    // manifest byte-identical; other kits build their room from kitGeometry),
    // then one mesh per world object.
    const manifest = options.manifest ?? buildKitShell(environmentId);
    for (const primitive of manifest) {
      instantiatePrimitive(scene, primitive);
    }
    for (const worldObject of model.worldObjects) {
      const mesh = instantiateWorldObject(scene, worldObject);
      if (worldObject.interactionWorks) {
        const ring = makeHighlightRing(scene, worldObject);
        interactables.set(worldObject.objectId, { mesh, ring });
      }
    }

    // DEF-056 (STILL OPEN after the ray import): Babylon 8.56 does NOT push
    // pick-ready world matrices / bounding infos onto meshes that are parented
    // after creation — the render loop draws them, but `scene.pick` can never
    // hit them until `computeWorldMatrix(true)` forces the full recompute.
    // REAL-BROWSER proof (Playwright grid scan): shell walls pick fine while
    // NOT ONE pd_* root/part/hitbox ever gets picked; after forcing a matrix
    // recompute + bounding refresh the same grid immediately finds them.
    // We therefore refresh every object hierarchy's picking state twice:
    //  1) right after assembly (before any frame);
    //  2) after the renderer has done its own first sync pass (frame 2),
    //     which is exactly when Babylon would also re-sync sibling meshes.
    const refreshPickingState = () => {
      for (const worldObject of model.worldObjects) {
        const root = scene.getNodeByName(meshNameFor(worldObject.objectId)) as Mesh | null;
        if (!root) continue;
        root.computeWorldMatrix(true);
        for (const child of root.getChildMeshes(false)) {
          (child as Mesh).computeWorldMatrix(true);
          (child as Mesh).getBoundingInfo();
        }
      }
    };
    refreshPickingState();

    const clearHover = () => {
      if (hoveredId !== null) {
        const entry = interactables.get(hoveredId);
        if (entry) {
          setMeshEmissive(entry.mesh, INTERACTABLE_EMISSIVE);
          if (entry.ring) entry.ring.isVisible = false;
        }
        trySetCursor(canvas, "");
        options.onHoverEnd?.(hoveredId);
        hoveredId = null;
      }
    };

    const applyHover = (objectId: string | null, origin?: { x: number; y: number }) => {
      if (objectId === hoveredId) return;
      clearHover();
      if (objectId === null) return;
      const entry = interactables.get(objectId);
      if (!entry) return;
      hoveredId = objectId;
      setMeshEmissive(entry.mesh, HOVER_EMISSIVE);
      if (entry.ring) entry.ring.isVisible = true;
      trySetCursor(canvas, "pointer");
      options.onHoverStart?.(objectId, origin);
    };

    // Picking: resolve the picked mesh (any composite child or the root)
    // back to a deterministic object id via the parent chain. Only
    // INTERACTABLE objects dispatch onPick — clicking the non-interactable
    // victim never fires an interaction.
    scene.onPointerDown = (_evt, pickInfo) => {
      const objectId = objectIdFromPickedMesh(pickInfo.pickedMesh);
      if (objectId !== null && interactables.has(objectId) && options.onPick) {
        options.onPick(objectId);
      }
    };

    // Hover affordance: only interactable objects ring + brighten + cursor.
    scene.onPointerMove = (evt, pickInfo) => {
      const objectId = objectIdFromPickedMesh(pickInfo.pickedMesh);
      const origin = { x: evt.clientX, y: evt.clientY };
      if (objectId !== null && interactables.has(objectId)) {
        applyHover(objectId, origin);
      } else {
        applyHover(null);
      }
    };

    if (typeof window !== "undefined") {
      resizeListener = () => engine.resize();
      window.addEventListener("resize", resizeListener);
    }

    // DEF-056: re-sync picking state once the renderer has completed its own
    // first pass (see refreshPickingState above). Only the REAL render loop
    // does this; injected test loops (no rendering) leave state untouched.
    let renderedFrames = 0;
    const renderFrame = () => {
      scene.render();
      renderedFrames += 1;
      if (renderedFrames === 2) {
        refreshPickingState();
      }
    };
    const runLoop = options.startRenderLoop ?? ((eng: Engine, _s: Scene) => eng.runRenderLoop(renderFrame));
    runLoop(engine, scene);

    // DEF-056 diagnostic hook (dev-only / ?pd-debug-pick=1): exposes the live
    // scene internals to a read-only browser probe. No-op in the normal flow.
    if (isPickDebugEnabled()) {
      try {
        const debugTarget = window as unknown as {
          __pdDebugScene?: unknown;
          __pdDebugPickLog?: Array<Record<string, unknown>>;
        };
        debugTarget.__pdDebugScene = { scene, engine, camera, model };
        if (options.onPick) {
          const log: Array<Record<string, unknown>> = [];
          const originalDown = scene.onPointerDown;
          if (originalDown) {
            scene.onPointerDown = (evt, pickInfo, type) => {
              const mesh = pickInfo?.pickedMesh ? pickInfo.pickedMesh.name : null;
              const objectId = objectIdFromPickedMesh(pickInfo?.pickedMesh);
              let rect: { left: number; top: number } | null = null;
              try {
                rect = engine.getInputElementClientRect();
              } catch {
                rect = null;
              }
              log.push({
                mesh,
                objectId,
                clientX: evt.clientX,
                clientY: evt.clientY,
                pointerX: scene.pointerX,
                pointerY: scene.pointerY,
                rectLeft: rect ? rect.left : null,
              });
              originalDown(evt, pickInfo, type);
            };
          }
          debugTarget.__pdDebugPickLog = log;
        }
      } catch {
        // The debug hook must never break the scene.
      }
    }

    return {
      ok: true,
      engine,
      scene,
      dispose: () => {
        if (resizeListener && typeof window !== "undefined") {
          window.removeEventListener("resize", resizeListener);
        }
        engine.stopRenderLoop();
        engine.dispose();
      },
      setObjectHighlight: (objectId: string, on: boolean) => {
        if (on) {
          highlights.add(objectId);
        } else {
          highlights.delete(objectId);
        }
        const entry = interactables.get(objectId);
        if (entry) {
          setMeshEmissive(entry.mesh, on ? INTERACTABLE_EMISSIVE : new Color3(0, 0, 0));
        }
      },
      isObjectHighlighted: (objectId: string) => highlights.has(objectId),
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return { ok: false, error: message };
  }
}

function instantiateWorldObject(scene: Scene, obj: SceneWorldObject): Mesh {
  const name = meshNameFor(obj.objectId);
  // ROOT mesh: an empty named parent that carries the object identity. All
  // visible geometry lives in CHILD meshes so parent-walking picking works.
  const root = new Mesh(name, scene);
  root.position = new Vector3(obj.position.x, obj.position.y, obj.position.z);
  root.rotation = new Vector3(obj.rotation.x, obj.rotation.y, obj.rotation.z);

  // Phase 12 Track B: parts come from THREE deterministic sources —
  //  1) the six legacy composite builders (golden apartment byte-identity);
  //  2) the generic template factory (template-backed composites — child
  //     parts were compiled at the DEFAULT variant in the registry/scene
  //     model and are applied VERBATIM here, scaled + hitbox included);
  //  3) the plain single-part primitive (all other objects).
  const templateParts = obj.compositeKind === null ? obj.templateParts : null;
  const isTemplateBacked = templateParts !== null && templateParts.length > 0;
  const parts =
    obj.compositeKind !== null
      ? buildObjectComposite(obj.compositeKind, { scale: obj.scale, color: obj.color })
      : isTemplateBacked
        ? templateParts
        : [singlePartDescriptor(obj)];

  // Phase 12: a materially-tinted template-backed object adjusts its diffuse
  // colors via the application-owned palette ONLY (no arbitrary strings).
  const tint =
    isTemplateBacked && obj.templateMaterial !== null ? materialTintFor(obj.templateMaterial) : null;
  parts.forEach((part, index) => {
    instantiateCompositePart(scene, root, obj.objectId, index, part, obj.interactionWorks, tint);
  });

  // Invisible pick hitbox (Phase 8_1 A2): small objects become reliably
  // clickable through a safe invisible box around the composite root. A
  // template's DECLARED hitbox is the extent basis when present (the
  // MIN_PICKABLE_EXTENT policy continues to apply); otherwise the world
  // scale (the factory's absolute bounds) is used, exactly as for legacy
  // composites and primitives.
  let hitboxEntry = { scale: obj.scale, hitboxScale: obj.hitboxScale };
  if (isTemplateBacked && obj.templateHitbox !== null) {
    hitboxEntry = { scale: obj.templateHitbox, hitboxScale: obj.hitboxScale };
  }
  if (needsPickHitbox(hitboxEntry)) {
    attachPickHitbox(scene, root, obj.objectId, hitboxEntry, obj.interactionWorks);
  }
  return root;
}

/** Single-part fallback descriptor for unlisted objects (box/cylinder/sphere/flat as today). */
function singlePartDescriptor(obj: SceneWorldObject): CompositePartDescriptor {
  return {
    kind:
      obj.primitiveKind === "cylinder"
        ? "cylinder"
        : obj.primitiveKind === "sphere"
          ? "sphere"
          : "box",
    size: { x: obj.scale.x, y: obj.scale.y, z: obj.scale.z },
    offset: { x: 0, y: 0, z: 0 },
    color: obj.color,
  };
}

/**
 * Parent one composite part onto the root with a registry-derived material.
 * `tint` (an application-owned material palette entry) optionally adjusts the
 * diffuse hue and adds a subtle emissive accent — never arbitrary strings.
 */
function instantiateCompositePart(
  scene: Scene,
  root: Mesh,
  objectId: string,
  index: number,
  part: CompositePartDescriptor,
  interactable: boolean,
  tint: MaterialTint | null = null,
): Mesh {
  const partName = `${PART_NAME_PREFIX}${objectId}_${index}`;
  let mesh: Mesh;
  switch (part.kind) {
    case "cylinder":
      mesh = MeshBuilder.CreateCylinder(
        partName,
        { diameter: Math.max(part.size.x, part.size.z), height: part.size.y },
        scene,
      );
      break;
    case "sphere":
      mesh = MeshBuilder.CreateSphere(
        partName,
        { diameter: Math.max(part.size.x, part.size.y, part.size.z) },
        scene,
      );
      break;
    case "torus":
      mesh = MeshBuilder.CreateTorus(
        partName,
        { diameter: Math.max(part.size.x, part.size.z), thickness: part.size.y, tessellation: 16 },
        scene,
      );
      break;
    case "box":
    default:
      mesh = MeshBuilder.CreateBox(
        partName,
        { width: part.size.x, height: part.size.y, depth: part.size.z },
        scene,
      );
      break;
  }
  mesh.parent = root;
  mesh.position = new Vector3(part.offset.x, part.offset.y, part.offset.z);
  if (part.rotation) {
    mesh.rotation = new Vector3(part.rotation.x, part.rotation.y, part.rotation.z);
  }
  const material = new StandardMaterial(`${partName}_material`, scene);
  // Phase 12: a material palette tint may adjust the diffuse hue (bounded
  // per-channel multiply — the output stays a valid #RRGGBB hex).
  const diffuseHex = tint !== null ? applyMaterialTint(part.color, tint) : part.color;
  material.diffuseColor = Color3.FromHexString(diffuseHex);
  material.specularColor = MATTE_SPECULAR;
  if (interactable) {
    material.emissiveColor = INTERACTABLE_EMISSIVE;
  } else if (tint !== null) {
    // Non-interactable template objects get the material's subtle emissive
    // accent (deterministic, bounded, palette-owned).
    material.emissiveColor = new Color3(tint.emissiveTint[0], tint.emissiveTint[1], tint.emissiveTint[2]);
  }
  mesh.material = material;
  if (interactable) {
    // DEF-056 (hover path): the InputManager's default pointerMovePredicate
    // only admits meshes with `enablePointerMoveEvents` (or an action
    // manager). Without this flag -- verified in a real browser -- the
    // engine's own move-picking always returns an empty pickInfo, so the
    // hover tooltip could never appear.
    mesh.enablePointerMoveEvents = true;
  }
  return mesh;
}

/**
 * Invisible pick hitbox (Phase 8_1 A2): a fully transparent (alpha 0) box
 * parented to the composite root and sized to the safe minimum pickable
 * extent. It never shows in screenshots and stays pickable, so tiny evidence
 * (knife, letter opener, laptop) has a reliable click target.
 */
function attachPickHitbox(
  scene: Scene,
  root: Mesh,
  objectId: string,
  entry: { scale: { x: number; y: number; z: number }; hitboxScale?: number },
  interactable: boolean = true,
): Mesh {
  const extent = pickHitboxExtent(entry);
  const hitName = `${HITBOX_NAME_PREFIX}${objectId}`;
  const box = MeshBuilder.CreateBox(
    hitName,
    { width: extent.x, height: extent.y, depth: extent.z },
    scene,
  );
  box.parent = root;
  const material = new StandardMaterial(`${hitName}_material`, scene);
  material.diffuseColor = new Color3(1, 1, 1);
  material.specularColor = MATTE_SPECULAR;
  material.alpha = 0; // fully transparent -> invisible in screenshots
  box.material = material;
  box.isPickable = true; // still-visible-for-picking configuration
  if (interactable) {
    box.enablePointerMoveEvents = true; // hover-pick admission (DEF-056)
  }
  return box;
}

function setMeshEmissive(mesh: Mesh, color: Color3): void {
  if (mesh.material instanceof StandardMaterial) {
    mesh.material.emissiveColor = color;
  }
  for (const child of mesh.getChildMeshes(false)) {
    setMeshEmissive(child as Mesh, color);
  }
}

/** A flat ring under an interactable object, hidden until the pointer hovers it. */
function makeHighlightRing(scene: Scene, obj: SceneWorldObject): Mesh | null {
  try {
    const ring = MeshBuilder.CreateTorus(
      `${RING_NAME_PREFIX}${obj.objectId}`,
      {
        diameter: Math.max(obj.scale.x, obj.scale.z) + 0.35,
        thickness: 0.05,
        tessellation: 24,
      },
      scene,
    );
    ring.position = new Vector3(obj.position.x, obj.position.y - obj.scale.y / 2 - 0.03, obj.position.z);
    const material = new StandardMaterial(`${RING_NAME_PREFIX}${obj.objectId}_material`, scene);
    material.diffuseColor = new Color3(0.9, 0.75, 0.35);
    material.specularColor = MATTE_SPECULAR;
    ring.material = material;
    ring.isVisible = false;
    return ring;
  } catch {
    // A ring failure must never break the scene — hover degrades to emissive only.
    return null;
  }
}

function trySetCursor(canvas: HTMLCanvasElement, cursor: string): void {
  try {
    canvas.style.cursor = cursor;
  } catch {
    // Best-effort only: styling must never throw out of picking.
  }
}

/**
 * Deterministic accent-color -> key-light tint conversion (Phase 11 Track B):
 * the kit manifest's #RRGGBB accent is normalized so its MAX channel maps to
 * full intensity, giving a stable recognizable tint while keeping every
 * channel within [0, 1]. The validator guarantees #RRGGBB input; a black or
 * all-zero accent falls back to the golden warm key tone.
 */
function accentToneOf(hex: string): Color3 {
  const r = parseInt(hex.slice(1, 3), 16) / 255;
  const g = parseInt(hex.slice(3, 5), 16) / 255;
  const b = parseInt(hex.slice(5, 7), 16) / 255;
  const max = Math.max(r, g, b);
  if (max <= 0) return new Color3(1, 0.86, 0.7);
  return new Color3(r / max, g / max, b / max);
}

/**
 * DEF-056 diagnostic gate: true only in `vite dev` or when the URL carries
 * `?pd-debug-pick=1`. In the production build without the query this is
 * always false, so normal users observe zero behavior change.
 */
function isPickDebugEnabled(): boolean {
  try {
    if (typeof window === "undefined") return false;
    if (import.meta.env?.DEV) return true;
    return /[?&]pd-debug-pick=1/.test(window.location.search);
  } catch {
    return false;
  }
}