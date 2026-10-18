/**
 * PHASE 48 QA RETEST — wrapper that runs the REAL committed QA spec
 * `e2e/phase48-random-case.spec.ts` verbatim. It exists ONLY so the Playwright
 * collector's `testDir` can be this directory, avoiding the pre-existing
 * ACL-locked `e2e/artifacts/**` trees that make a `testDir: "."` walk raise
 * EPERM. No test logic is altered or weakened.
 */
import "../phase48-random-case.spec";
