"""The pre-push hygiene grep: nothing private may be tracked in the public tree."""
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "hygiene_grep.sh"

# strings that must never reach the public tree, built in pieces so that this file is clean too
HOME_PATH = "/home" + "/bl/x"

# The macOS home-directory pattern is read back from the hygiene script itself (where it carries a
# bracketed character) and the brackets are stripped here, so no tracked file spells the name out.
_MAC_PATTERN = re.search(r"'(/Users/[^']+)'", SCRIPT.read_text()).group(1)
MAC_PATH = _MAC_PATTERN.replace("[", "").replace("]", "") + "/x"
NAS_PATH = "NAS" + "/Four/x"
SCRATCH = "claude" + "-scratch"


def _run(root, **env):
    full = {k: v for k, v in os.environ.items() if k != "DGS_HYGIENE_EXTRA"}
    full.update(env)
    return subprocess.run(["bash", str(SCRIPT), str(root)], capture_output=True, text=True,
                          env=full)


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _repo(tmp_path, files):
    """A throw-away git repository whose tracked files are {name: text}."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    _git(tmp_path, "init", "-q")
    for name, text in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(tmp_path, "add", "-A", "-f")     # -f: ignore any global gitignore, track what we planted
    return tmp_path


def test_hygiene_grep_clean():
    res = _run(ROOT)
    assert res.returncode == 0, res.stdout + res.stderr


def test_hygiene_grep_catches_planted_path(tmp_path):
    repo = _repo(tmp_path, {"notes.txt": f"see {HOME_PATH}\n"})
    res = _run(repo)
    assert res.returncode == 1
    assert "notes.txt" in res.stdout and HOME_PATH in res.stdout


@pytest.mark.parametrize("text", [MAC_PATH, NAS_PATH, SCRATCH, "ixkael" + ".u6x",
                                  "-----BEGIN RSA " + "PRIVATE KEY-----"])
def test_hygiene_grep_catches_each_content_pattern(tmp_path, text):
    repo = _repo(tmp_path, {"a/b.txt": f"x {text} y\n"})
    res = _run(repo)
    assert res.returncode == 1, text
    assert "a/b.txt" in res.stdout


def test_hygiene_grep_extra_patterns_from_file(tmp_path):
    repo = _repo(tmp_path / "repo", {"a/b.txt": "ran on SecretHost today\n"})
    extra = tmp_path / "extra.txt"
    extra.write_text("secrethost\n")
    # without the variable there is no extra check
    assert _run(repo).returncode == 0
    res = _run(repo, DGS_HYGIENE_EXTRA=str(extra))
    assert res.returncode == 1
    assert "a/b.txt" in res.stdout


def test_hygiene_grep_extra_file_unreadable_is_an_error(tmp_path):
    repo = _repo(tmp_path / "repo", {"a/b.txt": "fine\n"})
    res = _run(repo, DGS_HYGIENE_EXTRA=str(tmp_path / "missing.txt"))
    assert res.returncode == 1


def test_hygiene_grep_read_error_fails(tmp_path):
    repo = _repo(tmp_path / "repo", {"a/b.txt": "fine\n"})
    (repo / "a" / "b.txt").chmod(0)
    try:
        if (repo / "a" / "b.txt").open("rb"):      # running as root: the file is still readable
            pytest.skip("permissions not enforced")
    except PermissionError:
        pass
    try:
        res = _run(repo)
    finally:
        (repo / "a" / "b.txt").chmod(0o644)
    assert res.returncode == 1
    assert "error" in (res.stdout + res.stderr).lower()


@pytest.mark.parametrize("name", ["CLAUDE.md", "sub/CLAUDE.md", "DESIGN.md", "FIX_LOG.md",
                                  "CODE_REVIEW.md", "FIX_REVIEW.md", "settings.toml",
                                  "etc/settings.toml", ".claude/settings.json",
                                  "x/.claude/y.txt"])
def test_hygiene_grep_fails_on_excluded_file_name(tmp_path, name):
    repo = _repo(tmp_path, {name: "harmless\n"})
    res = _run(repo)
    assert res.returncode == 1, name
    assert name in res.stdout


def test_hygiene_grep_names_are_not_content_and_example_settings_allowed(tmp_path):
    # .gitignore legitimately names the excluded files; the example settings file is allowed
    repo = _repo(tmp_path, {".gitignore": "settings.toml\nCLAUDE.md\n.claude/\n",
                            "settings.example-isambard.toml": "workers = 64\n",
                            "docs/SETTINGS.md": "copy the example to settings.toml\n"})
    res = _run(repo)
    assert res.returncode == 0, res.stdout + res.stderr


def test_hygiene_grep_ignores_untracked_ignored_files(tmp_path):
    repo = _repo(tmp_path, {".gitignore": "secret.txt\n", "ok.txt": "fine\n"})
    (repo / "secret.txt").write_text(f"{HOME_PATH}\n")
    _git(repo, "add", "-A")      # the ignored file stays out
    assert _run(repo).returncode == 0
