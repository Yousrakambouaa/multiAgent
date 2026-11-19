import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from studio.config import JOBS


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


class Job:
    def __init__(self, directory, data):
        self.directory = Path(directory)
        self.data = data
        self._state_lock = threading.RLock()

    @classmethod
    def create(cls, request, cap, chat_id=None, root=JOBS):
        job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:6]
        job = cls(root / job_id, dict(id=job_id, request=request, chat_id=chat_id,
            status="planning", stage="validation", created_at=now(), updated_at=now(),
            cap_usd=cap, costs=[], tasks=[], artifacts=[], cancel_requested=False, repairs=0))
        job.save()
        return job

    @classmethod
    def load(cls, path):
        path = Path(path)
        return cls(path.parent, json.loads(path.read_text(encoding="utf-8")))

    def save(self):
        with self._state_lock:
            self.data["updated_at"] = now()
            write_json(self.directory / "job.json", self.data)

    def event(self, role, stage, **details):
        event = dict(at=now(), role=role, stage=stage, **details)
        self.data["stage"] = stage
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event) + "\n")
        self.save()

    def check_cancelled(self):
        # Read the persistent flag as well as the in-memory flag.
        saved = json.loads((self.directory / "job.json").read_text(encoding="utf-8"))
        if self.data.get("cancel_requested") or saved.get("cancel_requested"):
            self.data["cancel_requested"] = True
            raise JobCancelled("Job cancelled")


class JobCancelled(Exception):
    pass


class ProcessLock:
    """OS-held lock; crashes release it, preventing stale lock-file deadlocks."""
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        self.stream.seek(0)
        self.stream.write(b"0")
        self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.stream.close()
            self.stream = None
            raise RuntimeError(f"Another studio process holds {self.path.name}") from error
        return self

    def __exit__(self, *args):
        if self.stream:
            self.stream.close()
            self.stream = None
