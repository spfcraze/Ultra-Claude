"""
Secure Git credential helper for UltraClaude.

Replaces insecure NamedTemporaryFile(delete=False) pattern with a context manager
that guarantees cleanup and uses restrictive permissions.
"""
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .logging_config import get_logger

logger = get_logger("ultraclaude.git_credentials")


@contextmanager
def secure_credential_helper(token: str):
    """
    Context manager that creates a temporary credential helper script
    with restrictive permissions and guaranteed cleanup.

    Usage:
        with secure_credential_helper(token) as helper_path:
            subprocess.run(["git", "-c", f"credential.helper=!{helper_path}", "fetch"])

    The helper script is:
    - Created with 0o700 permissions (owner execute only)
    - Located in a private temp directory (0o700)
    - Guaranteed to be deleted when the context exits
    - Never leaves token on disk after operation
    """
    temp_dir = None
    helper_path = None

    try:
        # Create a private temp directory
        temp_dir = tempfile.mkdtemp(prefix="uc_git_")
        os.chmod(temp_dir, stat.S_IRWXU)  # 0o700 - owner only

        # Create the credential helper script inside the private dir
        helper_path = os.path.join(temp_dir, "credential-helper.sh")

        # Write with restrictive permissions from the start
        fd = os.open(helper_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
        try:
            os.write(fd, f'#!/bin/bash\necho "username=x-access-token"\necho "password={token}"\n'.encode())
        finally:
            os.close(fd)

        yield helper_path

    finally:
        # Guaranteed cleanup - overwrite file content before deletion
        if helper_path and os.path.exists(helper_path):
            try:
                # Overwrite with zeros before unlinking to prevent recovery
                size = os.path.getsize(helper_path)
                fd = os.open(helper_path, os.O_WRONLY)
                try:
                    os.write(fd, b'\x00' * size)
                finally:
                    os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(helper_path)
            except OSError as e:
                logger.warning(f"Failed to delete credential helper: {e}")

        if temp_dir and os.path.exists(temp_dir):
            try:
                os.rmdir(temp_dir)
            except OSError as e:
                logger.warning(f"Failed to remove temp directory: {e}")


def git_env() -> dict:
    """Return environment variables for secure git operations."""
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
