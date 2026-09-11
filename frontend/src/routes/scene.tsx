import { useEffect, useRef, useState } from "react";
import { buildApartmentManifest } from "../scene/apartment";
import { createBabylonScene } from "../scene/render";

type EngineState =
  | { status: "loading" }
  | { status: "ready"; primitiveCount: number }
  | { status: "error"; message: string };

/** "/scene" — Babylon.js placeholder apartment room built from primitives only. */
export default function ScenePage() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [engineState, setEngineState] = useState<EngineState>({ status: "loading" });

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const manifest = buildApartmentManifest();
    const result = createBabylonScene(canvas, manifest);

    if (!result.ok) {
      setEngineState({ status: "error", message: result.error });
      return;
    }

    setEngineState({ status: "ready", primitiveCount: manifest.length });
    return () => {
      result.dispose();
    };
  }, []);

  return (
    <section className="page scene">
      <h2>Placeholder apartment</h2>
      <p>
        A simple room built from local 3D shapes — floor, walls with a door gap, a table and a
        light. No external assets are loaded.
      </p>
      <div className="scene-canvas-shell" data-testid="scene-canvas">
        <canvas
          ref={canvasRef}
          width={800}
          height={480}
          className="scene-canvas"
          aria-label="3D placeholder apartment scene"
        />
      </div>
      {engineState.status === "error" && (
        <p className="scene-error" data-testid="scene-error" role="alert">
          Scene could not initialize: {engineState.message}
        </p>
      )}
      {engineState.status === "ready" && (
        <p className="scene-ready" data-testid="scene-ready">
          Scene ready — {engineState.primitiveCount} primitives rendered. Drag to orbit, scroll to
          zoom.
        </p>
      )}
    </section>
  );
}