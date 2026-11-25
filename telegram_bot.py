"""Authorized Telegram transport; orchestration lives in studio.supervisor."""
import asyncio
import logging
import os
import hashlib
import re

from telegram import BotCommand, BotCommandScopeChat, Update
from telegram.error import Conflict
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from studio.config import ROOT, safe_path
from studio.costs import Ledger
from studio.failures import failure_message
from studio.state import ProcessLock, now, write_json
from studio.supervisor import Supervisor
from studio.request_validation import NOISE

logger=logging.getLogger(__name__)
HELP_TEXT=("Unity Game Studio builds prompt-driven 2D and 3D prototypes and reports what was verified.\n"
    "/create <game description> — create a plan\n/plan — inspect the plan\n/approve — start Unity generation\n"
    "/status — progress\n/cancel — stop a job\n/budget — spending\n/result — project, report and preview\n\n"
    "Example: /create A woodland platformer with jumping, coins and patrolling enemies. "
    "Use /budget to inspect spending or /budget 10 to set the API cap. Complex games may produce a partial result with explicit limitations.")


def authorized(update):
    return update.effective_user is not None and update.effective_user.id==int(os.environ["TELEGRAM_ALLOWED_USER_ID"])


async def guard(update):
    if authorized(update): return True
    await update.effective_message.reply_text("Access denied."); return False


def supervisor(context):
    return context.application.bot_data["supervisor"]


async def start(update,context):
    await update.effective_message.reply_text(HELP_TEXT)


async def reply_chunks(message, text):
    # Plans can exceed Telegram's 4096-character limit. Keep the approval prompt.
    while text:
        end = min(len(text), 3500)
        while len(text[:end].encode("utf-16-le")) // 2 > 3500:
            end -= 1
        await message.reply_text(text[:end])
        text = text[end:]


def operation_running(application):
    task = application.bot_data.get("operation")
    if isinstance(task, asyncio.Task) and task.done():
        application.bot_data.pop("operation", None)
        return False
    return bool(task)


async def myid(update,context):
    await update.effective_message.reply_text(f"Your Telegram ID: {update.effective_user.id}")


async def status(update,context):
    if not await guard(update): return
    job=supervisor(context).job
    if not job:
        await update.effective_message.reply_text("Online. No job yet. Use /create <description>."); return
    tasks="\n".join(f"{task['name']}: {task['status']}" for task in job.data["tasks"])
    pending=[item["text"] for item in job.data.get("additional_instructions",[]) if item.get("status")=="pending_approval"]
    queued=[item["text"] for item in job.data.get("additional_instructions",[]) if item.get("status")=="queued"]
    update_text=("\nWill apply when you /approve:\n"+"\n".join(pending) if pending else "")
    if queued: update_text += "\nQueued for a follow-up (not applied to current build):\n"+"\n".join(queued)
    await update.effective_message.reply_text((f"Job {job.data['id']}\nStatus: {job.data['status']}\nStage: {job.data['stage']}\n"
        f"{tasks}\n{job.data.get('error','')}{update_text}")[:3900])


async def plan(update,context):
    if await guard(update): await reply_chunks(update.effective_message, supervisor(context).plan_text())


async def budget(update,context):
    if not await guard(update): return
    studio=supervisor(context)
    args=getattr(context,"args",[])
    if args:
        if len(args)!=1:
            await update.effective_message.reply_text("Use /budget to inspect spending or /budget 10 to set the current and future job cap.")
            return
        try:
            cap=studio.set_budget(args[0])
        except ValueError as error:
            await update.effective_message.reply_text(str(error)); return
        await update.effective_message.reply_text(f"API cap set to ${cap:g} for new games and the current resumable job. Use /approve to retry a stopped job.")
        return
    job=studio.job
    await update.effective_message.reply_text((Ledger(job).summary() if job else "No job.") +
        "\nUse /budget <dollars> to change the cap within the maximum configured in .env.")


async def cancel(update,context):
    if not await guard(update): return
    studio = supervisor(context)
    message = studio.cancel()
    task = context.application.bot_data.get("operation")
    if (studio.job and studio.job.data.get("cancel_requested") and studio.job.data.get("status") == "planning"
            and isinstance(task, asyncio.Task)):
        task.cancel()
    await update.effective_message.reply_text(message)


