import json
import shutil
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from studio.state import Job, write_json
from studio.unity import UnityFailure
from studio.unity_live import run_live


def envelope(value):
    return {"content":[{"type":"text","text":json.dumps({"success":True,"data":value})}]}


class LiveCompilationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=Path(__file__).resolve().parent/(".unity-live-test-"+uuid4().hex)
        self.temp.mkdir()
        self.addCleanup(shutil.rmtree,self.temp)
        self.job=Job.create("test",2,root=self.temp)
        self.project=self.job.directory/"UnityProject";self.project.mkdir()
        self.runner=SimpleNamespace(job=self.job,project=self.project)
        self.client=SimpleNamespace(connect=AsyncMock(),close=AsyncMock(),verify_project=AsyncMock(),
            read_resource=AsyncMock(return_value={"editor":{"play_mode":{"is_playing":False}},
                "compilation":{"is_compiling":False},"advice":{"ready_for_tools":True}}))

    async def run_compile(self,success,errors=None,playing=False):
        calls=[]
        async def call(name,args):
            calls.append((name,args))
            if name=="refresh_unity":
                write_json(self.project/"Artifacts/compilation.json",{
                    "project":str(self.project),"started_unix_ms":int(time.time()*1000)+1,
                    "completed":True,"success":success,"errors":errors or []})
                return envelope({})
            # Historical console errors must not override a new successful compiler result.
            return envelope(["Assets/Studio/Generated/Old.cs(1,1): error CS1061: stale diagnostic"])
        self.client.for_agent=lambda role,project:SimpleNamespace(call=call)
        if playing:
            self.client.read_resource.return_value={"editor":{"play_mode":{"is_playing":True}}}
        with patch("studio.unity_live.UnityMCPClient.from_environment",return_value=self.client):
            result=await run_live(self.runner,"compile",[],2)
        return result,calls

    async def test_fresh_completed_evidence_passes_despite_old_console_errors(self):
        result,calls=await self.run_compile(True)
        self.assertIn("Initial Refresh End",result)
        self.assertEqual(calls[0][1]["scope"],"all")
        self.client.verify_project.assert_awaited_once_with(self.project)
        self.client.close.assert_awaited_once()

    async def test_fresh_errors_are_propagated_to_targeted_repair(self):
        error="Assets/Studio/Generated/MobileControls.cs(37,20): error CS1061: Button has no onPointerUp"
        with self.assertRaisesRegex(UnityFailure,"MobileControls.cs.*CS1061") as failure:
            await self.run_compile(False,[error])
        self.assertEqual(failure.exception.owner,"developer")

    async def test_live_compile_does_not_run_during_play_mode(self):
        with self.assertRaisesRegex(UnityFailure,"Stop Play mode"):
            await self.run_compile(True,playing=True)
