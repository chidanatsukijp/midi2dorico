# Contributing

Three commit rules keep the history readable, plus one habit that keeps it clean.
They are enforced by a hook in `.githooks/commit-msg`.

## 1. 50/72

The **subject line** should be 50 characters, and must never exceed 72 — that is
where GitHub, `git log --oneline` and release notes truncate with `...`.

If a change has several parts, leave a blank line and use `-` bullets in the body:

```
refactor: share the Verovio pipeline

- render_musicxml owns open_score() and page_svg()
- render_pdf and preview_part import them
- drops ~35 duplicated lines
```

## 2. Conventional Commits prefix

| Prefix | Use for |
|---|---|
| `feat:` | a new capability |
| `fix:` | a bug, including edge cases |
| `refactor:` | decoupling / de-duplication with **no** change in output |
| `docs:` | README, AGENT.md, docstrings |
| `test:` | regression suite, audits |
| `chore:` | build, config, ignore files |

`perf:`, `build:`, `ci:` and `revert:` are accepted too.

## 3. Clean up before pushing

After `git commit`, run `git status`. Spot a scratch file or a stray line? While
the commit is still local:

```bash
git rm --cached scratch/verify.json
git commit --amend --no-edit
```

Then push. The history never shows the mistake.

## Enabling the hook

```bash
git config core.hooksPath .githooks
```

From then on a commit whose subject has no prefix, or is longer than 72
characters, is rejected before it is created.

## Before you push

```bash
python regression_test.py
python main.py --input test_orchestra.mid --config engraving_config.json --output out.musicxml
python audit_notation.py out.musicxml
```

All three must be clean — see [AGENT.md](AGENT.md) for the invariants behind them.
