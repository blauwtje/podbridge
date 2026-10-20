# Self-update: every new build reaches the PC on its own

## Goal
When the user opens podbridge on the Windows PC and GitHub has a newer build, podbridge downloads it, installs it silently and starts again, with no typing and no clicks.

## Decisions
1. podbridge updates itself at the start of `run`: it checks GitHub, downloads the setup, installs it silently and starts again, with no user clicks; closed by the user.
2. Every build counts: the newest release of any kind, a `build-<n>` prerelease or a `v*` tagged release, is the update target; closed by the user.
3. Windows restarts only when VB-CABLE is missing, unchanged; the update runs setup with `/NORESTART`, so an update never restarts Windows; closed by the user.
4. Own build: CI writes the release tag (`build-<n>` or the `v*` tag) as one line to `dist/podbridge/build.txt`; the installer copies it to `{app}\build.txt`; the app reads it from `paths.install_dir()`; closed by exo.
5. No build number means no update: a source run (no `sys.frozen`), a missing or empty `build.txt`, the env var `PODBRIDGE_NO_UPDATE` set to any non-empty value, or `run --no-update` each skip the check before any network call, so tests and dev runs never update; closed by exo.
6. Newest build: one unauthenticated `GET https://api.github.com/repos/blauwtje/podbridge/releases?per_page=20` with `User-Agent: podbridge` and `Accept: application/vnd.github+json`; the repo is the constant `blauwtje/podbridge`, taken from `origin`; among non-draft releases whose `tag_name` matches `^(build-[0-9]+|v[0-9A-Za-z.-]+)$` and that carry a `podbridge-setup.exe` asset, the one with the latest `published_at` wins, because the docs give no list order; closed by exo.
7. Update when the newest tag differs from the own build; the same tag is never tried twice: `update/attempted.txt` holds the last tag started, and a newest tag equal to it prints one line and starts normally, so a broken `build.txt` cannot loop; closed by exo.
8. Integrity: the asset's `digest` from the API must read `sha256:<64 hex>`, its `browser_download_url` must start with `https://github.com/blauwtje/podbridge/releases/download/`, the final URL after redirects must be `https`, and the SHA256 of the downloaded bytes must equal the digest; any miss deletes the file, prints one line and starts normally; GitHub computes the digest itself, so CI publishes no checksum file; closed by exo.
9. The setup is written to `{app}\update\podbridge-setup.exe` under Program Files, a folder only admins can write, never to `%APPDATA%`, because the elevated app runs it; the file name is fixed and no tag reaches a path or a command line; closed by exo.
10. Install: the app starts the setup detached with the argument list `/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG`, prints one line naming the new build, and exits at once without the `--pause` wait, so no podbridge process holds its files; Inno's default `CloseApplications=yes` closes any other running copy without a prompt in silent mode; closed by exo.
11. Start again: a new `[Run]` entry starts `{app}\podbridge.exe run --pause` with `nowait skipifnotsilent`, so it runs only after a silent setup; `RestartApplications` is not used, because it needs `RegisterApplicationRestart`; the existing `postinstall skipifsilent` entry stays for a normal install; closed by exo.
12. Failure never blocks start: any network, timeout, JSON or validation error prints one `podbridge: update skipped (<reason>)` line and `run` continues; the API call times out after 10 s, the download after `DOWNLOAD_TIMEOUT_S`; up to date prints nothing; closed by exo.
13. The one last manual install: the user opens `github.com/blauwtje/podbridge/releases` on the PC and clicks `podbridge-setup.exe` under the top entry; no extra short link is built, because `/releases/latest/download/` skips prereleases; closed by exo.
14. Land: push; closed by the user.
15. The repo `blauwtje/podbridge` is public, so the PC reads its releases without a login; made public 2026-10-20 after a clean `gitleaks` scan of all refs; closed by the user.

## Non-goals
- No code signing of `podbridge-setup.exe` and no signature check beyond the SHA256 digest (`installer/podbridge.iss`, `.github/workflows/release.yml`).
- No update check in `pair`, `firmware`, `doctor`, `restore` or `selftest` (`src/podbridge/__main__.py`).
- No GitHub token in the app or on the PC (`src/podbridge/update.py`).
- No change to the VB-CABLE install or its restart rule (`installer/podbridge.iss` `[Code]`).

