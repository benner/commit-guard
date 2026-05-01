import json
import re
import subprocess
from argparse import ArgumentParser, Namespace
from unittest.mock import MagicMock, patch

import pytest

from git_commit_guard import (
    GIT_TIMEOUT,
    MAX_SUBJECT_LEN,
    TYPES,
    Result,
    _download_if_missing,
    _ensure_nltk_data,
    _get_message,
    _get_range_revs,
    _git_timeout,
    _load_config,
    _parse_checks,
    _parse_config_checks,
    _report_jsonl,
    _report_text,
    _resolve_max_subject_length,
    _resolve_min_description_length,
    _resolve_no_trailing_chars,
    _resolve_require_lowercase,
    _resolve_required_trailers,
    _resolve_subject_pattern,
    _resolve_types,
    _strip_comments,
    check_body,
    check_imperative,
    check_required_trailers,
    check_signature,
    check_signed_off,
    check_subject,
    check_subject_pattern,
    main,
)


@pytest.fixture(scope="session", autouse=True)
def nltk_data():
    _ensure_nltk_data()


class TestResult:
    def test_ok_when_empty(self):
        assert Result().ok

    def test_not_ok_with_error(self):
        r = Result()
        r.error("bad")
        assert not r.ok

    def test_ok_with_only_warn(self):
        r = Result()
        r.warn("hmm")
        assert r.ok

    def test_ok_with_only_info(self):
        r = Result()
        r.info("fyi")
        assert r.ok


class TestStripComments:
    def test_removes_comment_lines(self):
        assert _strip_comments("# comment\nfoo") == "foo"

    def test_keeps_non_comment_lines(self):
        assert _strip_comments("foo\nbar") == "foo\nbar"

    def test_removes_indented_comments(self):
        assert _strip_comments("  # indented\nfoo") == "foo"

    def test_empty_string(self):
        assert _strip_comments("") == ""


class TestCheckSubject:
    def test_valid_simple(self):
        r = Result()
        desc = check_subject("fix: add token refresh", r)
        assert desc == "add token refresh"
        assert r.ok

    def test_valid_with_scope(self):
        r = Result()
        desc = check_subject("feat(auth): add login", r)
        assert desc == "add login"
        assert r.ok

    def test_valid_breaking_change(self):
        r = Result()
        check_subject("feat!: drop v1 support", r)
        assert r.ok

    def test_valid_breaking_change_with_scope(self):
        r = Result()
        check_subject("feat(api)!: drop v1", r)
        assert r.ok

    def test_invalid_format(self):
        r = Result()
        desc = check_subject("not a commit", r)
        assert desc is None
        assert not r.ok

    def test_unknown_type(self):
        r = Result()
        check_subject("unknown: add thing", r)
        assert not r.ok

    def test_uppercase_description(self):
        r = Result()
        check_subject("fix: Add token", r)
        assert not r.ok

    def test_uppercase_description_allowed(self):
        r = Result()
        check_subject("fix: Add token", r, require_lowercase=False)
        assert r.ok

    def test_lowercase_required_by_default(self):
        r = Result()
        check_subject("fix: add token", r)
        assert r.ok

    def test_trailing_period(self):
        r = Result()
        check_subject("fix: add token.", r)
        assert not r.ok

    def test_trailing_char_custom(self):
        r = Result()
        check_subject("fix: add token!", r, no_trailing_chars=frozenset("!"))
        assert not r.ok

    def test_trailing_char_space(self):
        r = Result()
        check_subject("fix: add token ", r, no_trailing_chars=frozenset(". "))
        assert not r.ok

    def test_trailing_chars_empty_disables_check(self):
        r = Result()
        check_subject("fix: add token.", r, no_trailing_chars=frozenset())
        assert r.ok

    def test_trailing_chars_multiple(self):
        r = Result()
        check_subject("fix: add token!", r, no_trailing_chars=frozenset(".!"))
        assert not r.ok

    def test_subject_too_long(self):
        r = Result()
        check_subject("fix: " + "a" * 68, r)  # 73 chars total
        assert not r.ok

    def test_subject_at_max_length(self):
        r = Result()
        check_subject("fix: " + "a" * 67, r)  # exactly 72 chars
        assert r.ok

    def test_scope_in_allowlist_passes(self):
        r = Result()
        check_subject("fix(auth): add token", r, allowed_scopes=frozenset(["auth"]))
        assert r.ok

    def test_scope_not_in_allowlist_fails(self):
        r = Result()
        check_subject("fix(api): add token", r, allowed_scopes=frozenset(["auth"]))
        assert not r.ok

    def test_no_scope_with_allowlist_passes(self):
        r = Result()
        check_subject("fix: add token", r, allowed_scopes=frozenset(["auth"]))
        assert r.ok

    def test_require_scope_without_scope_fails(self):
        r = Result()
        check_subject("fix: add token", r, require_scope=True)
        assert not r.ok

    def test_require_scope_with_scope_passes(self):
        r = Result()
        check_subject("fix(auth): add token", r, require_scope=True)
        assert r.ok

    def test_empty_allowlist_accepts_any_scope(self):
        r = Result()
        check_subject("fix(anything): add token", r, allowed_scopes=frozenset())
        assert r.ok

    def test_custom_max_length_enforced(self):
        r = Result()
        check_subject("fix: add thing", r, max_subject_length=10)
        assert not r.ok

    def test_custom_max_length_passes(self):
        r = Result()
        check_subject("fix: ok", r, max_subject_length=10)
        assert r.ok

    def test_min_description_length_zero_disables_check(self):
        r = Result()
        check_subject("fix: add x", r, min_description_length=0)
        assert r.ok

    def test_min_description_length_enforced(self):
        r = Result()
        check_subject("fix: add x", r, min_description_length=6)
        assert not r.ok
        assert any("description too short" in m for _, _, m in r.errors)

    def test_min_description_length_exact_passes(self):
        r = Result()
        check_subject("fix: hello", r, min_description_length=5)
        assert r.ok

    def test_custom_type_passes(self):
        r = Result()
        check_subject("wip: add thing", r, allowed_types=frozenset(["wip"]))
        assert r.ok

    def test_type_not_in_custom_list_fails(self):
        r = Result()
        check_subject("feat: add thing", r, allowed_types=frozenset(["wip"]))
        assert not r.ok

    @pytest.mark.parametrize(
        "type_",
        [
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
        ],
    )
    def test_all_valid_types(self, type_):
        r = Result()
        check_subject(f"{type_}: add thing", r)
        assert r.ok


