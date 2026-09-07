# GitHub Profile Design

## Goal
Create a clean GitHub Profile README for `ljj13` using an Engineering Dashboard visual language with Hardware Lab information architecture.

## Structure
1. Hero: bilingual identity and typing animation.
2. About: Shenzhen University, electronic information engineering, embedded/Linux/hardware focus.
3. Tech Stack: C, C++, Python, embedded/system/toolchain icons.
4. Project Lab: selected public repositories.
5. GitHub dashboard: stats, top languages, activity graph.
6. Contribution Snake: generated daily by GitHub Actions.
7. 3D Contribution Calendar: generated daily by GitHub Actions.

## Visual direction
- GitHub-native dark/light compatible appearance.
- Cyan/blue accents with restrained green contribution visuals.
- No trophy wall, Spotify, profile-view counters, or excessive badge grids.
- Prefer transparent/stateless widgets and generated SVG assets.

## Automation
- `.github/workflows/snake.yml` uses `Platane/snk@v3`, publishes generated SVG assets to an `output` branch.
- `.github/workflows/profile-3d.yml` uses `yoshi389111/github-profile-3d-contrib@latest`, commits generated assets into `profile-3d-contrib/`.
- Both workflows run daily and support manual dispatch.

## Deliverables
- `README.md`
- `.github/workflows/snake.yml`
- `.github/workflows/profile-3d.yml`
- setup notes describing first-run steps.
