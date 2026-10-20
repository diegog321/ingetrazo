# IngeTrazo experimental fork

This repository, `diegog321/ingetrazo`, is an **experimental fork** of
[ingelibre/ingetrazo](https://github.com/ingelibre/ingetrazo), the free
push/pull 3D modeller by Marco Sumari Tellez and contributors. It exists to
try features that are not (yet) in IngeTrazo itself. The first one planned is
PBR materials in the material editor: base colour, metallic, roughness,
normal, AO and emissive maps.

It is not an official IngeTrazo release. Report problems with the fork's own
changes here, not upstream.

## Branches

| Branch | What it holds | Who writes it |
|---|---|---|
| `main` | An exact mirror of upstream `main`. Never commit here. | The sync workflow, or GitHub's **Sync fork** button |
| `experimental` | Upstream plus this fork's changes. **Default branch.** All PRs target it. | Pull requests |
| feature branches | One per change, branched from `experimental` | You / Claude |

Keeping `main` pristine means `git diff main experimental` is always exactly
what the fork changes, and upstream updates come in as a plain fast-forward.

## Run it from source

Python 3.12+ on Linux, Windows or macOS. On Linux, PySide6 needs the usual Qt
system libraries (on Debian/Ubuntu: `libegl1 libgl1 libxkbcommon-x11-0
libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0`).

```bash
git clone -b experimental https://github.com/diegog321/ingetrazo.git
cd ingetrazo
scripts/fork/dev.sh setup     # creates ./venv and installs requirements.txt
scripts/fork/dev.sh run       # starts IngeTrazo
```

Other commands:

```bash
scripts/fork/dev.sh check     # main.py --check: what the install found / misses
scripts/fork/dev.sh test      # the fast test suite, batched exactly like CI
scripts/fork/dev.sh appimage  # build dist/IngeTrazo-<version>-x86_64.AppImage
scripts/fork/dev.sh sync      # merge upstream main into your current branch
```

Running from source next to an installed IngeTrazo is safe: the Flatpak keeps
its settings inside its own sandbox (`~/.var/app/com.ingetrazo.IngeTrazo`).
A non-Flatpak install (AppImage, Windows, macOS) shares the `IngeTrazo`
settings (language, preferences, recent files) with this copy.

## CI and builds

- **Every PR and every push to `experimental`** runs the fast test suite
  (`.github/workflows/ci.yml`, upstream's own gate).
- **A downloadable build** of any branch: Actions ▸ *release-linux* ▸
  *Run workflow* ▸ pick the branch. The AppImage and tarball appear as run
  artifacts (kept 7 days). The same works for *build-windows* and
  *release-macos*, unsigned.
- **Do not push `v*` tags.** Upstream's release workflows fire on them and
  would try to publish a release (the Flatpak and Windows signing steps
  fail without upstream's secrets anyway).

## Staying current with upstream

`.github/workflows/fork-sync-upstream.yml` runs every Monday (or by hand from
Actions):

1. It fast-forwards `main` from upstream.
2. If `main` is ahead of `experimental`, it opens a PR *main → experimental*.
   Merge it with a **merge commit**, not squash, so the next sync stays
   clean. If GitHub reports conflicts, run `scripts/fork/dev.sh sync` on a
   branch from `experimental`, resolve them, and open that as the PR instead.

Two one-time repository settings make it work:

- *Settings ▸ Actions ▸ General ▸ Workflow permissions*: tick **Allow GitHub
  Actions to create and approve pull requests** (for step 2).
- Optional, *Settings ▸ Secrets and variables ▸ Actions*: a `SYNC_TOKEN`
  secret holding a fine-grained personal access token for this repository
  with **Contents: write** and **Workflows: write**. GitHub refuses to let the
  built-in token sync commits that touch `.github/workflows/`. Without the
  secret, those weeks the job fails with a clear message and you press
  **Sync fork** on the repository page instead.

Keep the fork's changes easy to carry: put new code in new modules (for
example `core/pbr.py`, `views/pbr_panel.py`) and keep edits to big upstream
files such as `views/viewport.py` and `views/tray.py` small. That keeps
weekly merges painless.

## PBR materials (fork feature)

Materials tray ▸ **Surface (PBR)** edits the surface of the active named
material: a type (Generic, Metal, Glass, Water, Light), roughness, metallic,
normal or bump map, ambient occlusion and emission, each with a map slot,
and a live preview ball. **+ PBR set…** makes a material from the images of
a downloaded set (Poly Haven, ambientCG, …), assigning each file by its name.
Right-click a swatch ▸ **Edit surface (PBR)…** opens it for that material.

- Stored on the named material (`Material.surface`, `core/pbr.py`) in the
  `.igz`, maps embedded like any texture. Faces and the viewport are
  untouched: the viewport still shows base colour and texture.
- Exported to glTF/GLB with packed ORM, normal, occlusion and emissive maps
  (`core/pbr_gltf.py`), so **Render with Blender** gets the full material.
- Emission is in cd/m²; in Blender 683 cd/m² is an emission strength of 1,
  the same scale as Render with Blender's lights in watts.
- SKP, DAE and OBJ exports keep the base colour only.
- An upstream IngeTrazo build opens these files normally (base colour,
  textures and the finish survive) but drops the surfaces if it re-saves.

Every edit to an upstream file is marked `fork:pbr` (`git grep fork:pbr`),
and `tests/test_fork_pbr.py` checks each of those hooks is still in place
after a sync. The fork's own translations live in `i18n/fork/`.

## License

IngeTrazo is GPL-3.0-or-later, and so is this fork (see `LICENSE`). If you
distribute a build of it, you must also offer its complete source under the
same license. Mark files you change substantially, keep the existing
copyright headers, and keep the bundled assets' own notices
(`resources/*/SOURCES.md`, `vendor/`).
