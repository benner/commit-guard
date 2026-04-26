import json
import os
import re
import subprocess
import sys
import tomllib
from argparse import ArgumentParser
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import nltk
from nltk.corpus import wordnet

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

_NON_IMPERATIVE_SUFFIX_RE = re.compile(r"(?:ing|ed)$")
_TRAILER_RE = re.compile(r"^[\w-]+:\s+\S")

SUBJECT_RE = re.compile(
    r"^(?P<type>\w+)(?:\((?P<scope>[^)]+)\))?!?:\s+(?P<desc>.+)$",
)

SIGNED_OFF_RE = re.compile(
    r"^Signed-off-by:\s+.+\s+<.+>",
    re.MULTILINE,
)

MAX_SUBJECT_LEN = 72
GIT_TIMEOUT = 10


def _git_timeout():
    return int(os.environ.get("COMMIT_GUARD_GIT_TIMEOUT", GIT_TIMEOUT))


class Check(StrEnum):
    SUBJECT = "subject"
    IMPERATIVE = "imperative"
    BODY = "body"
    SIGNED_OFF = "signed-off"
    SIGNATURE = "signature"


ALL_CHECKS = frozenset(Check.__members__.values())


class OutputFormat(StrEnum):
    TEXT = "text"
    JSONL = "jsonl"


def _load_config(start=None):
    start = start or Path.cwd()
    for directory in [start, *start.parents]:
        config_path = directory / ".commit-guard.toml"
        if config_path.exists():
            with config_path.open("rb") as f:
                return tomllib.load(f)
    return {}


def _parse_config_checks(config, key):
    try:
        return [Check(v) for v in config.get(key, [])]
    except ValueError as e:
        sys.exit(f".commit-guard.toml: {e}")


class Level(StrEnum):
    ERROR = "error"
    WARN = "warn"
    INFO = "info"
    OK = "ok"


PREFIXES = {
    Level.ERROR: "\033[31m✗\033[0m",
    Level.WARN: "\033[33m⚠\033[0m",
    Level.INFO: "\033[34mi\033[0m",
    Level.OK: "\033[32m✓\033[0m",
}


@dataclass
class Result:
    errors: list = field(default_factory=list)

    def error(self, msg, check=None):
        self.errors.append((check, Level.ERROR, msg))

    def warn(self, msg, check=None):
        self.errors.append((check, Level.WARN, msg))

    def info(self, msg, check=None):
        self.errors.append((check, Level.INFO, msg))

    @property
    def ok(self):
        return not any(lvl == Level.ERROR for _, lvl, _ in self.errors)


def _ensure_nltk_data():
    _download_if_missing("taggers/averaged_perceptron_tagger_eng")
    _download_if_missing("tokenizers/punkt_tab")
    _download_if_missing("corpora/wordnet")


def _download_if_missing(resource):
    try:
        nltk.data.find(resource)
    except LookupError:
        nltk.download(resource.rsplit("/", maxsplit=1)[-1], quiet=True)


def _strip_comments(message):
    return "\n".join(
        line for line in message.split("\n") if not line.lstrip().startswith("#")
    )


def check_subject(  # noqa: PLR0913 Too many arguments in function definition (7 > 5)
    line,
    result,
    allowed_scopes=frozenset(),
    allowed_types=TYPES,
    max_subject_length=MAX_SUBJECT_LEN,
    min_description_length=0,
    *,
    require_scope=False,
):
    m = SUBJECT_RE.match(line)
    if not m:
        result.error(
            f"subject does not match 'type(scope): description': {line}",
            check=Check.SUBJECT,
        )
        return None

    if m.group("type") not in allowed_types:
        result.error(f"unknown type: {m.group('type')}", check=Check.SUBJECT)

    scope = m.group("scope")
    if require_scope and scope is None:
        result.error("scope is required", check=Check.SUBJECT)
    if allowed_scopes and scope is not None and scope not in allowed_scopes:
        result.error(f"unknown scope: {scope}", check=Check.SUBJECT)

    desc = m.group("desc")
    if desc[0].isupper():
        result.error("description must not start with uppercase", check=Check.SUBJECT)
    if desc.endswith("."):
        result.error("description must not end with period", check=Check.SUBJECT)
    if len(line) > max_subject_length:
        result.error(
            f"subject too long: {len(line)} > {max_subject_length}", check=Check.SUBJECT
        )
    if min_description_length > 0 and len(desc) < min_description_length:
        result.error(
            f"description too short: {len(desc)} < {min_description_length}",
            check=Check.SUBJECT,
        )
    return desc


def check_imperative(desc, result):
    _ensure_nltk_data()
    tokens = nltk.word_tokenize(desc.lower())
    if not tokens:
        return
    first = tokens[0]
    if _NON_IMPERATIVE_SUFFIX_RE.search(first):
        result.error(
            f"expected imperative verb, got '{first}' (non-imperative suffix)",
            check=Check.IMPERATIVE,
        )
        return
    base = wordnet.morphy(first, wordnet.VERB)
    if base is not None and base != first:
        result.error(
            f"expected imperative verb, got '{first}' (inflected form of '{base}')",
            check=Check.IMPERATIVE,
        )
        return
    tagged = nltk.pos_tag(["to", *tokens])
    if tagged[1][1] != "VB":
        if wordnet.morphy(first, wordnet.VERB) == first:
            return
        result.error(
            f"expected imperative verb, got '{tagged[1][0]}' (POS={tagged[1][1]})",
            check=Check.IMPERATIVE,
        )


