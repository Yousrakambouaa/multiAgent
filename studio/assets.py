import base64
import hashlib
import io
import json
import re
import shutil
from types import SimpleNamespace

from PIL import Image, ImageDraw

from studio.config import CACHE, ROOT, safe_path
from studio.api import ServiceRejected
from studio.costs import BudgetExceeded
from studio.state import JobCancelled, write_json


class ArtDesigner:
    async def create(self, service, spec, project, strict=False):
        if hasattr(service, "unity_context"):
            await service.unity_context("art")
        if getattr(spec, "version", 1) == 2:
            return await create_flexible_art(self, service, spec, project)
        folder = project / "Assets" / "Studio" / "Art"
        folder.mkdir(parents=True, exist_ok=True)
        result = {}
        manifest = []
        reference = None
        saved_path = service.job.directory / "art_manifest.json"
        saved_assets = {}
        if service.job.data.get("resume_inputs") and saved_path.is_file():
            saved_assets = {entry["id"]: entry for entry in json.loads(saved_path.read_text(encoding="utf-8"))
                            if entry.get("path") and entry.get("status", "ready") == "ready"}
        rejected_requests = getattr(service, "image_request_failures", {})
        service.image_request_failures = rejected_requests
        for asset in spec.required_assets:
            service.job.check_cancelled()
            saved = saved_assets.get(asset.id)
            if saved:
                target = safe_path(project, saved["path"])
                if target.is_file():
                    with Image.open(target) as check:
                        check.verify()
                    manifest.append(saved)
                    result[asset.kind] = saved["path"]
                    if asset.kind == "player" and saved.get("source") == "OpenRouter image generation":
                        reference = "data:image/png;base64," + base64.b64encode(target.read_bytes()).decode("ascii")
                    service.job.event("art", "asset_reused", asset=asset.id, source=saved.get("source"))
                    write_json(saved_path, manifest)
                    continue
            prompt = (f"2D game production asset. {spec.art_style}. Consistent palette: {', '.join(spec.palette)}. "
                      f"{asset.prompt}. Single asset, no text, no watermark, no sprite sheet, no collage. "
                      + ("Transparent background, centered silhouette with empty margins." if asset.transparent
                         else "Wide seamless atmospheric backdrop with quiet lower foreground."))
            digest = hashlib.sha256(json.dumps([asset.model_dump(), prompt,
                hashlib.sha256(reference.encode()).hexdigest() if reference else None]).encode()).hexdigest()
            cached = CACHE / "art" / (digest + ".png")
            provenance_path = cached.with_suffix(".json")
            provenance = {}
            image = None
            failures = []
            if cached.exists() and provenance_path.exists():
                image = Image.open(cached).convert("RGBA")
                provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
                provenance["cache_hit"] = True
            else:
                for model in service.registry.candidates("art", transparent=asset.transparent):
                    if service.job.data.get("image_service_blocked"):
                        break
                    try:
                        endpoint, ceiling = service.registry.image_endpoint(model, transparent=asset.transparent)
                        supported = endpoint["supported_parameters"]
                        request_kind = (model, asset.transparent, bool(reference and "input_references" in supported))
                        if request_kind in rejected_requests:
                            failures.append(rejected_requests[request_kind])
                            continue
                        if asset.transparent and "background" not in supported:
                            failures.append(f"{model}: transparent backgrounds are unsupported")
                            continue
                        payload = {"model": model, "prompt": prompt, "n": 1,
                                   "provider": {"order": [endpoint["provider_tag"]], "allow_fallbacks": False}}
                        if "aspect_ratio" in supported:
                            payload["aspect_ratio"] = "16:9" if asset.kind == "background" else "1:1"
                        if "output_format" in supported:
                            payload["output_format"] = "png"
                        if "resolution" in supported:
                            payload["resolution"] = "1K"
                        if asset.transparent:
                            payload["background"] = "transparent"
                        if reference and "input_references" in supported:
                            payload["input_references"]=[{"type":"image_url","image_url":{"url":reference}}]
                            payload["prompt"] += " Match only the style and palette of the reference; render the requested object."
                        response = await service.post("images", payload, "art", model, ceiling)
                        raw = base64.b64decode(response["data"][0]["b64_json"], validate=True)
                        if len(raw) > 20_000_000:
                            raise ValueError("Generated image exceeds size limit")
                        with Image.open(io.BytesIO(raw)) as generated:
                            if generated.width > 4096 or generated.height > 4096:
                                raise ValueError("Generated image exceeds dimension limit")
                            generated.load()
                            image = generated.convert("RGBA")
                        if asset.transparent and image.getextrema()[3][0] == 255:
                            raise ValueError("Provider returned an opaque image for a transparent sprite")
                        provenance = {"source": "OpenRouter image generation", "model": model, "prompt": prompt,
                                      "slicing": "single sprite; no sheet slicing assumed", "cache_hit": False}
                        break
                    except BudgetExceeded:
                        if service.job.data.get("budget_halted"):
                            raise
                        failures.append("Image request did not fit the remaining job budget")
                        continue
                    except JobCancelled:
                        raise
                    except Exception as error:
                        image = None
                        provenance = {}
                        detail = str(error)[:700]
                        failures.append(detail)
                        if (isinstance(error, ServiceRejected) and error.status in (400, 404, 422)
                                or isinstance(error, ValueError) and "opaque image" in detail):
                            rejected_requests[(model, asset.transparent,
                                bool(reference and "input_references" in supported))] = detail
                        service.job.event("art", "art_generation_failed", model=model,
                                          error=str(error)[:500] if isinstance(error,RuntimeError) else type(error).__name__)
                        if isinstance(error,ServiceRejected) and error.status in (401,402,403):
                            service.job.data["image_service_blocked"] = str(error)
                            identity = getattr(service.registry, "_identity", None)
                            service.job.data["image_service_blocked_key"] = identity if isinstance(identity, str) else None
                            service.job.save()
                            break
                if image is None:
                    if strict:
                        manifest.append(dict(id=asset.id, kind=asset.kind, status="unavailable", path="",
                            source="unavailable", reason="; ".join(dict.fromkeys(failures)) or
                                service.job.data.get("image_service_blocked") or
                                "No compatible image endpoint supports this asset's requested format/transparency",
                            requirement_ids=asset.requirement_ids))
                        service.job.event("art", "asset_unavailable", asset=asset.id, reason=manifest[-1]["reason"])
                        write_json(service.job.directory / "art_manifest.json", manifest)
                        continue
                    image = procedural(asset.kind, spec.palette)
                    provenance = {"source": "original procedural fallback", "license": "project-owned generated geometry",
                                  "reason": "Image service unavailable, transparency unsupported or budget insufficient", "cache_hit": False}
                cached.parent.mkdir(parents=True, exist_ok=True)
                # Do not permanently cache failures: a subsequent job can retry AI art.
                if provenance["source"] == "OpenRouter image generation":
                    image.save(cached)
                    write_json(provenance_path, provenance)
            size = (1280, 720) if asset.kind == "background" else (asset.width, asset.height)
            image = image.resize(size, Image.Resampling.LANCZOS)
            target = folder / f"{asset.id}.png"
            image.save(target, "PNG")
            if asset.kind=="player" and provenance["source"]=="OpenRouter image generation":
                reference="data:image/png;base64,"+base64.b64encode(target.read_bytes()).decode("ascii")
            with Image.open(target) as check:
                check.verify()
            manifest.append(dict(id=asset.id, kind=asset.kind, path=target.relative_to(project).as_posix(),
                                 width=size[0], height=size[1], pixels_per_unit=asset.pixels_per_unit, **provenance))
            result[asset.kind] = target.relative_to(project).as_posix()
            service.job.event("art", "asset_saved", asset=asset.id, source=provenance["source"])
            write_json(service.job.directory / "art_manifest.json", manifest)
        write_json(service.job.directory / "art_manifest.json", manifest)
        return result


