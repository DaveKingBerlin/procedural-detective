/**
 * Phase 49 — release metadata: the SINGLE source of truth for the downloadable
 * Ollama Bridge on the website side.
 *
 * Why this module exists (Phase49.md §19/§21/§24/§39):
 *   - the release host, the repository, the manifest location and the derived
 *     download/checksum/quick-start URLs live HERE and nowhere else — the
 *     release literals are never scattered across components;
 *   - the fixed Phase 49 release fits the GitHub Releases strategy documented
 *     in the phase contract, where an immutable artifact is addressed as
 *     `…/releases/download/v<VERSION>/<filename>`;
 *   - the VERSION is never hard-coded by the website: it is read from the
 *     manifest (`version`), which the release tooling generates from the
 *     bridge's canonical version source.
 *
 * Canonical manifest schema (frozen; bathroom Phase49.md §19) — the release
 * tooling MUST keep producing exactly this shape:
 *
 *   {
 *     "version": "1.0.0",                 // required, semver-ish "MAJOR.MINOR.PATCH"
 *     "gitCommit": "abcdef123",           // optional (null allowed)
 *     "releasedAt": "2026-10-19T00:00:00Z",// optional (null allowed)
 *     "artifacts": [
 *       {
 *         "platform": "windows" | "linux",
 *         "arch": "x64",
 *         "filename": "pd-ollama-bridge-v<VERSION>-<platform>-<arch>.<ext>",
 *         "sha256": "<64 hex chars>",     // optional here only so a placeholder
 *         "size": 12345678                //   manifest may ship pre-publication
 *       }
 *     ]
 *   }
 *
 * A checked-in placeholder manifest is bundled at
 * `frontend/public/releases/release-manifest.json` (copied to
 * `/releases/release-manifest.json` by the Vite build) so the page works even
 * when the release host is unreachable or publication has not happened yet.
 * `sha256`/`size` may be null in that placeholder; the UI omits a missing size
 * and never fabricates metadata.
 *
 * Every value crossing from JSON into the UI is re-validated by
 * {@link parseReleaseManifest} (an allow-list parse): a hostile or malformed
 * manifest can never smuggle a filename with a path fragment, an unknown
 * platform or a huge string into a rendered link.
 */

/** The two Phase 49 platforms (Phase49.md §3). */
export type ReleasePlatform = "windows" | "linux";

/** One downloadable bridge artifact — a validated subset of §19. */
export interface ReleaseArtifact {
  platform: ReleasePlatform;
  /** Architecture token ("x64"). */
  arch: string;
  /** Archive filename ("pd-ollama-bridge-v1.0.0-windows-x64.zip"). */
  filename: string;
  /** Lowercase SHA-256 hex digest, or null when the placeholder omits it. */
  sha256: string | null;
  /** Archive size in bytes, or null when unknown. */
  size: number | null;
}

/** The parsed, validated release manifest (Phase49.md §19). */
export interface ReleaseManifest {
  /** The release version — the website's ONLY version source. */
  version: string;
  gitCommit: string | null;
  releasedAt: string | null;
  artifacts: ReleaseArtifact[];
}

/** One platform entry the download section always renders (§23: never hidden). */
export interface ReleasePlatformInfo {
  platform: ReleasePlatform;
  /** "Windows" / "Linux". */
  label: string;
  /** The Phase 49 architecture for this platform. */
  arch: string;
}

/* ======================================================================
 * Canonical configuration — the ONE place release URLs and filenames live.
 * ==================================================================== */

/**
 * The canonical public source repository (Phase49.md §21). This is also the
 * "Source code" link target. It is the only repository literal in the app.
 */
export const RELEASE_REPOSITORY_URL = "https://github.com/DaveKingBerlin/procedural-detective";

/**
 * The canonical GitHub Releases download base (Phase49.md §21). Immutable,
 * versioned artifacts are addressed as `${RELEASE_BASE_URL}/v<VERSION>/<file>`.
 * Publication is not authorized yet, so these URLs are placeholders until the
 * release host actually carries the assets — the UI degrades gracefully.
 */
export const RELEASE_BASE_URL = `${RELEASE_REPOSITORY_URL}/releases/download`;

/**
 * Same-origin static manifest consumed at runtime. Vite copies
 * `frontend/public/releases/release-manifest.json` to this path, so the page
 * works even when the release host is down (§39).
 */
