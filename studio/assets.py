import base64
import hashlib
import io
import json

from PIL import Image, ImageDraw

from studio.config import CACHE
from studio.api import ServiceRejected
from studio.costs import BudgetExceeded
from studio.state import JobCancelled, write_json


class ArtDirector:
    async def create(self, service, spec, project):
        folder = project / "Assets" / "Studio" / "Art"
        folder.mkdir(parents=True, exist_ok=True)
        result = {}
        manifest = []
        reference = None
        for asset in spec.required_assets:
            service.job.check_cancelled()
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
            if cached.exists() and provenance_path.exists():
                image = Image.open(cached).convert("RGBA")
                provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
                provenance["cache_hit"] = True
            else:
                for model in service.registry.candidates("art"):
                    if service.job.data.get("image_service_blocked"):
                        break
                    try:
                        endpoint, ceiling = service.registry.image_endpoint(model)
                        supported = endpoint["supported_parameters"]
                        if asset.transparent and "background" not in supported:
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
                        break
                    except JobCancelled:
                        raise
                    except Exception as error:
                        image = None
                        provenance = {}
                        service.job.event("art", "art_generation_failed", model=model,
                                          error=str(error)[:500] if isinstance(error,RuntimeError) else type(error).__name__)
                        if isinstance(error,ServiceRejected) and error.status in (401,402,403):
                            service.job.data["image_service_blocked"] = str(error)
                            service.job.save()
                            break
                if image is None:
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
        return result


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