class TestCheckBody:
    def test_subject_only(self):
        r = Result()
        check_body(["fix: add thing"], r)
        assert not r.ok

    def test_subject_and_blank_only(self):
        r = Result()
        check_body(["fix: add thing", ""], r)
        assert not r.ok

    def test_valid_body(self):
        r = Result()
        check_body(["fix: add thing", "", "body text here"], r)
        assert r.ok

    def test_missing_blank_line(self):
        r = Result()
        check_body(["fix: add thing", "body text", "more"], r)
        assert not r.ok

    def test_missing_blank_line_two_lines(self):
        r = Result()
        check_body(["fix: add thing", "body text"], r)
        assert not r.ok
        assert any("blank line" in msg for _, _, msg in r.errors)

    def test_blank_body_content(self):
        r = Result()
        check_body(["fix: add thing", "", "   "], r)
        assert not r.ok

    def test_multiline_body(self):
        r = Result()
        check_body(["fix: add thing", "", "line one", "line two"], r)
        assert r.ok

    def test_trailer_only_is_not_body(self):
        r = Result()
        check_body(["fix: add thing", "", "Signed-off-by: Name <name@example.com>"], r)
        assert not r.ok

    def test_body_with_trailer_passes(self):
        r = Result()
        trailer = "Signed-off-by: Name <name@example.com>"
        check_body(["fix: add thing", "", "actual body", trailer], r)
        assert r.ok


class TestCheckSignedOff:
    def test_valid(self):
        r = Result()
        check_signed_off(
            "fix: add thing\n\nbody\n\nSigned-off-by: John Doe <john@example.com>",
            r,
        )
        assert r.ok

    def test_missing(self):
        r = Result()
        check_signed_off("fix: add thing\n\nbody", r)
        assert not r.ok

    def test_malformed_no_email(self):
        r = Result()
        check_signed_off("fix: add thing\n\nSigned-off-by: John Doe", r)
        assert not r.ok


class TestCheckRequiredTrailers:
    def test_present_passes(self):
        r = Result()
        check_required_trailers("fix: add x\n\nbody\n\nCloses: #42", ["Closes"], r)
        assert r.ok

    def test_missing_fails(self):
        r = Result()
        check_required_trailers("fix: add x\n\nbody", ["Closes"], r)
        assert not r.ok
        assert "missing required trailer: Closes" in r.errors[0][2]

    def test_multiple_all_present_passes(self):
        r = Result()
        check_required_trailers(
            "fix: add x\n\nbody\n\nCloses: #42\nReviewed-by: Jane",
            ["Closes", "Reviewed-by"],
            r,
        )
        assert r.ok

    def test_multiple_one_missing_fails(self):
        r = Result()
        check_required_trailers(
            "fix: add x\n\nbody\n\nCloses: #42",
            ["Closes", "Reviewed-by"],
            r,
        )
        assert not r.ok
        assert any("Reviewed-by" in msg for _, _, msg in r.errors)

    def test_case_sensitive(self):
        r = Result()
        check_required_trailers("fix: add x\n\nbody\n\ncloses: #42", ["Closes"], r)
        assert not r.ok

    def test_empty_required_list_always_passes(self):
        r = Result()
        check_required_trailers("fix: add x", [], r)
        assert r.ok


class TestResolveRequiredTrailers:
    def test_defaults_to_empty(self):
        assert _resolve_required_trailers(Namespace(require_trailer=None), {}) == []

    def test_cli_flag_single(self):
        result = _resolve_required_trailers(Namespace(require_trailer="Closes"), {})
        assert result == ["Closes"]

    def test_cli_flag_multiple(self):
        result = _resolve_required_trailers(
            Namespace(require_trailer="Closes,Reviewed-by"), {}
        )
        assert result == ["Closes", "Reviewed-by"]

    def test_cli_flag_strips_spaces(self):
        result = _resolve_required_trailers(
            Namespace(require_trailer="Closes, Reviewed-by"), {}
        )
        assert result == ["Closes", "Reviewed-by"]

    def test_config(self):
        result = _resolve_required_trailers(
            Namespace(require_trailer=None),
            {"require-trailers": ["Closes", "Reviewed-by"]},
        )
        assert result == ["Closes", "Reviewed-by"]

    def test_cli_overrides_config(self):
        result = _resolve_required_trailers(
            Namespace(require_trailer="Fixes"),
            {"require-trailers": ["Closes"]},
        )
        assert result == ["Fixes"]


class TestCheckSubjectPattern:
    def test_matching_subject_passes(self):
        r = Result()
        check_subject_pattern("feat: add PROJ-123 login", re.compile(r"[A-Z]+-\d+"), r)
        assert r.ok

    def test_non_matching_subject_fails(self):
        r = Result()
        check_subject_pattern(
            "feat: implement OAuth login flow", re.compile(r"[A-Z]+-\d+"), r
        )
        assert not r.ok
        assert "must match pattern" in r.errors[0][2]
        assert "[A-Z]+-\\d+" in r.errors[0][2]

    def test_error_includes_pattern(self):
        r = Result()
        check_subject_pattern("fix: oops", re.compile(r"#\d+"), r)
        assert "#\\d+" in r.errors[0][2]


class TestResolveSubjectPattern:
    def test_defaults_to_none(self):
        assert (
            _resolve_subject_pattern(Namespace(require_subject_pattern=None), {})
            is None
        )

    def test_cli_flag(self):
        result = _resolve_subject_pattern(
            Namespace(require_subject_pattern="[A-Z]+-\\d+"), {}
        )
        assert result == "[A-Z]+-\\d+"

    def test_config(self):
        result = _resolve_subject_pattern(
            Namespace(require_subject_pattern=None),
            {"require-subject-pattern": "#\\d+"},
        )
        assert result == "#\\d+"

    def test_cli_overrides_config(self):
        result = _resolve_subject_pattern(
            Namespace(require_subject_pattern="[A-Z]+-\\d+"),
            {"require-subject-pattern": "#\\d+"},
        )
        assert result == "[A-Z]+-\\d+"