async def create_flexible_art(designer, service, spec, project):
    """Use image endpoints only for images; import licensed native Unity assets.

    Missing assets remain explicit. No platformer shapes are substituted into v2.
    """
    images = [a for a in spec.required_assets if a.workflow == "image"]
    manifest = []
    if images and hasattr(service, "registry") and not list(service.registry.candidates("art")):
        provider = getattr(getattr(service.registry, "provider", None), "label", "Selected provider")
        service.job.event("art", "image_generation_unavailable", reason=f"{provider} has no compatible image-generation endpoint")
    if images:
        adapter = SimpleNamespace(required_assets=images, art_style=spec.art_style, palette=spec.palette)
        await designer.create(service, adapter, project, strict=True)
        manifest = json.loads((service.job.directory / "art_manifest.json").read_text(encoding="utf-8"))
        by_id = {a.id: a for a in images}
        for entry in manifest:
            entry["status"] = "ready" if entry.get("path") else "unavailable"
            entry["requirement_ids"] = by_id[entry["id"]].requirement_ids
        request = service.job.data.get("effective_request", service.job.data.get("request", ""))
        if allows_placeholder_art(request):
            for entry in manifest:
                if entry["status"] == "ready":
                    continue
                asset = by_id[entry["id"]]
                target = project / "Assets/Studio/Art" / (asset.id + ".png")
                target.parent.mkdir(parents=True, exist_ok=True)
                placeholder_image(asset, spec.palette).save(target, "PNG")
                entry.update(status="ready", path=target.relative_to(project).as_posix(),
                             source="original procedural placeholder", is_placeholder=True,
                             license="project-owned generated geometry", width=asset.width, height=asset.height,
                             pixels_per_unit=asset.pixels_per_unit)
                service.job.event("art", "placeholder_saved", asset=asset.id, reason=entry.get("reason", ""))
    suffixes = {"model": {".fbx", ".obj"}, "prefab": {".prefab"}, "audio": {".wav", ".ogg"},
                "animation": {".anim"}, "sprite": {".png", ".jpg"}, "texture": {".png", ".jpg"}}
    for asset in spec.required_assets:
        if asset.workflow == "image":
            continue
        entry = dict(id=asset.id, kind=asset.kind, requirement_ids=asset.requirement_ids,
                     status="unavailable", source=asset.workflow, path="", license=asset.license)
        try:
            if asset.workflow == "unavailable":
                raise ValueError("No configured tool or licensed asset can produce this requested asset")
            source = safe_path(ROOT / "asset_library", asset.library_path)
            if not source.is_file() or source.suffix.lower() not in suffixes[asset.kind]:
                raise ValueError("Licensed library asset is missing or is not a supported native Unity format")
            if source.stat().st_size > 50_000_000:
                raise ValueError("Library asset exceeds 50 MB import limit")
            target = project / "Assets" / "Studio" / "Art" / (asset.id + source.suffix.lower())
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            # Preserve GUIDs when available, so imported prefabs can retain references.
            if source.with_suffix(source.suffix + ".meta").is_file():
                shutil.copy2(source.with_suffix(source.suffix + ".meta"), target.with_suffix(target.suffix + ".meta"))
            entry.update(status="ready", path=target.relative_to(project).as_posix(),
                         pixels_per_unit=asset.pixels_per_unit, source="licensed local library",
                         library_path=asset.library_path)
        except (ValueError, OSError) as error:
            entry["reason"] = str(error)
        manifest.append(entry)
        service.job.event("art", "asset_" + entry["status"], asset=asset.id)
    write_json(service.job.directory / "art_manifest.json", manifest)
    return {entry["id"]: entry for entry in manifest}