async def deliver(bot,chat_id,job):
    if job.data["status"] not in ("completed", "partial"):
        report=job.data.get("failure_report") or {}
        if report:
            text = failure_message(report)
            while text:
                end = min(len(text), 3500)
                while len(text[:end].encode("utf-16-le")) // 2 > 3500:
                    end -= 1
                await bot.send_message(chat_id, text[:end])
                text = text[end:]
        else:
            await bot.send_message(chat_id,"No completed result yet. Use /status to inspect the saved job.")
        return
    if job.data["status"] == "partial":
        await bot.send_message(chat_id,"Partial prototype: some requested work or verification remains incomplete. Read validation_report.json and requirements.json.")
    delivery={"at":now(),"status":"sending","sent":[],"skipped":[]}
    delivery_path=job.directory/"telegram_delivery.json"
    for artifact in job.data["artifacts"]:
        path=safe_path(job.directory,artifact["path"])
        if not path.is_file():
            delivery["skipped"].append(path.name+": missing")
            await bot.send_message(chat_id,"An artifact is missing: "+path.name); continue
        if artifact.get("sha256") and hashlib.sha256(path.read_bytes()).hexdigest()!=artifact["sha256"]:
            delivery["skipped"].append(path.name+": changed after verification")
            await bot.send_message(chat_id,path.name+" changed after verification and will not be delivered as verified."); continue
        if path.stat().st_size>49_000_000:
            delivery["skipped"].append(path.name+": exceeds upload limit")
            await bot.send_message(chat_id,f"{path.name} exceeds the bot upload limit. Local file: {path}"); continue
        with path.open("rb") as stream:
            if path.name=="preview.png":
                await bot.send_photo(chat_id,stream,caption="Preview captured from the generated Unity scene during PlayMode testing.")
            else:
                await bot.send_document(chat_id,stream,filename=path.name,caption=path.name,read_timeout=120,write_timeout=120)
        delivery["sent"].append(path.name)
        write_json(delivery_path,delivery)
    delivery["status"]="completed" if not delivery["skipped"] else "partial"
    write_json(delivery_path,delivery)
    return delivery


async def result(update,context):
    if not await guard(update): return
    job=supervisor(context).job
    if not job:
        await update.effective_message.reply_text("No game result yet."); return
    await deliver(context.bot,update.effective_chat.id,job)


async def operation(update,context,callback):
    try:
        await callback()
    except Exception as error:
        logger.error("Studio operation failed (%s)",type(error).__name__)
        message=str(error) if isinstance(error,(ValueError,RuntimeError)) else type(error).__name__
        await update.effective_message.reply_text(message[:1800])
    finally:
        current = context.application.bot_data.get("operation")
        if current is asyncio.current_task() or current is True:
            context.application.bot_data.pop("operation",None)
            context.application.bot_data.pop("pre_job_instructions",None)


async def handle_request(update,context,request,explicit_create=False):
    if not await guard(update): return
    request=request.strip()
    if not request or request.casefold() in NOISE or len(request)>4000:
        await update.effective_message.reply_text("I only create Unity games. Please describe a specific game, including its mechanics and perspective.\nExample: /create A 3D driving game with a track and finish line."); return
    studio=supervisor(context)
    update_id = getattr(update, "update_id", None)
    if isinstance(update_id, int):
        seen = context.application.bot_data.setdefault("handled_requests", [])
        if update_id in seen:
            return
        seen.append(update_id)
        del seen[:-100]
    running = operation_running(context.application)
    # An explicit new game must never become an instruction on an older game.
    if explicit_create and not running and not studio.lock.locked() and studio.job and studio.job.data.get("status") in ("awaiting_approval", "interrupted"):
        studio.cancel()
    # Older versions stored a rejected job after asking a question. Reattach a
    # plain-text answer to that exact request instead of starting from the answer
    # alone and falling into another clarification cycle.
    previous=studio.job
    if (not explicit_create and previous and previous.data.get("status")=="rejected"
            and "clarif" in previous.data.get("error","").casefold()):
        request=(previous.data.get("request","")+"\n\nUser clarification: "+request).strip()
        if len(request)>4000:
            await update.effective_message.reply_text("I found your previous game request, but it plus this answer exceeds 4,000 characters. Use /create with a shorter combined description."); return
        explicit_create=True
        await update.effective_message.reply_text("Using your answer with the saved game request and starting generation.")
    if running or studio.busy():
        if explicit_create:
            await update.effective_message.reply_text("A game operation is still running. Use /cancel, wait for it to stop, then send /create with the new description.")
            return
        job=studio.job
        if job and job.data.get("status") in ("planning","building","awaiting_approval","interrupted"):
            await update.effective_message.reply_text(studio.add_instruction(request)); return
        if context.application.bot_data.get("operation") and (not job or job.data.get("status") not in ("planning","building","awaiting_approval","interrupted")):
            context.application.bot_data.setdefault("pre_job_instructions",[]).append(request)
            await update.effective_message.reply_text("Saved this instruction for the game creation that is starting."); return
        await update.effective_message.reply_text("An active job already exists. Use /status or /cancel."); return
    if explicit_create:
        context.application.bot_data.pop("pre_job_instructions", None)
    async def work():
        await update.effective_message.reply_text("Planning your game. I’ll send the generated plan for /approve before starting Unity.")
        initial=context.application.bot_data.pop("pre_job_instructions",[])
        async def notify(text):
            await reply_chunks(update.effective_message, text)
        job = await studio.submit(request,chat_id=update.effective_chat.id,notify=notify,
            explicit_create=explicit_create,initial_instructions=initial)
        if job.data["status"] in ("rejected","unsupported"):
            await update.effective_message.reply_text(job.data["error"])
        elif job.data["status"] in ("completed","partial"):
            await deliver(context.bot,update.effective_chat.id,job)
    context.application.bot_data["operation"] = context.application.create_task(operation(update,context,work),update=update)