## Assumptions
- code: the repo is public: `gh repo view blauwtje/podbridge` gives `PUBLIC`, and an anonymous `GET /repos/blauwtje/podbridge/releases` returns 200.
- code: release assets carry `"digest": "sha256:<hex>"` without any CI step: `build-3` and `build-4` show it via `gh api repos/blauwtje/podbridge/releases`.
- code: the asset is `podbridge-setup.exe` and the tag is `build-<run_number>` or the pushed `v*` tag (`.github/workflows/release.yml:99-103`).
- code: the exe is built `--onedir --console --uac-admin` (`scripts/build_exe.ps1:9`), so the app and the setup it starts run elevated without a UAC prompt; `PrivilegesRequired=admin` (`installer/podbridge.iss:13`).
- code: `__version__` is the hand-edited `0.1.0` (`src/podbridge/__init__.py:1`), so it cannot tell builds apart.
- code: HTTP is stdlib `urllib.request` (`src/podbridge/app.py:208-211`); `update.py` uses it too and does not import `app`, which pulls in the audio and Bluetooth modules.
- code: the base suite passes: `uv run pytest -q` gave 127 passed.
- code: `/VERYSILENT` reboots without asking unless `/NORESTART` is given; `/LOG` writes to the user's TEMP folder; `skipifnotsilent` skips an entry in a normal install (jrsoftware.org/ishelp topic_setupcmdline, topic_runsection).
- code: unauthenticated API calls get 60 per hour per IP, one per start.
- assume: the update check runs in `__main__.main` for `run` only, before the command is dispatched, so the `--pause` wait never holds the exe while setup replaces it.
- assume: the update line reads `podbridge: installing <tag>; podbridge starts again when it is done`.
- assume: `update/attempted.txt` and the old setup in `{app}\update` are overwritten by the next attempt; nothing else cleans them.
- assume: the newest by `published_at` always wins, so a newer `build-<n>` can replace a `v*` install and the reverse; that downgrade is accepted.
- assume: the `skipifnotsilent` entry also starts podbridge after a silent setup run by hand; that is accepted.
- assume: `update.py` defines its own 60 s download timeout matching `DOWNLOAD_TIMEOUT_S` in `src/podbridge/app.py:23`.
- assume: `attempted.txt` is written before setup starts; a failed write counts as "update skipped".
- assume: a failed silent setup restarts nothing and shows nothing; its log is the Inno log in the user's TEMP folder, which the README names.
- assume: `build.txt` is trimmed and compared as plain text only, never used in a path or command.
- assume: drafts and releases without `podbridge-setup.exe` are filtered out silently; a chosen asset with a missing or malformed digest prints the skip line.

## Acceptance
- A frozen app whose `build.txt` differs from the newest valid release downloads the setup, checks its SHA256 and starts it with the silent flags (Task 2).
- A digest mismatch, a missing or malformed digest, a download URL outside the repo, a tag outside the pattern, an API error and a timeout each start nothing and let `run` continue (Task 2).
- `PODBRIDGE_NO_UPDATE`, a source run and a missing `build.txt` make no network call (Task 2).
- `run --no-update` skips the check; `run` exits 0 without the pause when setup was started (Task 3).
- CI writes `build.txt` with the release tag and the installer starts podbridge after a silent setup only (Task 1).
- The README names the self-update, `PODBRIDGE_NO_UPDATE` and the releases page for the one manual install (Task 4).
- `uv run pytest` passes (Success criterion).

## Manual checks
- On the Windows PC, install the newest setup once from `github.com/blauwtje/podbridge/releases`, run a new CI build, open "podbridge", and see it install the new build and start again with no clicks.

## Plan basis
Repository: /Users/thomash/Documents/Code/personal/experiments/airpods-fix
Branch: fix/pause-shows-error
Worktree setup: uv sync
Land gate: none
Land: push-main
Commit: feat(update): podbridge installs the newest GitHub build itself at start
Lint: none
App: none
Allow: none

## Success criterion
`uv run pytest`
Red: none (each task's Proof proves its own red; the base suite passes)
Product run: none (Windows-only app with a USB Bluetooth radio; this is a macOS checkout)

## Checkpoint
- Blocks first: Task 2.
- Parallel: Tasks 1, 2 and 4.
- Shared state: Tasks 1 and 2 share the `build.txt` contract (one line, the release tag, in the install folder); Task 3 waits for Task 2's `update.apply`.
- Smallest safe split: packaging, the update module, the CLI hook and the README, one task each.

## Tasks
### Task 1: feat(packaging): stamp each build with its release tag and restart podbridge after a silent setup
Depends on: none | Files: `.github/workflows/release.yml`, `installer/podbridge.iss`, `tests/test_packaging.py` | Data: a job-level `RELEASE_TAG` env holding today's `tag_name` expression, written to `dist/podbridge/build.txt` after the PyInstaller step and reused as `tag_name`; one `[Run]` entry for `{app}\podbridge.exe` `run --pause` with `nowait skipifnotsilent` | Proof: uv run pytest tests/test_packaging.py
### Task 2: feat(update): find, verify and start the newest GitHub build
Depends on: none | Files: `src/podbridge/update.py`, `tests/test_update.py` | Data: an `apply(args) -> bool` entry, a `Release` named tuple (tag, url, sha256) chosen from the releases JSON, and `{app}\update\podbridge-setup.exe` plus `attempted.txt`, per `../build/references/security.md` checks 2, 3, 5, 7 and 10 with one negative test each | Risk: security boundary | Proof: uv run pytest tests/test_update.py
### Task 3: feat(cli): run updates itself before it starts
Depends on: 2 | Files: `src/podbridge/__main__.py`, `tests/test_cli.py` | Data: a `--no-update` flag on `run`; `main` calls `update.apply(args)` for `run` before dispatch and returns 0 without the pause when it returns True | Proof: uv run pytest tests/test_cli.py
### Task 4: docs(readme): describe the self-update
Depends on: none | Files: `README.md` | Data: an install line naming `github.com/blauwtje/podbridge/releases` for the one manual install, and a daily-use line on the self-update, `PODBRIDGE_NO_UPDATE` and the setup log in TEMP | Proof: grep -q PODBRIDGE_NO_UPDATE README.md