class TestCheckImperative:
    def test_imperative_verb_passes(self):
        r = Result()
        check_imperative("add token refresh", r)
        assert r.ok

    def test_ed_suffix_fails(self):
        r = Result()
        check_imperative("added token refresh", r)
        assert not r.ok

    def test_ing_suffix_fails(self):
        r = Result()
        check_imperative("adding token refresh", r)
        assert not r.ok

    def test_third_person_fails(self):
        r = Result()
        check_imperative("adds token refresh", r)
        assert not r.ok

    def test_third_person_es_suffix_fails(self):
        r = Result()
        check_imperative("fixes the bug", r)
        assert not r.ok

    def test_non_whitelist_imperative_passes(self):
        r = Result()
        check_imperative("refactor authentication module", r)
        assert r.ok

    def test_write_imperative_passes(self):
        r = Result()
        check_imperative("write unit tests", r)
        assert r.ok

    def test_tagger_misclassified_verb_passes(self):
        # 'disable' is tagged non-VB by the tagger but wordnet confirms it as a verb
        r = Result()
        check_imperative("disable feature flag", r)
        assert r.ok

    def test_refactor_passes(self):
        r = Result()
        check_imperative("refactor authentication module", r)
        assert r.ok

    def test_vendor_passes(self):
        r = Result()
        check_imperative("vendor third-party libs", r)
        assert r.ok

    def test_configure_passes(self):
        r = Result()
        check_imperative("configure logging pipeline", r)
        assert r.ok

    def test_empty_desc_passes(self):
        r = Result()
        check_imperative("", r)
        assert r.ok

    def test_pos_fallback_unknown_word_fails(self):
        r = Result()
        with (
            patch("git_commit_guard.wordnet.morphy", return_value=None),
            patch(
                "git_commit_guard.nltk.pos_tag",
                return_value=[("to", "TO"), ("xyzzy", "NN")],
            ),
        ):
            check_imperative("xyzzy something", r)
        assert not r.ok
        assert "POS=NN" in r.errors[0][2]


class TestDownloadIfMissing:
    def test_skips_download_when_present(self):
        with (
            patch("git_commit_guard.nltk.data.find"),
            patch("git_commit_guard.nltk.download") as mock_dl,
        ):
            _download_if_missing("tokenizers/punkt_tab")
        mock_dl.assert_not_called()

    def test_downloads_when_missing(self):
        with (
            patch("git_commit_guard.nltk.data.find", side_effect=LookupError),
            patch("git_commit_guard.nltk.download") as mock_dl,
        ):
            _download_if_missing("tokenizers/punkt_tab")
        mock_dl.assert_called_once_with("punkt_tab", quiet=True)


class TestCheckSignature:
    def test_unsigned_commit(self):
        r = Result()
        proc = MagicMock(returncode=1)
        with patch("git_commit_guard.subprocess.run", return_value=proc):
            check_signature("abc123", r)
        assert not r.ok

    def test_gpg_signed_commit(self):
        r = Result()
        proc = MagicMock(returncode=0, stderr="gpg signature verified")
        with patch("git_commit_guard.subprocess.run", return_value=proc):
            check_signature("abc123", r)
        assert r.ok
        assert any("GPG" in msg for _, _, msg in r.errors)

    def test_ssh_signed_commit(self):
        r = Result()
        proc = MagicMock(returncode=0, stderr="Good ssh signature")
        with patch("git_commit_guard.subprocess.run", return_value=proc):
            check_signature("abc123", r)
        assert r.ok
        assert any("SSH" in msg for _, _, msg in r.errors)


class TestGetMessage:
    def test_success(self):
        with patch(
            "git_commit_guard.subprocess.check_output",
            return_value="fix: add thing\n\n",
        ):
            assert _get_message("abc123") == "fix: add thing"

    def test_unknown_revision(self):
        err = subprocess.CalledProcessError(128, "git")
        err.stderr = "fatal: unknown revision 'abc'"
        with (
            patch("git_commit_guard.subprocess.check_output", side_effect=err),
            pytest.raises(SystemExit, match="no commits yet"),
        ):
            _get_message("abc")

    def test_ambiguous_argument(self):
        err = subprocess.CalledProcessError(128, "git")
        err.stderr = "fatal: ambiguous argument 'HEAD'"
        with (
            patch("git_commit_guard.subprocess.check_output", side_effect=err),
            pytest.raises(SystemExit, match="no commits yet"),
        ):
            _get_message("HEAD")

    def test_other_git_error(self):
        err = subprocess.CalledProcessError(128, "git")
        err.stderr = "fatal: not a git repository"
        with (
            patch("git_commit_guard.subprocess.check_output", side_effect=err),
            pytest.raises(SystemExit, match="git error"),
        ):
            _get_message("abc")


class TestLoadConfig:
    def test_returns_empty_when_no_file(self, tmp_path):
        assert _load_config(tmp_path) == {}

    def test_loads_file_in_start_dir(self, tmp_path):
        (tmp_path / ".commit-guard.toml").write_text('disable = ["signature"]\n')
        assert _load_config(tmp_path) == {"disable": ["signature"]}

    def test_loads_file_from_parent(self, tmp_path):
        (tmp_path / ".commit-guard.toml").write_text('disable = ["body"]\n')
        subdir = tmp_path / "sub"
        subdir.mkdir()
        assert _load_config(subdir) == {"disable": ["body"]}

    def test_first_found_wins(self, tmp_path):
        (tmp_path / ".commit-guard.toml").write_text('disable = ["body"]\n')
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / ".commit-guard.toml").write_text('disable = ["signature"]\n')
        assert _load_config(subdir) == {"disable": ["signature"]}


