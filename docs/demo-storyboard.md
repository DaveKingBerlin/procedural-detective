# Procedural Detective — Demo Video Storyboard (< 3 minutes)

Target total: **~2:55**, comfortably below the 3-minute submission limit.

---

## 0:00–0:15 — Problem / concept

> "AI can generate worlds. But can those worlds remain logically consistent?"

- Landing page on screen: *Procedural Detective — describe a crime, AI builds a
  logically solvable 3D investigation.*
- One-sentence premise over the static page.

---

## 0:15–0:35 — The prompt

- Click into the generation screen.
- Type the natural-language murder-mystery prompt, for example:

  ```text
  Victim: Sarah Miller
  Murderer: Thomas Reed
  Motive: €240,000 embezzlement
  Weapon: Kitchen knife
  Time: 22:17
  Witness: Emily Reed
  ```

- Hit **Generate**.

---

## 0:35–0:50 — Generation / validation

- Generation progress screen: *Creating case → Building world → Generating
  evidence → Checking consistency → Preparing investigation*.
- Emphasize that the case passes **deterministic validation** — the world is
  guaranteed to contain a solvable truth, not just pretty prose.

---

## 0:50–1:35 — 3D apartment investigation

- Enter the 3D Babylon.js scene.
- Interact with:
  - the **knife** (physical evidence, discovered),
  - the **laptop** (digital evidence: the embezzlement email),
  - the **email** record (financial motive).
- Show evidence being collected and knowledge updating in the panel.

---

## 1:35–2:05 — The accusation

- Open the accusation screen.
- Fill in WHO / WHY / WEAPON / WHEN.
- Submit the case.

---

## 2:05–2:25 — Truth reveal

- The reveal screen shows the canonical truth: WHO, WHY, WEAPON, WHEN,
  correct/incorrect per dimension, and overall **CASE SOLVED / NOT SOLVED**.
- The player's accusation is matched against the immutable server-side truth.

---

## 2:25–2:45 — Architecture (very brief)

- One clean frame:

  ```text
  Prompt → CaseTruth → Evidence/World generation
        → deterministic validation → 3D investigation → reveal
  ```

- One line: "AI generation + deterministic, truth-aware validation — the same
  structured evidence the player sees is what the solver proves the case with."

---

## 2:45–2:55 — Final message

> "Procedural Detective — every generated mystery has a real answer."

- Logo / link overlay.