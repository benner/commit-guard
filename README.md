# commit-guard

Opinionated conventional commit message linter with imperative mood detection.

Unlike regular expression only tools, commit-guard uses
NLP (nltk POS tagging) to verify that commit descriptions start with an
imperative verb.

## Example

```bash
$ commit-guard
✗ subject does not match 'type(scope): description':
  Merge pull request #5 from fix/branch
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

### Subject length

The default maximum subject line length is 72 characters. Override with
`--max-subject-length`:

```bash
commit-guard --max-subject-length 100
```

By default there is no minimum description length. Enforce one with
`--min-description-length`:

```bash
commit-guard --min-description-length 10
```

### Type validation

By default the standard conventional commit types are accepted. Use `--types`
to replace the allowed set entirely:

```bash
# restrict to a subset
commit-guard --types feat,fix,chore

# add a project-specific type
commit-guard --types feat,fix,docs,style,refactor,perf,test,build,ci,chore,revert,wip
```

### Scope validation

By default any scope is accepted and scope is optional. Use `--scopes` to
restrict allowed values and `--require-scope` to enforce that a scope is always
present:

```bash
# only allow known scopes
commit-guard --scopes auth,api,db

# require a scope
commit-guard --require-scope

# combine both
commit-guard --scopes auth,api --require-scope
```

### Configuration file

Place `.commit-guard.toml` in your project root (or any parent directory) to
set defaults for `enable`, `disable`, `scopes`, `require-scope`, `types`,
`max-subject-length`, and `min-description-length`. commit-guard searches
upward from the working directory and uses the first file found.

```toml
# .commit-guard.toml
disable = ["signature", "body"]
scopes = ["auth", "api", "db"]
require-scope = true
types = ["feat", "fix", "chore", "wip"]
max-subject-length = 100
min-description-length = 10
```

```toml
# .commit-guard.toml
enable = ["subject", "imperative"]
```

CLI flags (`--enable`, `--disable`, `--scopes`, `--require-scope`, `--types`,
`--max-subject-length`, `--min-description-length`) take full precedence and
ignore config file values when provided.

### Checking a range of commits

Use `--range` to check all commits in a revision range. All commits are
checked and a single non-zero exit code is returned if any fail:

```bash
# check all commits in a PR
commit-guard --range origin/main..HEAD

# check between two tags
commit-guard --range v1.0..v2.0

# only subject checks on a range
commit-guard --range origin/main..HEAD --enable subject,imperative
```

Merge commits are excluded by default. Use `--include-merges` to check them:

```bash
commit-guard --range origin/main..HEAD --include-merges
```

An empty range (no commits) exits non-zero by default — this catches
misconfigured range specs in CI. Use `--allow-empty` to exit 0 instead:

```bash
commit-guard --range origin/main..HEAD --allow-empty
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
      - id: commit-guard-signature
```

Install the hooks:

```bash
pre-commit install --hook-type commit-msg --hook-type post-commit
```

`commit-guard` runs at the `commit-msg` stage and checks message format.
`commit-guard-signature` runs at the `post-commit` stage and verifies
the GPG/SSH signature after the commit object is created.

To selectively enable or disable checks, pass `args`:

```yaml
      - id: commit-guard
        args: ["--enable", "subject,imperative"]
```

## Imperative mood detection

commit-guard combines two strategies to detect non-imperative descriptions:

1. nltk POS tagging — flags words tagged as past tense (`VBD`),
   gerund (`VBG`), third person (`VBZ`), etc.
2. WordNet morphology as a fallback for words the tagger misclassifies.

This catches common mistakes like `added logging` or `fixes bug` while
keeping false positives low.

## Conventional commit format

```text
type(scope): description

body

trailers
```

Default types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`,
`build`, `ci`, `chore`, `revert`. Override with `--types` or the `types` config
key.

Scope is optional. Mark breaking changes with `!` before
the colon.

## License

GPLv2