class TestParseConfigChecks:
    def test_disable_list(self):
        checks = _parse_config_checks({"disable": ["signature", "body"]}, "disable")
        assert len(checks) == 2

    def test_missing_key_returns_empty(self):
        assert _parse_config_checks({}, "disable") == []

    def test_invalid_check_name_exits(self):
        with pytest.raises(SystemExit, match=r"\.commit-guard\.toml"):
            _parse_config_checks({"disable": ["bogus"]}, "disable")


class TestResolveMaxSubjectLength:
    def test_defaults_when_no_config_or_flag(self):
        result = _resolve_max_subject_length(Namespace(max_subject_length=None), {})
        assert result == MAX_SUBJECT_LEN

    def test_cli_flag_overrides_default(self):
        result = _resolve_max_subject_length(Namespace(max_subject_length=50), {})
        assert result == 50

    def test_config_overrides_default(self):
        result = _resolve_max_subject_length(
            Namespace(max_subject_length=None), {"max-subject-length": 60}
        )
        assert result == 60

    def test_cli_overrides_config(self):
        result = _resolve_max_subject_length(
            Namespace(max_subject_length=50), {"max-subject-length": 60}
        )
        assert result == 50


class TestResolveMinDescriptionLength:
    def test_defaults_to_zero(self):
        result = _resolve_min_description_length(
            Namespace(min_description_length=None), {}
        )
        assert result == 0

    def test_cli_flag_overrides_default(self):
        result = _resolve_min_description_length(
            Namespace(min_description_length=10), {}
        )
        assert result == 10

    def test_config_overrides_default(self):
        result = _resolve_min_description_length(
            Namespace(min_description_length=None), {"min-description-length": 8}
        )
        assert result == 8

    def test_cli_overrides_config(self):
        result = _resolve_min_description_length(
            Namespace(min_description_length=10), {"min-description-length": 8}
        )
        assert result == 10


class TestResolveRequireLowercase:
    def test_cli_flag_overrides_default(self):
        assert (
            _resolve_require_lowercase(Namespace(require_lowercase=False), {}) is False
        )

    def test_config_overrides_default(self):
        result = _resolve_require_lowercase(
            Namespace(require_lowercase=None), {"require-lowercase": False}
        )
        assert result is False

    def test_default_is_true(self):
        assert _resolve_require_lowercase(Namespace(require_lowercase=None), {}) is True


class TestResolveNoTrailingChars:
    def test_cli_flag_overrides_default(self):
        result = _resolve_no_trailing_chars(Namespace(no_trailing_chars=".,!"), {})
        assert result == frozenset({".", "!"})

    def test_config_overrides_default(self):
        result = _resolve_no_trailing_chars(
            Namespace(no_trailing_chars=None), {"no-trailing-chars": [".", "!"]}
        )
        assert result == frozenset({".", "!"})

    def test_default_includes_common_punctuation_and_space(self):
        result = _resolve_no_trailing_chars(Namespace(no_trailing_chars=None), {})
        assert result == frozenset({".", "!", "?", " "})


class TestGitTimeout:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("COMMIT_GUARD_GIT_TIMEOUT", raising=False)
        assert _git_timeout() == GIT_TIMEOUT

    def test_env_var(self, monkeypatch):
        monkeypatch.setenv("COMMIT_GUARD_GIT_TIMEOUT", "30")
        assert _git_timeout() == 30


class TestResolveTypes:
    def test_defaults_when_no_config_or_flag(self):
        assert _resolve_types(Namespace(types=None), {}) == TYPES

    def test_cli_flag_replaces_defaults(self):
        result = _resolve_types(Namespace(types="wip,deploy"), {})
        assert result == frozenset({"wip", "deploy"})

    def test_config_replaces_defaults(self):
        result = _resolve_types(Namespace(types=None), {"types": ["wip", "deploy"]})
        assert result == frozenset({"wip", "deploy"})

    def test_cli_overrides_config(self):
        result = _resolve_types(Namespace(types="wip"), {"types": ["deploy"]})
        assert result == frozenset({"wip"})


class TestParseChecks:
    def test_invalid_check_name(self):
        parser = ArgumentParser()
        with pytest.raises(SystemExit):
            _parse_checks(parser, "invalid")


class TestReport:
    def test_all_passed(self, capsys):
        r = Result()
        ret = _report_text(r)
        assert ret == 0
        assert "all checks passed" in capsys.readouterr().out

    def test_with_error(self, capsys):
        r = Result()
        r.error("something broke")
        ret = _report_text(r)
        assert ret == 1
        assert "something broke" in capsys.readouterr().out

    def test_with_warning_returns_zero(self, capsys):
        r = Result()
        r.warn("heads up")
        ret = _report_text(r)
        assert ret == 0
        captured = capsys.readouterr().out
        assert "heads up" in captured
        assert "all checks passed" in captured

    def test_no_ansi_when_not_tty(self, capsys):
        r = Result()
        r.error("something broke")
        with patch("sys.stdout.isatty", return_value=False):
            _report_text(r)
        assert "\033[" not in capsys.readouterr().out

    def test_ansi_when_tty(self, capsys):
        r = Result()
        r.error("something broke")
        with patch("sys.stdout.isatty", return_value=True):
            _report_text(r)
        assert "\033[" in capsys.readouterr().out

    def test_ok_no_ansi_when_not_tty(self, capsys):
        r = Result()
        with patch("sys.stdout.isatty", return_value=False):
            _report_text(r)
        assert "\033[" not in capsys.readouterr().out