export const RELEASE_MANIFEST_URL = "/releases/release-manifest.json";

/** The checksums asset filename inside a release (Phase49.md §5/§20). */
export const RELEASE_CHECKSUMS_FILENAME = "SHA256SUMS.txt";

/** The quick-start document inside a release (Phase49.md §5/§25). */
export const RELEASE_QUICKSTART_FILENAME = "QUICKSTART.md";

/** Anchor id of the download section, shared with the §50 help link. */
export const RELEASE_DOWNLOAD_ANCHOR_ID = "local-ai-ollama-download";

/** Both Phase 49 platform entries, in render order (Windows first). */
export const RELEASE_PLATFORMS: readonly ReleasePlatformInfo[] = [
  { platform: "windows", label: "Windows", arch: "x64" },
  { platform: "linux", label: "Linux", arch: "x64" },
];

/* ======================================================================
 * Frozen, app-authored copy (Phase49.md §22/§24/§40/§49/§50). No DTO/JSON
 * string ever replaces these.
 * ==================================================================== */

/** "Local AI with Ollama" — the download section heading. */
export const RELEASE_HEADING = "Local AI with Ollama";

/** One-line section intro. */
export const RELEASE_INTRO =
  "Use Procedural Detective with an AI model running on your own computer.";

/** The mandatory prerequisite line. */
export const RELEASE_REQUIRES_OLLAMA = "Requires Ollama.";

/** Prefix of the visible version line ("Version 1.0.0"). */
export const RELEASE_VERSION_PREFIX = "Version ";

/** Download button label prefix. */
export const RELEASE_DOWNLOAD_LABEL_PREFIX = "Download for ";

/** The three secondary links. */
export const RELEASE_QUICK_START_LABEL = "Quick Start";
export const RELEASE_CHECKSUMS_LABEL = "SHA-256 checksums";
export const RELEASE_SOURCE_LABEL = "Source code";

/** Emphasised OS-detection badge (never a hidden alternative — §23). */
export const RELEASE_RECOMMENDED_LABEL = "Recommended for your system";

/** Loading copy while the manifest is fetched. */
export const RELEASE_LOADING_MESSAGE = "Loading download options…";

/** Clear unavailable state copy (§40); the Quick Start/Source links stay. */
export const RELEASE_UNAVAILABLE_MESSAGE =
  "Download options are temporarily unavailable.";
export const RELEASE_UNAVAILABLE_HINT =
  "You can still read the Quick Start guide or view the source code.";

/** Shown for a platform the current manifest does not carry (graceful degrade). */
export const RELEASE_ARTIFACT_MISSING_LABEL = "Not available for this release";

/* ======================================================================
 * Pure helpers.
 * ==================================================================== */

/** Human platform label ("Windows"/"Linux"). */
export function platformLabel(platform: ReleasePlatform): string {
  return platform === "windows" ? "Windows" : "Linux";
}

/** The download link label: "Download for Windows x64". */
export function downloadButtonLabel(platform: ReleasePlatform, arch: string): string {
  return `${RELEASE_DOWNLOAD_LABEL_PREFIX}${platformLabel(platform)} ${arch}`;
}

/**
 * The immutable artifact URL. The version comes from the manifest, the
 * filename from the same manifest, the base from the canonical config — so
 * there is exactly one code path that can produce a download URL.
 */
export function releaseArtifactUrl(
  version: string,
  filename: string,
  baseUrl: string = RELEASE_BASE_URL,
): string {
  return `${baseUrl}/v${version}/${filename}`;
}

/** The release's checksums asset URL (Phase49.md §20). */
export function checksumsUrl(version: string, baseUrl: string = RELEASE_BASE_URL): string {
  return `${baseUrl}/v${version}/${RELEASE_CHECKSUMS_FILENAME}`;
}

/**
 * The Quick Start link. Inside a published release it points at the archive's
 * QUICKSTART.md; without a known version (manifest unavailable) it falls back
 * to the repository so the link is still useful (§40).
 */
export function quickStartUrl(version: string | null, baseUrl: string = RELEASE_BASE_URL): string {
  return version !== null
    ? `${baseUrl}/v${version}/${RELEASE_QUICKSTART_FILENAME}`
    : RELEASE_REPOSITORY_URL;
}

