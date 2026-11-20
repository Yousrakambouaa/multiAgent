import json
import os
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from game_examples import examples
from studio.agents import QADebugger
from studio.generation import compile_with_repairs, save_implementation
from studio.state import Job, ProcessLock
from studio.unity import UnityFailure, UnityRunner

DIAGNOSTIC = ("[Licensing::Module] LicensingClient has failed validation; ignoring\n"
    "Assets/Studio/Generated/MobileControls.cs(4,12): error CS1061: 'Button' does not contain a definition for 'onPointerUp'")


class AutonomousRepairTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        backend=patch.dict(os.environ,{"STUDIO_QA_BACKEND":"direct"})
        backend.start();self.addCleanup(backend.stop)
        self.temp=Path(__file__).resolve().parent/(".autonomous-repair-test-"+uuid4().hex)
        self.temp.mkdir()
        self.addCleanup(shutil.rmtree,self.temp)
        self.job=Job.create("repair controls",2,root=self.temp)
        self.project=self.job.directory/"UnityProject"
        _,self.spec,self.plan=examples("racing")
        self.plan.scripts.scripts[0].filename="MobileControls.cs"
        self.broken=("using UnityEngine; using UnityEngine.UI; "
            "public class MobileControls : MonoBehaviour { public Button button; "
            "void Setup(System.Action handler) { button.onPointerUp.AddListener(handler); } }")
        self.plan.scripts.scripts[0].content=self.broken
        self.correct=("using UnityEngine; using UnityEngine.EventSystems; "
            "public class MobileControls : MonoBehaviour, IPointerUpHandler { "
            "public void OnPointerUp(PointerEventData eventData) { } }")
        self.service=SimpleNamespace(job=self.job,json=AsyncMock(return_value={"scripts":[
            {"filename":"MobileControls.cs","content":self.correct}]}))
        self.controller=SimpleNamespace(agents={"qa":QADebugger()},notify=AsyncMock())

    async def test_cs1061_drives_real_file_replacement_and_recompile_preserving_probes(self):
        compiled=[]
        async def compile():
            source=(self.project/"Assets/Studio/Generated/MobileControls.cs").read_text()
            compiled.append(source)
            if "button.onPointerUp" in source:
                raise UnityFailure("compile",DIAGNOSTIC)
            return {"status":"passed"}
        repaired,result=await compile_with_repairs(self.controller,self.service,self.spec,self.project,
            self.plan,runner=SimpleNamespace(compile=compile))
        self.assertEqual(result["status"],"passed")
        self.assertEqual(compiled,[self.broken,self.correct])
        self.assertEqual(repaired.probes,self.plan.probes)
        self.assertEqual(repaired.scenes,self.plan.scenes)
        payload=self.service.json.await_args.args[2]
        self.assertEqual(payload["current_files"]["scripts"][0]["content"],self.broken)
        self.assertEqual(payload["compiler_errors"][0]["code"],"CS1061")
        report=json.loads((self.job.directory/"compiler_repairs.json").read_text())
        self.assertEqual(report["status"],"passed")
        self.assertEqual(self.job.data["repairs"],1)

    async def test_qa_reads_local_file_edits_instead_of_stale_plan(self):
        save_implementation(self.job,self.project,self.spec,self.plan)
        path=self.project/"Assets/Studio/Generated/MobileControls.cs"
        path.write_text(self.broken+"\n// local edit")
        await QADebugger().repair_scripts(self.service,self.spec,self.plan,self.project,DIAGNOSTIC)
        self.assertIn("local edit",self.service.json.await_args.args[2]["current_files"]["scripts"][0]["content"])

    async def test_no_change_response_stops_instead_of_describing_success(self):
        self.service.json.return_value={"scripts":[{"filename":"MobileControls.cs","content":self.broken}]}
        runner=SimpleNamespace(compile=AsyncMock(side_effect=UnityFailure("compile",DIAGNOSTIC)))
        with self.assertRaisesRegex(ValueError,"no file changes"):
            await compile_with_repairs(self.controller,self.service,self.spec,self.project,self.plan,runner=runner)
        runner.compile.assert_awaited_once()

    async def test_three_repairs_are_a_hard_limit(self):
        async def reply(*args):
            source=(self.project/"Assets/Studio/Generated/MobileControls.cs").read_text()+"\n// unsuccessful repair"
            return {"scripts":[{"filename":"MobileControls.cs","content":source}]}
        self.service.json.side_effect=reply
        runner=SimpleNamespace(compile=AsyncMock(side_effect=UnityFailure("compile",DIAGNOSTIC)))
        with self.assertRaises(UnityFailure):
            await compile_with_repairs(self.controller,self.service,self.spec,self.project,self.plan,runner=runner)
        self.assertEqual(runner.compile.await_count,4)
        self.assertEqual(self.service.json.await_count,3)

    async def test_license_blocker_does_not_trigger_model_repairs(self):
        runner=SimpleNamespace(compile=AsyncMock(side_effect=UnityFailure("compile","No valid Unity license","infrastructure")))
        with self.assertRaises(UnityFailure):
            await compile_with_repairs(self.controller,self.service,self.spec,self.project,self.plan,runner=runner)
        self.service.json.assert_not_awaited()

    async def test_batch_mode_never_launches_on_an_open_project(self):
        folder=self.project/"Temp";folder.mkdir(parents=True)
        with ProcessLock(folder/"UnityLockfile"),patch.dict(os.environ,{"STUDIO_UNITY_MODE":"batch"}),patch(
                "studio.unity.asyncio.create_subprocess_exec") as process:
            with self.assertRaisesRegex(UnityFailure,"already open"):
                await UnityRunner(self.job,self.project).compile()
        process.assert_not_called()

    async def test_auto_mode_routes_an_open_project_to_live_without_a_batch_process(self):
        with patch.dict(os.environ,{"STUDIO_UNITY_MODE":"auto"}),patch(
                "studio.unity.project_is_open",return_value=True),patch(
                "studio.unity_live.run_live",AsyncMock(return_value="Application.AssetDatabase Initial Refresh End")) as live,patch(
                "studio.unity.asyncio.create_subprocess_exec") as process:
            self.assertEqual((await UnityRunner(self.job,self.project).compile())["status"],"passed")
        live.assert_awaited_once()
        process.assert_not_called()
