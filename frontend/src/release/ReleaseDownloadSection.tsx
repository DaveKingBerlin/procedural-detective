import { useEffect, useState } from "react";
import {
  RELEASE_ARTIFACT_MISSING_LABEL,
  RELEASE_BASE_URL,
  RELEASE_CHECKSUMS_LABEL,
  RELEASE_DOWNLOAD_ANCHOR_ID,
  RELEASE_HEADING,
  RELEASE_INTRO,
  RELEASE_LOADING_MESSAGE,
  RELEASE_PLATFORMS,
  RELEASE_QUICK_START_LABEL,
  RELEASE_RECOMMENDED_LABEL,
  RELEASE_REQUIRES_OLLAMA,
  RELEASE_SOURCE_LABEL,
  RELEASE_UNAVAILABLE_HINT,
  RELEASE_UNAVAILABLE_MESSAGE,
  RELEASE_VERSION_PREFIX,
  artifactMetadataLine,
  checksumsUrl,
  detectBrowserPlatform,
  downloadButtonLabel,
  findArtifact,
  loadReleaseManifest,
  platformLabel,
  quickStartUrl,
  releaseArtifactUrl,
  sourceCodeUrl,
  type ReleaseManifest,
  type ReleasePlatform,
} from "./releaseManifest";

/**
 * Phase 49 §22/§23/§24/§40/§49 — "Local AI with Ollama" download section.
 *
 * Renders the downloadable Ollama Bridge for BOTH required platforms from the
 * parsed release manifest. Design-system consistent (it uses the shared
 * landing-panel language via `.release-download` in src/index.css), not a
 * separate microsite.
 *
 * Guarantees:
 *   - the ONLY network call is the same-origin manifest fetch (via
 *     `loadManifest`, default {@link loadReleaseManifest}); artifact URLs are
 *     rendered as explicit anchors and are NEVER fetched or navigated to
 *     programmatically — the user must click (§23);
 *   - the version is displayed from the manifest, never hard-coded here;
 *   - OS detection (optional, injectable) only EMPHASISES the matching option;
 *     both platforms are always rendered and the alternative is never hidden;
 *   - loading / success / unavailable states are all explicit; a missing or
 *     malformed manifest shows the honest unavailable copy plus the Quick
 *     Start/Source links and never breaks the rest of the page (§39/§40).
 */
export interface ReleaseDownloadSectionProps {
  /** Manifest loader (tests inject a fake; defaults to the real fetch). */
  loadManifest?: () => Promise<ReleaseManifest | null>;
  /** Release base URL override (defaults to the canonical GitHub Releases path). */
  baseUrl?: string;
  /** OS-detection seam (defaults to a user-agent sniff; null = no emphasis). */
  detectPlatform?: () => ReleasePlatform | null;
}

type ReleaseView =
  | { status: "loading" }
  | { status: "ready"; manifest: ReleaseManifest }
  | { status: "unavailable" };

export default function ReleaseDownloadSection({
  loadManifest = loadReleaseManifest,
  baseUrl = RELEASE_BASE_URL,
  detectPlatform = detectBrowserPlatform,
}: ReleaseDownloadSectionProps = {}) {
  const [view, setView] = useState<ReleaseView>({ status: "loading" });
  // Detected once per mount; the value only ever adds emphasis.
  const [recommended] = useState<ReleasePlatform | null>(() => detectPlatform());

  useEffect(() => {
    let cancelled = false;
    void loadManifest()
      .then((manifest) => {
        if (cancelled) return;
        setView(manifest === null ? { status: "unavailable" } : { status: "ready", manifest });
      })
      .catch(() => {
        if (!cancelled) setView({ status: "unavailable" });
      });
    return () => {
      cancelled = true;
    };
  }, [loadManifest]);

  return (
    <section
      id={RELEASE_DOWNLOAD_ANCHOR_ID}
      className="release-download"
      data-testid="release-download-section"
      aria-labelledby="release-download-heading"
    >
      <h3 id="release-download-heading" className="release-download-heading">
        {RELEASE_HEADING}
      </h3>
      <p className="release-download-intro">{RELEASE_INTRO}</p>

      {view.status === "loading" && (
        <p className="release-download-loading" data-testid="release-download-loading" role="status">
          {RELEASE_LOADING_MESSAGE}
        </p>
      )}

      {view.status === "unavailable" && (
        <>
          <p
            className="release-download-unavailable"
            data-testid="release-download-unavailable"
            role="status"
          >
            {RELEASE_UNAVAILABLE_MESSAGE} {RELEASE_UNAVAILABLE_HINT}
          </p>
          <ReleaseLinks baseUrl={baseUrl} version={null} />
        </>
      )}

      {view.status === "ready" && (
        <ReadyRelease manifest={view.manifest} baseUrl={baseUrl} recommended={recommended} />
      )}
    </section>
  );
}

