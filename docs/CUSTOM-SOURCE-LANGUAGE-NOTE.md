# Repository language statistics note

Observation only — recorded in P6.1 to document what GitHub's language bar
measures and what it deliberately does not. Nothing in the repository
structure was changed to influence these numbers.

## What GitHub Linguist counts

GitHub's language bar (linguist) counts **repository-owned, linguist-recognized
source bytes**: this repository's own scripts and sources as committed to git.
It does **not** count:

- the full upstream Linux/U-Boot source (pinned by `source-lock.json`,
  never vendored here — see `docs/CUSTOM-SOURCE-INVENTORY.md`);
- Markdown documentation (classified as prose);
- build configs, logs, patches, JSON metadata and other data formats.

This repository is a board bring-up / OTA-firmware project whose own code is
predominantly build- and verification tooling written in Python, plus shell
and PowerShell drivers for the lab workflows. That is an accurate picture of
what this repository contains.

## Byte totals (repository-owned, by extension)

| Extension | Before P6.1 | After P6.1 | Delta |
|---|---|---|---|
| Python (`.py`) | 589,284 | 622,149 | +32,865 (`tools/verify_custom_source.py`, `tests/test_verify_custom_source.py`) |
| Shell (`.sh`) | 220,026 | 220,026 | — |
| PowerShell (`.ps1`) | 48,008 | 48,008 | — |
| C (`.c`) | 0 | 15,121 | +15,121 (`custom-src/u-boot/.../bootcount_eaidk310_raw.c` mirror) |
| Device tree (`.dts`/`.dtsi`) | 285,666 | 296,342 + 5,535 | +16,211 (`custom-src` board DTS mirrors) |

The "after" C and device-tree bytes are the `custom-src/` mirrors of files
that already existed inside patches — presentation, not new code. Expected
linguist outcome: Python stays first, Shell second, PowerShell third, C
appears at roughly 1–2%. That is accepted: the language bar is a by-product,
not a goal.

## Policy

- No `linguist-generated` / `linguist-vendored` / `linguist-language`
  overrides were added; tools and tests are real, maintained project code.
- No upstream source was copied in to inflate any language's share.
- `custom-src/` exists to make project-owned sources reviewable; the
  source-lock + patch-stack build model is unchanged.
