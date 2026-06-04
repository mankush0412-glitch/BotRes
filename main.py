"""
╔══════════════════════════════════════════════════════════╗
║       TELEGRAM RESTRICTED CONTENT SAVER BOT v4.0        ║
╚══════════════════════════════════════════════════════════╝
"""

import asyncio
import os
import re
import sys
import time
import logging
from datetime import datetime

from aiohttp import web
import motor.motor_asyncio
from pymongo import DESCENDING
from pyrogram import Client, filters, idle
from pyrogram.types import Message
from pyrogram.errors import (
    FloodWait, MessageIdInvalid, ChannelPrivate,
    SessionPasswordNeeded, PhoneCodeInvalid,
    PhoneCodeExpired, AuthKeyUnregistered,
)

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("SaveBot")

VERSION    = "4.0.0"
START_TIME = datetime.now()

# ── MongoDB ───────────────────────────────────────────────────────────────────
mongo_client = None
db           = None

async def init_db():
    global mongo_client, db
    if not config.MONGO_URI:
        logger.warning("MONGO_URI not set — database features disabled.")
        return
    mongo_client = motor.motor_asyncio.AsyncIOMotorClient(config.MONGO_URI)
    db = mongo_client["savebot"]
    await db["files"].create_index([("source_bot", 1), ("message_id", 1)], unique=True)
    await db["files"].create_index([("fetched_at", DESCENDING)])
    logger.info("MongoDB connected ✅")

async def db_save_file(source_bot: str, message_id: int, file_type: str,
                        file_name: str, caption: str):
    if db is None:
        return
    try:
        await db["files"].update_one(
            {"source_bot": source_bot, "message_id": message_id},
            {"$set": {
                "source_bot":  source_bot,
                "message_id":  message_id,
                "file_type":   file_type,
                "file_name":   file_name,
                "caption":     caption[:200] if caption else "",
                "fetched_at":  datetime.utcnow(),
            }},
            upsert=True
        )
    except Exception as e:
        logger.error(f"db_save_file: {e}")

async def db_get_history(limit: int = 10):
    if db is None:
        return []
    cursor = db["files"].find({}).sort("fetched_at", DESCENDING).limit(limit)
    return await cursor.to_list(length=limit)

async def db_count():
    if db is None:
        return 0
    return await db["files"].count_documents({})

# ── Clients ───────────────────────────────────────────────────────────────────
bot  = Client("SaveBot",
              api_id=config.API_ID,
              api_hash=config.API_HASH,
              bot_token=config.BOT_TOKEN)

user: Client | None = None
SESSION_FILE     = "usersession"
SESSION_STR_FILE = "session_string.txt"

LOGIN_STATE:    dict = {}
PROGRESS_CACHE: dict = {}
RUNNING_FETCHALL: set = set()   # prevent duplicate fetchall jobs

# ── Helpers ───────────────────────────────────────────────────────────────────

def is_owner(msg: Message) -> bool:
    return bool(msg.from_user and msg.from_user.id == config.OWNER_ID)

