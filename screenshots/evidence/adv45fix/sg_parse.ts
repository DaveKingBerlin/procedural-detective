/**
 * Phase45-Fix adversarial — feed the REAL frontend savegame parser the
 * savegames produced by the guard-repaired (probe3) and REAL-service (probe4)
 * paths.
 *
 * ZERO network. Run with the LOCAL vite-node binary from the repo root:
 *   & "frontend\node_modules\.bin\vite-node.cmd" "screenshots\evidence\adv45fix\sg_parse.ts"
 */
import {
  parseSavegameV1,
  utf8ByteLength,
  SavegameParseError,
} from "../../../frontend/src/savegame/savegameV1";

import repairedMedium from "./sg_repaired_medium.pdcase.json";
import repairedEasy from "./sg_repaired_easy.pdcase.json";
import repairedHard from "./sg_repaired_hard.pdcase.json";
import realEasy from "./sg_real_easy.pdcase.json";
import realMedium from "./sg_real_medium.pdcase.json";
import realHard from "./sg_real_hard.pdcase.json";
import goldenMedium from "./sg_golden_medium.pdcase.json";

function report(label: string, doc: any): boolean {
  const text = JSON.stringify(doc);
  try {
    const parsed = parseSavegameV1(text, utf8ByteLength(text));
    console.log(
      `${label}: PARSED OK publicCase.scene=${JSON.stringify(parsed.publicCase.scene)}`
    );
    return true;
  } catch (error) {
    if (error instanceof SavegameParseError) {
      console.log(`${label}: REJECTED -> ${error.message}`);
    } else {
      console.log(`${label}: THREW ${(error as Error).name}: ${(error as Error).message}`);
    }
    return false;
  }
}

const cases: Array<[string, any]> = [
  ["guard-repaired medium (unpinned scene)", repairedMedium],
  ["guard-repaired easy (unpinned scene)", repairedEasy],
  ["guard-repaired hard (unpinned scene)", repairedHard],
  ["REAL-service repaired easy", realEasy],
  ["REAL-service repaired medium", realMedium],
  ["REAL-service repaired hard", realHard],
  ["golden medium control", goldenMedium],
];

let failures = 0;
for (const [label, doc] of cases) {
  const ok = report(label, doc);
  if (!ok) failures += 1;
}

console.log(`\nROUND-TRIP FAILURES: ${failures}/${cases.length}`);
