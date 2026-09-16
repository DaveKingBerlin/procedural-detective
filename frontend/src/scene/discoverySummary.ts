import type { InvestigationSceneModel } from "./buildInvestigationScene";
import type { InvestigationSession } from "./investigationFlow";

/**
 * Player-facing discovery summary (Phase 8 F).
 *
 * Pure, deterministic derivation of the "Discover evidence" objective text
 * and the discovered-evidence summary strip in the scene route. The source
 * of truth is always the server-derived PlayerKnowledge snapshot exposed by
 * the investigation session: the client NEVER marks evidence known on its
 * own. Titles come from the read-record cache when available, otherwise from
 * the world object's application-owned label (or the safe object id).
 */
export interface DiscoverySummaryEntry {
  evidenceId: string;
  title: string;
  read: boolean;
}

export interface DiscoverySummary {
  /** Sorted by evidence id (stable regardless of interaction order). */
  entries: DiscoverySummaryEntry[];
  discoveredCount: number;
  /** Number of world objects published with an evidence link (the in-scene total). */
  discoverableCount: number;
}

export function summarizeDiscovery(
  discoveredIds: readonly string[],
  readIds: readonly string[],
  worldObjects: ReadonlyArray<{ evidenceId: string | null; label: string | null; objectId: string }>,
  recordTitles: ReadonlyMap<string, string>,
): DiscoverySummary {
  const readSet = new Set(readIds);
  const entries = [...new Set(discoveredIds)].sort().map((evidenceId) => {
    const title =
      recordTitles.get(evidenceId) ??
      worldObjects.find((obj) => obj.evidenceId === evidenceId)?.label ??
      evidenceId;
    const read = readSet.has(evidenceId);
    return { evidenceId, title, read };
  });
  const discoverableCount = worldObjects.filter((obj) => obj.evidenceId !== null).length;
  return { entries, discoveredCount: entries.length, discoverableCount };
}

/**
 * The objective line: players know what to do now, and it updates as
 * evidence is discovered. Direct 3D interaction is the PRIMARY method, so the
 * copy says so — "Find evidence, then accuse someone." is deliberately kept
 * as the leading phrase (QA e2e pins it), followed by the scene-first hint.
 */
export function objectiveText(summary: DiscoverySummary, hasInteracted: boolean): string {
  if (!hasInteracted) {
    return "Find evidence, then accuse someone. Click objects in the 3D scene to inspect them — the object list below is an accessibility fallback.";
  }
  if (summary.discoverableCount > 0) {
    return `Discovered ${summary.discoveredCount} / ${summary.discoverableCount} evidence items — keep clicking objects in the scene to find more, then make your accusation when you are ready.`;
  }
  return `Discovered ${summary.discoveredCount} evidence items — keep clicking objects in the scene, then make your accusation when you are ready.`;
}

/** Pull the summary straight off a (possibly not-yet-started) session. */
export function summaryFromSession(
  session: InvestigationSession | null,
  model: InvestigationSceneModel,
): DiscoverySummary {
  if (session === null) {
    return summarizeDiscovery([], [], model.worldObjects, new Map());
  }
  return summarizeDiscovery(
    session.discoveredEvidenceIdsSnapshot(),
    session.readEvidenceIdsSnapshot(),
    model.worldObjects,
    session.discoveredRecordTitles(),
  );
}