def check_body(lines, result):
    if len(lines) < 3:  # noqa: PLR2004
        result.error("missing body", check=Check.BODY)
        return
    if lines[1].strip():
        result.error("missing blank line between subject and body", check=Check.BODY)
    body_lines = [ln for ln in lines[2:] if not _TRAILER_RE.match(ln)]
    if not any(ln.strip() for ln in body_lines):
        result.error("missing body", check=Check.BODY)


def check_signed_off(message, result):
    if not SIGNED_OFF_RE.search(message):
        result.error("missing 'Signed-off-by' trailer", check=Check.SIGNED_OFF)


def check_required_trailers(message, required, result):
    for trailer in required:
        pattern = re.compile(rf"^{re.escape(trailer)}:\s+\S", re.MULTILINE)
        if not pattern.search(message):
            result.error(f"missing required trailer: {trailer}")


def check_signature(rev, result):
    proc = subprocess.run(  # noqa: S603
        ["git", "verify-commit", rev],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        timeout=_git_timeout(),
    )
    if proc.returncode != 0:
        result.error("commit is not signed (GPG/SSH)", check=Check.SIGNATURE)
        return

    output = proc.stderr.lower()
    sig_type = "SSH" if "ssh" in output else "GPG"
    result.info(f"signature type: {sig_type}", check=Check.SIGNATURE)


def _get_message(rev):
    try:
        return subprocess.check_output(  # noqa: S603
            ["git", "log", "-1", "--format=%B", rev],  # noqa: S607
            text=True,
            stderr=subprocess.PIPE,
            timeout=_git_timeout(),
        ).strip()
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.strip()
        if "unknown revision" in stderr or "ambiguous argument" in stderr:
            sys.exit("no commits yet")
        sys.exit(f"git error: {stderr}")


def _get_range_revs(rev_range, *, include_merges=False):
    cmd = ["git", "log", "--format=%H"]
    if not include_merges:
        cmd.append("--no-merges")
    cmd.append(rev_range)
    try:
        output = subprocess.check_output(  # noqa: S603
            cmd,
            text=True,
            stderr=subprocess.PIPE,
            timeout=_git_timeout(),
        ).strip()
    except subprocess.CalledProcessError as e:
        sys.exit(f"git error: {e.stderr.strip()}")
    return output.split("\n") if output else []


@dataclass
class Args:
    rev: str | None
    message: str
    enabled: frozenset
    allowed_scopes: frozenset
    require_scope: bool
    allowed_types: frozenset
    max_subject_length: int
    min_description_length: int
    rev_range: str | None
    allow_empty: bool
    include_merges: bool
    required_trailers: list
    output: OutputFormat


def _resolve_enabled(args, config, parser):
    if args.enable or args.disable:
        enabled = (
            frozenset(_parse_checks(parser, args.enable)) if args.enable else ALL_CHECKS
        )
        if args.disable:
            enabled = enabled - frozenset(_parse_checks(parser, args.disable))
    elif config.get("enable"):
        enabled = frozenset(_parse_config_checks(config, "enable"))
    elif config.get("disable"):
        enabled = ALL_CHECKS - frozenset(_parse_config_checks(config, "disable"))
    else:
        enabled = ALL_CHECKS
    return enabled


def _resolve_max_subject_length(args, config):
    if args.max_subject_length is not None:
        return args.max_subject_length
    if "max-subject-length" in config:
        return config["max-subject-length"]
    return MAX_SUBJECT_LEN


def _resolve_min_description_length(args, config):
    if args.min_description_length is not None:
        return args.min_description_length
    if "min-description-length" in config:
        return config["min-description-length"]
    return 0


def _resolve_required_trailers(args, config):
    if args.require_trailer:
        return [t.strip() for t in args.require_trailer.split(",")]
    if config.get("require-trailers"):
        return list(config["require-trailers"])
    return []


def _resolve_types(args, config):
    if args.types:
        return frozenset(t.strip() for t in args.types.split(","))
    if config.get("types"):
        return frozenset(config["types"])
    return TYPES


