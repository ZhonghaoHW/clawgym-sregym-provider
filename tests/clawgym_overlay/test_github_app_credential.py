from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = ROOT / "clawgym_overlay" / "bootstrap"


def test_git_credential_helper_is_narrowly_scoped() -> None:
    helper = (BOOTSTRAP / "github-app-git-credential").read_text()

    assert "ZhonghaoHW/clawgym.git" in helper
    assert "ZhonghaoHW/clawgym-sregym-provider.git" in helper
    assert "store|erase" in helper
    assert "github-app-installation-token" in helper
    assert "github-app.pem" not in helper
    assert "github_app_credential_error=root_required" in helper


def test_token_helper_keeps_the_key_and_cache_root_only() -> None:
    helper = (BOOTSTRAP / "github-app-installation-token").read_text()

    assert "/etc/clawgym/github-app.pem" in helper
    assert "/run/clawgym" in helper
    assert "-m 0700" in helper
    assert "stat.S_IMODE(details.st_mode) != 0o600" in helper
    assert "X-GitHub-Api-Version: 2026-03-10" in helper


def test_sudoers_never_grants_token_minting_directly() -> None:
    sudoers = (BOOTSTRAP / "ecs-user-wp4.sudoers").read_text()

    assert "github-app-git-credential" in sudoers
    assert "github-app-installation-token" not in sudoers


def test_documentation_keeps_the_key_root_only_but_runs_the_helper_as_ecs_user() -> None:
    documentation = (ROOT / "clawgym_overlay" / "docs" / "wp4-host-bootstrap.md").read_text()

    assert "-m 0750 github-app-installation-token" in documentation
    assert "-m 0750 github-app-git-credential" in documentation
    assert "!sudo -n /usr/local/lib/clawgym/github-app-git-credential" in documentation