def uptime_str() -> str:
    d = datetime.now() - START_TIME
    h, r = divmod(int(d.total_seconds()), 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s"

def media_info(m: Message):
    """Return (has_media, file_type, file_name, caption)."""
    cap = m.caption or ""
    if m.video:
        return True, "video", m.video.file_name or "video.mp4", cap
    if m.document:
        return True, "document", m.document.file_name or "file", cap
    if m.audio:
        return True, "audio", m.audio.file_name or "audio.mp3", cap
    if m.photo:
        return True, "photo", "photo.jpg", cap
    if m.animation:
        return True, "animation", "animation.gif", cap
    if m.voice:
        return True, "voice", "voice.ogg", cap
    if m.video_note:
        return True, "video_note", "video_note.mp4", cap
    if m.sticker:
        return True, "sticker", "sticker.webp", cap
    return False, "", "", ""

async def progress_cb(current: int, total: int, msg: Message, label: str):
    pct = current / total * 100
    now = time.time()
    if now - PROGRESS_CACHE.get(msg.id, 0) < 3:
        return
    PROGRESS_CACHE[msg.id] = now
    bar = "█" * int(pct/10) + "░" * (10 - int(pct/10))
    icon = "📥" if "Down" in label else "📤"
    try:
        await msg.edit_text(
            f"{icon} **{label}**\n"
            f"`[{bar}]` {pct:.1f}%\n"
            f"`{current/1024/1024:.2f} / {total/1024/1024:.2f} MB`"
        )
    except Exception:
        pass

async def send_media(dest: int, src: Message,
                     status: Message | None = None,
                     source_bot: str = "") -> tuple[bool, str]:
    """Download via user session → re-upload via bot (no restriction, no tag)."""
    u = user
    if not u:
        return False, "Account connected nahi. `/login` karo."
    fp = None
    try:
        fp = await u.download_media(
            src,
            progress=progress_cb if status else None,
            progress_args=(status, "Downloading") if status else (),
        )
        if not fp:
            return False, "Media nahi mila."
        if status:
            await status.edit_text("📤 Upload ho raha hai...")

        cap = src.caption or ""
        kw  = dict(
            progress=progress_cb if status else None,
            progress_args=(status, "Uploading") if status else (),
        )
        if src.video:
            await bot.send_video(dest, fp, caption=cap,
                                 duration=src.video.duration,
                                 width=src.video.width,
                                 height=src.video.height, **kw)
        elif src.document:
            await bot.send_document(dest, fp, caption=cap, **kw)
        elif src.audio:
            await bot.send_audio(dest, fp, caption=cap, **kw)
        elif src.photo:
            await bot.send_photo(dest, fp, caption=cap)
        elif src.animation:
            await bot.send_animation(dest, fp, caption=cap)
        elif src.voice:
            await bot.send_voice(dest, fp, **kw)
        elif src.video_note:
            await bot.send_video_note(dest, fp, **kw)
        elif src.sticker:
            await bot.send_sticker(dest, fp)
        else:
            await bot.send_document(dest, fp, caption=cap, **kw)

        # Save to MongoDB
        _, ftype, fname, fcap = media_info(src)
        if source_bot:
            await db_save_file(source_bot, src.id, ftype, fname, fcap)

        return True, "ok"
    except FloodWait as e:
        await asyncio.sleep(e.value)
        return False, f"Flood wait {e.value}s"
    except Exception as e:
        logger.error(f"send_media: {e}")
        return False, str(e)
    finally:
        if fp and os.path.exists(fp):
            os.remove(fp)

async def load_user_session() -> Client | None:
    """
    Session load karo — pehle SESSION_STRING env var check karo,
    phir session_string.txt file. Render pe files persist nahi hoti,
    isliye env var recommended hai.
    """
    global user

    # 1. Pehle env var se try karo (Render ke liye best)
    session_str = os.environ.get("SESSION_STRING", "").strip()

    # 2. Agar env var nahi, file se try karo
    if not session_str and os.path.exists(SESSION_STR_FILE):
        try:
            session_str = open(SESSION_STR_FILE).read().strip()
        except Exception:
            pass

    if not session_str:
        return None

    try:
        u = Client(SESSION_FILE, api_id=config.API_ID,
                   api_hash=config.API_HASH, session_string=session_str)
        await u.start()
        user = u
        return u
    except AuthKeyUnregistered:
        logger.warning("Session expired — SESSION_STRING invalid hai.")
        if os.path.exists(SESSION_STR_FILE):
            os.remove(SESSION_STR_FILE)
        return None
    except Exception as e:
        logger.warning(f"Session load failed: {e}")
        return None

# ══════════════════════════════════════════════════════════════════════════════
# /start  /help
# ══════════════════════════════════════════════════════════════════════════════

@bot.on_message(filters.command(["start", "help"]) & filters.private)
async def cmd_start(_, msg: Message):
    u = user
    acc = "✅ Connected" if u else "❌ Not logged in — /login karo"
    total = await db_count()
    await msg.reply_text(
        f"🤖 **Save Restricted Bot v{VERSION}**\n"
        f"Account: {acc}\n"
        f"Total saved (DB): `{total}` files\n\n"
        "**Main Commands:**\n"
        "`/fetchall @botname` — Us bot ka **saara content** lo ⭐\n"
        "`/fetch @botname command` — Specific command ka content lo\n"
        "`/history` — Pehle save kiya hua dekhna\n\n"
        "**Account:**\n"
        "`/login` · `/logout` · `/me` · `/status`\n\n"
        "**Aur:**\n"
        "`/save chatid msgid` · `/link url` · `/batch chatid s e`"
    )

# ══════════════════════════════════════════════════════════════════════════════
# /status  /me
# ══════════════════════════════════════════════════════════════════════════════

@bot.on_message(filters.command("status") & filters.private)
async def cmd_status(_, msg: Message):
    if not is_owner(msg): return
    u = user
    acc = "❌ Not logged in"
    if u:
        try:
            me = await u.get_me()
            acc = f"✅ {me.first_name} (@{me.username or 'N/A'})"
        except Exception:
            acc = "⚠️ Session issue — /logout then /login"
    total = await db_count()
    db_status = "✅ Connected" if db else "❌ Not connected (MONGO_URI missing)"
    await msg.reply_text(
        f"**🤖 Bot Status**\n\n"
        f"Version:  `{VERSION}`\n"
        f"Uptime:   `{uptime_str()}`\n"
        f"Account:  {acc}\n"
        f"Database: {db_status}\n"
        f"Total saved: `{total}` files"
    )

@bot.on_message(filters.command("me") & filters.private)
async def cmd_me(_, msg: Message):
    if not is_owner(msg): return
    u = user
    if not u:
        return await msg.reply_text("❌ Pehle /login karo.")
    try:
        me = await u.get_me()
        await msg.reply_text(
            f"👤 **Logged-in Account**\n\n"
            f"Name: `{me.first_name} {me.last_name or ''}`\n"
            f"Username: @{me.username or 'N/A'}\n"
            f"Phone: `{me.phone_number}`\n"
            f"ID: `{me.id}`"
        )
    except Exception as e:
        await msg.reply_text(f"❌ `{e}`")

# ══════════════════════════════════════════════════════════════════════════════
# /history — MongoDB se pehle fetch hua content
# ══════════════════════════════════════════════════════════════════════════════

@bot.on_message(filters.command("history") & filters.private)
async def cmd_history(_, msg: Message):
    if not is_owner(msg): return
    if db is None:
        return await msg.reply_text("❌ MongoDB connected nahi hai.")
    rows = await db_get_history(limit=15)
    if not rows:
        return await msg.reply_text("📂 Database abhi empty hai.")
    lines = ["**📂 Recently Fetched (last 15):**\n"]
    for r in rows:
        dt = r["fetched_at"].strftime("%d %b %H:%M")
        lines.append(
            f"• `{r['source_bot']}` — {r['file_type']} — `{r['file_name'][:30]}` — _{dt}_"
        )
    await msg.reply_text("\n".join(lines))

# ══════════════════════════════════════════════════════════════════════════════
# LOGIN FLOW
# ══════════════════════════════════════════════════════════════════════════════

@bot.on_message(filters.command("login") & filters.private)
async def cmd_login(_, msg: Message):
    if not is_owner(msg):
        return await msg.reply_text("❌ Sirf owner use kar sakta hai.")
    global user
    if user:
        try:
            me = await user.get_me()
            return await msg.reply_text(
                f"✅ Already logged in!\n"
                f"Account: **{me.first_name}** (@{me.username or 'N/A'})\n"
                "Naya login ke liye pehle /logout karo."
            )
        except Exception:
            pass
    LOGIN_STATE[msg.from_user.id] = {"step": "phone"}
    await msg.reply_text(
        "📱 **Step 1/2 — Phone Number**\n\n"
        "Apna Telegram phone number bhejo:\n`+91XXXXXXXXXX`\n\n"
        "_Cancel: /cancel_"
    )

@bot.on_message(filters.command("cancel") & filters.private)
async def cmd_cancel(_, msg: Message):
    uid = msg.from_user.id
    state = LOGIN_STATE.pop(uid, None)
    if state and state.get("client"):
        try: await state["client"].disconnect()
        except Exception: pass
    await msg.reply_text("❌ Login cancel.")

@bot.on_message(filters.command("logout") & filters.private)
async def cmd_logout(_, msg: Message):
    if not is_owner(msg): return
    global user
    if user:
        try: await user.stop()
        except Exception: pass
        user = None
    for f in [SESSION_STR_FILE, SESSION_FILE + ".session"]:
        if os.path.exists(f): os.remove(f)
    await msg.reply_text(
        "✅ Logout ho gaya.\n\n"
        "⚠️ Agar SESSION_STRING env var set hai toh Render dashboard mein\n"
        "usse bhi delete karo, warna restart pe phir login ho jaayega."
    )

@bot.on_message(
    filters.private
    & ~filters.command([
        "start","help","login","logout","cancel","me","status",
        "save","fetch","fetchall","link","batch","history"
    ])
)
async def conversation(_, msg: Message):
    if not is_owner(msg): return
    uid   = msg.from_user.id
    state = LOGIN_STATE.get(uid)
    if not state: return
    step  = state["step"]

    # ── Phone ─────────────────────────────────────────────────────────────────
    if step == "phone":
        phone = msg.text.strip()
        if not re.match(r"^\+\d{7,15}$", phone):
            return await msg.reply_text("❌ Format: `+91XXXXXXXXXX`")
        wait = await msg.reply_text("📲 OTP bhej raha hoon...")
        try:
            tmp = Client(SESSION_FILE, api_id=config.API_ID,
                         api_hash=config.API_HASH, in_memory=True)
            await tmp.connect()
            sent = await tmp.send_code(phone)
            LOGIN_STATE[uid].update({
                "step": "otp", "phone": phone,
                "phone_code_hash": sent.phone_code_hash, "client": tmp,
            })
            await wait.edit_text(
                "✅ OTP bheja gaya!\n\n"
                "📩 **Step 2/2 — OTP**\n"
                "Telegram se aaya code bhejo:\n"
                "_(Space ke saath: `1 2 3 4 5` ya bina: `12345`)_\n\n"
                "_Cancel: /cancel_"
            )
        except FloodWait as e:
            LOGIN_STATE.pop(uid, None)
            await wait.edit_text(f"⏳ {e.value}s baad try karo.")
        except Exception as e:
            LOGIN_STATE.pop(uid, None)
            await wait.edit_text(f"❌ Error: `{e}`")

    # ── OTP ───────────────────────────────────────────────────────────────────
    elif step == "otp":
        code = msg.text.strip().replace(" ", "")
        tmp: Client = state["client"]
        wait = await msg.reply_text("🔐 Verify ho raha hai...")
        try:
            await tmp.sign_in(state["phone"], state["phone_code_hash"], code)
            await _finish_login(uid, tmp, wait)
        except SessionPasswordNeeded:
            LOGIN_STATE[uid]["step"] = "2fa"
            await wait.edit_text(
                "🔒 **Two-Step Verification (2FA)**\n\n"
                "Aapka Telegram 2FA password chahiye.\n"
                "Apna **2FA password** bhejo:\n\n_Cancel: /cancel_"
            )
        except PhoneCodeInvalid:
            LOGIN_STATE.pop(uid, None)
            try: await tmp.disconnect()
            except Exception: pass
            await wait.edit_text("❌ Galat OTP. Dobara /login karo.")
        except PhoneCodeExpired:
            LOGIN_STATE.pop(uid, None)
            try: await tmp.disconnect()
            except Exception: pass
            await wait.edit_text("❌ OTP expire. Dobara /login karo.")
        except Exception as e:
            LOGIN_STATE.pop(uid, None)
            try: await tmp.disconnect()
            except Exception: pass
            await wait.edit_text(f"❌ `{e}`")

    # ── 2FA ───────────────────────────────────────────────────────────────────
    elif step == "2fa":
        tmp: Client = state["client"]
        wait = await msg.reply_text("🔐 2FA verify ho raha hai...")
        try:
            await tmp.check_password(msg.text.strip())
            await _finish_login(uid, tmp, wait)
        except Exception as e:
            LOGIN_STATE.pop(uid, None)
            try: await tmp.disconnect()
            except Exception: pass
            err = "Galat 2FA password." if "PASSWORD_HASH_INVALID" in str(e) else str(e)
            await wait.edit_text(f"❌ {err}\nDobara /login karo.")

async def _finish_login(uid: int, tmp: Client, wait: Message):
    global user
    try:
        session_str = await tmp.export_session_string()
        # File mein bhi save karo (local dev ke liye)
        open(SESSION_STR_FILE, "w").write(session_str)
        user = Client(SESSION_FILE, api_id=config.API_ID,
                      api_hash=config.API_HASH, session_string=session_str)
        await user.start()
        me = await user.get_me()
        LOGIN_STATE.pop(uid, None)
        try: await tmp.disconnect()
        except Exception: pass
        await wait.edit_text(
            f"✅ **Login Successful!**\n\n"
            f"👤 {me.first_name} {me.last_name or ''}\n"
            f"📱 `{me.phone_number}`\n"
            f"🔗 @{me.username or 'N/A'}\n"
            f"🆔 `{me.id}`\n\n"
            "Ab /fetchall @botname se saara content lo! 🎉\n\n"
            "⚠️ **Important:** Session sirf tab tak rahega jab tak container restart nahi hota.\n"
            "Permanent ke liye: `/session_string` command use karo aur jo string mile\n"
            "use Render Dashboard → Environment mein `SESSION_STRING` naam se add karo."
        )
    except Exception as e:
        LOGIN_STATE.pop(uid, None)
        await wait.edit_text(f"❌ Session save failed: `{e}`")

# ══════════════════════════════════════════════════════════════════════════════
# /session_string — Session string export karo (Render ke liye)
# ══════════════════════════════════════════════════════════════════════════════

@bot.on_message(filters.command("session_string") & filters.private)
async def cmd_session_string(_, msg: Message):
    if not is_owner(msg): return
    u = user
    if not u:
        return await msg.reply_text("❌ Pehle /login karo.")
    try:
        s = await u.export_session_string()
        await msg.reply_text(
            "🔑 **Your Session String:**\n\n"
            f"`{s}`\n\n"
            "⬆️ Is string ko copy karo aur Render Dashboard mein:\n"
            "**Environment** → Add `SESSION_STRING` = (yeh string)\n\n"
            "Isse bot restart pe bhi login raha karega. 🔒"
        )
    except Exception as e:
        await msg.reply_text(f"❌ `{e}`")

# ══════════════════════════════════════════════════════════════════════════════
# /fetchall @botname  ⭐ MAIN FEATURE
# ══════════════════════════════════════════════════════════════════════════════

@bot.on_message(filters.command("fetchall") & filters.private)
async def cmd_fetchall(_, msg: Message):
    if not is_owner(msg):
        return await msg.reply_text("❌ Only owner.")
    u = user
    if not u:
        return await msg.reply_text("❌ Pehle /login karo.")

    parts = msg.text.split()
    if len(parts) < 2:
        return await msg.reply_text(
            "❌ Format:\n`/fetchall @botname`\n\n"
            "Example:\n`/fetchall @MovieBot`\n`/fetchall @PDFCourseBot`"
        )

    botname = parts[1].lstrip("@")
    uid = msg.from_user.id

    if uid in RUNNING_FETCHALL:
        return await msg.reply_text(
            "⚠️ Ek fetchall already chal rahi hai.\n"
            "Pehle woh khatam hone do."
        )

    RUNNING_FETCHALL.add(uid)
    status = await msg.reply_text(
        f"🔍 **@{botname}** ki poori chat history scan ho rahi hai...\n"
        "⏳ Yeh thoda time lega..."
    )

    try:
        total_media = 0
        total_scanned = 0
        saved = 0
        failed = 0

        async for hist_msg in u.get_chat_history(botname):
            total_scanned += 1
            has_media, ftype, fname, fcap = media_info(hist_msg)

            if not has_media:
                continue

            total_media += 1

            if total_media % 5 == 0:
                try:
                    await status.edit_text(
                        f"📂 **@{botname}** scan chal raha hai...\n\n"
                        f"Scanned: `{total_scanned}` messages\n"
                        f"Media mili: `{total_media}`\n"
                        f"✅ Sent: `{saved}` | ❌ Failed: `{failed}`"
                    )
                except Exception:
                    pass

            ok, err = await send_media(
                msg.chat.id, hist_msg,
                source_bot=botname
            )
            if ok:
                saved += 1
            else:
                failed += 1
                logger.warning(f"fetchall skip [{hist_msg.id}]: {err}")

            await asyncio.sleep(1)

        await status.edit_text(
            f"✅ **@{botname} — FetchAll Done!**\n\n"
            f"Total messages scanned: `{total_scanned}`\n"
            f"Media found: `{total_media}`\n"
            f"Successfully sent: `{saved}` ✅\n"
            f"Failed: `{failed}` ❌\n\n"
            f"Sab kuch `/history` mein save hai 📂"
        )

    except Exception as e:
        logger.error(f"fetchall error: {e}")
        await status.edit_text(
            f"❌ Error aaya:\n`{str(e)}`\n\n"
            f"Possible reason: Aapka account @{botname} se pehle connected nahi hai.\n"
            f"Pehle us bot pe /start bhejo, fir dobara try karo."
        )
    finally:
        RUNNING_FETCHALL.discard(uid)

# ══════════════════════════════════════════════════════════════════════════════
# /fetch @botname <command>
# ══════════════════════════════════════════════════════════════════════════════

@bot.on_message(filters.command("fetch") & filters.private)
async def cmd_fetch(_, msg: Message):
    if not is_owner(msg):
        return await msg.reply_text("❌ Only owner.")
    u = user
    if not u:
        return await msg.reply_text("❌ Pehle /login karo.")

    parts = msg.text.split(maxsplit=2)
    if len(parts) < 3:
        return await msg.reply_text(
            "❌ Format:\n`/fetch @botname command`\n\n"
            "Examples:\n"
            "`/fetch @MovieBot /ep1`\n"
            "`/fetch @PDFBot /start`\n"
            "`/fetch @CourseBot lesson_2`"
        )

    botname = parts[1].lstrip("@")
    command = parts[2]
    status  = await msg.reply_text(
        f"📨 `@{botname}` ko bhej raha hoon: `{command}`\n"
        "⏳ 20 seconds wait..."
    )

    try:
        await u.send_message(botname, command)
        await asyncio.sleep(20)

        found = False
        async for m in u.get_chat_history(botname, limit=10):
            if m.outgoing:
                continue
            has_media, ftype, fname, fcap = media_info(m)
            if has_media:
                ok, err = await send_media(msg.chat.id, m, status, botname)
                if ok: await status.delete()
                else: await status.edit_text(f"❌ {err}")
                found = True
                break
            elif m.text and m.text.strip() != command.strip():
                await status.edit_text(f"💬 **Bot ka reply:**\n\n{m.text}")
                found = True
                break

        if not found:
            await status.edit_text(
                f"⚠️ `@{botname}` se response nahi mila.\n\n"
                f"Dobara try karo ya `/fetchall @{botname}` use karo."
            )
    except Exception as e:
        await status.edit_text(f"❌ `{e}`")

# ══════════════════════════════════════════════════════════════════════════════
# /save  /link  /batch
# ══════════════════════════════════════════════════════════════════════════════

@bot.on_message(filters.command("save") & filters.private)
async def cmd_save(_, msg: Message):
    if not is_owner(msg): return
    u = user
    if not u: return await msg.reply_text("❌ Pehle /login karo.")
    args = msg.text.split()
    if len(args) < 3:
        return await msg.reply_text("Format: `/save <chat_id> <msg_id>`")
    try:
        chat_id, msg_id = int(args[1]), int(args[2])
    except ValueError:
        return await msg.reply_text("❌ Numbers chahiye.")
    status = await msg.reply_text("🔍 Fetching...")
    try:
        orig = await u.get_messages(chat_id, msg_id)
        if not orig or orig.empty:
            return await status.edit_text("❌ Message nahi mila.")
        has_media, *_ = media_info(orig)
        if not has_media:
            return await status.edit_text(
                f"📝 **Text:**\n\n{orig.text or orig.caption or 'No content'}"
            )
        ok, err = await send_media(msg.chat.id, orig, status)
        if ok: await status.delete()
        else: await status.edit_text(f"❌ {err}")
    except ChannelPrivate:
        await status.edit_text("❌ Aapka account us chat mein nahi hai.")
    except Exception as e:
        await status.edit_text(f"❌ `{e}`")


@bot.on_message(filters.command("link") & filters.private)
async def cmd_link(_, msg: Message):
    if not is_owner(msg): return
    u = user
    if not u: return await msg.reply_text("❌ Pehle /login karo.")
    args = msg.text.split()
    if len(args) < 2:
        return await msg.reply_text(
            "Format: `/link <url>`\n"
            "Example: `/link https://t.me/c/1234567890/123`"
        )
    link   = args[1].strip()
    status = await msg.reply_text("🔍 Parsing link...")
    try:
        mp = re.match(r"https?://t\.me/c/(\d+)/(\d+)", link)
        mu = re.match(r"https?://t\.me/([^/?\s]+)/(\d+)", link)
        if mp:
            chat_id = int("-100" + mp.group(1))
            msg_id  = int(mp.group(2))
        elif mu:
            info    = await u.get_chat(mu.group(1))
            chat_id = info.id
            msg_id  = int(mu.group(2))
        else:
            return await status.edit_text("❌ Link format galat hai.")
        orig = await u.get_messages(chat_id, msg_id)
        if not orig or orig.empty:
            return await status.edit_text("❌ Message nahi mila.")
        has_media, *_ = media_info(orig)
        if not has_media:
            return await status.edit_text(
                f"📝 {orig.text or orig.caption or 'No content'}"
            )
        ok, err = await send_media(msg.chat.id, orig, status)
        if ok: await status.delete()
        else: await status.edit_text(f"❌ {err}")
    except ChannelPrivate:
        await status.edit_text("❌ Aapka account us chat mein nahi hai.")
    except Exception as e:
        await status.edit_text(f"❌ `{e}`")


@bot.on_message(filters.command("batch") & filters.private)
async def cmd_batch(_, msg: Message):
    if not is_owner(msg): return
    u = user
    if not u: return await msg.reply_text("❌ Pehle /login karo.")
    args = msg.text.split()
    if len(args) < 4:
        return await msg.reply_text("Format: `/batch <chat_id> <start> <end>`")
    try:
        chat_id, s, e = int(args[1]), int(args[2]), int(args[3])
    except ValueError:
        return await msg.reply_text("❌ Numbers chahiye.")
    if e - s > 50:
        return await msg.reply_text("❌ Max 50 messages ek baar.")
    total  = e - s + 1
    status = await msg.reply_text(f"🔄 Batch: `{s}` → `{e}` ({total} msgs)")
    ok_c = fail_c = skip_c = 0
    for mid in range(s, e + 1):
        try:
            orig = await u.get_messages(chat_id, mid)
            if not orig or orig.empty:
                skip_c += 1; continue
            has_media, *_ = media_info(orig)
            if not has_media:
                skip_c += 1; continue
            ok, _ = await send_media(msg.chat.id, orig)
            if ok: ok_c += 1
            else: fail_c += 1
            await status.edit_text(
                f"🔄 `{ok_c+fail_c+skip_c}/{total}` "
                f"✅`{ok_c}` ❌`{fail_c}` ⏭️`{skip_c}`"
            )
            await asyncio.sleep(1.5)
        except FloodWait as ex:
            await asyncio.sleep(ex.value)
        except Exception:
            fail_c += 1
    await status.edit_text(
        f"✅ **Batch Done!**\n"
        f"Saved:`{ok_c}` Failed:`{fail_c}` Skipped:`{skip_c}`"
    )

# ══════════════════════════════════════════════════════════════════════════════
# Auto-save forwarded
# ══════════════════════════════════════════════════════════════════════════════

@bot.on_message(
    filters.private & filters.forwarded
    & ~filters.command([
        "start","help","login","logout","cancel","me","status",
        "save","fetch","fetchall","link","batch","history"
    ])
)
async def auto_forward(_, msg: Message):
    if not is_owner(msg): return
    u = user
    if not u:
        return await msg.reply_text("❌ Pehle /login karo.")
    if not msg.forward_from_chat:
        return await msg.reply_text(
            "ℹ️ Protected message — source info nahi mili.\n"
            "Use `/fetch @botname command` ya `/fetchall @botname`."
        )
    status = await msg.reply_text("📥 Original fetch ho raha hai...")
    try:
        orig = await u.get_messages(
            msg.forward_from_chat.id, msg.forward_from_message_id
        )
        if not orig or orig.empty:
            return await status.edit_text("❌ Message nahi mila.")
        ok, err = await send_media(msg.chat.id, orig, status)
        if ok: await status.delete()
        else: await status.edit_text(f"❌ {err}")
    except ChannelPrivate:
        await status.edit_text("❌ Aapka account us channel mein nahi hai.")
    except Exception as e:
        await status.edit_text(f"❌ `{e}`")

# ══════════════════════════════════════════════════════════════════════════════
# Keep-alive web server
# ══════════════════════════════════════════════════════════════════════════════

async def health(request):
    u = user
    acc = "connected" if u else "not_logged_in"
    return web.Response(
        text=f"SaveBot v{VERSION} | uptime={uptime_str()} | account={acc}",
        status=200
    )

async def start_keepalive():
    port = int(os.environ.get("PORT", 10000))
    app  = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info(f"Keep-alive on :{port}")

# ══════════════════════════════════════════════════════════════════════════════
# Main — FIXED STARTUP ORDER
# bot.start() pehle, phir health server
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    logger.info(f"Starting SaveBot v{VERSION}...")
    await init_db()

    # User session load karo (env var ya file se)
    restored = await load_user_session()
    if restored:
        me = await restored.get_me()
        logger.info(f"User session restored: {me.first_name} (@{me.username})")
    else:
        logger.info("No user session — use /login command")

    # ✅ FIX: Bot pehle start karo, PHIR health server
    # Is se Render sirf tab 'Live' dikhayega jab bot actually Telegram se connected ho
    logger.info("Connecting to Telegram...")
    try:
        await bot.start()
        me = await bot.get_me()
        logger.info(f"Bot connected: @{me.username} ✅")
    except Exception as e:
        logger.error(f"Bot start FAILED: {e}")
        logger.error("BOT_TOKEN check karo — Render Dashboard → Environment Variables")
        sys.exit(1)  # Process crash karo taaki Render error dikhaye

    # Health server start karo (bot connected hone ke baad)
    await start_keepalive()

    logger.info("Bot ready! Waiting for messages...")
    await idle()

    # Cleanup
    if user and user.is_connected:
        await user.stop()
    await bot.stop()
    if mongo_client:
        mongo_client.close()

if __name__ == "__main__":
    asyncio.run(main())