class TestReportJsonl:
    def test_ok_commit(self, capsys):
        r = Result()
        ret = _report_jsonl(r, "abc1234567890", "fix: add thing")
        assert ret == 0
        out = capsys.readouterr().out

        data = json.loads(out)
        assert data["sha"] == "abc1234567890"
        assert data["subject"] == "fix: add thing"
        assert data["ok"] is True
        assert data["results"] == []

    def test_failed_commit(self, capsys):
        r = Result()
        r.error("missing body", check="body")
        ret = _report_jsonl(r, "abc1234567890", "fix: add thing")
        assert ret == 1

        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is False
        assert len(data["results"]) == 1
        assert data["results"][0] == {
            "check": "body",
            "level": "error",
            "message": "missing body",
        }

    def test_null_sha(self, capsys):
        r = Result()
        ret = _report_jsonl(r, None, "fix: add thing")
        assert ret == 0

        data = json.loads(capsys.readouterr().out)
        assert data["sha"] is None

    def test_check_none_in_results(self, capsys):
        r = Result()
        r.error("missing required trailer: Closes")
        _report_jsonl(r, "abc", "fix: add thing")

        data = json.loads(capsys.readouterr().out)
        assert data["results"][0]["check"] is None

    def test_output_is_single_line(self, capsys):
        r = Result()
        _report_jsonl(r, "abc", "fix: add thing")
        out = capsys.readouterr().out
        assert out.count("\n") == 1


_VALID_MSG = "fix: add thing\n\nbody text\n\nSigned-off-by: A User <a@b.com>"


