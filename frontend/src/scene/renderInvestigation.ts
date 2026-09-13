import {
  ArcRotateCamera,
  Color3,
  Color4,
  Engine,
  HemisphericLight,
  MeshBuilder,
  Scene,
  StandardMaterial,
  Vector3,
} from "@babylonjs/core";
import type { Mesh } from "@babylonjs/core";
import type { ScenePrimitive } from "./apartment";
import { buildApartmentManifest } from "./apartment";
import type { InvestigationSceneModel, SceneWorldObject } from "./buildInvestigationScene";
import { instantiatePrimitive } from "./render";

/**
 * Babylon.js glue for the investigation scene (Phase 6 F/G).
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
 */

/** Emissive accent applied to interactable world objects (subtle affordance). */
const INTERACTABLE_EMISSIVE = new Color3(0.12, 0.2, 0.05);

const MESH_NAME_PREFIX = "pd_obj_";

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
  const objectMeshes = new Map<string, Mesh>();

  try {
    const engine = options.createEngine
      ? options.createEngine(canvas)
      : new Engine(canvas, true, { preserveDrawingBuffer: true, stencil: true });
    const scene = new Scene(engine);
    scene.clearColor = new Color4(0.07, 0.08, 0.11, 1);

    const camera = new ArcRotateCamera("investigation_camera", 1.05, 1.18, 13, new Vector3(0, 1, 0), scene);
    if (options.cameraControl !== false && typeof canvas.addEventListener === "function") {
      camera.attachControl(canvas, true);
    }

    const hemi = new HemisphericLight("investigation_hemi", new Vector3(0.4, 1, -0.3), scene);
    hemi.intensity = 0.55;

    // Apartment shell from the Phase 2 manifest, then one mesh per world object.
    const manifest = options.manifest ?? buildApartmentManifest();
    for (const primitive of manifest) {
      instantiatePrimitive(scene, primitive);
    }
    for (const worldObject of model.worldObjects) {
      objectMeshes.set(worldObject.objectId, instantiateWorldObject(scene, worldObject));
    }

    // Picking: resolve the picked mesh back to a deterministic object id.
    scene.onPointerDown = (_evt, pickInfo) => {
      const mesh = pickInfo.pickedMesh;
      const objectId = mesh ? objectIdFromMeshName(mesh.name) : null;
      if (objectId && options.onPick) {
        options.onPick(objectId);
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
        const mesh = objectMeshes.get(objectId);
        if (mesh && mesh.material instanceof StandardMaterial) {
          mesh.material.emissiveColor = on ? INTERACTABLE_EMISSIVE : new Color3(0, 0, 0);
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
  if (obj.interactionWorks) {
    material.emissiveColor = INTERACTABLE_EMISSIVE;
  }
  mesh.material = material;
  return mesh;
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