import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { EvidenceReadResultDTO } from "../api/types";
import { evidenceContent, handleEvidencePanelKey } from "./evidenceContent";
import EvidencePanel from "./evidencePanel";
import { makeEmailRecord, makeHostileRecord } from "../scene/testFixtures";

/**
 * Evidence panel coverage (Phase 6 P):
 *  - every kind renders the right presentation rows (pure, no DOM);
 *  - Escape closes (pure key handler — no jsdom needed);
 *  - generated/HTML-looking/Unicode strings render INERT: the headless
 *    react-dom/server renderer produces literal text with no <script> child.
 */

function recordWithKind(kind: string, content: Record<string, unknown>): EvidenceReadResultDTO {
  return {
    evidenceId: "ev-1",
    kind,
    title: `Title for ${kind}`,
    description: `Description for ${kind}`,
    openedAt: "2026-09-11T22:20:00+02:00",
    readByPlayer: true,
    content,
  };
}

describe("evidenceContent — kind viewers (each kind renders)", () => {
  it("renders object evidence (subtype + location)", () => {
    const model = evidenceContent(recordWithKind("object", { subtype: "sharp_weapon", locationId: "kitchen" }));
    const labels = model.rows.filter((r) => r.type === "label-value").map((r) => r.label);
    expect(labels).toContain("Object type");
    expect(labels).toContain("Location");
  });

  it("renders email evidence (from/to/subject/timestamp/body)", () => {
    const model = evidenceContent(
      recordWithKind("email", {
        fromPersonId: "sarah_miller",
        toPersonIds: ["thomas_reed", "michael_cole"],
        subject: "Weekend plans",
        body: "Hello",
        timestamp: "2026-09-10T18:04:00+02:00",
      }),
    );
    const labels = model.rows.filter((r) => r.type === "label-value").map((r) => r.label);
    for (const expected of ["From", "To", "Subject", "Timestamp", "Body"]) {
      expect(labels).toContain(expected);
    }
    const toRow = model.rows.find((r) => r.type === "label-value" && r.label === "To");
    expect(toRow).toEqual({ type: "label-value", label: "To", value: "thomas_reed, michael_cole" });
  });

  it("renders financial evidence as a readable table plus suspicious flag", () => {
    const model = evidenceContent(
      recordWithKind("financial", {
        suspicious: true,
        rows: [
          { date: "2026-08-01", from: "corp", to: "thomas_reed", amount: 240000, currency: "EUR", description: "transfer" },
          { date: "2026-08-02", from: "a", to: "b", amount: 42.5, currency: "USD", description: "x" },
        ],
      }),
    );
    expect(model.rows.some((r) => r.type === "label-value" && r.label === "Suspicious" && r.value === "Yes")).toBe(true);
    const table = model.rows.find((r) => r.type === "table");
    expect(table).not.toBeUndefined();
    if (table && table.type === "table") {
      expect(table.headers).toEqual(["Date", "From", "To", "Amount", "Description"]);
      expect(table.rows[0][3]).toBe("EUR 240000");
      expect(table.rows).toHaveLength(2);
    }
  });

  it("renders cctv / cctv_observation / view_record as a camera line plus event list", () => {
    for (const kind of ["cctv", "cctv_observation", "view_record"]) {
      const model = evidenceContent(
        recordWithKind(kind, {
          cameraId: "CAM-01",
          events: [
            { time: "22:16:40", personId: "thomas_reed", action: "entered kitchen" },
            { time: "22:17:02", personId: "thomas_reed", action: "left kitchen" },
          ],
        }),
      );
      expect(model.rows.some((r) => r.type === "label-value" && r.value === "CAM-01")).toBe(true);
      const list = model.rows.find((r) => r.type === "list");
      expect(list).not.toBeUndefined();
      if (list && list.type === "list") {
        expect(list.items[0]).toBe("22:16:40 — thomas_reed: entered kitchen");
        expect(list.items).toHaveLength(2);
      }
    }
  });

  it("renders testimonial / witness_statement / statement / suspect_statement (speaker + statement)", () => {
    for (const kind of ["testimonial", "witness_statement", "statement", "suspect_statement"]) {
      const model = evidenceContent(recordWithKind(kind, { speakerName: "Neighbour", statement: "I heard a noise." }));
      const labels = model.rows.filter((r) => r.type === "label-value").map((r) => r.label);
      expect(labels).toContain("Speaker");
      expect(labels).toContain("Statement");
    }
  });

  it("renders the default kind as title + description only", () => {
    const model = evidenceContent(recordWithKind("forensic", { weaponId: "kitchen_knife" }));
    expect(model.title).toBe("Title for forensic");
    expect(model.description).toBe("Description for forensic");
    expect(model.rows).toEqual([]);
  });

  it("tolerates non-object / hostile content without throwing or echoing structure", () => {
    const model = evidenceContent(recordWithKind("email", "not-an-object" as unknown as Record<string, unknown>));
    expect(model.rows).toEqual([]);
    expect(model.title).toBe("Title for email");
  });
});

