"""Install the Telegram listener as a native per-user background service or task."""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

SERVICE_LABEL = "io.github.matteodante.filo.telegram"
SYSTEMD_UNIT = "filo-telegram.service"
WINDOWS_TASK = "Filo Telegram"
# Therapist, the product's earlier name, installed its listener under these names. An upgrade
# leaves it running, and two listeners on one bot make each other fail.
LEGACY_LAUNCHD_PLIST = "io.github.matteodante.therapist.telegram.plist"
LEGACY_SYSTEMD_UNIT = "therapist-telegram.service"
Runner = Callable[..., subprocess.CompletedProcess[str]]


class TelegramServiceError(RuntimeError):
    """A background-service operation failed."""


@dataclass(frozen=True)
class ServiceStatus:
    installed: bool
    running: bool
    detail: str


def install(
    command: list[str],
    data_dir: Path,
    *,
    environment: Mapping[str, str] | None = None,
    platform: str = sys.platform,
    home: Path | None = None,
    runner: Runner = subprocess.run,
) -> Path | str:
    home = home or Path.home()
    environment = _validated_environment(environment)
    if platform == "darwin":
        path = home / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        log_path = data_dir / "filo-telegram.log"
        domain = f"gui/{os.getuid()}"
        current = runner(
            ["launchctl", "print", f"{domain}/{SERVICE_LABEL}"],
            capture_output=True,
            text=True,
        )
        if current.returncode == 0:
            _run(runner, ["launchctl", "bootout", f"{domain}/{SERVICE_LABEL}"])
            _await_launchd_unload(runner, domain)
        payload: dict[str, object] = {
            "Label": SERVICE_LABEL,
            "ProgramArguments": command,
            "RunAtLoad": True,
            "KeepAlive": True,
            "ProcessType": "Background",
            "StandardOutPath": str(log_path),
            "StandardErrorPath": str(log_path),
        }
        if environment:
            payload["EnvironmentVariables"] = dict(environment)
        _atomic_write(path, plistlib.dumps(payload))
        _run(runner, ["launchctl", "bootstrap", domain, str(path)])
        return path
    if platform.startswith("linux"):
        path = home / ".config" / "systemd" / "user" / SYSTEMD_UNIT
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        unit = (
            "[Unit]\n"
            "Description=Filo private Telegram listener\n"
            "After=network-online.target\n\n"
            "[Service]\n"
            "Type=simple\n"
            f"ExecStart={_systemd_command(command)}\n"
            f"{_systemd_environment(environment)}"
            "Restart=on-failure\n"
            "RestartSec=3\n\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        )
        _atomic_write(path, unit.encode())
        _run(runner, ["systemctl", "--user", "daemon-reload"])
        _run(runner, ["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT])
        return path
    if platform.startswith("win"):
        _validate_command(command)
        task_command = subprocess.list2cmdline(command)
        _run(
            runner,
            [
                "schtasks.exe",
                "/Create",
                "/TN",
                WINDOWS_TASK,
                "/TR",
                task_command,
                "/SC",
                "ONLOGON",
                "/RL",
                "LIMITED",
                "/F",
            ],
        )
        _run(runner, ["schtasks.exe", "/Run", "/TN", WINDOWS_TASK])
        return f"Task Scheduler: {WINDOWS_TASK}"
    raise TelegramServiceError(
        "Background service installation supports macOS, systemd Linux, and Windows."
    )


def uninstall(
    *,
    platform: str = sys.platform,
    home: Path | None = None,
    runner: Runner = subprocess.run,
) -> bool:
    home = home or Path.home()
    if platform == "darwin":
        path = home / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"
        # Booted out whenever launchd holds it, including a job throttled between crashes.
        loaded = runner(
            ["launchctl", "print", f"gui/{os.getuid()}/{SERVICE_LABEL}"],
            capture_output=True,
            text=True,
        )
        if loaded.returncode == 0:
            _run(
                runner,
                ["launchctl", "bootout", f"gui/{os.getuid()}/{SERVICE_LABEL}"],
            )
    elif platform.startswith("linux"):
        path = home / ".config" / "systemd" / "user" / SYSTEMD_UNIT
        if path.exists():
            _run(
                runner,
                ["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT],
            )
    elif platform.startswith("win"):
        current = status(platform=platform, home=home, runner=runner)
        if not current.installed:
            return False
        if current.running:
            _run(runner, ["schtasks.exe", "/End", "/TN", WINDOWS_TASK])
        _run(runner, ["schtasks.exe", "/Delete", "/TN", WINDOWS_TASK, "/F"])
        return True
    else:
        raise TelegramServiceError(
            "Background service installation supports macOS, systemd Linux, and Windows."
        )
    existed = path.exists()
    path.unlink(missing_ok=True)
    if platform.startswith("linux"):
        _run(runner, ["systemctl", "--user", "daemon-reload"])
    return existed


def restart(
    *,
    platform: str = sys.platform,
    runner: Runner = subprocess.run,
) -> None:
    if platform == "darwin":
        _run(
            runner,
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{SERVICE_LABEL}"],
        )
    elif platform.startswith("linux"):
        _run(runner, ["systemctl", "--user", "restart", SYSTEMD_UNIT])
    elif platform.startswith("win"):
        current = status(platform=platform, runner=runner)
        if not current.installed:
            raise TelegramServiceError("The Windows background task is not installed.")
        if current.running:
            _run(runner, ["schtasks.exe", "/End", "/TN", WINDOWS_TASK])
        _run(runner, ["schtasks.exe", "/Run", "/TN", WINDOWS_TASK])
    else:
        raise TelegramServiceError(
            "Background service installation supports macOS, systemd Linux, and Windows."
        )


def legacy_definition(*, platform: str = sys.platform, home: Path | None = None) -> Path | None:
    """The Therapist listener definition still installed on this machine, if any."""
    home = home or Path.home()
    if platform == "darwin":
        path = home / "Library" / "LaunchAgents" / LEGACY_LAUNCHD_PLIST
    elif platform.startswith("linux"):
        path = home / ".config" / "systemd" / "user" / LEGACY_SYSTEMD_UNIT
    else:
        return None
    return path if path.exists() else None


def status(
    *,
    platform: str = sys.platform,
    home: Path | None = None,
    runner: Runner = subprocess.run,
) -> ServiceStatus:
    home = home or Path.home()
    if platform == "darwin":
        path = home / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"
        command = ["launchctl", "print", f"gui/{os.getuid()}/{SERVICE_LABEL}"]
    elif platform.startswith("linux"):
        path = home / ".config" / "systemd" / "user" / SYSTEMD_UNIT
        command = ["systemctl", "--user", "is-active", SYSTEMD_UNIT]
    elif platform.startswith("win"):
        result = runner(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                (
                    f"$task=Get-ScheduledTask -TaskName '{WINDOWS_TASK}' "
                    "-ErrorAction SilentlyContinue;"
                    "if($null -eq $task){exit 2};"
                    "$task.State;"
                    "if($task.State -eq 'Running'){exit 0}else{exit 3}"
                ),
            ],
            capture_output=True,
            text=True,
        )
        detail = (result.stdout or result.stderr or "").strip()
        return ServiceStatus(result.returncode in {0, 3}, result.returncode == 0, detail)
    else:
        raise TelegramServiceError(
            "Background service installation supports macOS, systemd Linux, and Windows."
        )
    result = runner(command, capture_output=True, text=True)
    detail = (result.stdout or result.stderr or "").strip()
    running = result.returncode == 0
    if platform == "darwin":
        # `launchctl print` succeeds for any loaded job, including one launchd is throttling
        # between crashes, so only its reported state says whether it is running.
        running = running and re.search(r"^\s*state = running\s*$", result.stdout or "", re.M)
    return ServiceStatus(path.exists(), bool(running), detail)


def _await_launchd_unload(runner: Runner, domain: str, attempts: int = 40) -> None:
    """Wait for a booted-out job to leave the domain.

    `launchctl bootout` returns before launchd finishes unloading, and
    bootstrapping a label that is still loaded fails with an I/O error.
    """
    for _ in range(attempts):
        probe = runner(
            ["launchctl", "print", f"{domain}/{SERVICE_LABEL}"],
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            return
        time.sleep(0.25)


def _run(runner: Runner, command: list[str]) -> None:
    result = runner(command, capture_output=True, text=True)
    if result.returncode:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        raise TelegramServiceError(f"{command[0]} failed: {detail}")


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _systemd_command(arguments: list[str]) -> str:
    _validate_command(arguments)
    return " ".join(f'"{value.replace("\\", "\\\\").replace('"', '\\"')}"' for value in arguments)


def _systemd_environment(environment: Mapping[str, str]) -> str:
    return "".join(
        f'Environment="{name}={_systemd_quote(value)}"\n' for name, value in environment.items()
    )


def _systemd_quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _validated_environment(environment: Mapping[str, str] | None) -> dict[str, str]:
    """Keep only non-empty, single-line settings safe to write into a service file."""
    if not environment:
        return {}
    for name, value in environment.items():
        if not name or any(character in f"{name}{value}" for character in "\0\r\n"):
            raise TelegramServiceError(
                "Service environment names and values cannot be empty or contain "
                "control characters."
            )
    return {name: value for name, value in environment.items() if value}


def _validate_command(arguments: list[str]) -> None:
    if not arguments or any(
        any(character in value for character in "\0\r\n") for value in arguments
    ):
        raise TelegramServiceError(
            "Service command arguments cannot be empty or contain control characters."
        )
