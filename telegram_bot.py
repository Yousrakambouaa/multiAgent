"""Authorized Telegram transport; orchestration lives in studio.supervisor."""
import asyncio
import logging
import os
import hashlib

from telegram import BotCommand, BotCommandScopeChat, Update
from telegram.error import Conflict
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from studio.config import ROOT, safe_path
from studio.costs import Ledger
from studio.state import ProcessLock, now, write_json
from studio.supervisor import Supervisor
from studio.request_validation import NOISE

logger=logging.getLogger(__name__)
HELP_TEXT=("Unity Game Studio creates verified 2D platformer prototypes.\n"
    "/create <game description> — propose a game\n/plan — inspect the plan\n/approve — build it\n"
    "/status — progress\n/cancel — stop a job\n/budget — spending\n/result — project, report and preview\n\n"
    "Example: /create A woodland platformer with jumping, coins and patrolling enemies. "
    "The default job cap is $2. Other game genres are not implemented in this MVP.")


def authorized(update):
    return update.effective_user is not None and update.effective_user.id==int(os.environ["TELEGRAM_ALLOWED_USER_ID"])


async def guard(update):
    if authorized(update): return True
    await update.effective_message.reply_text("Access denied."); return False


def supervisor(context):
    return context.application.bot_data["supervisor"]


async def start(update,context):
    await update.effective_message.reply_text(HELP_TEXT)


async def myid(update,context):
    await update.effective_message.reply_text(f"Your Telegram ID: {update.effective_user.id}")


async def status(update,context):
    if not await guard(update): return
    job=supervisor(context).job
    if not job:
        await update.effective_message.reply_text("Online. No job yet. Use /create <description>."); return
    tasks="\n".join(f"{task['name']}: {task['status']}" for task in job.data["tasks"])
    await update.effective_message.reply_text((f"Job {job.data['id']}\nStatus: {job.data['status']}\nStage: {job.data['stage']}\n"
        f"{tasks}\n{job.data.get('error','')}")[:3900])


async def plan(update,context):
    if await guard(update): await update.effective_message.reply_text(supervisor(context).plan_text()[:3900])


async def budget(update,context):
    if not await guard(update): return
    job=supervisor(context).job
    await update.effective_message.reply_text(Ledger(job).summary() if job else "No job. Default cap: $2; configurable with STUDIO_JOB_BUDGET_USD.")


async def cancel(update,context):
    if await guard(update): await update.effective_message.reply_text(supervisor(context).cancel())


async def deliver(bot,chat_id,job):
    if job.data["status"]!="completed":
        await bot.send_message(chat_id,"No completed, verified result yet. Use /status."); return
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
        context.application.bot_data.pop("operation",None)


async def handle_request(update,context,request):
    if not await guard(update): return
    request=request.strip()
    if not request or request.casefold() in NOISE or len(request)>4000:
        await update.effective_message.reply_text("I only create Unity games. Please describe a specific 2D platformer.\nExample: /create A Mario-style platformer with coins and enemies."); return
    studio=supervisor(context)
    if context.application.bot_data.get("operation") or studio.busy():
        await update.effective_message.reply_text("An active job or plan already exists. Use /status, /approve or /cancel."); return
    context.application.bot_data["operation"]=True
    async def work():
        await studio.submit(request,chat_id=update.effective_chat.id,notify=update.effective_message.reply_text)
        if studio.job.data["status"] in ("rejected","unsupported"):
            await update.effective_message.reply_text(studio.job.data["error"])
    context.application.create_task(operation(update,context,work),update=update)


async def create_game(update,context):
    await handle_request(update,context," ".join(context.args))


async def text_message(update,context):
    await handle_request(update,context,update.effective_message.text)


async def approve(update,context):
    if not await guard(update): return
    if context.application.bot_data.get("operation"):
        await update.effective_message.reply_text("An operation is already running. Use /status."); return
    studio=supervisor(context)
    context.application.bot_data["operation"]=True
    async def work():
        artifacts=await studio.approve(notify=update.effective_message.reply_text)
        if artifacts:
            await deliver(context.bot,update.effective_chat.id,studio.job)
    context.application.create_task(operation(update,context,work),update=update)


async def unsupported_message(update,context):
    if await guard(update): await update.effective_message.reply_text(HELP_TEXT)


async def on_error(update,context):
    logger.error("Telegram update failed (%s)",type(context.error).__name__)
    if isinstance(context.error,Conflict):
        logger.error("Another bot is polling this token. Stopping this duplicate process.")
        context.application.stop_running()
    elif isinstance(update,Update) and update.effective_message:
        try: await update.effective_message.reply_text("The operation failed. Use /status to inspect the saved job.")
        except Exception: logger.error("Could not deliver error notification")


async def on_startup(application):
    application.bot_data["supervisor"]=Supervisor()
    try:
        descriptions={"start":"Studio introduction","create":"Propose a platformer","status":"Current progress",
            "plan":"View game plan","approve":"Approve and build","cancel":"Cancel active job",
            "budget":"Inspect spending","result":"Get verified artifacts"}
        await application.bot.set_my_commands([BotCommand(k,v) for k,v in descriptions.items()],
            scope=BotCommandScopeChat(chat_id=int(os.environ["TELEGRAM_ALLOWED_USER_ID"])))
    except Exception as error:
        logger.error("Could not update command menu (%s)",type(error).__name__)
    logger.info("Telegram bot @%s is online",application.bot.username)


def build_application():
    token=os.environ.get("TELEGRAM_BOT_TOKEN","").strip()
    if not token: raise RuntimeError("Set TELEGRAM_BOT_TOKEN in .env")
    try: int(os.environ["TELEGRAM_ALLOWED_USER_ID"])
    except (KeyError,ValueError) as error: raise RuntimeError("Set numeric TELEGRAM_ALLOWED_USER_ID in .env") from error
    app=ApplicationBuilder().token(token).post_init(on_startup).build()
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
