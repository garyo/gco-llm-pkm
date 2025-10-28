"""Utility functions for tool implementations."""

import subprocess
from typing import Tuple, List, Optional
import logging


def run_command_with_error_handling(
    cmd: List[str],
    timeout: int = 15,
    logger: Optional[logging.Logger] = None
) -> Tuple[str, str, int]:
    """Run subprocess command with comprehensive error handling.

    Args:
        cmd: Command and arguments as list
        timeout: Timeout in seconds
        logger: Optional logger for diagnostics

    Returns:
        Tuple of (stdout, stderr, returncode)

    Note:
        - returncode 0 = success with matches
        - returncode 1 = success but no matches (normal for rg)
        - returncode 2+ = actual error
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        if logger:
            cmd_str = ' '.join(cmd)
            logger.debug(f"Command: {cmd_str}")
            logger.debug(f"Exit code: {result.returncode}")

            if result.stderr:
                logger.warning(f"Command stderr: {result.stderr.strip()}")

            # returncode 1 is normal for ripgrep (no matches found)
            if result.returncode > 1:
                logger.error(
                    f"Command failed with exit code {result.returncode}: {cmd_str}"
                )

        return result.stdout, result.stderr, result.returncode

    except subprocess.TimeoutExpired as e:
        error_msg = f"Command timed out after {timeout}s: {' '.join(cmd)}"
        if logger:
            logger.error(error_msg)
        return "", error_msg, -1

    except Exception as e:
        error_msg = f"Command execution failed: {str(e)}"
        if logger:
            logger.error(f"{error_msg} ({' '.join(cmd)})")
        return "", error_msg, -1
