/**
 * ADV-45FIX-01 re-verification — feed the REAL frontend savegame parser the
 * savegames produced by the kit-pinned guard repair (all 5 kits).
 *
 * ZERO network. Run from repo root with the LOCAL vite-node binary:
 *   & "frontend\node_modules\.bin\vite-node.cmd" "screenshots\evidence\adv45fix\adv2\sg_adv2_parse.ts"
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  parseSavegameV1,
  utf8ByteLength,
  SavegameParseError,
} from "../../../../frontend/src/savegame/savegameV1";

const here = path.dirname(fileURLToPath(import.meta.url));
const manifest: string[] = JSON.parse(
  fs.readFileSync(path.join(here, "sg_adv2_manifest.json"), "utf8")
);

let failures = 0;
let parsedCount = 0;
for (const name of manifest) {
  const text = fs.readFileSync(path.join(here, name), "utf8");
  try {
    const parsed = parseSavegameV1(text, utf8ByteLength(text));
    const scene = parsed.publicCase.scene;
    const ok =
      scene !== null &&
      typeof scene.environmentId === "string" &&
      scene.environmentId.length > 0 &&
      typeof scene.environmentVersion === "number" &&
      scene.environmentVersion >= 1;
    if (ok) {
      parsedCount += 1;
      console.log(
        `${name}: PARSED OK scene=${JSON.stringify(scene)}`
      );
    } else {
      failures += 1;
      console.log(`${name}: PARSED but scene contract VIOLATED -> ${JSON.stringify(scene)}`);
    }
  } catch (error) {
    failures += 1;
    if (error instanceof SavegameParseError) {
      console.log(`${name}: REJECTED -> ${error.message}`);
    } else {
      console.log(`${name}: THREW ${(error as Error).name}: ${(error as Error).message}`);
    }
  }
}

console.log(`\nROUND-TRIP: ${parsedCount}/${manifest.length} parsed OK, ${failures} failure(s)`);
if (failures > 0) process.exit(1);