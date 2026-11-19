import hashlib
import json
import zipfile

from studio.state import write_json


def package(job, project, report):
    report_path = job.directory / "validation_report.json"
    write_json(report_path, report)
    readme = (f"{job.data['id']}\n\nOpen UnityProject in Unity Hub using Unity 6000.3.24f1.\n"
              "Open the first scene in Assets/Studio/Scenes and press Play.\n"
              "Arrow keys or A/D: move. Space: jump. Touch buttons are on screen.\n\n"
              "Read validation_report.json for actual test scope and limitations.\n")
    (job.directory / "PLAY.txt").write_text(readme, encoding="utf-8")
    archive_path = job.directory / "unity_prototype.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for folder in ("Assets", "Packages", "ProjectSettings"):
            for path in (project / folder).rglob("*"):
                if path.is_file():
                    archive.write(path, "UnityProject/" + path.relative_to(project).as_posix())
        for name in ("validation_report.json", "game_spec.json", "scene_spec.json", "ui_plan.json", "art_manifest.json", "PLAY.txt"):
            archive.write(job.directory / name, name)
        archive.writestr("job.json",json.dumps(dict(job.data,status="completed"),indent=2))
    artifacts = [archive_path, report_path, job.directory / "PLAY.txt"]
    for filename in ("preview.png", "prototype.apk", "EditMode.xml", "PlayMode.xml"):
        path = project / "Artifacts" / filename
        if path.is_file() and (filename != "prototype.apk" or report.get("android", {}).get("status") == "passed"):
            artifacts.append(path)
    job.data["artifacts"] = [dict(path=str(p.relative_to(job.directory)), size=p.stat().st_size,
        sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in artifacts]
    job.save()
    return artifacts
