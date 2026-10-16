/**
 * Phase45 FINAL retest (QA-2) — feed the REAL frontend savegame parser
 * (`parseSavegameV1`) the exported savegames for provider-authored skipped
 * cases, plus the adversary's PRE-FIX red artifact as a control.
 *
 * ZERO network. Run from `frontend/` with:
 *   npx vite-node "..\screenshots\evidence\p45qa2\qa2_sg_probe.ts"
 */
import {
  parseSavegameV1,
  utf8ByteLength,
  SavegameParseError,
} from "../../../frontend/src/savegame/savegameV1";

import red from "../adv45/savegame_skipped_office.pdcase.json";
import greenApartment from "./qa2_skipped_apartment.pdcase.json";
import greenOffice from "./qa2_skipped_office.pdcase.json";
import greenMansion from "./qa2_skipped_mansion.pdcase.json";
import greenWarehouse from "./qa2_skipped_warehouse.pdcase.json";
import greenHotelSuite from "./qa2_skipped_hotel_suite.pdcase.json";

function report(label: string, doc: unknown): boolean {
  const text = JSON.stringify(doc);
  try {
    const parsed = parseSavegameV1(text, utf8ByteLength(text));
    const scene = parsed.publicCase.scene;
    console.log(
      `${label}: PARSED OK publicCase.scene=${JSON.stringify(scene)}`,
    );
    return true;
  } catch (error) {
    if (error instanceof SavegameParseError) {
      console.log(`${label}: REJECTED -> ${error.message}`);
    } else {
      console.log(
        `${label}: THREW ${(error as Error).name}: ${(error as Error).message}`,
      );
    }
    return false;
  }
}

console.log("=== PRE-FIX red control (adversary-captured artifact) ===");
const redOk = report("RED  savegame_skipped_office.pdcase.json", red);

console.log("=== POST-FIX green (QA-2 exported savegames) ===");
const results: Record<string, boolean> = {
  apartment: report("GREEN qa2_skipped_apartment.pdcase.json", greenApartment),
  office: report("GREEN qa2_skipped_office.pdcase.json", greenOffice),
  mansion: report("GREEN qa2_skipped_mansion.pdcase.json", greenMansion),
  warehouse: report("GREEN qa2_skipped_warehouse.pdcase.json", greenWarehouse),
  hotel_suite: report("GREEN qa2_skipped_hotel_suite.pdcase.json", greenHotelSuite),
};

console.log("=== SUMMARY ===");
console.log(`RED control rejected as expected: ${!redOk}`);
for (const [env, ok] of Object.entries(results)) {
  console.log(`GREEN ${env}: ${ok ? "PARSED OK" : "FAILED"}`);
}
const allGreen = Object.values(results).every(Boolean);
console.log(`OVERALL: ${!redOk && allGreen ? "PASS" : "FAIL"}`);
process.exit(!redOk && allGreen ? 0 : 1);
