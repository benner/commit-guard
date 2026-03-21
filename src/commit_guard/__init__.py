import re
import subprocess
import sys
from argparse import ArgumentParser
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import nltk

TYPES = frozenset(
    {
        "feat",
        "fix",
        "docs",
        "style",
        "refactor",
        "perf",
        "test",
        "build",
        "ci",
        "chore",
        "revert",
    }
)

IMPERATIVE_VERBS = frozenset(
    {
        "add",
        "allow",
        "apply",
        "avoid",
        "bump",
        "change",
        "check",
        "clean",
        "clear",
        "configure",
        "correct",
        "create",
        "catch",
        "define",
        "delete",
        "deprecate",
        "disable",
        "document",
        "drop",
        "enable",
        "enforce",
        "ensure",
        "exclude",
        "export",
        "extend",
        "extract",
        "fix",
        "format",
        "guard",
        "handle",
        "ignore",
        "implement",
        "import",
        "improve",
        "include",
        "increase",
        "initialize",
        "inline",
        "install",
        "introduce",
        "invalidate",
        "limit",
        "log",
        "make",
        "mark",
        "merge",
        "migrate",
        "move",
        "normalize",
        "open",
        "optimize",
        "override",
        "parse",
        "pass",
        "patch",
        "pin",
        "port",
        "prevent",
        "print",
        "provide",
        "publish",
        "reduce",
        "refactor",
        "release",
        "remove",
        "rename",
        "reorganize",
        "replace",
        "require",
        "reset",
        "resolve",
        "restore",
        "restrict",
        "return",
        "revert",
        "report",
        "run",
        "separate",
        "set",
        "show",
        "simplify",
        "skip",
        "sort",
        "split",
        "start",
        "store",
        "stop",
        "support",
        "suppress",
        "switch",
        "sync",
        "track",
        "trim",
        "unify",
        "update",
        "upgrade",
        "use",
        "wait",
        "validate",
        "vendor",
        "verify",
        "wrap",
    }
)

SUBJECT_RE = re.compile(
    r"^(?P<type>\w+)(?:\((?P<scope>[^)]+)\))?!?:\s+(?P<desc>.+)$",
)

SIGNED_OFF_RE = re.compile(
    r"^Signed-off-by:\s+.+\s+<.+>",
    re.MULTILINE,
)

MAX_SUBJECT_LEN = 72
GIT_TIMEOUT = 10


class Check(StrEnum):
    SUBJECT = "subject"
    IMPERATIVE = "imperative"
    BODY = "body"
    SIGNED_OFF = "signed-off"
    SIGNATURE = "signature"


ALL_CHECKS = frozenset(Check.__members__.values())


class Level(StrEnum):
    ERROR = "error"
    WARN = "warn"
    INFO = "info"


PREFIXES = {
    Level.ERROR: "\033[31m✗\033[0m",
    Level.WARN: "\033[33m⚠\033[0m",
    Level.INFO: "\033[34mi\033[0m",
}


@dataclass
class Result:
    errors: list = field(default_factory=list)

    def error(self, msg):
        self.errors.append((Level.ERROR, msg))

    def warn(self, msg):
        self.errors.append((Level.WARN, msg))

    def info(self, msg):
        self.errors.append((Level.INFO, msg))

    @property
    def ok(self):
        return not any(lvl == Level.ERROR for lvl, _ in self.errors)


def _ensure_nltk_data():
    _download_if_missing("taggers/averaged_perceptron_tagger_eng")
    _download_if_missing("tokenizers/punkt_tab")


def _download_if_missing(resource):
    try:
        nltk.data.find(resource)
    except LookupError:
        nltk.download(resource.rsplit("/", maxsplit=1)[-1], quiet=True)


def _strip_comments(message):
    return "\n".join(
        line for line in message.split("\n") if not line.lstrip().startswith("#")
    )


def check_subject(line, result):
    m = SUBJECT_RE.match(line)
    if not m:
        result.error(f"subject does not match 'type(scope): description': {line}")
        return None

    if m.group("type") not in TYPES:
        result.error(f"unknown type: {m.group('type')}")

    desc = m.group("desc")
    if desc[0].isupper():
        result.error("description must not start with uppercase")
    if desc.endswith("."):
        result.error("description must not end with period")
    if len(line) > MAX_SUBJECT_LEN:
        result.error(f"subject too long: {len(line)} > {MAX_SUBJECT_LEN}")
    return desc