class TestMain:
    def test_from_message_file(self, tmp_path, capsys):
        f = tmp_path / "msg"
        f.write_text(_VALID_MSG)
        with patch(
            "sys.argv",
            ["cg", "--message-file", str(f), "--disable", "signature"],
        ):
            assert main() == 0
        assert "all checks passed" in capsys.readouterr().out

    def test_from_stdin(self):
        stdin = MagicMock()
        stdin.isatty.return_value = False
        stdin.read.return_value = _VALID_MSG
        with (
            patch("sys.argv", ["cg", "--disable", "signature"]),
            patch("sys.stdin", stdin),
        ):
            assert main() == 0

    def test_from_rev(self):
        with (
            patch("sys.argv", ["cg", "abc123", "--disable", "signature"]),
            patch("git_commit_guard._get_message", return_value=_VALID_MSG),
        ):
            assert main() == 0

    def test_from_head(self):
        stdin = MagicMock()
        stdin.isatty.return_value = True
        with (
            patch("sys.argv", ["cg", "--disable", "signature"]),
            patch("sys.stdin", stdin),
            patch("git_commit_guard._get_message", return_value=_VALID_MSG),
        ):
            assert main() == 0

    def test_enable_flag(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text(_VALID_MSG)
        with patch("sys.argv", ["cg", "--message-file", str(f), "--enable", "subject"]):
            assert main() == 0

    def test_invalid_message_returns_one(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text("not a valid commit")
        with patch(
            "sys.argv",
            [
                "cg",
                "--message-file",
                str(f),
                "--disable",
                "signature,body,signed-off,imperative",
            ],
        ):
            assert main() == 1

    def test_signature_skipped_without_rev(self, tmp_path, capsys):
        f = tmp_path / "msg"
        f.write_text(_VALID_MSG)
        with patch(
            "sys.argv",
            ["cg", "--message-file", str(f), "--enable", "signature"],
        ):
            ret = main()
        assert ret == 0
        assert "all checks passed" in capsys.readouterr().out

    def test_imperative_only_no_subject_check(self, tmp_path):
        # imperative enabled, subject not — desc starts as None, parsed from line
        f = tmp_path / "msg"
        f.write_text(_VALID_MSG)
        with patch(
            "sys.argv",
            ["cg", "--message-file", str(f), "--enable", "imperative"],
        ):
            assert main() == 0

    def test_signature_with_rev(self):
        proc = MagicMock(returncode=0, stderr="gpg signature verified")
        with (
            patch("sys.argv", ["cg", "abc123", "--enable", "signature"]),
            patch("git_commit_guard._get_message", return_value=_VALID_MSG),
            patch("git_commit_guard.subprocess.run", return_value=proc),
        ):
            assert main() == 0

    def test_invalid_check_name_exits(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text(_VALID_MSG)
        with (
            patch("sys.argv", ["cg", "--message-file", str(f), "--enable", "bogus"]),
            pytest.raises(SystemExit),
        ):
            main()

    def test_config_disable_applied(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text(_VALID_MSG)
        disabled = {"disable": ["signature", "body", "signed-off", "imperative"]}
        with (
            patch("sys.argv", ["cg", "--message-file", str(f)]),
            patch("git_commit_guard._load_config", return_value=disabled),
        ):
            assert main() == 0

    def test_config_enable_applied(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text(_VALID_MSG)
        with (
            patch("sys.argv", ["cg", "--message-file", str(f)]),
            patch(
                "git_commit_guard._load_config",
                return_value={"enable": ["subject"]},
            ),
        ):
            assert main() == 0

    def test_scopes_flag_valid(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text("fix(auth): add token\n\nbody\n\nSigned-off-by: A User <a@b.com>")
        argv = [
            "cg",
            "--message-file",
            str(f),
            "--disable",
            "signature",
            "--scopes",
            "auth,api",
        ]
        with patch("sys.argv", argv):
            assert main() == 0

    def test_scopes_flag_invalid(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text("fix(db): add thing\n\nbody\n\nSigned-off-by: A User <a@b.com>")
        argv = [
            "cg",
            "--message-file",
            str(f),
            "--disable",
            "signature",
            "--scopes",
            "auth,api",
        ]
        with patch("sys.argv", argv):
            assert main() == 1

    def test_require_scope_flag(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text(_VALID_MSG)
        argv = [
            "cg",
            "--message-file",
            str(f),
            "--disable",
            "signature",
            "--require-scope",
        ]
        with patch("sys.argv", argv):
            assert main() == 1

    def test_scopes_from_config(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text("fix(db): add thing\n\nbody\n\nSigned-off-by: A User <a@b.com>")
        argv = ["cg", "--message-file", str(f), "--disable", "signature"]
        with (
            patch("sys.argv", argv),
            patch("git_commit_guard._load_config", return_value={"scopes": ["auth"]}),
        ):
            assert main() == 1

    def test_require_scope_from_config(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text(_VALID_MSG)
        argv = ["cg", "--message-file", str(f), "--disable", "signature"]
        with (
            patch("sys.argv", argv),
            patch(
                "git_commit_guard._load_config",
                return_value={"require-scope": True},
            ),
        ):
            assert main() == 1

    def test_cli_overrides_config(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text(_VALID_MSG)
        with (
            patch("sys.argv", ["cg", "--message-file", str(f), "--enable", "subject"]),
            patch(
                "git_commit_guard._load_config",
                return_value={"disable": ["subject"]},
            ),
        ):
            assert main() == 0

    def test_types_flag_valid(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text("wip: add thing\n\nbody\n\nSigned-off-by: A User <a@b.com>")
        argv = [
            "cg",
            "--message-file",
            str(f),
            "--disable",
            "signature",
            "--types",
            "wip,feat,fix",
        ]
        with patch("sys.argv", argv):
            assert main() == 0

    def test_types_flag_invalid(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text("chore: add thing\n\nbody\n\nSigned-off-by: A User <a@b.com>")
        argv = [
            "cg",
            "--message-file",
            str(f),
            "--disable",
            "signature",
            "--types",
            "feat,fix",
        ]
        with patch("sys.argv", argv):
            assert main() == 1

    def test_types_from_config(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text("wip: add thing\n\nbody\n\nSigned-off-by: A User <a@b.com>")
        argv = ["cg", "--message-file", str(f), "--disable", "signature"]
        with (
            patch("sys.argv", argv),
            patch(
                "git_commit_guard._load_config",
                return_value={"types": ["wip", "feat"]},
            ),
        ):
            assert main() == 0

    def test_max_subject_length_flag_passes(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text("fix: ok\n\nbody\n\nSigned-off-by: A User <a@b.com>")
        argv = [
            "cg",
            "--message-file",
            str(f),
            "--disable",
            "signature",
            "--max-subject-length",
            "10",
        ]
        with patch("sys.argv", argv):
            assert main() == 0

    def test_max_subject_length_flag_fails(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text(_VALID_MSG)
        argv = [
            "cg",
            "--message-file",
            str(f),
            "--disable",
            "signature",
            "--max-subject-length",
            "5",
        ]
        with patch("sys.argv", argv):
            assert main() == 1

    def test_max_subject_length_from_config(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text(_VALID_MSG)
        argv = ["cg", "--message-file", str(f), "--disable", "signature"]
        with (
            patch("sys.argv", argv),
            patch(
                "git_commit_guard._load_config",
                return_value={"max-subject-length": 5},
            ),
        ):
            assert main() == 1

    def test_max_subject_length_cli_overrides_config(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text(_VALID_MSG)
        argv = [
            "cg",
            "--message-file",
            str(f),
            "--disable",
            "signature",
            "--max-subject-length",
            "100",
        ]
        with (
            patch("sys.argv", argv),
            patch(
                "git_commit_guard._load_config",
                return_value={"max-subject-length": 5},
            ),
        ):
            assert main() == 0

    def test_min_description_length_flag_passes(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text("fix: add thing\n\nbody\n\nSigned-off-by: A User <a@b.com>")
        argv = [
            "cg",
            "--message-file",
            str(f),
            "--disable",
            "signature",
            "--min-description-length",
            "5",
        ]
        with patch("sys.argv", argv):
            assert main() == 0

    def test_min_description_length_flag_fails(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text("fix: add x\n\nbody\n\nSigned-off-by: A User <a@b.com>")
        argv = [
            "cg",
            "--message-file",
            str(f),
            "--disable",
            "signature,imperative",
            "--min-description-length",
            "6",
        ]
        with patch("sys.argv", argv):
            assert main() == 1

    def test_min_description_length_from_config(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text("fix: add x\n\nbody\n\nSigned-off-by: A User <a@b.com>")
        argv = ["cg", "--message-file", str(f), "--disable", "signature,imperative"]
        with (
            patch("sys.argv", argv),
            patch(
                "git_commit_guard._load_config",
                return_value={"min-description-length": 6},
            ),
        ):
            assert main() == 1

    def test_min_description_length_cli_overrides_config(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text("fix: add x\n\nbody\n\nSigned-off-by: A User <a@b.com>")
        argv = [
            "cg",
            "--message-file",
            str(f),
            "--disable",
            "signature,imperative",
            "--min-description-length",
            "3",
        ]
        with (
            patch("sys.argv", argv),
            patch(
                "git_commit_guard._load_config",
                return_value={"min-description-length": 6},
            ),
        ):
            assert main() == 0

    def test_types_cli_overrides_config(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text("wip: add thing\n\nbody\n\nSigned-off-by: A User <a@b.com>")
        argv = [
            "cg",
            "--message-file",
            str(f),
            "--disable",
            "signature",
            "--types",
            "wip",
        ]
        with (
            patch("sys.argv", argv),
            patch(
                "git_commit_guard._load_config",
                return_value={"types": ["deploy"]},
            ),
        ):
            assert main() == 0

    def test_range_all_pass(self):
        with (
            patch(
                "sys.argv",
                ["cg", "--range", "origin/main..HEAD", "--disable", "signature"],
            ),
            patch(
                "git_commit_guard._get_range_revs",
                return_value=["abc1234", "def5678"],
            ),
            patch("git_commit_guard._get_message", return_value=_VALID_MSG),
        ):
            assert main() == 0

    def test_range_one_fails(self):
        messages = {"abc1234": _VALID_MSG, "def5678": "not a valid commit message"}
        with (
            patch(
                "sys.argv",
                [
                    "cg",
                    "--range",
                    "origin/main..HEAD",
                    "--disable",
                    "signature,body,signed-off,imperative",
                ],
            ),
            patch(
                "git_commit_guard._get_range_revs",
                return_value=["abc1234", "def5678"],
            ),
            patch(
                "git_commit_guard._get_message",
                side_effect=lambda rev: messages[rev],
            ),
        ):
            assert main() == 1

    def test_range_all_fail_returns_one(self):
        with (
            patch(
                "sys.argv",
                [
                    "cg",
                    "--range",
                    "origin/main..HEAD",
                    "--disable",
                    "signature,body,signed-off,imperative",
                ],
            ),
            patch(
                "git_commit_guard._get_range_revs",
                return_value=["abc1234"],
            ),
            patch(
                "git_commit_guard._get_message",
                return_value="not a valid commit message",
            ),
        ):
            assert main() == 1

    def test_range_empty_returns_one(self, capsys):
        with (
            patch("sys.argv", ["cg", "--range", "origin/main..HEAD"]),
            patch("git_commit_guard._get_range_revs", return_value=[]),
        ):
            assert main() == 1
        assert "no commits in range" in capsys.readouterr().err

    def test_range_empty_with_allow_empty_returns_zero(self, capsys):
        with (
            patch("sys.argv", ["cg", "--range", "origin/main..HEAD", "--allow-empty"]),
            patch("git_commit_guard._get_range_revs", return_value=[]),
        ):
            assert main() == 0
        assert "no commits in range" in capsys.readouterr().err

    def test_allow_empty_without_range_exits(self):
        with (
            patch("sys.argv", ["cg", "--allow-empty"]),
            pytest.raises(SystemExit),
        ):
            main()

    def test_include_merges_without_range_exits(self):
        with (
            patch("sys.argv", ["cg", "--include-merges"]),
            pytest.raises(SystemExit),
        ):
            main()

    def test_range_include_merges_flag(self):
        with (
            patch(
                "sys.argv",
                [
                    "cg",
                    "--range",
                    "origin/main..HEAD",
                    "--include-merges",
                    "--disable",
                    "signature",
                ],
            ),
            patch(
                "git_commit_guard._get_range_revs",
                return_value=["abc1234"],
            ) as mock,
            patch("git_commit_guard._get_message", return_value=_VALID_MSG),
        ):
            main()
        mock.assert_called_once_with("origin/main..HEAD", include_merges=True)

    def test_range_conflicts_with_rev(self):
        with (
            patch("sys.argv", ["cg", "abc123", "--range", "origin/main..HEAD"]),
            pytest.raises(SystemExit),
        ):
            main()

    def test_range_conflicts_with_message_file(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text(_VALID_MSG)
        with (
            patch(
                "sys.argv",
                ["cg", "--message-file", str(f), "--range", "origin/main..HEAD"],
            ),
            pytest.raises(SystemExit),
        ):
            main()


class TestGetRangeRevs:
    def test_returns_shas(self):
        with patch(
            "git_commit_guard.subprocess.check_output",
            return_value="abc1234\ndef5678",
        ):
            assert _get_range_revs("origin/main..HEAD") == ["abc1234", "def5678"]

    def test_excludes_merges_by_default(self):
        with patch("git_commit_guard.subprocess.check_output", return_value="") as mock:
            _get_range_revs("origin/main..HEAD")
        assert "--no-merges" in mock.call_args[0][0]

    def test_includes_merges_when_requested(self):
        with patch("git_commit_guard.subprocess.check_output", return_value="") as mock:
            _get_range_revs("origin/main..HEAD", include_merges=True)
        assert "--no-merges" not in mock.call_args[0][0]

    def test_empty_range_returns_empty_list(self):
        with patch("git_commit_guard.subprocess.check_output", return_value=""):
            assert _get_range_revs("origin/main..HEAD") == []

    def test_invalid_range_exits(self):
        err = subprocess.CalledProcessError(128, "git")
        err.stderr = "fatal: bad revision 'bogus'"
        with (
            patch("git_commit_guard.subprocess.check_output", side_effect=err),
            pytest.raises(SystemExit, match="git error"),
        ):
            _get_range_revs("bogus")


class TestRequireTrailerIntegration:
    def test_require_trailer_flag_passes(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text(
            "fix: add thing\n\nbody\n\nCloses: #42\nSigned-off-by: A <a@b.com>"
        )
        argv = [
            "cg",
            "--message-file",
            str(f),
            "--disable",
            "signature,imperative",
            "--require-trailer",
            "Closes",
        ]
        with patch("sys.argv", argv):
            assert main() == 0

    def test_require_trailer_flag_fails(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text("fix: add thing\n\nbody\n\nSigned-off-by: A <a@b.com>")
        argv = [
            "cg",
            "--message-file",
            str(f),
            "--disable",
            "signature,imperative",
            "--require-trailer",
            "Closes",
        ]
        with patch("sys.argv", argv):
            assert main() == 1

    def test_require_trailer_multiple_passes(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text(
            "fix: add thing\n\nbody\n\n"
            "Closes: #42\nReviewed-by: Jane\nSigned-off-by: A <a@b.com>"
        )
        argv = [
            "cg",
            "--message-file",
            str(f),
            "--disable",
            "signature,imperative",
            "--require-trailer",
            "Closes,Reviewed-by",
        ]
        with patch("sys.argv", argv):
            assert main() == 0

    def test_require_trailer_from_config(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text("fix: add thing\n\nbody\n\nSigned-off-by: A <a@b.com>")
        argv = ["cg", "--message-file", str(f), "--disable", "signature,imperative"]
        with (
            patch("sys.argv", argv),
            patch(
                "git_commit_guard._load_config",
                return_value={"require-trailers": ["Closes"]},
            ),
        ):
            assert main() == 1

    def test_require_trailer_cli_overrides_config(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text("fix: add thing\n\nbody\n\nFixes: #99\nSigned-off-by: A <a@b.com>")
        argv = [
            "cg",
            "--message-file",
            str(f),
            "--disable",
            "signature,imperative",
            "--require-trailer",
            "Fixes",
        ]
        with (
            patch("sys.argv", argv),
            patch(
                "git_commit_guard._load_config",
                return_value={"require-trailers": ["Closes"]},
            ),
        ):
            assert main() == 0


class TestRequireSubjectPatternIntegration:
    def test_matching_pattern_passes(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text(
            "fix: resolve PROJ-42 auth timeout\n\nbody\n\nSigned-off-by: A <a@b.com>"
        )
        argv = [
            "cg",
            "--message-file",
            str(f),
            "--disable",
            "signature,imperative",
            "--require-subject-pattern",
            "[A-Z]+-[0-9]+",
        ]
        with patch("sys.argv", argv):
            assert main() == 0

    def test_non_matching_pattern_fails(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text("fix: resolve auth timeout\n\nbody\n\nSigned-off-by: A <a@b.com>")
        argv = [
            "cg",
            "--message-file",
            str(f),
            "--disable",
            "signature,imperative",
            "--require-subject-pattern",
            "[A-Z]+-[0-9]+",
        ]
        with patch("sys.argv", argv):
            assert main() == 1

    def test_invalid_regex_exits(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text("fix: add thing\n\nbody\n\nSigned-off-by: A <a@b.com>")
        argv = [
            "cg",
            "--message-file",
            str(f),
            "--disable",
            "signature,imperative",
            "--require-subject-pattern",
            "[unclosed",
        ]
        with patch("sys.argv", argv), pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2

    def test_pattern_from_config(self, tmp_path):
        f = tmp_path / "msg"
        f.write_text("fix: resolve auth timeout\n\nbody\n\nSigned-off-by: A <a@b.com>")
        argv = ["cg", "--message-file", str(f), "--disable", "signature,imperative"]
        with (
            patch("sys.argv", argv),
            patch(
                "git_commit_guard._load_config",
                return_value={"require-subject-pattern": "[A-Z]+-[0-9]+"},
            ),
        ):
            assert main() == 1


class TestOutputJsonl:
    def test_single_commit_ok(self, tmp_path, capsys):

        f = tmp_path / "msg"
        f.write_text(_VALID_MSG)
        argv = [
            "cg",
            "--message-file",
            str(f),
            "--disable",
            "signature,imperative",
            "--output",
            "jsonl",
        ]
        with patch("sys.argv", argv):
            assert main() == 0
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is True
        assert data["subject"] == "fix: add thing"
        assert data["sha"] is None

    def test_single_commit_fail(self, tmp_path, capsys):

        f = tmp_path / "msg"
        f.write_text("fix: add thing")
        argv = [
            "cg",
            "--message-file",
            str(f),
            "--disable",
            "signature,imperative",
            "--output",
            "jsonl",
        ]
        with patch("sys.argv", argv):
            assert main() == 1
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is False
        assert any(r["check"] == "body" for r in data["results"])

    def test_range_emits_one_line_per_commit(self, capsys):
        revs = ["aaa", "bbb"]
        messages = ["fix: add thing\n\nbody\n\nSigned-off-by: A <a@b.com>"] * len(revs)
        with (
            patch(
                "sys.argv",
                [
                    "cg",
                    "--range",
                    "HEAD~2..HEAD",
                    "--disable",
                    "signature,imperative",
                    "--output",
                    "jsonl",
                ],
            ),
            patch("git_commit_guard._get_range_revs", return_value=revs),
            patch("git_commit_guard._get_message", side_effect=messages),
        ):
            assert main() == 0
        lines = capsys.readouterr().out.strip().splitlines()
        assert len(lines) == len(revs)
        for line, rev in zip(lines, revs, strict=True):
            data = json.loads(line)
            assert data["sha"] == rev
            assert data["ok"] is True

    def test_range_failing_commit_returns_nonzero(self, capsys):
        with (
            patch(
                "sys.argv",
                [
                    "cg",
                    "--range",
                    "HEAD~1..HEAD",
                    "--disable",
                    "signature,imperative",
                    "--output",
                    "jsonl",
                ],
            ),
            patch("git_commit_guard._get_range_revs", return_value=["aaa"]),
            patch("git_commit_guard._get_message", return_value="bad message"),
        ):
            assert main() == 1
        data = json.loads(capsys.readouterr().out)
        assert data["ok"] is False


class TestOutputFile:
    def test_single_commit_writes_jsonl_to_file(self, tmp_path, capsys):
        msg_file = tmp_path / "msg"
        msg_file.write_text(_VALID_MSG)
        out_file = tmp_path / "results.jsonl"
        with patch(
            "sys.argv",
            [
                "cg",
                "--message-file",
                str(msg_file),
                "--disable",
                "signature,imperative",
                "--output-file",
                str(out_file),
            ],
        ):
            assert main() == 0
        assert "all checks passed" in capsys.readouterr().out
        data = json.loads(out_file.read_text())
        assert data["ok"] is True
        assert data["subject"] == "fix: add thing"

    def test_output_jsonl_and_output_file_both_written(self, tmp_path, capsys):
        msg_file = tmp_path / "msg"
        msg_file.write_text(_VALID_MSG)
        out_file = tmp_path / "results.jsonl"
        with patch(
            "sys.argv",
            [
                "cg",
                "--message-file",
                str(msg_file),
                "--disable",
                "signature,imperative",
                "--output",
                "jsonl",
                "--output-file",
                str(out_file),
            ],
        ):
            assert main() == 0
        stdout_data = json.loads(capsys.readouterr().out)
        file_data = json.loads(out_file.read_text())
        assert stdout_data["ok"] is True
        assert file_data["ok"] is True
        assert stdout_data["subject"] == file_data["subject"]

    def test_range_writes_one_line_per_commit(self, tmp_path):
        revs = ["aaa", "bbb"]
        messages = [_VALID_MSG] * len(revs)
        out_file = tmp_path / "results.jsonl"
        with (
            patch(
                "sys.argv",
                [
                    "cg",
                    "--range",
                    "HEAD~2..HEAD",
                    "--disable",
                    "signature,imperative",
                    "--output-file",
                    str(out_file),
                ],
            ),
            patch("git_commit_guard._get_range_revs", return_value=revs),
            patch("git_commit_guard._get_message", side_effect=messages),
        ):
            assert main() == 0
        lines = out_file.read_text().strip().splitlines()
        assert len(lines) == len(revs)
        for line, rev in zip(lines, revs, strict=True):
            assert json.loads(line)["sha"] == rev

    def test_failed_commit_written_to_file(self, tmp_path):
        msg_file = tmp_path / "msg"
        msg_file.write_text("fix: add thing")
        out_file = tmp_path / "results.jsonl"
        with patch(
            "sys.argv",
            [
                "cg",
                "--message-file",
                str(msg_file),
                "--disable",
                "signature,imperative",
                "--output-file",
                str(out_file),
            ],
        ):
            assert main() == 1
        data = json.loads(out_file.read_text())
        assert data["ok"] is False
