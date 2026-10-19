/**
 * ADV-49 probe E: hostile `release-manifest.json` values against the website's
 * allow-list parser. Asserts no hostile value can become a non-repo download
 * href and that malformed manifests degrade to the unavailable state.
 */
import { describe, expect, it } from "vitest";
import {
  RELEASE_BASE_URL,
  parseReleaseManifest,
  releaseArtifactUrl,
} from "../../../frontend/src/release/releaseManifest";

const OK_FILE = "pd-ollama-bridge-v1.0.0-windows-x64.zip";

function downloadUrl(m: unknown): string | null {
  const p = parseReleaseManifest(m);
  if (p === null) return null;
  const a = p.artifacts.find((x) => x.platform === "windows" && x.arch === "x64");
  if (!a) return null;
  return releaseArtifactUrl(p.version, a.filename, RELEASE_BASE_URL);
}

describe("ADV-49 hostile manifest probes", () => {
  it("rejects an absurdly long version", () => {
    expect(parseReleaseManifest({ version: "1." + "0".repeat(5000) })).toBeNull();
  });

  it("rejects version path traversal / scheme / slash", () => {
    for (const v of [
      "1.0.0/../..",
      "1.0.0../../evil",
      "http://evil",
      "1.0.0\r\nx",
    ]) {
      expect(parseReleaseManifest({ version: v }), v).toBeNull();
    }
    // Observation, not a defect: `safeString` trims BEFORE the control-char
    // check, so a trailing newline is silently normalised to a valid version.
    expect(parseReleaseManifest({ version: "1.0.0\n" })?.version).toBe("1.0.0");
  });

  it("drops dangerous windows filenames (no slash/traversal/scheme/%2e)", () => {
    const cases = [
      "../../../../etc/passwd",
      "..\\..\\evil.exe",
      "http://evil.example/x.zip",
      "javascript:alert(1)",
      "data:text/html,<script>",
      "%2e%2e/evil.zip",
      "/abs/evil.zip",
      "C:\\evil.zip",
      "  evil.zip  evil  ",
    ];
    for (const filename of cases) {
      const parsed = parseReleaseManifest({
        version: "1.0.0",
        artifacts: [{ platform: "windows", arch: "x64", filename }],
      });
      expect(parsed?.artifacts.length ?? 0, filename).toBe(0);
    }
  });

  it("never emits a download href outside RELEASE_BASE_URL even for hostile input", () => {
    const hostile = {
      version: "1.0.0",
      artifacts: [
        { platform: "windows", arch: "x64", filename: OK_FILE },
        { platform: "windows", arch: "x64", filename: "javascript:alert(1)" },
        { platform: "windows", arch: "x64", filename: "//evil.example/x.zip" },
      ],
    };
    const url = downloadUrl(hostile);
    expect(url).toBe(`${RELEASE_BASE_URL}/v1.0.0/${OK_FILE}`);
    expect(url!.startsWith(RELEASE_BASE_URL)).toBe(true);
  });

  it("keeps an accepted sha256 field verbatim but it is never rendered as a URL", () => {
    const parsed = parseReleaseManifest({
      version: "1.0.0",
      artifacts: [
        { platform: "windows", arch: "x64", filename: OK_FILE, sha256: "javascript:alert(1)" },
      ],
    });
    // Parser accepts an arbitrary (non-hex) sha256 string; it is display-dead.
    expect(parsed?.artifacts[0].sha256).toBe("javascript:alert(1)");
  });

  it("ignores extra keys, wrong types, nested objects, oversized sizes", () => {
    const parsed = parseReleaseManifest({
      version: "1.0.0",
      evil: { nested: { deep: "x".repeat(100000) } },
      artifacts: [
        { platform: 123, arch: "x64", filename: OK_FILE },
        { platform: "windows", arch: ["x64"], filename: OK_FILE },
        { platform: "windows", arch: "x64", filename: OK_FILE, size: -1 },
        { platform: "windows", arch: "x64", filename: OK_FILE, size: 1e308 * 10 },
        [{ platform: "linux", arch: "x64", filename: "ok.tar.gz" }],
        null,
        "string",
      ],
    });
    expect(parsed).not.toBeNull();
    // Entries with a numeric-but-invalid size (-1, Infinity) keep the artifact
    // but null the size; wrong platform/arch/filename shapes are dropped.
    expect(parsed?.artifacts.length).toBe(2);
    expect(parsed?.artifacts.every((a) => a.size === null)).toBe(true);
  });

  it("duplicate platform entries: first valid wins", () => {
    const parsed = parseReleaseManifest({
      version: "1.0.0",
      artifacts: [
        { platform: "windows", arch: "x64", filename: "first.zip" },
        { platform: "windows", arch: "x64", filename: "second.zip" },
      ],
    });
    expect(parsed?.artifacts.find((a) => a.platform === "windows")?.filename).toBe("first.zip");
  });

  it("live status dump for the record", () => {
    // eslint-disable-next-line no-console
    console.log(
      JSON.stringify(
        {
          longVersion: parseReleaseManifest({ version: "1." + "0".repeat(5000) }),
          shaJavascriptKept: parseReleaseManifest({
            version: "1.0.0",
            artifacts: [{ platform: "windows", arch: "x64", filename: OK_FILE, sha256: "javascript:alert(1)" }],
          })?.artifacts[0].sha256,
        },
        null,
        2,
      ),
    );
    expect(true).toBe(true);
  });
});
