import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_macos_setup_runs_inside_a_fresh_pseudoterminal() -> None:
    installer = (ROOT / "install.sh").read_text()

    assert 'Darwin) script -q -e /dev/null "$FILO" setup </dev/tty ;;' in installer


def test_release_version_is_synchronized_across_installers() -> None:
    with (ROOT / "pyproject.toml").open("rb") as project_file:
        version = tomllib.load(project_file)["project"]["version"]

    assert f'FILO_VERSION="v{version}"' in (ROOT / "install.sh").read_text()
    assert f'$FiloVersion = "v{version}"' in (ROOT / "install.ps1").read_text()
    assert f'__version__ = "{version}"' in (ROOT / "src/filo/__init__.py").read_text()


def test_installers_replace_an_existing_tool_during_update() -> None:
    assert "tool install --force --python 3.12" in (ROOT / "install.sh").read_text()
    assert "tool install --force --python 3.12" in (ROOT / "install.ps1").read_text()
