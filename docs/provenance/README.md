<!--
SPDX-FileCopyrightText: 2026 LowGuiGui <https://github.com/LowGuiGui>
SPDX-License-Identifier: GPL-2.0-or-later
-->

# Where this history came from

Everything up to commit `836f200` was produced mechanically from the Windows project, and
nothing in it was written for this repository. This folder records how, so the result can be
checked and reproduced.

## Source

- Repository: [LowGuiGui/gbfr_auto](https://github.com/LowGuiGui/gbfr_auto), a fork of
  [zhiyual/gbfr_auto](https://github.com/zhiyual/gbfr_auto). It is archived read-only.
- Branch `dev` at `894cbb8ea2673036562cf000a46f1abd9f8bc73a`, the archive's final commit.
  Because the archive is read-only, that branch can no longer move.

## How it was made

git-filter-repo 2.47.0, the PyPI release run through uv (`--version` prints `a40bce548d2c`),
with git 2.53.0:

    git clone --single-branch --branch dev --no-tags git@github.com:LowGuiGui/gbfr_auto.git seed
    cd seed
    uvx --from git-filter-repo==2.47.0 git-filter-repo \
        --paths-from-file ../paths.txt \
        --replace-message ../messages.txt

The two input files are kept here unchanged: [`paths.txt`](paths.txt) and
[`messages.txt`](messages.txt). The run was given them by absolute path from outside the
clone.

84 of the 100 commits survived. The other 16 touched only paths that were not kept, became
empty, and were dropped. The resulting tip was `836f200a70b415d5c11b9a008a04a4309e9c8dc8`. A
second run from a separate fresh clone produced the same tip and an identical commit map, so
the result is reproducible.

## What was kept, and why

- **Platform-free core:** `applog.py`, `config.py`, `opencv.py`, `framediff.py`,
  `geometry.py`, `pages.py`, `supervisor.py`, `backend.py`, and their tests.
- **The app layer, parked:** `main.py`, `option.py`, `vigem.py` (including its earlier path
  `tools/vigem.py`), `hook/gbfr_hook.c`, `icon.ico`, and the tests that exercise them
  (`test_page_dispatch`, `test_template_refresh`, `test_option_keys`, `test_option_modes`,
  `test_diagnostics`, `test_hotkeys`, `test_vigem`). They are here for their history, not
  because they run on Linux. The change after this record moved their platform-free parts
  into the core (`pagetree.py`, `xusb.py`) and took the rest out of the working tree. When
  the Linux port needs one of them, bring it back from `1db218a`, the last commit that has
  them all, already wired to `pagetree.py` and `xusb.py`:

      git restore --source=1db218a -- main.py option.py

  `hook/gbfr_hook.c` is not in the working tree either. It stays in history for the probe's
  L5 step, which may build its focus spoof as an `.asi`, and comes back the same way:
  `git restore --source=1db218a -- hook/gbfr_hook.c`.

  Restoring them brings upstream's code back. `main.py`, `option.py` and `hook/gbfr_hook.c`
  were created upstream and still contain its lines (`git blame -C -C` at `1db218a`
  attributes about 350 of `main.py`'s 868 lines, 51 of `option.py`'s 350 and 192 of
  `hook/gbfr_hook.c`'s 520 to upstream), and `icon.ico` is upstream's image. Upstream's code
  is GPL-2.0-only, so restored as they are, these files would make the program GPL-2.0-only
  again, and they predate the SPDX headers. Rewrite their upstream parts before they return,
  as COPYRIGHT describes for the files rewritten on 2026-10-05. `vigem.py` is the fork's own.
- **Data and licence:** `template/*.png`, `LICENSE`, `COPYRIGHT`.
- **Tooling:** `.gitignore`, `.pre-commit-config.yaml`, `requirements.txt`,
  `requirements-dev.txt`, `ruff.toml`, `tests/conftest.py`, `tests/README.md`.

Left in the archive:

- the Windows platform layer (`window_capture.py`, `window_input.py`, `xinput.py`,
  `procinfo.py`, and the rest of `hook/`)
- the Windows probe (`tools/windows_probe.py`) and the remaining Windows-only tests
- the Windows CI workflow and both READMEs
- about 96 MB of build output that upstream committed and later deleted, mostly a 93 MB
  `dist/GBFR Auto.exe`

The history contains a single rename, `tools/vigem.py` to `vigem.py`. git-filter-repo does
not follow renames, which is why both paths are listed.

## Commit messages

Every bare `#NN` became `gbfr_auto#NN`, which GitHub does not turn into a link. Left
alone, those numbers would link to this repository's issues and pull requests instead of the
archive's. The owner-qualified form, `LowGuiGui/gbfr_auto#NN`, would link correctly, but
GitHub would then add a permanent "referenced this issue" entry to an archive issue for every
commit that mentions it. The archive is linked at the top of this page. Upstream's seven
commits contain no issue references. Every rewritten number is 67 or lower, and the
archive's highest is 68, so all of them refer to the fork's own issues and pull requests.
The one reference that was already qualified, `LizardByte/Sunshine#1822`, is unchanged.

File contents were not rewritten, so code and comments still carry bare numbers such as
`#16` or `#45`. Those refer to the archive as well.

git-filter-repo also rewrote abbreviated hashes of surviving commits inside messages. It
flagged six tokens it could not map:

- `65e591b` is the only dropped commit among them: upstream's removal of its committed build
  output, which touched only paths that were not kept. Look it up in the archive.
- `1cf6494` was never on `dev`. It was the old base of a pull request.
- `999999404`, `8177f975`, `33022335687` and `1090670` are not commit IDs at all. They are a
  match score, the start of a SHA-256, a CI run ID and a Steam app ID.

## Translating old hashes

[`commit-map.txt`](commit-map.txt) is git-filter-repo's own map, with one `old new` pair per
line. Dropped commits map to forty zeros.

## What did not carry over

- **Signatures.** The archive's commits are SSH-signed. Rewriting a commit invalidates its
  signature, so the signatures were dropped. The signed originals stay in the archive, and
  the commit map ties each one to its rewrite.
- **Tags.** The only tag, `v1.0.0`, is upstream's Windows release.
- **The `main` branch,** which mirrored upstream.

## How the result was checked

These checks were run on the result before anything was built on it. Each one could have
failed.

- All 42 files at the new tip have the same blob hash as the same path at `894cbb8`. The
  comparison asserts that all 42 rows were compared, and a copy of the listing with one hash
  deliberately corrupted was flagged, so it can fail.
- For every listed path, `git log -- <path>` counts the same number of commits before and
  after.
- Author and committer names, emails and dates are unchanged on every surviving commit.
- No bare `#NN` remains in any message.
- GitHub's Markdown API, asked to render `gbfr_auto#16` next to `LowGuiGui/gbfr_auto#16`,
  linked only the second.
