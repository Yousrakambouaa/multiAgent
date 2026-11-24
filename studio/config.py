import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def load_project_env(root=ROOT):
    # An inherited but empty shell value is still considered "set" by dotenv
    # and masks the real project value. Treat blank provider config as unset.
    for key in ("AI_PROVIDER", "OPENROUTER_API_KEY", "DAHL_API_KEY"):
        if key in os.environ and not os.environ[key].strip():
            os.environ.pop(key, None)
    load_dotenv(Path(root) / ".env")


load_project_env()
JOBS = ROOT / "jobs"
CACHE = ROOT / ".studio_cache"
TEMPLATES = ROOT / "studio" / "templates"
UNITY = Path(os.environ.get("UNITY_EDITOR_PATH", r"C:\Program Files\Unity\Hub\Editor\6000.3.24f1\Editor\Unity.exe"))
DEFAULT_CAP = float(os.environ.get("STUDIO_JOB_BUDGET_USD", "10"))
MAX_JOB_CAP = float(os.environ.get("STUDIO_MAX_JOB_BUDGET_USD", "50"))
BUILD_ANDROID = os.environ.get("STUDIO_BUILD_ANDROID", "1") == "1"


def safe_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("Path must stay inside its artifact directory")
    return path