def check_imperative(desc, result):
    tokens = nltk.word_tokenize(desc.lower())
    if not tokens:
        return
    first = tokens[0]
    if first in IMPERATIVE_VERBS:
        return
    tagged = nltk.pos_tag(tokens)
    if tagged[0][1] != "VB":
        result.error(
            f"expected imperative verb, got '{tagged[0][0]}' (POS={tagged[0][1]})",
        )


def check_body(lines, result):
    if len(lines) < 3:  # noqa: PLR2004
        result.error("missing body")
        return
    if lines[1].strip():
        result.error("missing blank line between subject and body")
    if not any(ln.strip() for ln in lines[2:]):
        result.error("missing body")


def check_signed_off(message, result):
    if not SIGNED_OFF_RE.search(message):
        result.error("missing 'Signed-off-by' trailer")


def check_signature(rev, result):
    proc = subprocess.run(  # noqa: S603
        ["git", "verify-commit", rev],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        timeout=GIT_TIMEOUT,
    )
    if proc.returncode != 0:
        result.error("commit is not signed (GPG/SSH)")
        return

    output = proc.stderr.lower()
    sig_type = "SSH" if "ssh" in output else "GPG"
    result.info(f"signature type: {sig_type}")


def _get_message(rev):
    try:
        return subprocess.check_output(  # noqa: S603
            ["git", "log", "-1", "--format=%B", rev],  # noqa: S607
            text=True,
            stderr=subprocess.PIPE,
            timeout=GIT_TIMEOUT,
        ).strip()
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip()
        if "unknown revision" in stderr or "ambiguous argument" in stderr:
            sys.exit("no commits yet")
        sys.exit(f"git error: {stderr}")


@dataclass
class Args:
    rev: str | None
    message: str
    enabled: frozenset


def _parse_checks(parser, value):
    try:
        return [Check(c.strip()) for c in value.split(",")]
    except ValueError as e:
        parser.error(str(e))


def _parse_args():
    checks_list = ",".join(sorted(Check))
    parser = ArgumentParser(description="conventional commit checker")
    parser.add_argument("rev", nargs="?", default=None)
    parser.add_argument("--message-file", type=Path)
    parser.add_argument(
        "--enable",
        metavar="CHECK[,CHECK,...]",
        help=f"run only these checks ({checks_list})",
    )
    parser.add_argument(
        "--disable",
        metavar="CHECK[,CHECK,...]",
        help=f"skip these checks ({checks_list})",
    )
    args = parser.parse_args()

    enabled = (
        frozenset(_parse_checks(parser, args.enable)) if args.enable else ALL_CHECKS
    )
    if args.disable:
        enabled = enabled - frozenset(_parse_checks(parser, args.disable))

    if args.message_file:
        rev = None
        message = _strip_comments(args.message_file.read_text().strip())
    elif args.rev:
        rev = args.rev
        message = _strip_comments(_get_message(rev))
    elif not sys.stdin.isatty():
        rev = None
        message = _strip_comments(sys.stdin.read().strip())
    else:
        rev = "HEAD"
        message = _strip_comments(_get_message(rev))

    return Args(rev=rev, message=message, enabled=enabled)


def _report(result):
    for level, msg in result.errors:
        sys.stderr.write(f"  {PREFIXES[level]} {msg}\n")

    if result.ok:
        sys.stderr.write("  \033[32m✓\033[0m all checks passed\n")

    return 0 if result.ok else 1


def main():
    args = _parse_args()
    lines = args.message.split("\n")

    if Check.IMPERATIVE in args.enabled:
        _ensure_nltk_data()

    result = Result()

    desc = None
    if Check.SUBJECT in args.enabled:
        desc = check_subject(lines[0], result)
    if Check.IMPERATIVE in args.enabled:
        if desc is None:
            m = SUBJECT_RE.match(lines[0])
            desc = m.group("desc") if m else None
        if desc:
            check_imperative(desc, result)
    if Check.BODY in args.enabled:
        check_body(lines, result)
    if Check.SIGNED_OFF in args.enabled:
        check_signed_off(args.message, result)
    if Check.SIGNATURE in args.enabled:
        if args.rev:
            check_signature(args.rev, result)
        else:
            result.warn("signature check skipped (no commit ref)")

    return _report(result)
