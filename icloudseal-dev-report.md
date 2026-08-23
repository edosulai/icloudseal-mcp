# icloudseal-mcp productization report

Session: Icloudseal Dev. Gold standard: `whatseal@2.0.3` (public npm, GitHub
`edosulai/whatseal-mcp`, tags `v2.0.0`–`v2.0.3`). This repo is Python, so the
real install path is **PyPI / pipx / uv**, not npm. No thin npm wrapper was
invented.

## What shipped in the tree (0.9.0)

- Version `0.9.0` in `pyproject.toml` and `icloudseal_mcp/__init__.py`.
- MIT `LICENSE` + `MANIFEST.in`.
- Packaged skill + helper in the wheel:
  `icloudseal_mcp/data/skills/icloudseal/` and
  `icloudseal_mcp/data/native-approval.swift`.
- `icloudseal-mcp setup` / `install-skill` defaults to
  **copilot, claude, codex, agents, hermes**.
- Whole-directory home symlinks (dotfiles checkouts) are skipped unless
  `--force` on both install and uninstall.
- VS Code / Hermes MCP snippets: installed package uses
  `icloudseal-mcp-server`; git checkout still documents `mcp-wrapper.sh`.
- README hero (mark, badges, TOC, pipx/uv quick start, security) without
  deleting domain coverage.
- SKILL.md trigger (first 57 chars, complete):
  `Use for iCloud Mail, Notes, Calendar, or sealed MCP.`
- Tests: **63 passed**. Ruff clean on touched Python. `python -m build` +
  `twine check` **PASSED**.
- Dummy-HOME `icloudseal-mcp setup` installed five hosts and did **not**
  write the live home. Live `~/.copilot` is a symlink into the dotfiles
  checkout (stamp still `0.8.4`); that is the skip-symlink behaviour
  working, not a live install.
- Wheel resolution from `/tmp` (cwd not the checkout) uses
  `site-packages/icloudseal_mcp/data/…`.
- Graphify updated (`graphify-out/` remains gitignored).

## GitHub

- Repo: https://github.com/edosulai/icloudseal-mcp (**private**).
- Default branch: `main`. No tags or GitHub Releases existed before this
  session.
- Visibility was **not** flipped. whatseal is public; this repo stays
  private until a human says otherwise.

## PyPI — blocked (honest)

Playbook required a real registry publish. Name `icloudseal-mcp` is free
(PyPI JSON 404). Publish did **not** run because no PyPI token exists on
this Mac:

- API Tokens note: 59 rows, `ANY_PYPI []`, `ENV_PYPI []`.
- Hermes dotenv: no `PYPI` / `TWINE` keys (npm + GitHub tokens only).
- No `~/.pypirc`. Keychain internet password for `pypi.org`: none.
- EasyCopy: no `pypi-` prefix items.
- `pypi.org/user/edosulai/` exists as a profile page (WAF title) with
  **zero published projects**.

Do not mint a token from this session. After a human adds a PyPI API
token (Trusted Publisher or `pypi-` token in the API Tokens note),
publish from the tagged commit with:

```bash
python -m twine upload dist/icloudseal_mcp-0.9.0*
```

## Review

- Static scan of added + untracked text: secrets/shell/eval/pickle/home/email = 0.
- Tests: 63 passed (plus the wheel-fallback isolation after review).
- Independent reviewer (`sa-0-56db4297`): `security_concerns=[]`.
  `passed=false` only because `LICENSE` and `icloudseal_mcp/data/*`
  were still untracked — those files are in this commit. Suggestion
  (non-blocking): wheel-fallback test used to hit the checkout via
  `module_dir.parent`; the test now monkeypatches `__file__` to a
  fake site-packages tree.
- First reviewer (`sa-0-ade7ec0a`) hit max iterations with no JSON
  (fail-closed, discarded).
- Protected `AGENTS.md` / `CLAUDE.md` still say Copilot-only. Write
  approval timed out twice; not retried. README / SKILL / ARCHITECTURE
  are the public source of truth.

## Git tag / GitHub Release

Remaining work asked for annotated `v0.9.0` + GitHub Release once
review passed and the tree was ready. PyPI is still **not**
published — the release notes say so. Do not treat the GitHub tag as
a registry release.

## Hygiene

- No secrets, home paths, or emails in the diff.
- `.gitignore` now ignores `/data/` (repo-root) so package data under
  `icloudseal_mcp/data/` is committable.
- Live Mail / Notes / Keychain were not mutated except a read of the
  API Tokens **names** (Service / Env var / sha8). Token values were
  not printed.
- Dummy skill install used `HOME=/tmp/icloudseal-wheel-test/home` only.

## Commands a human can copy

```bash
pipx install icloudseal-mcp   # after PyPI exists
# or from this checkout:
pip install -e ".[dev]"
icloudseal-mcp setup
printf 'Y\n' | hermes mcp add icloudseal --command icloudseal-mcp-server
icloudseal-mcp mail setup --email you@icloud.com
```