def _resolve_scopes(args, config):
    if args.scopes:
        allowed_scopes = frozenset(s.strip() for s in args.scopes.split(","))
    elif config.get("scopes"):
        allowed_scopes = frozenset(config["scopes"])
    else:
        allowed_scopes = frozenset()

    if args.require_scope:
        require_scope = True
    elif "require-scope" in config:
        require_scope = config["require-scope"]
    else:
        require_scope = False

    return allowed_scopes, require_scope


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
    parser.add_argument(
        "--scopes",
        metavar="SCOPE[,SCOPE,...]",
        help="allowed scope values (any scope accepted if not set)",
    )
    parser.add_argument(
        "--require-scope",
        action="store_true",
        default=False,
        help="require a scope in the subject line",
    )
    parser.add_argument(
        "--types",
        metavar="TYPE[,TYPE,...]",
        help="allowed commit types (replaces defaults when set)",
    )
    parser.add_argument(
        "--max-subject-length",
        type=int,
        default=None,
        metavar="N",
        help=f"maximum subject line length (default: {MAX_SUBJECT_LEN})",
    )
    parser.add_argument(
        "--min-description-length",
        type=int,
        default=None,
        metavar="N",
        help="minimum description length in characters (default: 0, off)",
    )
    parser.add_argument(
        "--range",
        dest="rev_range",
        metavar="REF..REF",
        help="check all commits in the given revision range",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        default=False,
        help="exit 0 when --range yields no commits (default: exit 1)",
    )
    parser.add_argument(
        "--require-trailer",
        metavar="TRAILER[,TRAILER,...]",
        help="require these trailers in the commit message",
    )
    parser.add_argument(
        "--include-merges",
        action="store_true",
        default=False,
        help="include merge commits when checking a range (default: excluded)",
    )
    parser.add_argument(
        "--output",
        choices=[f.value for f in OutputFormat],
        default=OutputFormat.TEXT,
        help="output format: text (default) or jsonl",
    )
    args = parser.parse_args()
    config = _load_config()
    enabled = _resolve_enabled(args, config, parser)
    allowed_scopes, require_scope = _resolve_scopes(args, config)
    allowed_types = _resolve_types(args, config)
    max_subject_length = _resolve_max_subject_length(args, config)
    min_description_length = _resolve_min_description_length(args, config)
    required_trailers = _resolve_required_trailers(args, config)

    if args.allow_empty and not args.rev_range:
        parser.error("--allow-empty requires --range")
    if args.include_merges and not args.rev_range:
        parser.error("--include-merges requires --range")

    if args.rev_range:
        if args.rev is not None or args.message_file:
            parser.error("--range cannot be combined with rev or --message-file")
        rev = None
        message = ""
    elif args.message_file:
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

    return Args(
        rev=rev,
        message=message,
        enabled=enabled,
        allowed_scopes=allowed_scopes,
        require_scope=require_scope,
        allowed_types=allowed_types,
        max_subject_length=max_subject_length,
        min_description_length=min_description_length,
        rev_range=args.rev_range,
        allow_empty=args.allow_empty,
        include_merges=args.include_merges,
        required_trailers=required_trailers,
        output=OutputFormat(args.output),
    )


def _report_jsonl(result, sha, subject):
    record = {
        "sha": sha,
        "subject": subject,
        "ok": result.ok,
        "results": [
            {"check": check, "level": str(level), "message": msg}
            for check, level, msg in result.errors
        ],
    }
    print(json.dumps(record))
    return 0 if result.ok else 1


def _report_text(result):
    for check, level, msg in result.errors:
        prefix = f"[{check}] " if check else ""
        print(f"  {PREFIXES[level]} {prefix}{msg}")

    if result.ok:
        print(f"  {PREFIXES[Level.OK]} all checks passed")

    return 0 if result.ok else 1


def _run_checks(args, rev, message, result):
    lines = message.split("\n")
    desc = None
    if Check.SUBJECT in args.enabled:
        desc = check_subject(
            lines[0],
            result,
            args.allowed_scopes,
            args.allowed_types,
            args.max_subject_length,
            args.min_description_length,
            require_scope=args.require_scope,
        )
    if Check.IMPERATIVE in args.enabled:
        if desc is None:
            m = SUBJECT_RE.match(lines[0])
            desc = m.group("desc") if m else None
        if desc:
            check_imperative(desc, result)
    if Check.BODY in args.enabled:
        check_body(lines, result)
    if Check.SIGNED_OFF in args.enabled:
        check_signed_off(message, result)
    if args.required_trailers:
        check_required_trailers(message, args.required_trailers, result)
    if Check.SIGNATURE in args.enabled and rev:
        check_signature(rev, result)


def main():
    args = _parse_args()

    if args.rev_range:
        revs = _get_range_revs(args.rev_range, include_merges=args.include_merges)
        if not revs:
            sys.stderr.write("no commits in range\n")
            return 0 if args.allow_empty else 1
        failed = False
        for rev in revs:
            message = _strip_comments(_get_message(rev))
            subject = message.split("\n")[0]
            result = Result()
            _run_checks(args, rev, message, result)
            if args.output == OutputFormat.JSONL:
                if _report_jsonl(result, rev, subject) != 0:
                    failed = True
            else:
                print(f"{rev[:7]} {subject}")
                if _report_text(result) != 0:
                    failed = True
        return 1 if failed else 0

    subject = args.message.split("\n")[0]
    result = Result()
    _run_checks(args, args.rev, args.message, result)
    if args.output == OutputFormat.JSONL:
        return _report_jsonl(result, args.rev, subject)
    return _report_text(result)
