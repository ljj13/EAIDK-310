# ERRATUM — 6.18.55 candidate-1 diagnosis correction (P8.2)

Supersedes the verdict wording in earlier records (original evidence is
immutable and remains valid; only the INTERPRETATION changes).

## Corrected classification

| Field | Earlier wording | Corrected |
|---|---|---|
| CANDIDATE_1_KERNEL_BAD | YES (implied by "hard hang") | **NO** |
| CANDIDATE_1_TRIAL_VALID | FAIL_HARD_HANG | **INVALID** |
| CAUSE | (unattributed) | **OTA_INFRASTRUCTURE_MISCONFIGURATION** |
| CAUSE_DETAIL | — | **STALE_TRIAL_CONF_ROOT_UUID** (`bd2a6dbf…` rendered vs `781e1dc3…` actual) + FIRST_ARM_MISSING_CANDIDATE_CONF |
| ROOT_WAIT | — | TRUE (kernel sat in rootwait; loglevel=4 hid the wait) |
| WATCHDOG_CLASSIFICATION | INVALID | **INVALID** (count consumed by a stable-fallback boot; real candidate boot was rollback-classified ⇒ WDT not armed) |
| KERNEL_REGRESSION_EVIDENCE | — | **NONE** — the identical bundle later booted to userspace and passed both health gates (P8.1) |

## candidate-2 relationship

candidate-2 is the **same kernel bundle** (sha256
`869c410a08484fd8337ad7a5fa214d54ef807f4fd270a9f18c592397d0c43eea`) —
`SAME_KERNEL_BUNDLE=TRUE`.  What changed between the two trials is the
**trial configuration identity** (candidate conf content + its sha256, now
part of the candidate identity), produced by the P8.1/P8.2 contract fixes.
`TRIAL_CONF_FIXED=TRUE`, `BOARD_TRIAL=PASS`, `HEALTH=PASS`.
