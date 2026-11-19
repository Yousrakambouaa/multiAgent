import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

from studio.agents import demo_spec
from studio.api import ServiceRejected
from studio.assets import ArtDesigner
from studio.state import Job


class AssetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.project=self.root/"project"
        self.job=Job.create("art",2,root=self.root/"jobs")
        self.registry=Mock()
        self.registry.candidates.return_value=["image/model"]
        self.registry.image_endpoint.return_value=({"provider_tag":"test","supported_parameters":{
            "background":{},"output_format":{},"aspect_ratio":{},"input_references":{}}},.02)
        self.service=SimpleNamespace(job=self.job,registry=self.registry,post=AsyncMock())
        patcher=patch("studio.assets.CACHE",self.root/"cache");patcher.start();self.addCleanup(patcher.stop)

    async def test_payment_failure_uses_owned_assets_and_stops_retrying(self):
        self.service.post.side_effect=ServiceRejected(402,"Insufficient credits")
        result=await ArtDesigner().create(self.service,demo_spec(),self.project)
        self.service.post.assert_awaited_once()
        manifest=json.loads((self.job.directory/"art_manifest.json").read_text())
        self.assertTrue(all(a["source"]=="original procedural fallback" for a in manifest))
        self.assertTrue((self.project/result["player"]).is_file())

    async def test_opaque_image_does_not_masquerade_as_transparent_sprite(self):
        image=Image.new("RGBA",(128,128),"red");stream=io.BytesIO();image.save(stream,"PNG")
        self.service.post.return_value={"data":[{"b64_json":base64.b64encode(stream.getvalue()).decode()}]}
        await ArtDesigner().create(self.service,demo_spec(),self.project)
        manifest=json.loads((self.job.directory/"art_manifest.json").read_text())
        self.assertEqual(next(a for a in manifest if a["kind"]=="player")["source"],"original procedural fallback")

    async def test_valid_images_use_reference_and_cache_without_more_calls(self):
        image=Image.new("RGBA",(128,128));image.putpixel((64,64),(255,100,30,255))
        stream=io.BytesIO();image.save(stream,"PNG")
        self.service.post.return_value={"data":[{"b64_json":base64.b64encode(stream.getvalue()).decode()}]}
        await ArtDesigner().create(self.service,demo_spec(),self.project)
        self.assertIn("input_references",self.service.post.await_args_list[1].args[1])
        self.service.post.reset_mock()
        await ArtDesigner().create(self.service,demo_spec(),self.root/"cached_project")
        self.service.post.assert_not_called()


if __name__=="__main__":unittest.main()
