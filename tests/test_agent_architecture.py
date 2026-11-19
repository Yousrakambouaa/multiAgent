"""Regression contracts for the five-agent limit and existing task profiles."""
import inspect
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pydantic import ValidationError

from studio import agents, assets, supervisor
from studio.agents import AGENT_MODEL_PROFILES, GameDesigner, UnityDeveloper, demo_spec
from studio.registry import Registry
from studio.scenes import assemble_scenes
from studio.schemas import UIPlan
from studio.supervisor import Supervisor, TASKS


class AgentArchitectureTests(unittest.TestCase):
    def test_exactly_five_agent_classes_and_registered_instances(self):
        defined={value for module in (agents,assets,supervisor) for value in vars(module).values()
                 if inspect.isclass(value) and value.__module__==module.__name__}
        self.assertEqual({cls.__name__ for cls in defined},
                         {"Supervisor","GameDesigner","UnityDeveloper","ArtDesigner","QADebugger"})
        with tempfile.TemporaryDirectory() as temp:
            controller=Supervisor(Path(temp))
            self.assertEqual(len(controller.agents),5)
            self.assertEqual(set(controller.agents),set(AGENT_MODEL_PROFILES))
            self.assertIs(controller.agents["supervisor"],controller)
            self.assertEqual({type(agent) for agent in controller.agents.values()},defined)
            self.assertEqual(len({id(agent) for agent in controller.agents.values()}),5)

    def test_all_task_owners_are_one_of_the_five_agents(self):
        self.assertTrue({owner for owner,_,_ in TASKS}.issubset(AGENT_MODEL_PROFILES))
        owners={name:owner for owner,name,_ in TASKS}
        self.assertEqual(owners["ui_plan"],"designer")
        self.assertEqual(owners["scene_spec"],"developer")
        self.assertEqual(owners["assembly"],"developer")
        self.assertEqual(owners["artifacts"],"supervisor")

    def test_existing_model_profiles_have_exactly_one_agent_owner(self):
        profiles=[profile for owned in AGENT_MODEL_PROFILES.values() for profile in owned]
        self.assertEqual(len(profiles),len(set(profiles)))
        self.assertEqual(set(profiles),set(Registry().roles))
        self.assertIn("ui",AGENT_MODEL_PROFILES["designer"])
        self.assertIn("scene",AGENT_MODEL_PROFILES["developer"])

    def test_scene_assembly_preserves_the_existing_deterministic_output(self):
        spec=demo_spec()
        ui=UIPlan(title=spec.name,subtitle="Follow the sun",play_label="Play",accent="#F5BD58",touch_button_size=100)
        art={a.kind:f"Assets/Studio/Art/{a.id}.png" for a in spec.required_assets}
        self.assertEqual(UnityDeveloper().assemble_scenes(spec,ui,art).model_dump(),
                         assemble_scenes(spec,ui,art).model_dump())


class ConsolidatedTaskTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.spec=demo_spec()
        self.ui=UIPlan(title=self.spec.name,subtitle="Follow the sun",play_label="Play",accent="#F5BD58",touch_button_size=100)
        art={a.kind:f"Assets/Studio/Art/{a.id}.png" for a in self.spec.required_assets}
        self.scenes=assemble_scenes(self.spec,self.ui,art)

    async def test_game_designer_ui_task_keeps_profile_schema_and_request_limit(self):
        service=SimpleNamespace(json=AsyncMock(return_value=self.ui.model_dump()))
        result=await GameDesigner().plan_ui(service,self.spec)
        self.assertEqual(result,self.ui)
        args=service.json.await_args.args
        self.assertEqual(args[0],"ui")
        self.assertEqual(args[2]["game_spec"],self.spec.model_dump())
        self.assertEqual(args[3],1200)

    async def test_unity_developer_scene_repair_keeps_profile_and_schema(self):
        service=SimpleNamespace(json=AsyncMock(return_value=self.scenes.model_dump()))
        result=await UnityDeveloper().repair_scenes(service,self.spec,self.scenes,"Missing reference")
        self.assertEqual(result,self.scenes)
        args=service.json.await_args.args
        self.assertEqual(args[0],"scene")
        self.assertEqual(args[2]["game_spec"],self.spec.model_dump())
        self.assertEqual(args[2]["errors"],"Missing reference")
        self.assertEqual(args[3],11000)

    async def test_moved_ui_and_scene_tasks_still_reject_invalid_outputs(self):
        service=SimpleNamespace(json=AsyncMock(return_value={"unexpected":"invalid contract"}))
        with self.assertRaises(ValidationError):
            await GameDesigner().plan_ui(service,self.spec)
        with self.assertRaises(ValidationError):
            await UnityDeveloper().repair_scenes(service,self.spec,self.scenes,"failure")


if __name__=="__main__": unittest.main()
