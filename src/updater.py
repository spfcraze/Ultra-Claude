import asyncio
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, List

import httpx

from src import __version__

GITHUB_REPO = "spfcraze/AutoWrkers"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}"
GITHUB_RAW_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}"
GITHUB_ZIP_URL = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/main.zip"

# Files and directories to preserve during updates (user data)
PRESERVE_PATTERNS = [
    "*.db",              # SQLite databases
    "*.sqlite",          # SQLite databases
    "*.sqlite3",         # SQLite databases
    ".env",              # Environment variables
    ".env.*",            # Environment variants
    "config.yaml",       # User config
    "config.yml",        # User config
    "config.json",       # User config
    "sessions.json",     # Session data
    "data/",             # Data directory
    "backups/",          # Backups directory
    ".claude/",          # Claude config
    "venv/",             # Virtual environment
    ".venv/",            # Virtual environment
    "__pycache__/",      # Python cache
    "*.pyc",             # Compiled Python
    ".git/",             # Git directory (if exists)
    "logs/",             # Log files
    "*.log",             # Log files
]


@dataclass
class UpdateInfo:
    current_version: str
    latest_version: Optional[str]
    update_available: bool
    release_url: Optional[str] = None
    release_notes: Optional[str] = None
    published_at: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "update_available": self.update_available,
            "release_url": self.release_url,
            "release_notes": self.release_notes,
            "published_at": self.published_at,
            "error": self.error,
        }


