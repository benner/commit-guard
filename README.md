# commit-guard

Opinionated conventional commit message linter with imperative mood detection.

Unlike regular expression only tools, commit-guard uses
NLP (nltk POS tagging) to verify that commit descriptions start with an
imperative verb.

## Example

```bash
$ commit-guard
  ✗ [subject] subject does not match 'type(scope): description': WIP
  ✗ [signed-off] missing 'Signed-off-by' trailer
  ✗ [signature] commit is not signed (GPG/SSH)
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
    lowercase start, no trailing `.` `!` `?` or space, max 72 chars
* `imperative` - First word is an imperative verb (for example `add` not `added`)
* `body` - Blank line separates subject from body, and body is non-empty
* `signed-off` - `Signed-off-by:` trailer exists
* `signature` - Verify GPG or SSH signature via GitHub public key lookup, with
    fallback to `git verify-commit`

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

### Subject format

By default the description must start with a lowercase letter. To allow
uppercase descriptions:

```bash
commit-guard --no-require-lowercase
```

In `.commit-guard.toml`:

```toml
require-lowercase = false
```

By default `.`, `!`, `?`, and space are forbidden as trailing characters.
To change the set (any character is valid):

```bash
commit-guard --no-trailing-chars ".,"
commit-guard --no-trailing-chars ".,!"
```

In `.commit-guard.toml`:

```toml
no-trailing-chars = [".", "!"]
```

Pass an empty list to disable the check entirely:

```toml
no-trailing-chars = []
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

### Required subject pattern

Require the commit subject to match a regular expression. Useful for
enforcing ticket references or any custom naming convention:

```bash
commit-guard --require-subject-pattern "[A-Z]+-[0-9]+"
commit-guard --require-subject-pattern "#[0-9]+"
```

In `.commit-guard.toml`:

```toml
require-subject-pattern = "[A-Z]+-[0-9]+"
```

An invalid regex causes an immediate error at startup (exit 2). This
check runs independently of `--enable`/`--disable`.

### Required custom trailers

Require arbitrary trailers to be present in the commit message. Multiple
trailers can be specified as a comma-separated list:

```bash
commit-guard --require-trailer Closes
commit-guard --require-trailer "Closes,Reviewed-by"
```

In `.commit-guard.toml`:

```toml
require-trailers = ["Closes", "Reviewed-by"]
```

Trailer matching is case-sensitive and requires at least one non-space
character after the colon (e.g. `Closes: #42`). This check runs
independently of `--enable`/`--disable`.

### Signature verification

The `signature` check tries to verify the commit without any local keyring setup:

1. Look up the commit author's email in the GitHub API to find their GitHub
   username.
2. Fetch their public keys from `github.com/{username}.gpg` and
   `github.com/{username}.keys`.
3. Try GPG verification: import the fetched key into a temporary keyring and
   run `git verify-commit`.
4. Try SSH verification: write a temporary `allowed_signers` file and run
   `git verify-commit` with the SSH allowed-signers config.
5. If any key verifies, the check passes. If none do, it fails.

If the author's email is not found on GitHub, or the API is unreachable, the
check fails with a clear error — there is no silent fallback.

### Configuration file

Place `.commit-guard.toml` in your project root (or any parent directory) to
set defaults for `enable`, `disable`, `scopes`, `require-scope`, `types`,
`max-subject-length`, `min-description-length`, `require-lowercase`,
`no-trailing-chars`, `require-subject-pattern`, and `require-trailers`.
commit-guard searches upward from the working directory and uses the first
file found.

```toml
# .commit-guard.toml
disable = ["signature", "body"]
scopes = ["auth", "api", "db"]
require-scope = true
types = ["feat", "fix", "chore", "wip"]
max-subject-length = 100
min-description-length = 10
require-lowercase = false
no-trailing-chars = [".", "!"]
require-trailers = ["Closes", "Reviewed-by"]
```

```toml
# .commit-guard.toml
enable = ["subject", "imperative"]
```

