import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telegram import Chat, Message, Update, User
from telegram.ext import MessageHandler
from datetime import datetime, timezone

import telegram_bot as bot


class TelegramTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher=patch.dict(os.environ,{"TELEGRAM_ALLOWED_USER_ID":"42"})
        patcher.start(); self.addCleanup(patcher.stop)
        self.message=SimpleNamespace(text="Create a platformer",reply_text=AsyncMock())
        self.update=SimpleNamespace(effective_message=self.message,effective_user=SimpleNamespace(id=42),effective_chat=SimpleNamespace(id=42))
        self.tasks=[]
        def launch(coroutine,**kwargs):
            task=asyncio.create_task(coroutine); self.tasks.append(task); return task
        self.studio=Mock(); self.studio.busy.return_value=False
        self.studio.job=SimpleNamespace(data={"status":"awaiting_approval"})
        self.studio.submit=AsyncMock()
        self.context=SimpleNamespace(args=[],bot=Mock(),application=SimpleNamespace(bot_data={"supervisor":self.studio},create_task=launch))

    async def test_tt_never_triggers_job_or_api(self):
        await bot.handle_request(self.update,self.context,"tt")
        self.studio.submit.assert_not_called(); self.assertEqual(self.tasks,[])
        self.assertIn("specific 2D platformer",self.message.reply_text.await_args.args[0])

    async def test_authorized_request_runs_in_background_and_waits_for_approval(self):
        await bot.handle_request(self.update,self.context,"Make a Mario-like platformer")
        self.assertEqual(len(self.tasks),1)
        await asyncio.gather(*self.tasks)
        self.studio.submit.assert_awaited_once()
        self.studio.approve.assert_not_called()
        self.assertNotIn("operation",self.context.application.bot_data)

    async def test_every_sensitive_command_checks_authorization(self):
        self.update.effective_user.id=7
        for callback in (bot.create_game,bot.approve,bot.status,bot.plan,bot.budget,bot.cancel,bot.result):
            with self.subTest(command=callback.__name__):
                self.message.reply_text.reset_mock()
                await callback(self.update,self.context)
                self.message.reply_text.assert_awaited_once_with("Access denied.")
        self.studio.submit.assert_not_called()

    async def test_prevents_overlapping_requests(self):
        self.context.application.bot_data["operation"]=True
        await bot.text_message(self.update,self.context)
        self.studio.submit.assert_not_called()
        self.assertIn("already exists",self.message.reply_text.await_args.args[0])

    async def test_status_works_while_building(self):
        self.studio.job.data=dict(id="test",status="building",stage="PlayMode",tasks=[])
        await bot.status(self.update,self.context)
        self.assertIn("PlayMode",self.message.reply_text.await_args.args[0])

    async def test_unverified_artifacts_are_not_sent(self):
        sender=SimpleNamespace(send_message=AsyncMock(),send_document=AsyncMock(),send_photo=AsyncMock())
        await bot.deliver(sender,42,SimpleNamespace(data={"status":"failed"}))
        sender.send_document.assert_not_called(); sender.send_photo.assert_not_called()

    async def test_delivery_sends_real_files(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp); (directory/"report.json").write_text("{}")
            job=SimpleNamespace(directory=directory,data={"status":"completed","artifacts":[{"path":"report.json"}]})
            sender=SimpleNamespace(send_message=AsyncMock(),send_document=AsyncMock(),send_photo=AsyncMock())
            await bot.deliver(sender,42,job)
            sender.send_document.assert_awaited_once()

    async def test_plain_messages_and_all_commands_registered(self):
        with patch.dict(os.environ,{"TELEGRAM_BOT_TOKEN":"123:ABC"}): app=bot.build_application()
        update=Update(1,message=Message(1,datetime.now(timezone.utc),Chat(42,"private"),from_user=User(42,"Tester",False),text="tt"))
        first=next(h for h in app.handlers[0] if h.check_update(update))
        self.assertIsInstance(first,MessageHandler); self.assertIs(first.callback,bot.text_message)
        commands=set().union(*(getattr(h,"commands",set()) for h in app.handlers[0]))
        self.assertTrue({"create","status","plan","approve","cancel","budget","result"}.issubset(commands))


if __name__=="__main__": unittest.main()
