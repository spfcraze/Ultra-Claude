"""
Daemon management for Autowrkers 24/7 operation.

Supports systemd (Linux) and launchd (macOS) for automatic startup and recovery.
"""
import asyncio
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from .logging_config import get_logger

logger = get_logger("autowrkers.daemon")

# Service configuration
SERVICE_NAME = "autowrkers"
SERVICE_DESCRIPTION = "Autowrkers - Multi-session Claude Code Manager"


class DaemonStatus(Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    NOT_INSTALLED = "not_installed"
    UNKNOWN = "unknown"


@dataclass
class DaemonInfo:
    status: DaemonStatus
    pid: Optional[int] = None
    uptime: Optional[str] = None
    service_path: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        import sys as _sys
        is_installed = self.status != DaemonStatus.NOT_INSTALLED
        is_running = self.status == DaemonStatus.RUNNING
        platform = "linux" if _sys.platform.startswith("linux") else ("macos" if _sys.platform == "darwin" else _sys.platform)
        service_type = "systemd" if platform == "linux" else ("launchd" if platform == "macos" else "unknown")
        return {
            "status": self.status.value,
            "installed": is_installed,
            "running": is_running,
            "platform": platform,
            "service_type": service_type,
            "pid": self.pid,
            "uptime": self.uptime,
            "service_path": self.service_path,
            "error": self.error,
        }


class DaemonManager:
    """Manages Autowrkers as a system service for 24/7 operation."""

    def __init__(self):
        self._project_root = self._find_project_root()
        self._python_path = self._find_python()
        self._is_linux = sys.platform.startswith("linux")
        self._is_macos = sys.platform == "darwin"

    def _find_python(self) -> str:
        """Find the best Python interpreter, preferring the project venv."""
        venv_python = self._project_root / "venv" / "bin" / "python3"
        if venv_python.exists():
            return str(venv_python)
        return sys.executable

    def _find_project_root(self) -> Path:
        """Find the Autowrkers project root directory."""
        current = Path(__file__).parent.parent
        if (current / "main.py").exists():
            return current
        return current

    # ==================== Service File Generation ====================

    def _generate_systemd_service(self, host: str = "127.0.0.1", port: int = 8420) -> str:
        """Generate systemd service unit file content."""
        user = os.environ.get("USER", "root")
        working_dir = str(self._project_root)

        home_dir = os.environ.get('HOME', f'/home/{user}')

        return f"""[Unit]
Description={SERVICE_DESCRIPTION}
After=network.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={working_dir}
ExecStart={self._python_path} main.py start --host {host} --port {port}
Restart=always
RestartSec=10
Environment="PATH={os.environ.get('PATH', '/usr/bin:/bin')}"
Environment="HOME={home_dir}"

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier={SERVICE_NAME}

[Install]
WantedBy=default.target
"""

    def _generate_launchd_plist(self, host: str = "127.0.0.1", port: int = 8420) -> str:
        """Generate macOS launchd plist file content."""
        working_dir = str(self._project_root)
        home_dir = os.environ.get("HOME", "~")
        log_dir = f"{home_dir}/.autowrkers/logs"

        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.autowrkers.daemon</string>

    <key>ProgramArguments</key>
    <array>
        <string>{self._python_path}</string>
        <string>main.py</string>
        <string>start</string>
        <string>--host</string>
        <string>{host}</string>
        <string>--port</string>
        <string>{str(port)}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>{working_dir}</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
        <key>Crashed</key>
        <true/>
    </dict>

    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>StandardOutPath</key>
    <string>{log_dir}/stdout.log</string>

    <key>StandardErrorPath</key>
    <string>{log_dir}/stderr.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>{os.environ.get('PATH', '/usr/bin:/bin')}</string>
        <key>HOME</key>
        <string>{home_dir}</string>
    </dict>
</dict>
</plist>
"""

    # ==================== Service Paths ====================

    def _get_systemd_service_path(self) -> Path:
        """Get the systemd service file path."""
        # User-level service (doesn't require root)
        user_service_dir = Path.home() / ".config" / "systemd" / "user"
        return user_service_dir / f"{SERVICE_NAME}.service"

    def _get_launchd_plist_path(self) -> Path:
        """Get the launchd plist file path."""
        return Path.home() / "Library" / "LaunchAgents" / f"com.{SERVICE_NAME}.daemon.plist"

    # ==================== Install ====================

    async def install(self, host: str = "127.0.0.1", port: int = 8420) -> dict:
        """Install Autowrkers as a system service."""
        if self._is_linux:
            return await self._install_systemd(host, port)
        elif self._is_macos:
            return await self._install_launchd(host, port)
        else:
            return {
                "success": False,
                "error": f"Unsupported platform: {sys.platform}",
                "hint": "Daemon mode is only supported on Linux and macOS",
            }

    async def _install_systemd(self, host: str, port: int) -> dict:
        """Install systemd user service."""
        service_path = self._get_systemd_service_path()
        service_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Write service file
            service_content = self._generate_systemd_service(host, port)
            service_path.write_text(service_content)

            # Reload systemd user daemon
            result = subprocess.run(
                ["systemctl", "--user", "daemon-reload"],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                return {
                    "success": False,
                    "error": f"Failed to reload systemd: {result.stderr}",
                }

            # Enable service
            result = subprocess.run(
                ["systemctl", "--user", "enable", SERVICE_NAME],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                return {
                    "success": False,
                    "error": f"Failed to enable service: {result.stderr}",
                }

            return {
                "success": True,
                "service_path": str(service_path),
                "message": f"Service installed at {service_path}",
                "commands": {
                    "start": f"systemctl --user start {SERVICE_NAME}",
                    "stop": f"systemctl --user stop {SERVICE_NAME}",
                    "status": f"systemctl --user status {SERVICE_NAME}",
                    "logs": f"journalctl --user -u {SERVICE_NAME} -f",
                },
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _install_launchd(self, host: str, port: int) -> dict:
        """Install macOS launchd service."""
        plist_path = self._get_launchd_plist_path()
        plist_path.parent.mkdir(parents=True, exist_ok=True)

        # Create log directory
        log_dir = Path.home() / ".autowrkers" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Write plist file
            plist_content = self._generate_launchd_plist(host, port)
            plist_path.write_text(plist_content)

            return {
                "success": True,
                "service_path": str(plist_path),
                "message": f"Service installed at {plist_path}",
                "commands": {
                    "start": f"launchctl load {plist_path}",
                    "stop": f"launchctl unload {plist_path}",
                    "status": f"launchctl list | grep autowrkers",
                    "logs": f"tail -f ~/.autowrkers/logs/stdout.log",
                },
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ==================== Uninstall ====================

    async def uninstall(self) -> dict:
        """Uninstall the Autowrkers service."""
        if self._is_linux:
            return await self._uninstall_systemd()
        elif self._is_macos:
            return await self._uninstall_launchd()
        else:
            return {"success": False, "error": f"Unsupported platform: {sys.platform}"}

    async def _uninstall_systemd(self) -> dict:
        """Uninstall systemd user service."""
        try:
            # Stop service if running
            subprocess.run(
                ["systemctl", "--user", "stop", SERVICE_NAME],
                capture_output=True
            )

            # Disable service
            subprocess.run(
                ["systemctl", "--user", "disable", SERVICE_NAME],
                capture_output=True
            )

            # Remove service file
            service_path = self._get_systemd_service_path()
            if service_path.exists():
                service_path.unlink()

            # Reload daemon
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)

            return {
                "success": True,
                "message": "Service uninstalled successfully",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _uninstall_launchd(self) -> dict:
        """Uninstall macOS launchd service."""
        plist_path = self._get_launchd_plist_path()

        try:
            # Unload service if loaded
            subprocess.run(
                ["launchctl", "unload", str(plist_path)],
                capture_output=True
            )

            # Remove plist file
            if plist_path.exists():
                plist_path.unlink()

            return {
                "success": True,
                "message": "Service uninstalled successfully",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ==================== Start/Stop/Restart ====================

    async def start(self) -> dict:
        """Start the Autowrkers service."""
        if self._is_linux:
            result = subprocess.run(
                ["systemctl", "--user", "start", SERVICE_NAME],
                capture_output=True,
                text=True
            )
        elif self._is_macos:
            plist_path = self._get_launchd_plist_path()
            result = subprocess.run(
                ["launchctl", "load", str(plist_path)],
                capture_output=True,
                text=True
            )
        else:
            return {"success": False, "error": f"Unsupported platform: {sys.platform}"}

        if result.returncode != 0:
            return {"success": False, "error": result.stderr or "Failed to start service"}

        # Verify service actually started (systemctl start can return 0 even if it fails shortly after)
        await asyncio.sleep(2)
        status = await self.get_status()
        if status.status == DaemonStatus.RUNNING:
            return {"success": True, "message": "Service started"}
        else:
            # Get journal logs for the failure reason
            error_detail = ""
            if self._is_linux:
                log_result = subprocess.run(
                    ["journalctl", "--user", "-u", SERVICE_NAME, "-n", "5", "--no-pager", "-o", "cat"],
                    capture_output=True, text=True
                )
                if log_result.returncode == 0 and log_result.stdout.strip():
                    error_detail = log_result.stdout.strip()
            return {
                "success": False,
                "error": f"Service failed to start.{(' ' + error_detail) if error_detail else ''}"
            }

    async def stop(self) -> dict:
        """Stop the Autowrkers service."""
        if self._is_linux:
            result = subprocess.run(
                ["systemctl", "--user", "stop", SERVICE_NAME],
                capture_output=True,
                text=True
            )
        elif self._is_macos:
            plist_path = self._get_launchd_plist_path()
            result = subprocess.run(
                ["launchctl", "unload", str(plist_path)],
                capture_output=True,
                text=True
            )
        else:
            return {"success": False, "error": f"Unsupported platform: {sys.platform}"}

        if result.returncode == 0:
            return {"success": True, "message": "Service stopped"}
        else:
            return {"success": False, "error": result.stderr or "Failed to stop service"}

    async def restart(self) -> dict:
        """Restart the Autowrkers service."""
        if self._is_linux:
            result = subprocess.run(
                ["systemctl", "--user", "restart", SERVICE_NAME],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                return {"success": True, "message": "Service restarted"}
            else:
                return {"success": False, "error": result.stderr or "Failed to restart"}
        elif self._is_macos:
            await self.stop()
            await asyncio.sleep(1)
            return await self.start()
        else:
            return {"success": False, "error": f"Unsupported platform: {sys.platform}"}

    # ==================== Status ====================

    async def get_status(self) -> DaemonInfo:
        """Get the current status of the Autowrkers service."""
        if self._is_linux:
            return await self._get_systemd_status()
        elif self._is_macos:
            return await self._get_launchd_status()
        else:
            return DaemonInfo(
                status=DaemonStatus.UNKNOWN,
                error=f"Unsupported platform: {sys.platform}"
            )

    async def _get_systemd_status(self) -> DaemonInfo:
        """Get systemd service status."""
        service_path = self._get_systemd_service_path()

        if not service_path.exists():
            return DaemonInfo(
                status=DaemonStatus.NOT_INSTALLED,
                service_path=str(service_path)
            )

        try:
            # Check if service is active
            result = subprocess.run(
                ["systemctl", "--user", "is-active", SERVICE_NAME],
                capture_output=True,
                text=True
            )
            is_active = result.stdout.strip() == "active"

            # Get PID if running
            pid = None
            if is_active:
                result = subprocess.run(
                    ["systemctl", "--user", "show", SERVICE_NAME, "--property=MainPID"],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    pid_str = result.stdout.strip().split("=")[-1]
                    pid = int(pid_str) if pid_str.isdigit() else None

            # Get uptime
            uptime = None
            if is_active:
                result = subprocess.run(
                    ["systemctl", "--user", "show", SERVICE_NAME, "--property=ActiveEnterTimestamp"],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    uptime = result.stdout.strip().split("=")[-1]

            return DaemonInfo(
                status=DaemonStatus.RUNNING if is_active else DaemonStatus.STOPPED,
                pid=pid,
                uptime=uptime,
                service_path=str(service_path)
            )
        except Exception as e:
            return DaemonInfo(status=DaemonStatus.UNKNOWN, error=str(e))

    async def _get_launchd_status(self) -> DaemonInfo:
        """Get launchd service status."""
        plist_path = self._get_launchd_plist_path()

        if not plist_path.exists():
            return DaemonInfo(
                status=DaemonStatus.NOT_INSTALLED,
                service_path=str(plist_path)
            )

        try:
            # Check if service is loaded
            result = subprocess.run(
                ["launchctl", "list"],
                capture_output=True,
                text=True
            )

            is_running = "com.autowrkers.daemon" in result.stdout

            # Get PID if running
            pid = None
            if is_running:
                for line in result.stdout.split("\n"):
                    if "com.autowrkers.daemon" in line:
                        parts = line.split()
                        if parts and parts[0].isdigit():
                            pid = int(parts[0])
                        break

            return DaemonInfo(
                status=DaemonStatus.RUNNING if is_running else DaemonStatus.STOPPED,
                pid=pid,
                service_path=str(plist_path)
            )
        except Exception as e:
            return DaemonInfo(status=DaemonStatus.UNKNOWN, error=str(e))

    # ==================== Health Monitoring ====================

    async def check_health(self) -> dict:
        """Check the health of the running service."""
        import httpx

        status = await self.get_status()

        result = {
            "daemon": status.to_dict(),
            "server": None,
            "healthy": False,
        }

        if status.status != DaemonStatus.RUNNING:
            result["error"] = "Daemon is not running"
            return result

        # Check if server is responding
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get("http://localhost:8420/health")
                if response.status_code == 200:
                    result["server"] = response.json()
                    result["healthy"] = result["server"].get("status") == "healthy"
                else:
                    result["error"] = f"Server returned {response.status_code}"
        except httpx.ConnectError:
            result["error"] = "Cannot connect to server"
        except Exception as e:
            result["error"] = str(e)

        return result

    def get_logs(self, lines: int = 100) -> list:
        """Get recent service logs."""
        if self._is_linux:
            try:
                result = subprocess.run(
                    ["journalctl", "--user", "-u", SERVICE_NAME, "-n", str(lines), "--no-pager"],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    return result.stdout.strip().split("\n") if result.stdout.strip() else []
                return [f"Error: {result.stderr}"]
            except Exception as e:
                return [f"Error retrieving logs: {e}"]
        elif self._is_macos:
            log_file = Path.home() / ".autowrkers" / "logs" / "stdout.log"
            if log_file.exists():
                try:
                    with open(log_file, "r") as f:
                        all_lines = f.readlines()
                        return [line.rstrip() for line in all_lines[-lines:]]
                except Exception as e:
                    return [f"Error reading logs: {e}"]
            return ["No log file found"]
        return ["Logs not available on this platform"]


# Global instance
daemon_manager = DaemonManager()


# Graceful shutdown handler
def setup_graceful_shutdown():
    """Set up signal handlers for graceful shutdown."""
    def handle_shutdown(signum, frame):
        logger.info("Received shutdown signal, saving state...")

        # Import here to avoid circular imports
        from .session_manager import manager

        # Save session state
        manager._save_sessions()
        logger.info("Session state saved, exiting...")
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)