/** The canonical public source-code link. */
export function sourceCodeUrl(): string {
  return RELEASE_REPOSITORY_URL;
}

/** "ZIP" / "TAR.GZ" derived from the artifact filename (§24 archive type). */
export function archiveTypeLabel(filename: string): string {
  const lower = filename.toLowerCase();
  if (lower.endsWith(".tar.gz")) return "TAR.GZ";
  if (lower.endsWith(".tgz")) return "TGZ";
  if (lower.endsWith(".zip")) return "ZIP";
  if (lower.endsWith(".tar")) return "TAR";
  return "ARCHIVE";
}

/** "18.4 MB" / "512 KB" — decimal units, or null when the size is unknown. */
export function formatApproxSize(bytes: number | null): string | null {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes <= 0) return null;
  const megabytes = bytes / 1_000_000;
  if (megabytes >= 1) return `${megabytes.toFixed(1)} MB`;
  return `${Math.round(bytes / 1_000)} KB`;
}

/**
 * The one-line option metadata, e.g. `Windows x64 · v1.0.0 · ZIP · 18.4 MB`
 * (Phase49.md §24). The size segment is omitted when the manifest has none.
 */
export function artifactMetadataLine(artifact: ReleaseArtifact, version: string): string {
  const segments = [
    `${platformLabel(artifact.platform)} ${artifact.arch}`,
    `v${version}`,
    archiveTypeLabel(artifact.filename),
  ];
  const size = formatApproxSize(artifact.size);
  if (size !== null) segments.push(size);
  return segments.join(" · ");
}

/** The artifact for a platform/arch, or null when the manifest lacks it. */
export function findArtifact(
  manifest: ReleaseManifest | null,
  platform: ReleasePlatform,
  arch: string = "x64",
): ReleaseArtifact | null {
  if (manifest === null) return null;
  return (
    manifest.artifacts.find(
      (artifact) => artifact.platform === platform && artifact.arch === arch,
    ) ?? null
  );
}

/* ======================================================================
 * Trust-boundary parse (allow-list).
 * ==================================================================== */

/** "MAJOR.MINOR.PATCH" with an optional pre-release/build suffix. */
const VERSION_PATTERN = /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/;
/** A safe archive filename: no slashes, no traversal, no scheme. */
const FILENAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
/** A short architecture token. */
const ARCH_PATTERN = /^[A-Za-z0-9_-]{1,16}$/;
/**
 * A SHA-256 digest: EXACTLY 64 hexadecimal characters (case-insensitive).
 * The field is advertised as a checksum, so anything else (a scheme,
 * `javascript:`, markup, an overlong/undersized blob) is dropped rather than
 * accepted verbatim (ADV-49-010).
 */
const SHA256_PATTERN = /^[0-9a-fA-F]{64}$/;

/** Trim + bound an untrusted string; reject control characters/newlines. */
function safeString(value: unknown, maxLength: number): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (trimmed === "" || trimmed.length > maxLength) return null;
  if (/[\r\n\u0000-\u001f]/.test(trimmed)) return null;
  return trimmed;
}

/**
 * Validate a SHA-256 field: EXACTLY 64 hex characters (case-insensitive),
 * normalised to lowercase. Anything else — a scheme, markup, an overlong or
 * undersized string — is dropped to null rather than smuggled into the typed
 * artifact (ADV-49-010).
 */
function safeSha256(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return SHA256_PATTERN.test(trimmed) ? trimmed.toLowerCase() : null;
}

/**
 * Parse an untrusted manifest JSON value into a {@link ReleaseManifest}, or
 * null when it is unusable. Unknown fields are dropped; unknown platforms,
 * unsafe filenames, malformed versions and non-hex `sha256` values are
 * dropped, and a manifest with no valid version resolves to null (the caller
 * renders the unavailable state).
 */
