/**
 * Phase45 FINAL re-review — feed the REAL frontend savegame parser every
 * savegame produced by the FIXED backend (ADV-45-01 retest).
 *
 * ZERO network. Read-only. Run with vite-node from the repo root:
 *   npx vite-node screenshots/evidence/adv45/sg_probe_fix2.ts
 */
import {
  parseSavegameV1,
  utf8ByteLength,
  SavegameParseError,
} from "../../../frontend/src/savegame/savegameV1";

import skipApartment from "./sg_skip_apartment.pdcase.json";
import skipOffice from "./sg_skip_office.pdcase.json";
import skipHotel from "./sg_skip_hotel_suite.pdcase.json";
import skipWarehouse from "./sg_skip_warehouse.pdcase.json";
import skipMansion from "./sg_skip_mansion.pdcase.json";
import nsMismatchOffice from "./sg_ns_mismatch_office.pdcase.json";
import composedApartment from "./sg_composed_apartment.pdcase.json";
import unsupported from "./sg_unsupported.pdcase.json";

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
  ["skip apartment", skipApartment],
  ["skip office", skipOffice],
  ["skip hotel_suite", skipHotel],
  ["skip warehouse", skipWarehouse],
  ["skip mansion", skipMansion],
  ["ns-mismatch office", nsMismatchOffice],
  ["composed apartment", composedApartment],
  ["unsupported planet_mars (LATENT)", unsupported],
];

let failures = 0;
for (const [label, doc] of cases) {
  const ok = report(label, doc);
  if (label.startsWith("unsupported")) {
    // expected to be REJECTED (latent, not production-reachable)
    if (ok) {
      console.log("  NOTE: unsupported-kit savegame unexpectedly parsed");
    }
  } else if (!ok) {
    failures += 1;
  }
}

console.log(`\nROUND-TRIP FAILURES (excluding the latent unsupported-kit case): ${failures}`);
