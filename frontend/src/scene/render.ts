import { ArcRotateCamera } from "@babylonjs/core/Cameras/arcRotateCamera";
import { Engine } from "@babylonjs/core/Engines/engine";
import { HemisphericLight } from "@babylonjs/core/Lights/hemisphericLight";
import { PointLight } from "@babylonjs/core/Lights/pointLight";
import { Color3, Color4 } from "@babylonjs/core/Maths/math.color";
import { Vector3 } from "@babylonjs/core/Maths/math.vector";
import { StandardMaterial } from "@babylonjs/core/Materials/standardMaterial";
import { MeshBuilder } from "@babylonjs/core/Meshes/meshBuilder";
import { Scene } from "@babylonjs/core/scene";
import type { ScenePrimitive } from "./apartment";

export interface BabylonSceneHandle {
  engine: Engine;
  scene: Scene;
  dispose: () => void;
}

export type CreateSceneResult =
  | ({ ok: true } & BabylonSceneHandle)
  | { ok: false; error: string };

const DEFAULT_COLORS: Record<ScenePrimitive["kind"], string> = {
  floor: "#3b4252",
  wall: "#6b7280",
  door: "#7c4a21",
  table: "#8a5a2b",
  light: "#fff4e0",
  rug: "#4f3d2e",
  chair: "#5f4b36",
  art: "#4a5a7a",
};

/** Dim specular color: shell primitives read as flat/cast (Phase 8 E). */
const MATTE_SPECULAR = new Color3(0.08, 0.08, 0.08);

/**
 * Thin Babylon.js glue: engine + arc-rotate camera + hemisphere light, then one
 * primitive (box/sphere/point light) per manifest entry.
 *
 * Only local primitives are used — no network assets, no remote GLB/GLTF.
 * Riskier engine initialization is wrapped in try/catch; on failure a visible
 * error string is returned so tests can detect an engine failure.
 */
export function createBabylonScene(canvas: HTMLCanvasElement, manifest: ScenePrimitive[]): CreateSceneResult {
  try {
    const engine = new Engine(canvas, true, { preserveDrawingBuffer: true, stencil: true });
    const scene = new Scene(engine);
    scene.clearColor = new Color4(0.07, 0.08, 0.11, 1);

    const camera = new ArcRotateCamera("camera", 1.05, 1.18, 13, new Vector3(0, 1, 0), scene);
    camera.attachControl(canvas, true);

    const hemi = new HemisphericLight("hemi", new Vector3(0.4, 1, -0.3), scene);
    hemi.intensity = 0.55;

    for (const primitive of manifest) {
      instantiatePrimitive(scene, primitive);
    }

    const onResize = () => engine.resize();
    window.addEventListener("resize", onResize);

    engine.runRenderLoop(() => scene.render());

    const dispose = () => {
      window.removeEventListener("resize", onResize);
      engine.stopRenderLoop();
      engine.dispose();
    };

    return { ok: true, engine, scene, dispose };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return { ok: false, error: message };
  }
}

export function instantiatePrimitive(scene: Scene, primitive: ScenePrimitive): void {
  const position = new Vector3(primitive.position.x, primitive.position.y, primitive.position.z);
  const color = primitive.color ?? DEFAULT_COLORS[primitive.kind];

  switch (primitive.kind) {
    case "floor":
    case "wall":
    case "door":
    case "table":
    case "rug":
    case "chair":
    case "art":
      createBox(scene, primitive.id, primitive, color);
      return;
    case "light": {
      const light = new PointLight(`${primitive.id}_point`, position, scene);
      light.diffuse = Color3.FromHexString(color);
      light.intensity = 0.6;
      // Small emissive marker so the light source is visible in the scene.
      const marker = MeshBuilder.CreateSphere(`${primitive.id}_marker`, { diameter: 0.18 }, scene);
      marker.position = position;
      const material = new StandardMaterial(`${primitive.id}_marker_material`, scene);
      material.diffuseColor = new Color3(0.2, 0.2, 0.2);
      material.emissiveColor = Color3.FromHexString(color);
      marker.material = material;
      return;
    }
  }
}

function createBox(scene: Scene, id: string, primitive: ScenePrimitive, colorHex: string) {
  const scale = primitive.scale ?? { x: 1, y: 1, z: 1 };
  const mesh = MeshBuilder.CreateBox(id, { width: scale.x, height: scale.y, depth: scale.z }, scene);
  mesh.position = new Vector3(primitive.position.x, primitive.position.y, primitive.position.z);
  if (primitive.rotation) {
    mesh.rotation = new Vector3(primitive.rotation.x, primitive.rotation.y, primitive.rotation.z);
  }
  const material = new StandardMaterial(`${id}_material`, scene);
  material.diffuseColor = Color3.FromHexString(colorHex);
  material.specularColor = MATTE_SPECULAR;
  mesh.material = material;
  return mesh;
}