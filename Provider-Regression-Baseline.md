# Procedural Detective – Provider & Regression Baseline

Status: 2026-10-16

## Goal

This baseline documents the currently successful generation paths after Phase 45.

It serves as a reference for future phases and as a fast regression check to determine whether changes:

- degrade cloud generation,
- degrade local Ollama generation,
- break individual difficulty levels,
- reduce playability reachability,
- or reintroduce model-specific failure modes.

---

## Current Reference Models

### Cloud / OpenRouter – Primary Reference

Model:

```text
google/gemini-3.8-flash
```

Status:

| Difficulty | Generation | Playability | Interactable Objects | Status |
|---|---|---|---|---|
| Easy | successful | successful | visible / clickable | PASS |
| Medium | successful | successful | visible / clickable | PASS |
| Hard | successful | successful | visible / clickable | PASS |

Assessment:

```text
PRIMARY CLOUD BASELINE
```

Gemini 3.8 Flash is currently the preferred cloud model for further development and regression testing.

---

## Local / Ollama Bridge – Local Reference

Provider:

```text
Local Ollama via Bridge
```

Status:

| Difficulty | Generation | Playability | Interactable Objects | Status |
|---|---|---|---|---|
| Easy | successful | successful | tested | PASS |
| Medium | successful | successful | tested | PASS |
| Hard | successful | successful | tested | PASS |

Assessment:

```text
PRIMARY LOCAL BASELINE
```

The local models running through the Ollama bridge are now a practically working generation path, not merely a fallback.

The latest development phases have visibly improved the robustness of generation with local AI models.

---

## Secondary Compatibility Test

Model:

```text
deepseek/deepseek-v4.1-flash
```

Status:

```text
CURRENTLY NOT RELIABLE
```

Known behavior:

- Generation can complete several stages successfully.
- Repair may remain ineffective.
- In observed tests, the repair budget was exhausted.
- DeepSeek is currently not a release or reference requirement.

DeepSeek should currently be treated only as a:

```text
SECONDARY COMPATIBILITY / STRESS TEST
```

A DeepSeek-only failure is not considered a blocker as long as Gemini and Ollama remain green.

---

# Regression Matrix

Before completing a major future phase, at least the following matrix should be executed:

| Provider | Easy | Medium | Hard | Required |
|---|---:|---:|---:|---|
| Gemini 3.8 Flash / OpenRouter | PASS | PASS | PASS | Yes |
| Local Ollama Bridge | PASS | PASS | PASS | Yes |
| DeepSeek V4.1 Flash | optional | optional | optional | No |

---

# Minimum Pass Criteria per Case

A case counts as PASS only if more than the API generation itself succeeds.

The following criteria must be satisfied:

- [ ] Generation completes successfully.
- [ ] The case is published / loaded.
- [ ] Playability Validation succeeds.
- [ ] No critical dimension is unreachable.
- [ ] WHO is reachable.
- [ ] WHY is reachable.
- [ ] WEAPON is reachable.
- [ ] WHEN is reachable, where required by the case contract.
- [ ] The Difficulty Contract is satisfied.
- [ ] Required red herrings are present.
- [ ] Meaningful Inspectables satisfy the Difficulty Contract.
- [ ] Distinct meaningful sources satisfy the Difficulty Contract.
- [ ] The scene renders correctly.
- [ ] Relevant objects are visible.
- [ ] Relevant objects are clickable.
- [ ] Investigation / inspection works.
- [ ] Witness / evidence flow works.
- [ ] Accuse / Reveal works.
- [ ] No late validation or publication failures occur.
- [ ] No infinite or ineffective repair loops occur.
- [ ] No obvious truth leaks occur.

---

# Phase 45 Invariant

The central invariant after Phase 45 is:

```text
SOLVER UNIQUE
+
PLAYER REACHABLE
+
DIFFICULTY VALID
=
PUBLISHABLE / PLAYABLE CASE
```

A case must not be published if:

```text
solverOutcome == unique
```

while critical solution dimensions remain unreachable to the player.

---

# Cloud-vs-Local Regression Rule

A new phase should be considered generation-stable when:

```text
Gemini Easy   = PASS
Gemini Medium = PASS
Gemini Hard   = PASS

AND

Ollama Easy   = PASS
Ollama Medium = PASS
Ollama Hard   = PASS
```

If only Gemini succeeds:

```text
CLOUD REGRESSION STATUS = PASS
LOCAL REGRESSION STATUS = FAIL
```

If only Ollama succeeds:

```text
LOCAL REGRESSION STATUS = PASS
CLOUD REGRESSION STATUS = FAIL
```

If both succeed:

```text
PROVIDER INDEPENDENCE BASELINE = PASS
```

---

# Recommended Use for Phase 46+

After any major change to:

- generation pipeline
- prompts
- structured output
- evidence
- evidence completion
- world graph
- kit composition
- playability
- scene composition
- inspectables
- repair / regeneration
- provider bridge
- savegame / restore

the baseline should be rerun.

---

# Compact Release Gate

For a fast manual check:

```text
[ ] Gemini Easy
[ ] Gemini Medium
[ ] Gemini Hard

[ ] Ollama Easy
[ ] Ollama Medium
[ ] Ollama Hard

[ ] Objects visible
[ ] Objects clickable
[ ] Evidence inspectable
[ ] All critical dimensions reachable
[ ] Accuse / Reveal works
[ ] No late validation failure
```

If all required items are satisfied:

```text
GENERATION REGRESSION BASELINE: PASS
```

---

# Current Status

```text
Cloud baseline:
google/gemini-3.8-flash
Easy / Medium / Hard = PASS

Local baseline:
Ollama via Bridge
Easy / Medium / Hard = PASS

Secondary compatibility:
deepseek/deepseek-v4.1-flash
currently not reliable / non-blocking
```

Overall assessment:

```text
PHASE 45 PROVIDER / PLAYABILITY BASELINE = GREEN
```
