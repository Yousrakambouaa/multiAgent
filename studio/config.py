import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
JOBS = ROOT / "jobs"
CACHE = ROOT / ".studio_cache"
TEMPLATES = ROOT / "studio" / "templates"
UNITY = Path(os.environ.get("UNITY_EDITOR_PATH", r"C:\Program Files\Unity\Hub\Editor\6000.3.24f1\Editor\Unity.exe"))
DEFAULT_CAP = float(os.environ.get("STUDIO_JOB_BUDGET_USD", "2"))
BUILD_ANDROID = os.environ.get("STUDIO_BUILD_ANDROID", "0") == "1"


def safe_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("Path must stay inside its artifact directory")
    return path
