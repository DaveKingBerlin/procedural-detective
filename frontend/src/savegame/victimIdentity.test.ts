import { describe, expect, it } from "vitest";
import canonical from "./fixtures/v1_demo_apartment.pdcase.json";
import { reExportV1, serializeSavegameV1 } from "./exportV1";
import { SavegameParseError, parseSavegameV1, utf8ByteLength } from "./savegameV1";

/**
 * Phase 47 — savegame person identity compatibility.
 *
 * The optional public identity fields (age/gender/occupation/background) are
 * ADDITIVE: legacy fixtures without them still parse byte-identically, a save
 * carrying them round-trips, and malformed values fail closed.
 */

const CANONICAL_TEXT: string = JSON.stringify(canonical);

function parse(text: string): ReturnType<typeof parseSavegameV1> {
  return parseSavegameV1(text, utf8ByteLength(text));
}

function withPersonIdentity(fields: Record<string, unknown>): string {
  const doc = JSON.parse(CANONICAL_TEXT);
  Object.assign(doc.case.publicCase.persons[0], fields);
  return JSON.stringify(doc);
}

describe("Phase 47 — legacy fixtures without identity", () => {
  it("still parse and keep the identity keys absent (byte-identical re-export)", () => {
    const definition = parse(CANONICAL_TEXT);
    const person = definition.publicCase.persons[0];
    expect(person.personId).toBe("sarah_miller");
    expect(person.age).toBeUndefined();
    expect(person.gender).toBeUndefined();
    expect(person.occupation).toBeUndefined();
    expect(person.background).toBeUndefined();

    const reExported = reExportV1(definition, "2099-01-01T00:00:00Z");
    expect("age" in reExported.case.publicCase.persons[0]).toBe(false);
    expect("gender" in reExported.case.publicCase.persons[0]).toBe(false);
    // The re-export is accepted again (validator drift guard).
    expect(() => parse(serializeSavegameV1(reExported))).not.toThrow();
  });
});

describe("Phase 47 — a save carrying identity round-trips", () => {
  const TEXT = withPersonIdentity({
    age: 47,
    gender: "female",
    occupation: "Historian",
    background: "Public background note.",
  });

  it("parses the optional identity fields", () => {
    const definition = parse(TEXT);
    expect(definition.publicCase.persons[0]).toMatchObject({
      age: 47,
      gender: "female",
      occupation: "Historian",
      background: "Public background note.",
    });
  });

  it("survives export -> serialize -> parse (round-trip)", () => {
    const definition = parse(TEXT);
    const exported = serializeSavegameV1(reExportV1(definition, "2099-01-01T00:00:00Z"));
    const reloaded = parse(exported);
    expect(reloaded.publicCase.persons[0]).toMatchObject({
      age: 47,
      gender: "female",
      occupation: "Historian",
      background: "Public background note.",
    });
  });

  it("accepts an explicit null identity value", () => {
    const definition = parse(withPersonIdentity({ age: null, gender: null, occupation: null }));
    expect(definition.publicCase.persons[0].age).toBeNull();
    expect(definition.publicCase.persons[0].gender).toBeNull();
    expect(definition.publicCase.persons[0].occupation).toBeNull();
  });
});

describe("Phase 47 — malformed identity values fail closed", () => {
  it("rejects a non-integer / out-of-range age", () => {
    expect(() => parse(withPersonIdentity({ age: "47" }))).toThrow(SavegameParseError);
    expect(() => parse(withPersonIdentity({ age: 1.5 }))).toThrow(SavegameParseError);
    expect(() => parse(withPersonIdentity({ age: -1 }))).toThrow(SavegameParseError);
    expect(() => parse(withPersonIdentity({ age: 999 }))).toThrow(SavegameParseError);
  });

  it("rejects an empty-string gender/occupation", () => {
    expect(() => parse(withPersonIdentity({ gender: "" }))).toThrow(SavegameParseError);
    expect(() => parse(withPersonIdentity({ occupation: "" }))).toThrow(SavegameParseError);
  });

  it("rejects an unknown person key", () => {
    expect(() => parse(withPersonIdentity({ nickname: "S" }))).toThrow(SavegameParseError);
  });
});