class Updater:
    def __init__(self):
        self.current_version = __version__
        self._project_root = self._find_project_root()

    def _find_project_root(self) -> Path:
        current = Path(__file__).parent.parent
        if (current / ".git").exists():
            return current
        if (current / "pyproject.toml").exists():
            return current
        return current

    def _parse_version(self, version: str) -> tuple:
        version = version.lstrip("v")
        parts = version.split(".")
        result = []
        for part in parts:
            try:
                result.append(int(part.split("-")[0].split("+")[0]))
            except ValueError:
                result.append(0)
        while len(result) < 3:
            result.append(0)
        return tuple(result[:3])

    def _is_newer_version(self, latest: str, current: str) -> bool:
        latest_tuple = self._parse_version(latest)
        current_tuple = self._parse_version(current)
        return latest_tuple > current_tuple

    async def check_for_updates(self) -> UpdateInfo:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{GITHUB_API_URL}/releases/latest",
                    headers={"Accept": "application/vnd.github.v3+json"},
                )

                if response.status_code == 404:
                    return await self._check_commits_for_updates(client)

                if response.status_code != 200:
                    return UpdateInfo(
                        current_version=self.current_version,
                        latest_version=None,
                        update_available=False,
                        error=f"GitHub API error: {response.status_code}",
                    )

                data = response.json()
                latest_version = data.get("tag_name", "").lstrip("v")

                if not latest_version:
                    return await self._check_commits_for_updates(client)

                update_available = self._is_newer_version(latest_version, self.current_version)

                return UpdateInfo(
                    current_version=self.current_version,
                    latest_version=latest_version,
                    update_available=update_available,
                    release_url=data.get("html_url"),
                    release_notes=data.get("body", "")[:500] if data.get("body") else None,
                    published_at=data.get("published_at"),
                )

        except httpx.TimeoutException:
            return UpdateInfo(
                current_version=self.current_version,
                latest_version=None,
                update_available=False,
                error="Connection timeout",
            )
        except Exception as e:
            return UpdateInfo(
                current_version=self.current_version,
                latest_version=None,
                update_available=False,
                error=str(e),
            )

    async def _check_commits_for_updates(self, client: httpx.AsyncClient) -> UpdateInfo:
        try:
            response = await client.get(
                f"{GITHUB_RAW_URL}/main/src/__init__.py",
                follow_redirects=True,
            )

            if response.status_code != 200:
                response = await client.get(
                    f"{GITHUB_RAW_URL}/master/src/__init__.py",
                    follow_redirects=True,
                )

            if response.status_code == 200:
                content = response.text
                for line in content.split("\n"):
                    if "__version__" in line and "=" in line:
                        version = line.split("=")[1].strip().strip("\"'")
                        update_available = self._is_newer_version(version, self.current_version)
                        return UpdateInfo(
                            current_version=self.current_version,
                            latest_version=version,
                            update_available=update_available,
                            release_url=f"https://github.com/{GITHUB_REPO}",
                        )

            return UpdateInfo(
                current_version=self.current_version,
                latest_version=None,
                update_available=False,
                error="Could not determine latest version",
            )

        except Exception as e:
            return UpdateInfo(
                current_version=self.current_version,
                latest_version=None,
                update_available=False,
                error=str(e),
            )

    def is_git_repo(self) -> bool:
        return (self._project_root / ".git").exists()

    async def get_local_git_status(self) -> dict:
        if not self.is_git_repo():
            return {"is_git": False, "error": "Not a git repository"}

        try:
            result = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "--short", "HEAD",
                cwd=str(self._project_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await result.communicate()
            local_commit = stdout.decode().strip() if result.returncode == 0 else None

            result = await asyncio.create_subprocess_exec(
                "git", "status", "--porcelain",
                cwd=str(self._project_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await result.communicate()
            has_changes = bool(stdout.decode().strip()) if result.returncode == 0 else False

            result = await asyncio.create_subprocess_exec(
                "git", "rev-parse", "--abbrev-ref", "HEAD",
                cwd=str(self._project_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await result.communicate()
            branch = stdout.decode().strip() if result.returncode == 0 else "unknown"

            return {
                "is_git": True,
                "local_commit": local_commit,
                "branch": branch,
                "has_uncommitted_changes": has_changes,
            }

        except Exception as e:
            return {"is_git": True, "error": str(e)}

    def _should_preserve(self, path: Path) -> bool:
        """Check if a file/directory should be preserved during update"""
        name = path.name
        rel_path = str(path)

        for pattern in PRESERVE_PATTERNS:
            if pattern.endswith("/"):
                # Directory pattern
                dir_name = pattern.rstrip("/")
                if name == dir_name or f"/{dir_name}/" in rel_path or rel_path.startswith(f"{dir_name}/"):
                    return True
            elif "*" in pattern:
                # Glob pattern
                import fnmatch
                if fnmatch.fnmatch(name, pattern):
                    return True
            else:
                # Exact match
                if name == pattern:
                    return True
        return False

    def _get_preserved_files(self) -> List[Path]:
        """Get list of files that should be preserved"""
        preserved = []
        for item in self._project_root.rglob("*"):
            if self._should_preserve(item):
                preserved.append(item)
        return preserved

    async def update_via_download(self, create_backup: bool = True) -> dict:
        """Update by downloading from GitHub (no git required)"""
        try:
            # Step 1: Create backup if requested
            backup_path = None
            if create_backup:
                backup_dir = self._project_root / "backups"
                backup_dir.mkdir(exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_path = backup_dir / f"backup_{timestamp}"

                # Backup preserved files
                preserved_files = []
                for item in self._project_root.iterdir():
                    if self._should_preserve(item):
                        preserved_files.append(item.name)
                        dest = backup_path / item.name
                        if item.is_dir():
                            shutil.copytree(item, dest, dirs_exist_ok=True)
                        else:
                            backup_path.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(item, dest)

                print(f"[INFO] Created backup at {backup_path}")
                print(f"[INFO] Preserved files: {preserved_files}")

            # Step 2: Download the latest zip from GitHub
            print("[INFO] Downloading latest version from GitHub...")
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                response = await client.get(GITHUB_ZIP_URL)

                if response.status_code != 200:
                    return {
                        "success": False,
                        "error": f"Failed to download update: HTTP {response.status_code}",
                    }

                zip_content = response.content

            # Step 3: Extract to temp directory
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                zip_path = temp_path / "update.zip"

                # Write zip file
                zip_path.write_bytes(zip_content)

                # Extract zip
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_path)

                # Find extracted directory (usually repo-name-branch)
                extracted_dirs = [d for d in temp_path.iterdir() if d.is_dir()]
                if not extracted_dirs:
                    return {"success": False, "error": "No directory found in downloaded archive"}

                source_dir = extracted_dirs[0]

                # Step 4: Copy new files, preserving user data
                updated_files = []
                skipped_files = []

                for item in source_dir.rglob("*"):
                    if item.is_file():
                        rel_path = item.relative_to(source_dir)
                        dest_path = self._project_root / rel_path

                        # Skip if this should be preserved and exists
                        if self._should_preserve(dest_path) and dest_path.exists():
                            skipped_files.append(str(rel_path))
                            continue

                        # Create parent directories
                        dest_path.parent.mkdir(parents=True, exist_ok=True)

                        # Copy file
                        shutil.copy2(item, dest_path)
                        updated_files.append(str(rel_path))

            # Step 5: Reinstall dependencies
            print("[INFO] Updating dependencies...")
            result = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", "-e", ".",
                cwd=str(self._project_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            pip_stdout, pip_stderr = await result.communicate()

            return {
                "success": True,
                "method": "download",
                "files_updated": len(updated_files),
                "files_preserved": len(skipped_files),
                "backup_path": str(backup_path) if backup_path else None,
                "restart_required": True,
                "message": f"Updated {len(updated_files)} files. {len(skipped_files)} user files preserved.",
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    async def update(self, force: bool = False, method: str = "auto") -> dict:
        """
        Update Autowrkers to the latest version.

        Args:
            force: Force update even with uncommitted changes (git only)
            method: Update method - "auto", "git", or "download"
        """
        # Auto-select method
        if method == "auto":
            method = "git" if self.is_git_repo() else "download"

        if method == "download":
            return await self.update_via_download(create_backup=True)

        # Git-based update (original logic)
        if not self.is_git_repo():
            return await self.update_via_download(create_backup=True)

        git_status = await self.get_local_git_status()
        if git_status.get("has_uncommitted_changes") and not force:
            return {
                "success": False,
                "error": "You have uncommitted changes. Commit or stash them first, or use force=true.",
            }

        try:
            result = await asyncio.create_subprocess_exec(
                "git", "fetch", "origin",
                cwd=str(self._project_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await result.communicate()
            if result.returncode != 0:
                return {"success": False, "error": f"git fetch failed: {stderr.decode()}"}

            branch = git_status.get("branch", "main")

            if force:
                result = await asyncio.create_subprocess_exec(
                    "git", "reset", "--hard", f"origin/{branch}",
                    cwd=str(self._project_root),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            else:
                result = await asyncio.create_subprocess_exec(
                    "git", "pull", "origin", branch,
                    cwd=str(self._project_root),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

            stdout, stderr = await result.communicate()

            if result.returncode != 0:
                return {"success": False, "error": f"git pull failed: {stderr.decode()}"}

            output = stdout.decode()
            already_up_to_date = "Already up to date" in output or "Already up-to-date" in output

            result = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", "-e", ".",
                cwd=str(self._project_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            pip_stdout, pip_stderr = await result.communicate()

            return {
                "success": True,
                "method": "git",
                "already_up_to_date": already_up_to_date,
                "git_output": output,
                "pip_output": pip_stdout.decode()[:500],
                "restart_required": not already_up_to_date,
            }

        except Exception as e:
            return {"success": False, "error": str(e)}


updater = Updater()