async def create_game(update,context):
    if not await guard(update): return
    request = " ".join(context.args)
    if not request:
        await update.effective_message.reply_text("Use /create <game description>. Example: /create A 3D driving game with a finish line.")
        return
    await handle_request(update,context,request,explicit_create=True)


async def text_message(update,context):
    if (update.effective_message.text or "").lstrip().startswith("/"):
        await unsupported_message(update, context)
        return
    await handle_request(update,context,update.effective_message.text)


async def approve(update,context):
    if not await guard(update): return
    if operation_running(context.application):
        await update.effective_message.reply_text("An operation is already running. Use /status."); return
    studio=supervisor(context)
    async def work():
        async def notify(text):
            await reply_chunks(update.effective_message, text)
        artifacts=await studio.approve(notify=notify)
        if artifacts:
            await deliver(context.bot,update.effective_chat.id,studio.job)
    context.application.bot_data["operation"] = context.application.create_task(operation(update,context,work),update=update)


async def unsupported_message(update,context):
    if not await guard(update): return
    # CommandHandler requires a BOT_COMMAND entity at offset 0. Recover pasted,
    # indented or caption commands without returning the unrelated /start menu.
    message = update.effective_message
    match = re.fullmatch(r"/([a-z][a-z0-9_]*)(?:@([a-z0-9_]+))?(?:\s+(.*))?", (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip(), re.I | re.S)
    if match:
        name, username, arguments = match.groups()
        own_name = context.bot.username
        if username and isinstance(own_name, str) and username.casefold() != own_name.casefold():
            return
        handlers = {"start": start, "help": start, "myid": myid, "create": create_game,
                    "status": status, "plan": plan, "approve": approve, "cancel": cancel,
                    "budget": budget, "result": result}
        callback = handlers.get(name.casefold())
        if callback:
            logger.info("Recovered Telegram command /%s from fallback (update_id=%s)", name.casefold(), getattr(update, "update_id", None))
            context.args = (arguments or "").split()
            await callback(update, context)
            return
        await message.reply_text("Unknown command. Use /create <game description> or /help.")
        return
    await message.reply_text("Send a text game description or /create <game description>.")


async def on_error(update,context):
    logger.error("Telegram update failed (%s)",type(context.error).__name__)
    if isinstance(context.error,Conflict):
        logger.error("Another bot is polling this token. Stopping this duplicate process.")
        context.application.stop_running()
    elif isinstance(update,Update) and update.effective_message:
        try: await update.effective_message.reply_text("The operation failed. Use /status to inspect the saved job.")
        except Exception: logger.error("Could not deliver error notification")


async def on_startup(application):
    studio=Supervisor()
    application.bot_data["supervisor"]=studio
    try:
        descriptions={"start":"Studio introduction","create":"Plan a Unity game","status":"Current progress",
            "plan":"View game plan","approve":"Approve plan and build","cancel":"Cancel active job",
            "budget":"Inspect spending","result":"Get verified artifacts"}
        await application.bot.set_my_commands([BotCommand(k,v) for k,v in descriptions.items()],
            scope=BotCommandScopeChat(chat_id=int(os.environ["TELEGRAM_ALLOWED_USER_ID"])))
    except Exception as error:
        logger.error("Could not update command menu (%s)",type(error).__name__)
    logger.info("Telegram bot @%s is online",application.bot.username)
    if studio.job and studio.job.data.get("status")=="interrupted":
        async def resume():
            job=studio.job
            chat_id=job.data.get("chat_id")
            async def notify(text):
                if chat_id is not None:
                    for offset in range(0, len(text), 1900):
                        await application.bot.send_message(chat_id,text[offset:offset + 1900])
            try:
                artifacts=await studio.approve(notify=notify)
                if artifacts and chat_id is not None and job.data.get("status") in ("completed","partial"):
                    await deliver(application.bot,chat_id,job)
            finally:
                application.bot_data.pop("operation",None)
        application.bot_data["operation"] = application.create_task(resume())


async def on_shutdown(application):
    from studio.mcp_client import shutdown_environment_clients
    await shutdown_environment_clients()


def build_application():
    token=os.environ.get("TELEGRAM_BOT_TOKEN","").strip()
    if not token: raise RuntimeError("Set TELEGRAM_BOT_TOKEN in .env")
    try: int(os.environ["TELEGRAM_ALLOWED_USER_ID"])
    except (KeyError,ValueError) as error: raise RuntimeError("Set numeric TELEGRAM_ALLOWED_USER_ID in .env") from error
    app=ApplicationBuilder().token(token).post_init(on_startup).post_shutdown(on_shutdown).build()
    for command,callback in {"start":start,"help":start,"myid":myid,"create":create_game,"status":status,
            "plan":plan,"approve":approve,"cancel":cancel,"budget":budget,"result":result}.items():
        app.add_handler(CommandHandler(command,callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_message))
    app.add_handler(MessageHandler(filters.ALL,unsupported_message)); app.add_error_handler(on_error)
    return app


def main():
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING); logging.getLogger("httpcore").setLevel(logging.WARNING)
    with ProcessLock(ROOT/".studio_cache"/"controller.lock"):
        build_application().run_polling()


if __name__=="__main__": main()