describe("Escape closes the evidence panel", () => {
  it("maps Escape to close and ignores other keys", () => {
    expect(handleEvidencePanelKey("Escape")).toBe("close");
    expect(handleEvidencePanelKey("ArrowUp")).toBeNull();
    expect(handleEvidencePanelKey("Enter")).toBeNull();
    expect(handleEvidencePanelKey("")).toBeNull();
  });
});

describe("generated text is escaped/inert", () => {
  it("renders HTML-looking strings as literal text with no <script> child", () => {
    const html = renderToStaticMarkup(
      <EvidencePanel record={makeHostileRecord()} onClose={() => {}} />,
    );

    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<img");
    // Unicode + emoji survive as inert text
    expect(html).toContain("ひらがな");
    expect(html).toContain("你好");
    expect(html).toContain("😀");
  });

  it("renders a financial table and email fields with React-escaped text", () => {
    const record = makeEmailRecord({
      content: {
        fromPersonId: "sarah_miller",
        toPersonIds: ["<b>thomas</b>"],
        subject: "a <script>alert(1)</script> email",
        body: "body",
        timestamp: "t",
      },
    });
    const html = renderToStaticMarkup(<EvidencePanel record={record} onClose={() => {}} />);
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).toContain("&lt;b&gt;thomas&lt;/b&gt;");
    expect(html).not.toContain("<b>thomas</b>");
  });

  it("puts the literal long text in the DOM (no truncation, no structure loss)", () => {
    const longDescription = `LONG-${"x".repeat(900)}-END`;
    const html = renderToStaticMarkup(
      <EvidencePanel
        record={makeEmailRecord({ description: longDescription })}
        onClose={() => {}}
      />,
    );
    expect(html).toContain(longDescription);
  });
});

describe("EvidencePanel markup", () => {
  it("exposes panel test id, a close button and visible focus surface", () => {
    const html = renderToStaticMarkup(
      <EvidencePanel record={makeEmailRecord()} onClose={() => {}} />,
    );
    expect(html).toContain('data-testid="evidence-panel"');
    expect(html).toContain('data-testid="evidence-close"');
    expect(html).toContain('role="dialog"');
    expect(html).toContain('aria-label="Evidence: Weekend plans"');
  });

  it("shows the object label + evidence title together when opened from an object (Phase 8_1 D1)", () => {
    const html = renderToStaticMarkup(
      <EvidencePanel
        record={makeEmailRecord()}
        onClose={() => {}}
        objectLabel="Kitchen knife"
      />,
    );
    expect(html).toContain("Kitchen knife — Weekend plans");
    expect(html).toContain('<h3 class="evidence-panel-title">Kitchen knife — Weekend plans</h3>');
  });

  it("renders the small-evidence preview swatch with the PUBLIC registry color + label (Phase 8_1 D2)", () => {
    const html = renderToStaticMarkup(
      <EvidencePanel
        record={makeEmailRecord()}
        onClose={() => {}}
        objectLabel="Kitchen knife"
        preview={{ objectId: "kitchen_knife", label: "Kitchen knife", color: "#c8ccd4" }}
      />,
    );
    expect(html).toContain('data-testid="evidence-preview"');
    expect(html).toContain('data-testid="evidence-preview-swatch"');
    expect(html).toContain('style="background-color:#c8ccd4"');
    expect(html).toContain("Kitchen knife");
    // No image loading, no remote content anywhere in the preview.
    expect(html).not.toContain("<img");
    expect(html).not.toContain("http");
  });

  it("keeps hostile object labels and preview labels INERT (escaped, no truth)", () => {
    const html = renderToStaticMarkup(
      <EvidencePanel
        record={makeHostileRecord()}
        onClose={() => {}}
        objectLabel="<script>alert(1)</script>"
        preview={{ objectId: "hostile", label: "<img src=x onerror=alert(1)>", color: "#8d8d93" }}
      />,
    );
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("murderer");
  });
});