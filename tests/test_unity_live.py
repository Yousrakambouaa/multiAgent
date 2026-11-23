import unittest
from unittest.mock import AsyncMock, patch

from studio.unity import UnityFailure
from studio.unity_live import require_editor_idle


class UnityEditorIdleTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_for_play_mode_transition_without_stopping_editor(self):
        resource=AsyncMock(side_effect=[
            {"editor":{"play_mode":{"is_playing":True,"is_changing":True}}},
            {"editor":{"play_mode":{"is_playing":False,"is_changing":False}}},
        ])
        with patch("studio.unity_live.asyncio.sleep",new=AsyncMock()) as sleep:
            await require_editor_idle(resource,"PlayMode",10**12)
        self.assertEqual(resource.await_count,2)
        sleep.assert_awaited_once_with(.25)

    async def test_stable_play_mode_is_left_running_and_reported(self):
        resource=AsyncMock(return_value={
            "editor":{"play_mode":{"is_playing":True,"is_changing":False}}})
        with self.assertRaisesRegex(UnityFailure,"Stop Play mode"):
            await require_editor_idle(resource,"PlayMode",10**12)
        resource.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
