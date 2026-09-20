import type { RevealResponse } from "../api/types";
import { asText } from "./revealFormat";

/**
 * Post-reveal proof board (Phase 18C).
 *
 * Pure, deterministic derivation of the four proof cards (WHO / WHY /
 * WEAPON / WHEN), each listing the supporting discovered-evidence nodes
 * (title + point). The server supplies EVERY word: this module NEVER
 * invents reasoning — it only re-groups the reveal DTO's own explanation
 * text.
 *
 * Two sources, in order of preference:
 *  1. `explanation.dimensions` — the backend's authoritative per-dimension
 *     grouping (always present from the current backend);
 *  2. flat-list fallback — when `dimensions` is absent (older server) or
 *     carries no recoverable point, the flat `explanation.evidence` list
 *     is grouped HEURISTICALLY by a deterministic keyword classifier.
 *     This is a degraded-mode fallback ONLY: it never crashes, every flat
 *     item lands in exactly one card, and the four cards always render.
 *
 * Safety: every value funnels through `asText` and renders as inert text;
 * no hidden truth, winner or `proc.*` token ever enters this module — the
 * reveal DTO itself is the only input.
 */

export type ProofDimensionId = "WHO" | "WHY" | "WEAPON" | "WHEN";

export const PROOF_DIMENSION_ORDER: readonly ProofDimensionId[] = ["WHO", "WHY", "WEAPON", "WHEN"];

/** One proof node inside a card (mirrors the server's point entries). */
export interface ProofNode {
  evidenceId: string;
  title: string;
  point: string;
}

export interface ProofCard {
  dimension: ProofDimensionId;
  label: string;
  nodes: ProofNode[];
}

export interface ProofBoardModel {
  cards: ProofCard[];
  /** True when the server-published `dimensions` block was used verbatim. */
  sourcedFromDimensions: boolean;
  /** True when the heuristic flat-list fallback grouping was used. */
  fallback: boolean;
}

const CARD_LABELS: Record<ProofDimensionId, string> = {
  WHO: "WHO",
  WHY: "WHY",
  WEAPON: "WEAPON",
  WHEN: "WHEN",
};

/**
 * Deterministic keyword classifier used ONLY by the flat-list fallback
 * (pre-18C servers). Fixed patterns + fixed priority order (WHO > WHY >
 * WEAPON > WHEN) so an item matching several dimensions lands in the same
 * card on every render. The priority reflects the case's own structure:
 * people-first evidence, then motive, weapon, and finally timing detail.
 */
