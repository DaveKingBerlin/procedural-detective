/**
 * Phase45 adversarial probe — feed the real frontend savegame parser a
 * savegame whose `publicCase.scene.environmentId` is null (the shape the
 * backend emits for a provider-authored skipped case).
 *
 * ZERO network. Read-only. Run with vite-node from the frontend dir.
 */
import {
  parseSavegameV1,
  utf8ByteLength,
  SavegameParseError,
} from "../../../frontend/src/savegame/savegameV1";
import fixture from "../../../frontend/src/savegame/fixtures/v1_demo_apartment.pdcase.json";
import backendSavegame from "./savegame_skipped_office.pdcase.json";

function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T;
}

function report(label: string, doc: any): void {
  const text = JSON.stringify(doc);
  try {
    const parsed = parseSavegameV1(text, utf8ByteLength(text));
    console.log(`${label}: PARSED OK publicCase.scene=${JSON.stringify(parsed.publicCase.scene)}`);
  } catch (error) {
    if (error instanceof SavegameParseError) {
      console.log(`${label}: REJECTED -> ${error.message}`);
    } else {
      console.log(`${label}: THREW ${(error as Error).name}: ${(error as Error).message}`);
    }
  }
}

const base: any = fixture;

report("control unmodified", clone(base));

const envNull = clone(base);
envNull.case.publicCase.scene.environmentId = null;
envNull.case.publicCase.scene.environmentVersion = null;
report("publicCase.scene env null", envNull);

const idOnly = clone(base);
idOnly.case.publicCase.scene.environmentId = null;
report("publicCase.scene environmentId null only", idOnly);

const caseSceneNull = clone(base);
caseSceneNull.case.scene.environmentId = null;
caseSceneNull.case.scene.environmentVersion = null;
report("case.scene env null (bootstrap shape)", caseSceneNull);

// THE decisive one: the ACTUAL backend-produced savegame for a
// provider-authored skipped case.
report("ACTUAL backend savegame (provider-authored skip)", clone(backendSavegame));
