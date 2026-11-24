import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from telegram import Chat, Message, MessageEntity, Update, User
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
        self.studio.lock=asyncio.Lock()
        self.studio.job=SimpleNamespace(data={"status":"awaiting_approval"})
        self.studio.submit=AsyncMock(return_value=SimpleNamespace(data={"status":"awaiting_approval"}))
        self.studio.add_instruction=Mock(return_value="Added to the current plan.")
        self.context=SimpleNamespace(args=[],bot=Mock(),application=SimpleNamespace(bot_data={"supervisor":self.studio},create_task=launch))

    async def test_tt_never_triggers_job_or_api(self):
        await bot.handle_request(self.update,self.context,"tt")
        self.studio.submit.assert_not_called(); self.assertEqual(self.tasks,[])
        self.assertIn("specific game",self.message.reply_text.await_args.args[0])

    async def test_create_returns_a_plan_and_keeps_approve_as_the_build_gate(self):
        self.context.args=["Make a Mario-like platformer"]
        await bot.create_game(self.update,self.context)
        self.assertEqual(len(self.tasks),1)
        await asyncio.gather(*self.tasks)
        self.studio.submit.assert_awaited_once()
        self.assertTrue(self.studio.submit.await_args.kwargs["explicit_create"])
        self.studio.approve.assert_not_called()
        self.assertNotIn("operation",self.context.application.bot_data)

    async def test_approve_command_starts_the_saved_build(self):
        self.studio.approve=AsyncMock(return_value=[])
        await bot.approve(self.update,self.context)
        await asyncio.gather(*self.tasks)
        self.studio.approve.assert_awaited_once()

    async def test_authorized_budget_command_updates_the_persistent_cap(self):
        self.context.args=["20"]
        self.studio.set_budget.return_value=20
        await bot.budget(self.update,self.context)
        self.studio.set_budget.assert_called_once_with("20")
        self.assertIn("$20",self.message.reply_text.await_args.args[0])

    async def test_unauthorized_budget_change_is_rejected(self):
        self.context.args=["50"]
        self.update.effective_user.id=7
        await bot.budget(self.update,self.context)
        self.studio.set_budget.assert_not_called()
        self.message.reply_text.assert_awaited_once_with("Access denied.")

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
        self.studio.add_instruction.assert_called_once_with("Create a platformer")
        self.assertIn("Added to the current plan",self.message.reply_text.await_args.args[0])

    async def test_new_details_during_generation_are_saved_to_the_running_job(self):
        self.studio.job.data={"status":"building"}
        self.studio.busy.return_value=True
        self.studio.add_instruction.return_value="Saved with this job; queued for later."
        await bot.text_message(self.update,self.context)
        self.studio.submit.assert_not_called()
        self.studio.add_instruction.assert_called_once_with("Create a platformer")
        self.assertIn("queued",self.message.reply_text.await_args.args[0])

    async def test_answer_to_legacy_clarification_reuses_the_original_game_request(self):
        self.studio.job.data={"status":"rejected","request":"Create a 3D racing game for PC",
                              "error":"Please clarify before building: choose a control scheme"}
        self.message.text="Use WASD and a chase camera"
        await bot.text_message(self.update,self.context)
        await asyncio.gather(*self.tasks)
        self.assertIn("Create a 3D racing game for PC",self.studio.submit.await_args.args[0])
        self.assertIn("Use WASD and a chase camera",self.studio.submit.await_args.args[0])
        self.assertTrue(self.studio.submit.await_args.kwargs["explicit_create"])

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

    async def test_actual_telegram_command_entity_routes_create_and_start_separately(self):
        with patch.dict(os.environ,{"TELEGRAM_BOT_TOKEN":"123:ABC"}): app=bot.build_application()
        for command, callback in (("create",bot.create_game),("start",bot.start)):
            text="/"+command+" A 3D driving game"
            message=Message(2,datetime.now(timezone.utc),Chat(42,"private"),from_user=User(42,"Tester",False),
                            text=text,entities=[MessageEntity("bot_command",0,len(command)+1)])
            message.set_bot(SimpleNamespace(username="StudioBot"))
            update=Update(2,message=message)
            selected=next(h for h in app.handlers[0] if h.check_update(update))
            self.assertIs(selected.callback,callback)

    async def test_pasted_create_without_command_entity_and_caption_never_show_start_menu(self):
        for field in ("text","caption"):
            self.message.text=None; self.message.caption=None
            setattr(self.message,field,"  /create A 3D driving game")
            self.studio.job.data["status"]="failed"
            self.studio.submit.reset_mock();self.message.reply_text.reset_mock()
            await bot.unsupported_message(self.update,self.context)
            await asyncio.gather(*self.tasks)
            self.studio.submit.assert_awaited_once_with("A 3D driving game",chat_id=42,
                notify=self.studio.submit.await_args.kwargs["notify"],explicit_create=True,initial_instructions=[])
            self.assertTrue(all(call.args[0]!=bot.HELP_TEXT for call in self.message.reply_text.await_args_list))

    async def test_explicit_new_create_supersedes_waiting_plan(self):
        self.studio.busy.side_effect=lambda:self.studio.job.data["status"]=="awaiting_approval"
        self.studio.cancel.side_effect=lambda:self.studio.job.data.update(status="cancelled")
        self.context.args=["A new puzzle game"]
        await bot.create_game(self.update,self.context)
        await asyncio.gather(*self.tasks)
        self.studio.cancel.assert_called_once()
        self.studio.submit.assert_awaited_once()
        self.studio.add_instruction.assert_not_called()

    async def test_explicit_create_during_build_never_modifies_old_game(self):
        self.studio.job.data["status"]="building";self.studio.busy.return_value=True
        self.context.args=["A different game"]
        await bot.create_game(self.update,self.context)
        self.studio.submit.assert_not_called();self.studio.add_instruction.assert_not_called()
        self.assertIn("/cancel",self.message.reply_text.await_args.args[0])

    async def test_finished_task_latch_does_not_block_new_request(self):
        task=asyncio.create_task(asyncio.sleep(0));await task
        self.context.application.bot_data["operation"]=task
        self.studio.job.data["status"]="failed"
        await bot.handle_request(self.update,self.context,"A puzzle",True)
        await asyncio.gather(*self.tasks)
        self.studio.submit.assert_awaited_once()

    async def test_new_explicit_game_does_not_inherit_pre_job_instructions(self):
        self.context.application.bot_data["pre_job_instructions"]=["Old game's snowy track"]
        self.studio.job.data["status"]="failed"
        await bot.handle_request(self.update,self.context,"A new puzzle",True)
        await asyncio.gather(*self.tasks)
        self.assertEqual(self.studio.submit.await_args.kwargs["initial_instructions"],[])

    async def test_duplicate_update_does_not_create_another_job(self):
        self.update.update_id=100
        self.studio.job.data["status"]="failed"
        await bot.handle_request(self.update,self.context,"A puzzle",True)
        await asyncio.gather(*self.tasks)
        await bot.handle_request(self.update,self.context,"A puzzle",True)
        self.studio.submit.assert_awaited_once()

    async def test_long_plan_keeps_all_content_and_final_approval_instruction(self):
        text="Game plan 🎮\n"*600+"Send /approve"
        await bot.reply_chunks(self.message,text)
        chunks=[call.args[0] for call in self.message.reply_text.await_args_list]
        self.assertEqual("".join(chunks),text)
        self.assertTrue(all(len(chunk.encode("utf-16-le"))//2<=3500 for chunk in chunks))
        self.assertIn("/approve",chunks[-1])


if __name__=="__main__": unittest.main()