const FALLBACK_PATTERNS: ReadonlyArray<{ dimension: ProofDimensionId; patterns: RegExp[] }> = [
  {
    dimension: "WHO",
    patterns: [/suspect/i, /murderer/i, /alibi/i, /witness/i, /victim/i, /fingerprint/i, /known to/i, /\bsaw\b/i, /\bperson\b/i],
  },
  {
    dimension: "WHY",
    patterns: [/motive/i, /money/i, /debt/i, /loan/i, /jealous/i, /revenge/i, /dispute/i, /argument/i, /scheme/i, /threat/i, /embezzle/i, /blackmail/i, /affair/i, /secret/i, /transfer/i, /owed/i],
  },
  {
    dimension: "WEAPON",
    patterns: [/weapon/i, /knife/i, /scissors/i, /opener/i, /blade/i, /\bgun\b/i, /trophy/i, /ice ?pick/i, /sharp/i, /traces/i, /\bmatch/i, /forensic/i, /wound/i, /blood/i],
  },
  {
    dimension: "WHEN",
    patterns: [/\d{1,2}:\d{2}/, /timestamp/i, /\btime\b/i, /evening/i, /night/i, /morning/i, /afternoon/i, /minute/i, /hour/i, /observed/i, /clock/i, /cctv/i, /camera/i, /entered/i, /arriv/i, /left the/i, /hurr/i],
  },
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function toProofNode(value: unknown): ProofNode | null {
  if (!isRecord(value)) return null;
  const evidenceId = asText(value.evidenceId);
  if (evidenceId === "") return null;
  return { evidenceId, title: asText(value.title), point: asText(value.point) };
}

function nodesOf(list: unknown): ProofNode[] {
  if (!Array.isArray(list)) return [];
  return dedupe(list.map(toProofNode).filter((node) => node !== null) as ProofNode[]);
}

/** Stable per-card dedupe (same evidence+title+point listed twice counts once). */
function dedupe(nodes: ProofNode[]): ProofNode[] {
  const seen = new Set<string>();
  const out: ProofNode[] = [];
  for (const node of nodes) {
    const key = `${node.evidenceId}\u0000${node.title}\u0000${node.point}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(node);
  }
  return out;
}

function cardsFrom(groups: Record<ProofDimensionId, ProofNode[]>): ProofCard[] {
  return PROOF_DIMENSION_ORDER.map((dimension) => ({
    dimension,
    label: CARD_LABELS[dimension],
    nodes: groups[dimension],
  }));
}

/** Deterministic overflow: zero-keyword items fill the smallest card first
 *  (ties broken by the fixed WHO/WHY/WEAPON/WHEN order). Purely a
 *  degraded-mode distribution — every item is still represented once. */
function assignOverflow(node: ProofNode, groups: Record<ProofDimensionId, ProofNode[]>): void {
  let target: ProofDimensionId = "WHO";
  let smallest = Number.MAX_SAFE_INTEGER;
  for (const dimension of PROOF_DIMENSION_ORDER) {
    if (groups[dimension].length < smallest) {
      target = dimension;
      smallest = groups[dimension].length;
    }
  }
  groups[target].push(node);
}

/** Purely deterministic heuristic grouping of the flat evidence list. */
function groupFlatHeuristically(flat: ProofNode[]): ProofBoardModel {
  const groups: Record<ProofDimensionId, ProofNode[]> = { WHO: [], WHY: [], WEAPON: [], WHEN: [] };
  for (const node of flat) {
    let best: ProofDimensionId | null = null;
    let bestScore = 0;
    for (const { dimension, patterns } of FALLBACK_PATTERNS) {
      let score = 0;
      for (const pattern of patterns) {
        if (pattern.test(`${node.title} ${node.point}`)) score += 1;
      }
      if (score > bestScore) {
        best = dimension;
        bestScore = score;
      }
    }
    if (best === null) {
      assignOverflow(node, groups);
    } else {
      groups[best].push(node);
    }
  }
  const deduped: Record<ProofDimensionId, ProofNode[]> = {
    WHO: dedupe(groups.WHO),
    WHY: dedupe(groups.WHY),
    WEAPON: dedupe(groups.WEAPON),
    WHEN: dedupe(groups.WHEN),
  };
  return { cards: cardsFrom(deduped), sourcedFromDimensions: false, fallback: true };
}

/**
 * Build the proof board from a validated reveal DTO. Never throws: an
 * absent or uninformative `dimensions` block simply triggers the fallback.
 */
export function buildProofBoard(reveal: RevealResponse): ProofBoardModel {
  const flat = nodesOf(reveal.explanation.evidence);

  const dimensions = reveal.explanation.dimensions;
  if (dimensions !== null && dimensions !== undefined) {
    const groups: Record<ProofDimensionId, ProofNode[]> = {
      WHO: nodesOf(dimensions.who),
      WHY: nodesOf(dimensions.why),
      WEAPON: nodesOf(dimensions.weapon),
      WHEN: nodesOf(dimensions.when),
    };
    const informative = groups.WHO.length > 0 || groups.WHY.length > 0 || groups.WEAPON.length > 0 || groups.WHEN.length > 0;
    // A present-and-informative server block wins; a present-but-empty block
    // with a non-empty flat list is treated as uninformative (older server),
    // so the fallback still shows every published point.
    if (informative || flat.length === 0) {
      return { cards: cardsFrom(groups), sourcedFromDimensions: true, fallback: false };
    }
  }

  return groupFlatHeuristically(flat);
}