function ReadyRelease({
  manifest,
  baseUrl,
  recommended,
}: {
  manifest: ReleaseManifest;
  baseUrl: string;
  recommended: ReleasePlatform | null;
}) {
  return (
    <>
      <ul className="release-download-options" data-testid="release-download-options">
        {RELEASE_PLATFORMS.map((info) => {
          const artifact = findArtifact(manifest, info.platform, info.arch);
          const isRecommended = recommended === info.platform;
          return (
            <li
              key={info.platform}
              className={`release-download-option${
                isRecommended ? " release-download-option--recommended" : ""
              }`}
              data-testid={`release-download-option-${info.platform}`}
            >
              <span className="release-download-option-name">
                {platformLabel(info.platform)} {info.arch}
              </span>
              {artifact !== null ? (
                <>
                  <a
                    className="release-download-link"
                    data-testid={`release-download-${info.platform}`}
                    href={releaseArtifactUrl(manifest.version, artifact.filename, baseUrl)}
                  >
                    {downloadButtonLabel(info.platform, info.arch)}
                  </a>
                  <span
                    className="release-download-meta"
                    data-testid={`release-download-meta-${info.platform}`}
                  >
                    {artifactMetadataLine(artifact, manifest.version)}
                  </span>
                </>
              ) : (
                <span
                  className="release-download-missing"
                  data-testid={`release-download-missing-${info.platform}`}
                >
                  {RELEASE_ARTIFACT_MISSING_LABEL}
                </span>
              )}
              {isRecommended && (
                <span
                  className="release-download-badge"
                  data-testid={`release-download-recommended-${info.platform}`}
                >
                  {RELEASE_RECOMMENDED_LABEL}
                </span>
              )}
            </li>
          );
        })}
      </ul>

      <p className="release-download-version" data-testid="release-download-version">
        {RELEASE_VERSION_PREFIX}
        {manifest.version}
      </p>
      <p className="release-download-requires">{RELEASE_REQUIRES_OLLAMA}</p>
      <ReleaseLinks baseUrl={baseUrl} version={manifest.version} />
    </>
  );
}

function ReleaseLinks({
  baseUrl,
  version,
}: {
  baseUrl: string;
  version: string | null;
}) {
  return (
    <p className="release-download-links" data-testid="release-download-links">
      <a className="release-download-link" data-testid="release-quick-start" href={quickStartUrl(version)}>
        {RELEASE_QUICK_START_LABEL}
      </a>
      {version !== null && (
        <>
          <span className="release-download-sep" aria-hidden="true">
            {" · "}
          </span>
          <a
            className="release-download-link"
            data-testid="release-checksums"
            href={checksumsUrl(version, baseUrl)}
          >
            {RELEASE_CHECKSUMS_LABEL}
          </a>
        </>
      )}
      <span className="release-download-sep" aria-hidden="true">
        {" · "}
      </span>
      <a className="release-download-link" data-testid="release-source" href={sourceCodeUrl()}>
        {RELEASE_SOURCE_LABEL}
      </a>
    </p>
  );
}