export function parseReleaseManifest(raw: unknown): ReleaseManifest | null {
  if (typeof raw !== "object" || raw === null) return null;
  const candidate = raw as Record<string, unknown>;

  const version = safeString(candidate.version, 64);
  if (version === null || !VERSION_PATTERN.test(version)) return null;

  const artifacts: ReleaseArtifact[] = [];
  const rawArtifacts = candidate.artifacts;
  if (Array.isArray(rawArtifacts)) {
    for (const entry of rawArtifacts) {
      if (typeof entry !== "object" || entry === null) continue;
      const artifact = entry as Record<string, unknown>;

      const platform = artifact.platform;
      if (platform !== "windows" && platform !== "linux") continue;

      const arch = safeString(artifact.arch, 16);
      if (arch === null || !ARCH_PATTERN.test(arch)) continue;

      const filename = safeString(artifact.filename, 200);
      if (
        filename === null ||
        !FILENAME_PATTERN.test(filename) ||
        filename.includes("..")
      ) {
        continue;
      }

      const sizeRaw = artifact.size;
      const size =
        typeof sizeRaw === "number" && Number.isFinite(sizeRaw) && sizeRaw >= 0
          ? sizeRaw
          : null;

      artifacts.push({
        platform,
        arch,
        filename,
        sha256: safeSha256(artifact.sha256),
        size,
      });
    }
  }

  return {
    version,
    gitCommit: safeString(candidate.gitCommit, 80),
    releasedAt: safeString(candidate.releasedAt, 64),
    artifacts,
  };
}

/**
 * Bounded manifest-fetch timeout (ADV-49-008). A same-origin request that
 * STALLS (rather than failing or 404ing) must not leave the download section
 * in the loading state forever: after this bound the fetch is aborted and the
 * caller renders the honest unavailable state (§39/§40). Kept short — the
 * manifest is a tiny same-origin static file, not a generation request.
 */
export const RELEASE_MANIFEST_TIMEOUT_MS = 5000;

/**
 * Fetch + parse the bundled same-origin manifest. NEVER throws: a missing
 * file, a non-2xx response, a network failure, a malformed body OR a hung
 * request (bounded by {@link RELEASE_MANIFEST_TIMEOUT_MS}) all resolve to null
 * so the caller can render the honest unavailable state (§40).
 *
 * The `fetchImpl`/`url`/`timeoutMs` parameters exist for tests; production
 * callers use the defaults (the global fetch, {@link RELEASE_MANIFEST_URL} and
 * {@link RELEASE_MANIFEST_TIMEOUT_MS}). This is the ONLY network call this
 * module performs — artifact URLs are never fetched here; they are handed to
 * the browser as explicit anchors.
 */
export function loadReleaseManifest(
  fetchImpl?: typeof fetch,
  url: string = RELEASE_MANIFEST_URL,
  timeoutMs: number = RELEASE_MANIFEST_TIMEOUT_MS,
): Promise<ReleaseManifest | null> {
  const impl: typeof fetch | null =
    fetchImpl ?? (typeof fetch === "function" ? fetch : null);
  if (impl === null) return Promise.resolve(null);

  // A bounded abort so a hanging response can never block the section
  // indefinitely; when the runtime has no AbortController we still fall back
  // to the request promise (a stalled fetch then resolves to null when the
  // host eventually errors), so the function NEVER hangs on the abort itself.
  const controller =
    typeof AbortController === "function" ? new AbortController() : null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const request = impl(url, {
    headers: { Accept: "application/json" },
    ...(controller !== null ? { signal: controller.signal } : {}),
  })
    .then((response) => {
      if (!response.ok) return null;
      return response.json() as Promise<unknown>;
    })
    .then((raw) => (raw === null ? null : parseReleaseManifest(raw)))
    .catch(() => null);

  if (controller === null) return request;

  const timedOut = new Promise<ReleaseManifest | null>((resolve) => {
    timer = setTimeout(() => {
      controller.abort();
      resolve(null);
    }, timeoutMs);
  });

  return Promise.race([request, timedOut]).finally(() => {
    if (timer !== null) clearTimeout(timer);
  });
}

/**
 * Detect the browser's likely platform for EMPHASIS ONLY (Phase49.md §23).
 * The result may only highlight the matching download; it never hides the
 * other platform and never triggers a download. Returns null when unknown.
 */
export function detectBrowserPlatform(): ReleasePlatform | null {
  if (typeof navigator === "undefined") return null;
  const ua = String(navigator.userAgent ?? "");
  const platform = String(navigator.platform ?? "");
  const haystack = `${ua} ${platform}`.toLowerCase();
  if (haystack.includes("win")) return "windows";
  if (haystack.includes("linux") && !haystack.includes("android")) return "linux";
  return null;
}
