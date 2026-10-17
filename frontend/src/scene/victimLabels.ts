/**
 * Phase 47 — frozen victim-profile label constants.
 *
 * Every literal the victim inspection surface shows lives here (mirroring the
 * `witness/witnessModel.ts` label-constants pattern) so labels stay
 * consistent and are never scattered across components. This is the ONLY
 * place a Phase 47 UI string is authored; the component renders these values
 * verbatim.
 */
export const VICTIM_LABELS = Object.freeze({
  /** Header fallback when the discovery title is absent. */
  fallbackTitle: "Victim",
  /** Separator between the "Victim" prefix and the discovery title. */
  titleSeparator: " — ",
  identityHeading: "Identity",
  nameLabel: "Name",
  genderLabel: "Gender",
  ageLabel: "Age",
  occupationLabel: "Occupation",
  discoveryHeading: "Discovery",
  foundAtLabel: "Found at",
  discoveredAtLabel: "Discovered at",
  knownFactsHeading: "Known facts",
  forensicHeading: "Forensic findings",
  timelineHeading: "Timeline",
  /** Neutral copy shown when no discovery block is published. */
  noDiscovery: "No confirmed discovery information yet.",
  /** Neutral copy shown when there are no victim-related read facts. */
  noKnownFacts: "No additional known facts yet.",
  /** Neutral copy shown before any victim-related forensic record is read. */
  noForensics: "No confirmed forensic findings yet.",
  /** Neutral copy shown before the discovery timeline record is read. */
  noTimeline: "No timeline recorded yet.",
  /** Accessible name of the panel dialog. */
  panelAriaLabel: "Victim profile",
  /** Close-button accessible label + visible text. */
  closeLabel: "Close victim record",
  closeText: "Close",
});

/** Human labels for the closed Phase 47 gender vocabulary. */
export const VICTIM_GENDER_LABELS: Readonly<Record<string, string>> = Object.freeze({
  male: "Male",
  female: "Female",
  other: "Other",
  unknown: "Unknown",
});

/** Human label for a closed gender token; unknown/malformed values -> null. */
export function victimGenderLabel(gender: string | null): string | null {
  if (gender === null) return null;
  return VICTIM_GENDER_LABELS[gender] ?? null;
}