def asset_summary(manifest):
    ready = [entry for entry in manifest if entry.get("status") == "ready"]
    missing = [entry for entry in manifest if entry.get("status") != "ready"]
    lines = [f"Art Designer: {len(ready)} assets ready, {len(missing)} unavailable."]
    lines.extend(f"- Ready: {entry['id']} ({entry.get('source', 'imported')})" for entry in ready)
    lines.extend(f"- Unavailable: {entry['id']}: {entry.get('reason', 'No usable asset produced')}" for entry in missing)
    return "\n".join(lines)


def allows_placeholder_art(request):
    """Require explicit user permission; never silently replace custom artwork."""
    for clause in re.split(r"[.!?;\n]", str(request)):
        if not re.search(r"\bplaceholders?\b", clause, re.I):
            continue
        if re.search(r"\b(?:no|not|never|without|avoid|forbid|disallow|don't|do not)\b", clause, re.I):
            continue
        if re.search(r"\b(?:generate|create|use|allow|accept|provide|include)\b", clause, re.I):
            return True
    return False


def placeholder_image(asset, palette):
    """An explicitly permitted, visibly simple sprite/texture, with honest provenance."""
    colors = palette or ["#203044", "#62D49F", "#FFD166", "#F4F7FB"]
    foreground = colors[1 % len(colors)]
    image = Image.new("RGBA", (asset.width, asset.height), (0, 0, 0, 0) if asset.transparent else colors[0])
    draw = ImageDraw.Draw(image)
    width, height = asset.width, asset.height
    margin = max(2, min(width, height) // 10)
    draw.rounded_rectangle((margin, margin, width - margin, height - margin),
                           radius=min(width, height) // 5, fill=foreground,
                           outline=colors[-1], width=max(2, min(width, height) // 32))
    return image


def procedural(kind, palette):
    """Original fallback shapes, requiring no downloaded third-party license."""
    image = Image.new("RGBA", (256, 256))
    d = ImageDraw.Draw(image)
    if kind == "player":
        d.rounded_rectangle((65, 76, 192, 218), 28, fill=palette[1], outline=palette[3], width=7)
        d.ellipse((74, 27, 181, 130), fill=palette[2])
        d.ellipse((137, 67, 151, 81), fill=palette[0])
        d.rounded_rectangle((54, 18, 196, 53), 13, fill=palette[1])
    elif kind == "coin":
        d.ellipse((30, 30, 226, 226), fill=palette[2], outline="#FFF5BD", width=15)
        d.ellipse((66, 66, 190, 190), outline="#C88B28", width=10)
        d.line((128, 89, 128, 167), fill="#FFF5BD", width=14)
    elif kind == "enemy":
        d.ellipse((22, 55, 234, 227), fill="#DE795D", outline=palette[0], width=9)
        d.ellipse((65, 111, 82, 133), fill=palette[0]); d.ellipse((168, 111, 185, 133), fill=palette[0])
    elif kind == "background":
        image = Image.new("RGBA", (1280, 720), palette[0]); d = ImageDraw.Draw(image)
        d.ellipse((830, 40, 1100, 310), fill=palette[2])
        for index, color in enumerate(["#244F59", "#306F66", palette[1]]):
            y = 330 + index * 95
            d.polygon([(0, y+100), (180,y-20), (460,y+70), (760,y-50), (1050,y+70), (1280,y-20), (1280,720), (0,720)], fill=color)
    else:
        d.rounded_rectangle((0, 10, 255, 255), 16, fill="#74573E")
        d.rounded_rectangle((0, 0, 255, 68), 15, fill=palette[1])
        d.line((10, 25, 240, 25), fill=palette[3], width=5)
        for x in (35, 100, 170, 230):
            d.ellipse((x-5, 120, x+6, 133), fill="#96764C")
    return image
