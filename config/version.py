"""The deployed commit, for the reporting connector's report_health.

GIT_COMMIT in the environment wins. Without it we ask git, so a server that deploys with
`git pull` reports the right version without anyone remembering to edit .env. The answer is
read once, when the app starts, so restart the service after a deploy.
"""
import subprocess
from pathlib import Path


def deployed_commit(env_value: str = "", repo: Path | None = None) -> str:
    """The short commit id, or "" when it can't be known (no git, not a checkout)."""
    if (env_value or "").strip():
        return env_value.strip()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"], cwd=repo or Path(__file__).resolve().parent.parent,
            capture_output=True, text=True, timeout=3, check=True)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()
