import plistlib
import subprocess
from pathlib import Path

import pytest

from filo.telegram_service import (
    SERVICE_LABEL,
    SYSTEMD_UNIT,
    WINDOWS_TASK,
    TelegramServiceError,
    install,
    restart,
    status,
    uninstall,
)


class FakeRunner:
    """Stand-in for `subprocess.run` that tracks whether launchd holds the job."""

    def __init__(
        self, responses: dict[tuple[str, ...], int] | None = None, *, loaded: bool = True
    ) -> None:
        self.commands: list[list[str]] = []
        self.responses = responses or {}
        self.loaded = loaded

    def __call__(self, command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        default = 0
        if command[:2] == ["launchctl", "bootout"]:
            self.loaded = False
        elif command[:2] == ["launchctl", "bootstrap"]:
            self.loaded = True
        elif command[:2] == ["launchctl", "print"]:
            default = 0 if self.loaded else 1
        code = self.responses.get(tuple(command), default)
        return subprocess.CompletedProcess(command, code, stdout="active\n", stderr="")


def test_macos_service_install_status_and_uninstall(tmp_path: Path) -> None:
    runner = FakeRunner()
    home = tmp_path / "home"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    command = ["/opt/filo", "--data-dir", str(data_dir), "telegram"]

    path = install(command, data_dir, platform="darwin", home=home, runner=runner)

    payload = plistlib.loads(path.read_bytes())
    assert payload["Label"] == SERVICE_LABEL
    assert payload["ProgramArguments"] == command
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is True
    assert path.stat().st_mode & 0o777 == 0o600
    assert status(platform="darwin", home=home, runner=runner).running is True
    assert uninstall(platform="darwin", home=home, runner=runner) is True
    assert not path.exists()


def test_linux_service_uses_user_systemd_and_quotes_arguments(tmp_path: Path) -> None:
    runner = FakeRunner()
    home = tmp_path / "home"
    data_dir = tmp_path / "data with spaces"
    data_dir.mkdir()
    command = ["/opt/filo app/filo", "--data-dir", str(data_dir), "telegram"]

    path = install(command, data_dir, platform="linux", home=home, runner=runner)

    unit = path.read_text()
    assert path.name == SYSTEMD_UNIT
    assert 'ExecStart="/opt/filo app/filo" "--data-dir"' in unit
    assert ["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT] in runner.commands
    assert uninstall(platform="linux", home=home, runner=runner) is True
    assert runner.commands[-1] == ["systemctl", "--user", "daemon-reload"]


def test_macos_service_carries_the_local_endpoint_into_the_background_agent(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    home = tmp_path / "home"
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    path = install(
        ["/opt/filo", "telegram"],
        data_dir,
        environment={"FILO_LOCAL_BASE_URL": "http://rig.invalid:8080/v1", "EMPTY": ""},
        platform="darwin",
        home=home,
        runner=runner,
    )

    payload = plistlib.loads(path.read_bytes())
    assert payload["EnvironmentVariables"] == {"FILO_LOCAL_BASE_URL": "http://rig.invalid:8080/v1"}


def test_linux_service_carries_the_local_endpoint_into_the_background_unit(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    home = tmp_path / "home"

    path = install(
        ["/opt/filo", "telegram"],
        tmp_path,
        environment={"FILO_LOCAL_BASE_URL": 'http://rig.invalid:8080/v1"'},
        platform="linux",
        home=home,
        runner=runner,
    )

    unit = path.read_text()
    assert 'Environment="FILO_LOCAL_BASE_URL=http://rig.invalid:8080/v1\\""\n' in unit
    assert unit.index("Environment=") < unit.index("Restart=on-failure")


def test_service_without_environment_writes_no_settings(tmp_path: Path) -> None:
    runner = FakeRunner()

    path = install(
        ["/opt/filo", "telegram"],
        tmp_path,
        platform="linux",
        home=tmp_path / "home",
        runner=runner,
    )

    assert "Environment=" not in path.read_text()


def test_service_rejects_control_characters_in_the_environment(tmp_path: Path) -> None:
    with pytest.raises(TelegramServiceError, match="control characters"):
        install(
            ["/opt/filo", "telegram"],
            tmp_path,
            environment={"FILO_LOCAL_BASE_URL": "http://rig.invalid/v1\nRestart=always"},
            platform="linux",
            home=tmp_path / "home",
            runner=FakeRunner(),
        )


def test_macos_reinstall_waits_for_launchd_to_release_the_label(tmp_path: Path) -> None:
    runner = FakeRunner()
    home = tmp_path / "home"
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    install(["/opt/filo", "telegram"], data_dir, platform="darwin", home=home, runner=runner)

    verbs = [command[1] for command in runner.commands if command[0] == "launchctl"]
    assert verbs == ["print", "bootout", "print", "bootstrap"]


def test_service_reports_native_manager_failure(tmp_path: Path) -> None:
    command = ["systemctl", "--user", "daemon-reload"]
    runner = FakeRunner({tuple(command): 1})

    with pytest.raises(TelegramServiceError, match="systemctl failed"):
        install(
            ["/opt/filo", "telegram"],
            tmp_path,
            platform="linux",
            home=tmp_path,
            runner=runner,
        )


def test_linux_service_rejects_control_characters_in_command(tmp_path: Path) -> None:
    with pytest.raises(TelegramServiceError, match="control characters"):
        install(
            ["/opt/filo", "--data-dir", "/tmp/data\n[Install]"],
            tmp_path,
            platform="linux",
            home=tmp_path,
            runner=FakeRunner(),
        )


def test_windows_task_install_status_restart_and_uninstall(tmp_path: Path) -> None:
    runner = FakeRunner()
    command = [
        r"C:\Program Files\Filo\filo.exe",
        "--data-dir",
        r"C:\Users\Test User\Filo",
        "telegram",
    ]

    location = install(command, tmp_path, platform="win32", runner=runner)

    create = runner.commands[0]
    assert location == f"Task Scheduler: {WINDOWS_TASK}"
    assert create[:4] == ["schtasks.exe", "/Create", "/TN", WINDOWS_TASK]
    assert create[create.index("/SC") + 1] == "ONLOGON"
    assert create[create.index("/RL") + 1] == "LIMITED"
    assert '"C:\\Program Files\\Filo\\filo.exe"' in create[create.index("/TR") + 1]
    assert runner.commands[1] == ["schtasks.exe", "/Run", "/TN", WINDOWS_TASK]
    assert status(platform="win32", runner=runner).running is True

    restart(platform="win32", runner=runner)
    assert ["schtasks.exe", "/End", "/TN", WINDOWS_TASK] in runner.commands
    assert runner.commands[-1] == ["schtasks.exe", "/Run", "/TN", WINDOWS_TASK]

    assert uninstall(platform="win32", runner=runner) is True
    assert runner.commands[-1] == [
        "schtasks.exe",
        "/Delete",
        "/TN",
        WINDOWS_TASK,
        "/F",
    ]


def test_windows_uninstall_reports_missing_task(tmp_path: Path) -> None:
    status_command_prefix = ("powershell.exe", "-NoProfile", "-NonInteractive")

    class MissingTaskRunner(FakeRunner):
        def __call__(
            self, command: list[str], **kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            if tuple(command[:3]) == status_command_prefix:
                self.commands.append(command)
                return subprocess.CompletedProcess(command, 2, stdout="", stderr="")
            return super().__call__(command, **kwargs)

    runner = MissingTaskRunner()

    assert uninstall(platform="win32", runner=runner) is False
    assert all("/Delete" not in command for command in runner.commands)
