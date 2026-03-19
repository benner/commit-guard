# commit-guard

Opinionated conventional commit message linter with imperative mood detection.

Unlike regular expression only tools, commit-guard uses
NLP (nltk POS tagging) to verify that commit descriptions start with an
imperative verb.

## Installation

Standalone via [uv](https://docs.astral.sh/uv/) — no install needed:

```bash
./commit-guard.py
```

## Usage

```bash
# check HEAD
commit-guard.py

# check specific commit
commit-guard.py abc1234

# check commit message file (for git hooks)
commit-guard.py --message-file .git/COMMIT_EDITMSG

# pipe message via stdin
echo "fix(auth): add token refresh" | commit-guard.py
```

### Selecting checks

All checks run by default. Use `--enable` or `--disable` with
comma-separated values:

```bash
# only check subject format and imperative mood
commit-guard.py --enable subject,imperative

# skip body and signature checks
commit-guard.py --disable body,signed-off,signature
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
    commit-guard.py "$rev" || git log -1 --oneline "$rev"
done

# only subject checks on a PR range
git rev-list --no-merges origin/main..HEAD | while read -r rev; do
    commit-guard.py "$rev" --enable subject,imperative
done
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
