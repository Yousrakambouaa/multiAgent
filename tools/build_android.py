"""Build Android from an unchanged, already-tested source archive, then repackage."""
import asyncio
import hashlib
import json
import zipfile

from studio.artifacts import package
from studio.config import JOBS, ROOT
from studio.state import Job, ProcessLock
from studio.unity import UnityFailure, UnityRunner, available_space


async def main():
    job_id=(JOBS/"current.txt").read_text().strip();job=Job.load(JOBS/job_id/"job.json")
    report=json.loads((job.directory/"validation_report.json").read_text(encoding="utf-8"))
    if report["stages"]["PlayMode"].get("passed",0)<10 or report["stages"]["EditMode"].get("passed",0)<2:
        raise RuntimeError("Run all required Unity tests before building")
    project=job.directory/"UnityProject"
    with zipfile.ZipFile(job.directory/"unity_prototype.zip") as archive:
        for name in archive.namelist():
            if name.startswith("UnityProject/"):
                path=job.directory/name
                if not path.is_file() or hashlib.sha256(path.read_bytes()).digest()!=hashlib.sha256(archive.read(name)).digest():
                    raise RuntimeError("Source changed after verification: "+name)
    print("Source matches the verified archive. Available bytes:",available_space(project),flush=True)
    job.data["status"]="building";job.event("unity","android")
    try:
        report["android"]=await UnityRunner(job,project).android()
        print("APK build and integrity checks passed",flush=True)
    except UnityFailure as error:
        report["android"]={"status":"failed","error":str(error),"device_test":"unverified"}
        print("Android failed:",str(error)[-1800:],flush=True)
    package(job,project,report)
    job.data["status"]="completed";job.event("supervisor","completed")
    print("Final Android status:",report["android"]["status"],flush=True)


if __name__=="__main__":
    with ProcessLock(ROOT/".studio_cache/controller.lock"),ProcessLock(JOBS/".unity.lock"):
        asyncio.run(main())