CLI flags (`--enable`, `--disable`, `--scopes`, `--require-scope`, `--types`,
`--max-subject-length`, `--min-description-length`, `--no-require-lowercase`,
`--no-trailing-chars`, `--require-trailer`) take
full precedence and ignore config file values when provided.

### Environment variables

| Variable                   | Default | Description                                  |
| -------------------------- | ------- | -------------------------------------------- |
| `COMMIT_GUARD_GIT_TIMEOUT` | `10`    | Timeout in seconds for git subprocess calls. |

```bash
COMMIT_GUARD_GIT_TIMEOUT=30 commit-guard --range origin/main..HEAD
```

In GitHub Actions, set it at the step or job level:

```yaml
- uses: benner/commit-guard@v0.19.0
  env:
    COMMIT_GUARD_GIT_TIMEOUT: 30
  with:
    range: ${{ env.PR_BASE }}..${{ env.PR_HEAD }}
```

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

### Machine-readable output

Use `--output jsonl` to emit one JSON line per commit to stdout instead of the
default human-readable text:

```bash
commit-guard --range origin/main..HEAD --output jsonl
```

Each line is a JSON object:

```json
{
  "sha": "abc1234...",
  "subject": "feat: add thing",
  "ok": false,
  "results": [{"check": "body", "level": "error", "message": "missing body"}]
}
```

`sha` is `null` when reading from a file or stdin. `results` is empty when all
checks pass. Pipe to `jq` for filtering:

```bash
commit-guard --range origin/main..HEAD --output jsonl | jq 'select(.ok == false)'
```

Use `--output-file FILE` to write JSONL to a file while keeping human-readable
text on stdout:

```bash
commit-guard --range origin/main..HEAD --output-file results.jsonl
```

`--output-file` is independent of `--output`: combining both writes JSONL to
both stdout and the file.

In GitHub Actions, `output-file` is the recommended way to get machine-readable
results — text stays in the CI log and the file is accessible to subsequent steps
via `steps.<id>.outputs.output-file`.

### GitHub Actions

```yaml
steps:
  - uses: actions/checkout@v4
    with:
      fetch-depth: 0
  - uses: benner/commit-guard@v0.19.0
```

Check all commits in a pull request:

```yaml
jobs:
  lint-commits:
    runs-on: ubuntu-latest
    env:
      PR_BASE: ${{ github.event.pull_request.base.sha }}
      PR_HEAD: ${{ github.event.pull_request.head.sha }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: benner/commit-guard@v0.19.0
        with:
          range: ${{ env.PR_BASE }}..${{ env.PR_HEAD }}
```

Check a specific commit SHA (mirrors the positional CLI argument):

```yaml
      - uses: benner/commit-guard@v0.19.0
        with:
          rev: ${{ github.sha }}
```

All inputs are optional and mirror the CLI flags:

```yaml
jobs:
  lint-commits:
    runs-on: ubuntu-latest
    env:
      PR_BASE: ${{ github.event.pull_request.base.sha }}
      PR_HEAD: ${{ github.event.pull_request.head.sha }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: benner/commit-guard@v0.19.0
        with:
          range: ${{ env.PR_BASE }}..${{ env.PR_HEAD }}
          disable: signed-off,signature
          scopes: auth,api,db
          require-scope: 'true'
          require-subject-pattern: '[A-Z]+-[0-9]+'
          require-trailer: 'Closes,Reviewed-by'
          max-subject-length: '100'
          min-description-length: '10'
          no-require-lowercase: 'true'
          no-trailing-chars: '.,!'
          allow-empty: 'true'
          include-merges: 'true'
          output-file: results.jsonl
```

When `output-file` is set the action exposes the path as an output:

```yaml
      - uses: benner/commit-guard@v0.19.0
        id: cg
        with:
          range: ${{ env.PR_BASE }}..${{ env.PR_HEAD }}
          output-file: results.jsonl
      - run: jq 'select(.ok == false)' "${{ steps.cg.outputs.output-file }}"
```

### pre-commit

Add to your `.pre-commit-config.yaml`:

```yaml
---
repos:
  - repo: https://github.com/benner/commit-guard
    rev: v0.19.0
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
