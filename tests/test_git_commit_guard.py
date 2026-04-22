import subprocess
from argparse import ArgumentParser
from unittest.mock import MagicMock, patch

import pytest

from git_commit_guard import (
    Result,
    _download_if_missing,
    _ensure_nltk_data,
    _get_message,
    _load_config,
    _parse_checks,
    _parse_config_checks,
    _report,
    _strip_comments,
    check_body,
    check_imperative,
    check_signature,
    check_signed_off,
    check_subject,
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

    def test_trailing_period(self):
        r = Result()
        check_subject("fix: add token.", r)
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
        assert any("GPG" in msg for _, msg in r.errors)

    def test_ssh_signed_commit(self):
        r = Result()
        proc = MagicMock(returncode=0, stderr="Good ssh signature")
        with patch("git_commit_guard.subprocess.run", return_value=proc):
            check_signature("abc123", r)
        assert r.ok
        assert any("SSH" in msg for _, msg in r.errors)


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
        assert len(checks) == 2  # noqa: PLR2004

    def test_missing_key_returns_empty(self):
        assert _parse_config_checks({}, "disable") == []

    def test_invalid_check_name_exits(self):
        with pytest.raises(SystemExit, match=r"\.commit-guard\.toml"):
            _parse_config_checks({"disable": ["bogus"]}, "disable")


class TestParseChecks:
    def test_invalid_check_name(self):
        parser = ArgumentParser()
        with pytest.raises(SystemExit):
            _parse_checks(parser, "invalid")


class TestReport:
    def test_all_passed(self, capsys):
        r = Result()
        ret = _report(r)
        assert ret == 0
        assert "all checks passed" in capsys.readouterr().err

    def test_with_error(self, capsys):
        r = Result()
        r.error("something broke")
        ret = _report(r)
        assert ret == 1
        assert "something broke" in capsys.readouterr().err

    def test_with_warning_returns_zero(self, capsys):
        r = Result()
        r.warn("heads up")
        ret = _report(r)
        assert ret == 0
        captured = capsys.readouterr().err
        assert "heads up" in captured
        assert "all checks passed" in captured


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
        assert "all checks passed" in capsys.readouterr().err

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
        assert "all checks passed" in capsys.readouterr().err

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
