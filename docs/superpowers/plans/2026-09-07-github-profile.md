# GitHub Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `ljj13` GitHub Profile README with an engineering-dashboard layout, selected project cards, GitHub activity widgets, a contribution snake, and a 3D contribution calendar.

**Architecture:** The profile is a static `README.md` backed by external SVG/image services for typing, skill icons, stats, and activity. Two GitHub Actions workflows generate repository-owned assets: one publishes snake SVGs to an `output` branch, while the other commits 3D contribution SVGs into the default branch.

**Tech Stack:** GitHub Markdown/HTML, GitHub Actions YAML, Platane/snk v3, yoshi389111/github-profile-3d-contrib, GitHub Readme Stats, GitHub Readme Activity Graph, Skill Icons, Readme Typing SVG.

**Spec:** `docs/superpowers/specs/2026-09-07-github-profile-design.md`

## Global Constraints

- GitHub username is exactly `ljj13`.
- Visual direction is Engineering Dashboard + Hardware Lab.
- Keep the page clean and compatible with GitHub light/dark themes.
- Do not include trophy walls, Spotify, profile-view counters, or excessive badge grids.
- Profile repository must be public and named exactly `ljj13/ljj13`.
- Workflows run daily and support `workflow_dispatch`.

---

### Task 1: Create Profile README

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: public repositories under `ljj13` and external widget endpoints.
- Produces: the complete GitHub profile page.

- [ ] **Step 1: Create the profile repository**

Create a public repository named exactly `ljj13` under GitHub account `ljj13`, initialize it with no generated template files or with a placeholder README that can be replaced.

- [ ] **Step 2: Add the complete README**

Create `README.md` containing the approved hero, About, Tech Stack, Project Lab, GitHub dashboard, contribution snake, and 3D contribution sections.

- [ ] **Step 3: Verify rendering**

Open `https://github.com/ljj13` and confirm the profile README is rendered automatically and all external image widgets load without horizontal overflow on desktop.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "feat: add engineering dashboard profile"
```

### Task 2: Add Contribution Snake Automation

**Files:**
- Create: `.github/workflows/snake.yml`

**Interfaces:**
- Consumes: GitHub contribution data for `ljj13`.
- Produces: `github-contribution-grid-snake.svg` and `github-contribution-grid-snake-dark.svg` on branch `output`.

- [ ] **Step 1: Add workflow permissions and triggers**

Configure `workflow_dispatch`, a daily cron schedule, and `contents: write`.

- [ ] **Step 2: Generate snake assets**

Use `Platane/snk/svg-only@v3` with `github_user_name: ljj13` and two output variants, including dark-mode palette support.

- [ ] **Step 3: Publish output branch**

Use `crazy-max/ghaction-github-pages@v4` with `build_dir: dist` and `GITHUB_TOKEN` to publish generated SVGs to the `output` branch.

- [ ] **Step 4: Verify workflow**

Run the workflow manually from Actions and verify the `output` branch contains both SVG files and the README displays them.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/snake.yml
git commit -m "ci: generate contribution snake"
```

### Task 3: Add 3D Contribution Automation

**Files:**
- Create: `.github/workflows/profile-3d.yml`

**Interfaces:**
- Consumes: GitHub contribution data for `ljj13`.
- Produces: generated SVG files under `profile-3d-contrib/` in the default branch.

- [ ] **Step 1: Add workflow triggers and write permission**

Configure `workflow_dispatch`, a daily cron schedule, and `contents: write`.

- [ ] **Step 2: Generate 3D contribution assets**

Use `actions/checkout@v5` and `yoshi389111/github-profile-3d-contrib@latest` with `USERNAME: ljj13`.

- [ ] **Step 3: Commit generated SVGs**

Configure git identity for GitHub Actions, add `profile-3d-contrib/`, and commit/push only when files changed.

- [ ] **Step 4: Verify workflow**

Run the workflow manually and confirm `profile-3d-contrib/profile-night-rainbow.svg` or another generated theme exists and renders in the README.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/profile-3d.yml
git commit -m "ci: generate 3d contribution calendar"
```

### Task 4: Final Rendering Verification

**Files:**
- Modify if needed: `README.md`

**Interfaces:**
- Consumes: completed README and generated assets.
- Produces: validated public profile.

- [ ] **Step 1: Check all images**

Confirm the typing SVG, skill icons, stats card, top languages card, activity graph, snake, and 3D contribution image all load.

- [ ] **Step 2: Check theme behavior**

Switch GitHub between light and dark appearance and confirm the contribution snake uses the appropriate variant and no text becomes unreadable.

- [ ] **Step 3: Check mobile-width layout**

Resize the browser to a narrow viewport and confirm paired cards wrap acceptably without clipping.

- [ ] **Step 4: Commit any final fixes**

```bash
git add README.md
git commit -m "fix: polish profile rendering"
```
