import { ArcRotateCamera } from "@babylonjs/core/Cameras/arcRotateCamera";
import { Engine } from "@babylonjs/core/Engines/engine";
import { DirectionalLight } from "@babylonjs/core/Lights/directionalLight";
import { HemisphericLight } from "@babylonjs/core/Lights/hemisphericLight";
import { Color3, Color4 } from "@babylonjs/core/Maths/math.color";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { MeshBuilder } from "@babylonjs/core/Meshes/meshBuilder";
import type { Mesh } from "@babylonjs/core/Meshes/mesh";
import { Scene } from "@babylonjs/core/scene";
import type { ScenePrimitive } from "./apartment";
import { buildApartmentManifest } from "./apartment";
import type { InvestigationSceneModel, SceneWorldObject } from "./buildInvestigationScene";
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
 */

/** Subtle warm emissive accent applied to interactable world objects. */
const INTERACTABLE_EMISSIVE = new Color3(0.16, 0.13, 0.05);

/** Brighter emissive used while the pointer hovers an interactable object. */
const HOVER_EMISSIVE = new Color3(0.34, 0.3, 0.2);

/** Dim specular color — keeps every material looking cast/matte, not glossy. */
const MATTE_SPECULAR = new Color3(0.1, 0.1, 0.1);

const MESH_NAME_PREFIX = "pd_obj_";
const RING_NAME_PREFIX = "pd_ring_";

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

    const camera = new ArcRotateCamera(
      "investigation_camera",
      1.1,
      1.16,
      14.5,
      new Vector3(0, 1.05, 0),
      scene,
    );
    if (options.cameraControl !== false && typeof canvas.addEventListener === "function") {
      camera.attachControl(canvas, true);
    }

    // Warm key light from above-right, plus a soft hemisphere ambient fill.
    const key = new DirectionalLight("investigation_key", new Vector3(-0.7, -1, -0.35), scene);
    key.diffuse = new Color3(1, 0.86, 0.7);
    key.intensity = 0.85;

    const hemi = new HemisphericLight("investigation_hemi", new Vector3(0.35, 1, -0.25), scene);
    hemi.diffuse = new Color3(1, 0.93, 0.84);
    hemi.intensity = 0.5;

    // Apartment shell from the Phase 2 manifest, then one mesh per world object.
    const manifest = options.manifest ?? buildApartmentManifest();
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

    const clearHover = () => {
      if (hoveredId !== null) {
        const entry = interactables.get(hoveredId);
        if (entry) {
          setMeshEmissive(entry.mesh, INTERACTABLE_EMISSIVE);
          if (entry.ring) entry.ring.isVisible = false;
        }
        trySetCursor(canvas, "");
        hoveredId = null;
      }
    };

    const applyHover = (objectId: string | null) => {
      if (objectId === hoveredId) return;
      clearHover();
      if (objectId === null) return;
      const entry = interactables.get(objectId);
      if (!entry) return;
      hoveredId = objectId;
      setMeshEmissive(entry.mesh, HOVER_EMISSIVE);
      if (entry.ring) entry.ring.isVisible = true;
      trySetCursor(canvas, "pointer");
    };

    // Picking: resolve the picked mesh back to a deterministic object id.
    scene.onPointerDown = (_evt, pickInfo) => {
      const mesh = pickInfo.pickedMesh;
      const objectId = mesh ? objectIdFromMeshName(mesh.name) : null;
      if (objectId && options.onPick) {
        options.onPick(objectId);
      }
    };

    // Hover affordability: only interactable objects ring + brighten + cursor.
    scene.onPointerMove = (_evt, pickInfo) => {
      const mesh = pickInfo.pickedMesh;
      const objectId = mesh ? objectIdFromMeshName(mesh.name) : null;
      if (objectId !== null) {
        applyHover(interactables.has(objectId) ? objectId : null);
      } else {
        applyHover(null);
      }
    };

    if (typeof window !== "undefined") {
      resizeListener = () => engine.resize();
      window.addEventListener("resize", resizeListener);
    }

    const runLoop = options.startRenderLoop ?? ((eng: Engine, s: Scene) => eng.runRenderLoop(() => s.render()));
    runLoop(engine, scene);

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
        if (entry && entry.mesh.material instanceof StandardMaterial) {
          entry.mesh.material.emissiveColor = on ? INTERACTABLE_EMISSIVE : new Color3(0, 0, 0);
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
  const mesh = createObjectMesh(scene, name, obj);
  mesh.name = name;
  mesh.position = new Vector3(obj.position.x, obj.position.y, obj.position.z);
  mesh.rotation = new Vector3(obj.rotation.x, obj.rotation.y, obj.rotation.z);
  const material = new StandardMaterial(`${name}_material`, scene);
  material.diffuseColor = Color3.FromHexString(obj.color);
  material.specularColor = MATTE_SPECULAR;
  if (obj.interactionWorks) {
    material.emissiveColor = INTERACTABLE_EMISSIVE;
  }
  mesh.material = material;
  return mesh;
}

function setMeshEmissive(mesh: Mesh, color: Color3): void {
  if (mesh.material instanceof StandardMaterial) {
    mesh.material.emissiveColor = color;
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

function createObjectMesh(scene: Scene, name: string, obj: SceneWorldObject): Mesh {
  const { x: sx, y: sy, z: sz } = obj.scale;
  switch (obj.primitiveKind) {
    case "cylinder":
      return MeshBuilder.CreateCylinder(`${name}_geom`, { diameter: Math.max(sx, sz), height: sy }, scene);
    case "sphere":
      return MeshBuilder.CreateSphere(`${name}_geom`, { diameter: Math.max(sx, sy, sz) }, scene);
    case "box":
    case "flat":
    default:
      return MeshBuilder.CreateBox(`${name}_geom`, { width: sx, height: sy, depth: sz }, scene);
  }
}