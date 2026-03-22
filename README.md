# commit-guard

Opinionated conventional commit message linter with imperative mood detection.

Unlike regular expression only tools, commit-guard uses
NLP (nltk POS tagging) to verify that commit descriptions start with an
imperative verb.

## Example

```bash
$ commit-guard
✗ subject does not match 'type(scope): description': Merge pull request #5 from fix/branch
✗ missing 'Signed-off-by' trailer
✗ commit is not signed (GPG/SSH)
```

## Installation

From PyPI:

```bash
uv tool install git-commit-guard
```

or:

```bash
pipx install git-commit-guard
```

From a local clone:

```bash
uv tool install -e .
```

During development:

```bash
uv run commit-guard
```

## Usage

```bash
# check HEAD
commit-guard

# check specific commit
commit-guard abc1234

# check commit message file (for git hooks)
commit-guard --message-file .git/COMMIT_EDITMSG

# pipe message via stdin
echo "fix(auth): add token refresh" | commit-guard
```

### Selecting checks

All checks run by default. Use `--enable` or `--disable` with
comma-separated values:

```bash
# only check subject format and imperative mood
commit-guard --enable subject,imperative

# skip body and signature checks
commit-guard --disable body,signed-off,signature
```

Available checks:

* `subject` - Format matches `type(scope): description`, valid type,
    lowercase start, no trailing period, max 72 chars
* `imperative` - First word is an imperative verb (for example `add` not `added`)
* `body` - Body is present after a blank line
* `signed-off` - `Signed-off-by:` trailer exists
* `signature` - Verify GPG or SSH signature

### Checking a range of commits

```bash
# all non-merge commits between tags
git rev-list --no-merges v1.0..v2.0 | while read -r rev; do
    commit-guard "$rev" || git log -1 --oneline "$rev"
done

# only subject checks on a PR range
git rev-list --no-merges origin/main..HEAD | while read -r rev; do
    commit-guard "$rev" --enable subject,imperative
done
```

### pre-commit

Add to your `.pre-commit-config.yaml`:

```yaml
---
repos:
  - repo: https://github.com/benner/commit-guard
    rev: v0.1.0
    hooks:
      - id: commit-guard
```

Install the hook:

```bash
pre-commit install --hook-type commit-msg
```

To selectively enable or disable checks, pass `args`:

```yaml
      - id: commit-guard
        args: ["--enable", "subject,imperative"]
```

## Imperative mood detection

commit-guard combines two strategies to detect non-imperative descriptions:

1. A whitelist common commit verbs (`add`, `fix`, `remove`, etc.)
   that pass immediately without NLP.
2. nltk POS tagging as a fallback — flags words tagged as past tense (`VBD`),
   gerund (`VBG`), third person (`VBZ`), etc.

This catches common mistakes like `added logging` or `fixes bug` while
keeping false positives low.

## Conventional commit format

```text
type(scope): description

body

trailers
```

Supported types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`,
`build`, `ci`, `chore`, `revert`.

Scope is optional. Mark breaking changes with `!` before
the colon.

## License

GPLv2

# some random code
