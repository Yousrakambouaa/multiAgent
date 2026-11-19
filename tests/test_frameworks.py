import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from studio.frameworks import autogen_json, crewai_json


class FrameworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_autogen_uses_only_the_metered_service(self):
        service=SimpleNamespace(json=AsyncMock(return_value={"scripts":[]}),job=SimpleNamespace(data={"costs":[]}))
        result=await autogen_json(service,"qa","Return JSON",{"game_spec":{"name":"test"}},500)
        self.assertEqual(result,{"scripts":[]})
        service.json.assert_awaited_once()
        self.assertEqual(service.json.await_args.args[0],"qa")

    async def test_crewai_uses_only_the_metered_service(self):
        service=SimpleNamespace(json=AsyncMock(return_value={"name":"test"}))
        result=await crewai_json(service,"designer","Return JSON",{"name":"test"},500)
        self.assertEqual(result,{"name":"test"}); service.json.assert_awaited_once()


if __name__=="__main__": unittest.main()
