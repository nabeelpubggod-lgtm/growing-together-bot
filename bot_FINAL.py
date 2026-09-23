import os, re, logging, secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from supabase import create_client, Client
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ConversationHandler,
    MessageHandler, ContextTypes, filters
)

load_dotenv()
TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
CHANNEL_ID = os.environ["CHANNEL_ID"]
GROUP_ID = int(os.getenv("GROUP_ID")) if os.getenv("GROUP_ID", "").strip() else None
DAILY_LIMIT = 5
INTERVAL = max(5, int(os.getenv("PUBLISH_INTERVAL_MINUTES", "20")))
COINS_REQUIRED = 3
COINS_PER_PARTICIPATION = 1
ADMIN_BONUS_COINS = 5
SESSION_MINUTES = max(1, int(os.getenv("PARTICIPATION_SESSION_MINUTES", "30")))
TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Kolkata"))
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
TRACK_PARTICIPATION_URL = os.environ["TRACK_PARTICIPATION_URL"]
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

PLATFORMS = {
    "yt": ("YouTube Shorts", ("youtube.com", "youtu.be")),
    "ig": ("Instagram Reels", ("instagram.com",)),
    "tt": ("TikTok", ("tiktok.com",)),
}
CHOOSE_PLATFORM, GET_URL = range(2)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("growing_together")


def now():
    return datetime.now(TZ)


def admin(uid):
    return uid in ADMIN_IDS


def save_user(u):
    supabase.table("users").upsert({
        "user_id": u.id,
        "username": u.username or "",
        "first_name": u.first_name or "",
    }, on_conflict="user_id").execute()


def get_user(uid):
    r = supabase.table("users").select("*").eq("user_id", uid).limit(1).execute()
    return r.data[0] if r.data else None


def get_coins(uid):
    row = get_user(uid)
    return int(row["coins"]) if row else 0


def change_coins(uid, delta):
    result = supabase.rpc("increment_user_coins", {"p_user_id": uid, "p_delta": delta}).execute()
    return result.data


def spend_coins(uid, amount):
    result = supabase.rpc("spend_user_coins", {"p_user_id": uid, "p_amount": amount}).execute()
    return bool(result.data)


def daily_count(uid):
    start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    r = (supabase.table("submissions")
         .select("id", count="exact")
         .eq("user_id", uid)
         .gte("created_at", start.isoformat())
         .lt("created_at", end.isoformat())
         .execute())
    return int(r.count or 0)


def valid_url(url, p):
    if not re.match(r"^https?://", url, re.I):
        return False
    host = re.sub(r"^https?://", "", url, flags=re.I).split("/")[0].split(":")[0].lower()
    return any(host == d or host.endswith("." + d) for d in PLATFORMS[p][1])


def main_menu(uid=None):
    coins = get_coins(uid) if uid else 0
    if uid is not None and admin(uid):
        submit_label = "📤 Submit post (Admin — FREE)"
    elif coins >= COINS_REQUIRED:
        submit_label = "📤 Submit post"
    else:
        submit_label = f"🔒 Submit ({coins}/{COINS_REQUIRED} coins)"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(submit_label, callback_data="submit")],
        [InlineKeyboardButton("🪙 My coins", callback_data="coins"),
         InlineKeyboardButton("📊 My submissions", callback_data="mine")],
        [InlineKeyboardButton("📜 Rules", callback_data="rules")]
    ])


async def channel_member(bot, uid):
    try:
        m = await bot.get_chat_member(CHANNEL_ID, uid)
        return m.status in ("member", "administrator", "creator")
    except Exception as e:
        log.warning("Membership check failed for %s: %s", uid, e)
        return False


def admin_post_markup():
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        f"🤝 Participate +{ADMIN_BONUS_COINS} 🪙", callback_data="start:ADMIN_MAIN"
    )]])


async def ensure_admin_post(app):
    key = "admin_post_message_id"
    r = supabase.table("settings").select("value").eq("key", key).limit(1).execute()
    if r.data:
        url_check = supabase.table("settings").select("value").eq("key", "admin_post_url").limit(1).execute()
        if not url_check.data:
            public_channel = os.getenv("PUBLIC_CHANNEL_USERNAME", "growingtogether789").lstrip("@")
            message_id = r.data[0]["value"]
            admin_target = os.getenv("ADMIN_PARTICIPATION_TARGET_URL", f"https://t.me/{public_channel}/{message_id}")
            supabase.table("settings").upsert({"key": "admin_post_url", "value": admin_target}, on_conflict="key").execute()
        return
    text = (
        "📢 *ADMIN POST — COMMUNITY PARTICIPATION*\n\n"
        f"🤝 Participate in this community post and receive *{ADMIN_BONUS_COINS} 🪙 coins*.\n\n"
        f"🪙 You need only *{COINS_REQUIRED} coins* to submit your own creator post.\n\n"
        "Coins are community participation points. They do not represent guaranteed views, likes, comments or subscribers.\n\n"
        "🚀 *Growing Together*\n"
        "Create • Learn • Improve • Grow"
    )
    try:
        msg = await app.bot.send_message(CHANNEL_ID, text, reply_markup=admin_post_markup(), parse_mode="Markdown")
        try:
            await app.bot.pin_chat_message(CHANNEL_ID, msg.message_id, disable_notification=True)
        except Exception as e:
            log.warning("Could not pin admin post: %s", e)
        supabase.table("settings").upsert({"key": key, "value": str(msg.message_id)}, on_conflict="key").execute()
        public_channel = os.getenv("PUBLIC_CHANNEL_USERNAME", "growingtogether789").lstrip("@")
        admin_target = os.getenv("ADMIN_PARTICIPATION_TARGET_URL", f"https://t.me/{public_channel}/{msg.message_id}")
        supabase.table("settings").upsert({"key": "admin_post_url", "value": admin_target}, on_conflict="key").execute()
        log.info("Created admin participation post %s", msg.message_id)
    except Exception as e:
        log.error("Could not create admin post: %s", e)


async def start(update, ctx):
    save_user(update.effective_user)
    member = await channel_member(ctx.bot, update.effective_user.id)
    if not member:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join Growing Together", url="https://t.me/growingtogether789")],
                                   [InlineKeyboardButton("✅ Verify membership", callback_data="verify")]])
        await update.message.reply_text(
            "🚀 *Growing Together*\n\nJoin our Telegram channel first. Then tap Verify.\n\n"
            "You earn community coins from participation and use 3 coins to submit a creator post.",
            reply_markup=kb, parse_mode="Markdown")
        return
    await update.message.reply_text(
        f"🚀 *Growing Together*\n\n"
        f"🪙 Coins: *{get_coins(update.effective_user.id)}*\n"
        f"📤 Submission cost: *{COINS_REQUIRED} coins*\n\n"
        "Participate in community posts to earn coins.\n"
        "No sub4sub, like-for-like or guaranteed engagement.",
        reply_markup=main_menu(update.effective_user.id), parse_mode="Markdown")


async def verify(update, ctx):
    q = update.callback_query
    await q.answer()
    if await channel_member(ctx.bot, q.from_user.id):
        save_user(q.from_user)
        await q.message.edit_text(
            f"✅ *Membership verified!*\n\n🪙 Coins: *{get_coins(q.from_user.id)}*\n\n"
            "Use the menu below to participate or submit when you have enough coins.",
            reply_markup=main_menu(q.from_user.id), parse_mode="Markdown")
    else:
        await q.answer("You haven't joined the channel yet.", show_alert=True)


async def coins(update, ctx):
    q = update.callback_query
    await q.answer()
    row = get_user(q.from_user.id)
    coins_now = int(row["coins"]) if row else 0
    earned = int(row["total_earned"]) if row else 0
    spent = int(row["total_spent"]) if row else 0
    await q.message.reply_text(
        f"🪙 *Your Growing Together Coins*\n\nBalance: *{coins_now}*\nEarned: {earned}\nSpent: {spent}\n\nSubmission cost: {COINS_REQUIRED} 🪙",
        parse_mode="Markdown")


async def rules(update, ctx):
    q = update.callback_query
    if q:
        await q.answer()
        target = q.message
    else:
        target = update.message
    await target.reply_text(
        f"📜 *Rules*\n\n"
        f"• {COINS_REQUIRED} community coins = 1 submission.\n"
        f"• One participation reward per creator/admin post.\n"
        f"• Submit only your own public content.\n"
        f"• Duplicate URLs are rejected.\n"
        f"• {DAILY_LIMIT} submission per day by default.\n"
        f"• Auto-approval checks eligibility, URL format and duplicates; it does not guarantee content-policy approval by YouTube, Instagram or TikTok.\n"
        f"• Coins are not guaranteed views, likes, comments or subscribers.\n"
        f"• No coordinated artificial engagement or sub4sub.", parse_mode="Markdown")


async def begin_submit(update, ctx):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not await channel_member(ctx.bot, uid):
        await q.message.reply_text("❌ Join Growing Together first, then tap Verify membership.")
        return ConversationHandler.END
    if not admin(uid):
        balance = get_coins(uid)
        if balance < COINS_REQUIRED:
            await q.message.reply_text(f"🔒 You need {COINS_REQUIRED} coins to submit. You currently have {balance} 🪙.")
            return ConversationHandler.END
        if daily_count(uid) >= DAILY_LIMIT:
            await q.message.reply_text(f"⏳ Daily limit reached ({DAILY_LIMIT} member posts). Try again tomorrow.")
            return ConversationHandler.END
    kb = [[InlineKeyboardButton(v[0], callback_data=f"p:{k}")] for k, v in PLATFORMS.items()]
    await q.message.reply_text("1️⃣ Choose the platform:", reply_markup=InlineKeyboardMarkup(kb))
    return CHOOSE_PLATFORM


async def choose_platform(update, ctx):
    q = update.callback_query
    await q.answer()
    p = q.data.split(":", 1)[1]
    if p not in PLATFORMS:
        await q.message.reply_text("❌ Invalid platform.")
        return ConversationHandler.END
    ctx.user_data["p"] = p
    await q.message.reply_text(f"2️⃣ {PLATFORMS[p][0]}\n\nSend the public post URL.")
    return GET_URL


async def receive_url(update, ctx):
    u = update.effective_user
    p = ctx.user_data.get("p")
    url = (update.message.text or "").strip()
    if not p or p not in PLATFORMS:
        await update.message.reply_text("❌ Submission session expired. Start again.")
        return ConversationHandler.END
    if not valid_url(url, p):
        await update.message.reply_text("❌ That URL doesn't match the selected platform. Send the correct public URL.")
        return GET_URL
    if not await channel_member(ctx.bot, u.id):
        await update.message.reply_text("❌ You must remain a member of Growing Together to submit.")
        return ConversationHandler.END
    save_user(u)
    existing = supabase.table("submissions").select("id").eq("url", url).limit(1).execute()
    if existing.data:
        await update.message.reply_text("⚠️ That exact URL has already been submitted.")
        return ConversationHandler.END

    is_admin = admin(u.id)
    cost = 0 if is_admin else COINS_REQUIRED
    if not is_admin:
        if get_coins(u.id) < COINS_REQUIRED:
            await update.message.reply_text(f"❌ You need {COINS_REQUIRED} coins. Your balance is {get_coins(u.id)}.")
            return ConversationHandler.END
        if daily_count(u.id) >= DAILY_LIMIT:
            await update.message.reply_text(f"⏳ Daily limit reached ({DAILY_LIMIT} member posts).")
            return ConversationHandler.END
        if not spend_coins(u.id, COINS_REQUIRED):
            await update.message.reply_text("❌ Your coin balance changed. Please try again.")
            return ConversationHandler.END

    payload = {
        "user_id": u.id,
        "username": u.username or "",
        "platform": PLATFORMS[p][0],
        "url": url,
        "status": "approved",
        "created_at": now().isoformat(),
        "approved_at": now().isoformat(),
        "coins_spent": cost,
    }
    try:
        inserted = supabase.table("submissions").insert(payload).execute()
        if not inserted.data:
            raise RuntimeError("Supabase did not return the new submission")
        sid = int(inserted.data[0]["id"])
    except Exception:
        if cost:
            try:
                change_coins(u.id, cost)
            except Exception:
                log.exception("Refund failed after submission insert failure")
        log.exception("Failed to create submission")
        await update.message.reply_text("❌ Could not save your submission. Your coins were refunded if they were charged.")
        return ConversationHandler.END

    waiting = queue_rows(2)
    if len(waiting) == 1 and int(waiting[0]["id"]) == sid:
        try:
            if await publish_one(ctx, sid):
                if is_admin:
                    await update.message.reply_text(f"✅ Admin submission #{sid} published immediately.\n🪙 Cost: 0 coins.")
                else:
                    await update.message.reply_text(f"🎉 Submission #{sid} approved and published immediately!\n🪙 -{COINS_REQUIRED} coins.")
                return ConversationHandler.END
        except Exception:
            log.exception("Immediate publish failed for #%s", sid)

    position = queue_pos(sid)
    if is_admin:
        await update.message.reply_text(f"✅ Admin submission #{sid} approved and queued.\n🪙 Cost: 0 coins.\n📊 Queue position: #{position}\n⏳ The queue publishes every {INTERVAL} minutes.")
    else:
        await update.message.reply_text(f"🎉 Submission #{sid} approved and queued.\n🪙 -{COINS_REQUIRED} coins.\n📊 Queue position: #{position}\n⏳ The queue publishes every {INTERVAL} minutes.")
    return ConversationHandler.END


async def cancel(update, ctx):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def create_participation_session(uid, post_key, target_url):
    token = secrets.token_urlsafe(32)
    expires_at = now() + timedelta(minutes=SESSION_MINUTES)

    result = (
        supabase.table("participation_sessions")
        .insert({
            "user_id": uid,
            "post_key": post_key,
            "token": token,
            "target_url": target_url,
            "expires_at": expires_at.isoformat(),
        })
        .execute()
    )

    if not result.data:
        raise RuntimeError("Could not create participation session")

    tracked_url = f"{TRACK_PARTICIPATION_URL}?token={token}"
    return tracked_url


def queue_rows(limit=50):
    r = (supabase.table("submissions").select("*")
         .eq("status", "approved")
         .is_("published_at", "null")
         .order("approved_at", desc=False)
         .order("id", desc=False)
         .limit(limit).execute())
    return r.data or []


def queue_pos(sid):
    rows = queue_rows(10000)
    for i, r in enumerate(rows, 1):
        if int(r["id"]) == int(sid):
            return i
    return 0


async def mine(update, ctx):
    q = update.callback_query
    await q.answer()
    r = (supabase.table("submissions").select("id,platform,status,url,published_at")
         .eq("user_id", q.from_user.id).order("id", desc=True).limit(10).execute())
    rows = r.data or []
    if not rows:
        await q.message.reply_text("No submissions yet.")
        return
    out = ["📊 *Your submissions*"]
    for row in rows:
        extra = f" • queue #{queue_pos(row['id'])}" if row["status"] == "approved" and not row["published_at"] else ""
        out.append(f"#{row['id']} — {row['platform']} — {row['status']}{extra}")
    await q.message.reply_text("\n".join(out), parse_mode="Markdown")

async def start_participation(update, ctx):
    q = update.callback_query
    await q.answer()
    if not await channel_member(ctx.bot, q.from_user.id):
        await q.answer("Join Growing Together first.", show_alert=True)
        return
    save_user(q.from_user)
    key = q.data.split(":", 1)[1]
    if key == "ADMIN_MAIN":
        amount = ADMIN_BONUS_COINS
        r = supabase.table("settings").select("value").eq("key", "admin_post_url").limit(1).execute()
        target_url = r.data[0]["value"] if r.data else None
    elif key.startswith("SPOT_"):
        amount = COINS_PER_PARTICIPATION
        try:
            sid = int(key.split("_", 1)[1])
        except ValueError:
            await q.answer("Invalid participation post.", show_alert=True)
            return
        r = supabase.table("submissions").select("url").eq("id", sid).eq("status", "approved").limit(1).execute()
        target_url = r.data[0]["url"] if r.data else None
    else:
        await q.answer("Invalid participation post.", show_alert=True)
        return
    if not target_url:
        await q.answer("This participation post is not configured yet.", show_alert=True)
        return
    try:
        tracked_url = create_participation_session(q.from_user.id, key, target_url)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Open Video / Post", url=tracked_url)],
            [InlineKeyboardButton(f"✅ Claim +{amount} Coins", callback_data=f"claim:{key}")]
        ])
        await ctx.bot.send_message(
            chat_id=q.from_user.id,
            text=(
                "🤝 *Participation started.*\n\n"
                "1️⃣ Tap *Open Video / Post* first.\n"
                "2️⃣ Visit the post.\n"
                "3️⃣ Return to Telegram.\n"
                "4️⃣ Tap *Claim*.\n\n"
                "⚠️ The reward is given only after the tracking system confirms the tracked link was opened."
            ),
            reply_markup=kb,
            parse_mode="Markdown",
        )
    except Exception:
        log.exception("Could not create participation session")
        await q.answer("Could not start participation. Try again.", show_alert=True)


async def claim_participation(update, ctx):
    q = update.callback_query
    if not await channel_member(ctx.bot, q.from_user.id):
        await q.answer("Join Growing Together first.", show_alert=True)
        return
    save_user(q.from_user)
    key = q.data.split(":", 1)[1]
    if key == "ADMIN_MAIN":
        amount = ADMIN_BONUS_COINS
    elif key.startswith("SPOT_"):
        amount = COINS_PER_PARTICIPATION
    else:
        await q.answer("Invalid participation post.", show_alert=True)
        return
    try:
        rows = (
            supabase.table("participation_sessions")
            .select("*")
            .eq("user_id", q.from_user.id)
            .eq("post_key", key)
            .is_("claimed_at", "null")
            .order("id", desc=True)
            .limit(10)
            .execute()
        )
        session = None
        current = now()
        for row in rows.data or []:
            if not row.get("clicked_at"):
                continue
            expires = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
            if current <= expires:
                session = row
                break
        if not session:
            await q.answer(
                "❌ First open the Video / Post using the tracked button, then return and tap Claim.",
                show_alert=True,
            )
            return

        reward = supabase.rpc(
            "award_participation",
            {
                "p_user_id": q.from_user.id,
                "p_post_key": key,
                "p_amount": amount,
                "p_note": "Community participation reward",
            },
        ).execute()
        if not reward.data:
            await q.answer("Already counted for this post.", show_alert=True)
            return

        supabase.table("participation_sessions").update(
            {"claimed_at": now().isoformat()}
        ).eq("id", session["id"]).execute()
        await q.answer(f"+{amount} coin(s) added! 🪙", show_alert=True)
        try:
            await q.message.reply_text(
                f"✅ Participation verified!\n\n"
                f"🪙 +{amount} coins added.\n"
                f"💰 New balance: {get_coins(q.from_user.id)}"
            )
        except Exception:
            pass
    except Exception:
        log.exception("Participation claim failed")
        await q.answer("Could not verify participation. Try again.", show_alert=True)


async def approve(update, ctx):
    q = update.callback_query
    await q.answer()
    if not admin(q.from_user.id):
        return
    sid = int(q.data.split(":")[1])
    r = supabase.table("submissions").select("*").eq("id", sid).limit(1).execute()
    row = r.data[0] if r.data else None
    if row:
        supabase.table("submissions").update({"status": "approved", "approved_at": now().isoformat()}).eq("id", sid).eq("status", "pending").execute()
    await q.message.edit_text(f"✅ Submission #{sid} approved. Queue position: #{queue_pos(sid)}")
    if row:
        try:
            await ctx.bot.send_message(row["user_id"], f"✅ Your submission #{sid} was approved and queued.")
        except Exception:
            pass


async def reject_button(update, ctx):
    q = update.callback_query
    await q.answer()
    if not admin(q.from_user.id):
        return
    await q.message.reply_text(f"Use /reject {q.data.split(':')[1]} reason")


async def pending(update, ctx):
    if not admin(update.effective_user.id):
        return
    r = (supabase.table("submissions").select("id,username,platform,url")
         .eq("status", "pending").order("id", desc=False).limit(30).execute())
    rows = r.data or []
    if not rows:
        await update.message.reply_text("No pending submissions.")
        return
    await update.message.reply_text("\n\n".join(
        f"#{row['id']} @{row['username'] or 'unknown'}\n{row['platform']}\n{row['url']}" for row in rows
    ))


async def approve_cmd(update, ctx):
    if not admin(update.effective_user.id) or not ctx.args:
        return
    sid = int(ctx.args[0])
    r = supabase.table("submissions").select("user_id").eq("id", sid).limit(1).execute()
    row = r.data[0] if r.data else None
    supabase.table("submissions").update({"status": "approved", "approved_at": now().isoformat()}).eq("id", sid).eq("status", "pending").execute()
    await update.message.reply_text(f"✅ Approved #{sid}.")
    if row:
        try:
            await ctx.bot.send_message(row["user_id"], f"✅ Submission #{sid} approved and queued.")
        except Exception:
            pass


async def reject_cmd(update, ctx):
    if not admin(update.effective_user.id) or len(ctx.args) < 2:
        return
    sid = int(ctx.args[0])
    reason = " ".join(ctx.args[1:])
    r = supabase.table("submissions").select("user_id,coins_spent,status").eq("id", sid).limit(1).execute()
    row = r.data[0] if r.data else None
    if not row:
        await update.message.reply_text(f"Submission #{sid} not found.")
        return
    supabase.table("submissions").update({"status": "rejected", "reason": reason, "coins_spent": 0}).eq("id", sid).in_("status", ["pending", "approved"]).execute()
    if row.get("coins_spent", 0):
        change_coins(row["user_id"], int(row["coins_spent"]))
    await update.message.reply_text(f"❌ Rejected #{sid}. Coins refunded if applicable.")
    try:
        await ctx.bot.send_message(row["user_id"], f"❌ Submission #{sid} rejected.\nReason: {reason}\n🪙 Submission coins refunded.")
    except Exception:
        pass


async def queue_cmd(update, ctx):
    if not admin(update.effective_user.id):
        return
    rows = queue_rows()
    if not rows:
        await update.message.reply_text("Queue is empty.")
        return
    await update.message.reply_text("\n\n".join(
        f"{i}. #{row['id']} @{row['username'] or 'unknown'} — {row['platform']}\n{row['url']}"
        for i, row in enumerate(rows, 1)
    ))


async def publish_one(ctx, sid=None):
    if sid is not None:
        r = (supabase.table("submissions").select("*").eq("id", sid).eq("status", "approved")
             .is_("published_at", "null").limit(1).execute())
    else:
        rows = queue_rows(1)
        r = type("R", (), {"data": rows})()
    row = r.data[0] if r.data else None
    if not row:
        return False
    msg = (f"🚀 *Growing Together — Creator Spotlight*\n\n"
           f"👤 @{row['username'] or 'creator'}\n📱 {row['platform']}\n\n"
           "Discover a creator from our community 👇")
    kb = InlineKeyboardMarkup([
    [
        InlineKeyboardButton(
            "▶️ Watch / Visit post",
            url=row["url"]
        )
    ],
    [
        InlineKeyboardButton(
            f"🤝 Participate +{COINS_PER_PARTICIPATION} 🪙",
            callback_data=f"start:SPOT_{row['id']}"
        )
    ]
])
    await ctx.bot.send_message(CHANNEL_ID, msg, reply_markup=kb, parse_mode="Markdown")
    supabase.table("submissions").update({"published_at": now().isoformat()}).eq("id", row["id"]).execute()
    try:
        await ctx.bot.send_message(row["user_id"], f"📢 Your submission #{row['id']} is now featured in Growing Together.")
    except Exception:
        pass
    return True


async def publish(update, ctx):
    if not admin(update.effective_user.id):
        return
    sid = int(ctx.args[0]) if ctx.args else None
    await update.message.reply_text("✅ Published." if await publish_one(ctx, sid) else "Nothing eligible to publish.")


async def auto_publish(ctx):
    try:
        await publish_one(ctx)
    except Exception as e:
        log.exception("Auto publish failed: %s", e)


async def stats(update, ctx):
    if not admin(update.effective_user.id):
        return
    users = supabase.table("users").select("user_id,coins").execute().data or []
    submissions = supabase.table("submissions").select("id,status,published_at").execute().data or []
    users_count = len(users)
    submissions_count = len(submissions)
    pending_count = sum(1 for x in submissions if x.get("status") == "pending")
    queued_count = sum(1 for x in submissions if x.get("status") == "approved" and not x.get("published_at"))
    published_count = sum(1 for x in submissions if x.get("published_at"))
    coins_total = sum(int(x.get("coins") or 0) for x in users)
    await update.message.reply_text(
        f"📊 *Growing Together V3*\n\nCreators: {users_count}\nSubmissions: {submissions_count}\n"
        f"Pending: {pending_count}\nQueued: {queued_count}\nPublished: {published_count}\nCoins in circulation: {coins_total}",
        parse_mode="Markdown")


async def panel(update, ctx):
    if not admin(update.effective_user.id):
        return
    await update.message.reply_text(
        "🛠 *Admin Panel*\n\n"
        "/pending\n/queue\n/approve ID\n/reject ID reason\n"
        "/publish ID\n/publish\n/stats\n/adminpost\n"
        "/lockgroup\n/unlockgroup\n\n"
        f"Members: {COINS_REQUIRED} coins/post, max {DAILY_LIMIT}/day\n"
        "Admins: unlimited and free\n"
        f"Member participation: +{COINS_PER_PARTICIPATION}\n"
        f"Admin participation: +{ADMIN_BONUS_COINS}",
        parse_mode="Markdown",
    )


async def adminpost(update, ctx):
    if not admin(update.effective_user.id):
        return
    text = (
        "📢 *ADMIN POST — COMMUNITY PARTICIPATION*\n\n"
        f"🤝 Participate in this community post and receive *{ADMIN_BONUS_COINS} 🪙 coins*.\n\n"
        f"🪙 Members need *{COINS_REQUIRED} coins* to submit their own creator post.\n\n"
        "Coins are community participation points — not guaranteed views, likes, comments or subscribers.\n\n"
        "🚀 *Growing Together*"
    )
    message = await ctx.bot.send_message(
        CHANNEL_ID,
        text,
        reply_markup=admin_post_markup(),
        parse_mode="Markdown",
    )
    pin_failed = False
    try:
        await ctx.bot.pin_chat_message(CHANNEL_ID, message.message_id, disable_notification=True)
    except Exception as exc:
        pin_failed = True
        log.warning("Admin post pin failed: %s", exc)
    public_channel = os.getenv("PUBLIC_CHANNEL_USERNAME", "growingtogether789").lstrip("@")
    target = os.getenv(
        "ADMIN_PARTICIPATION_TARGET_URL",
        f"https://t.me/{public_channel}/{message.message_id}",
    )
    supabase.table("settings").upsert(
        {"key": "admin_post_message_id", "value": str(message.message_id)},
        on_conflict="key",
    ).execute()
    supabase.table("settings").upsert(
        {"key": "admin_post_url", "value": target},
        on_conflict="key",
    ).execute()
    if pin_failed:
        await update.message.reply_text(
            "✅ Admin post published.\n⚠️ Pin failed.\n"
            f"🪙 Member reward: +{ADMIN_BONUS_COINS}. Admin cost: 0."
        )
    else:
        await update.message.reply_text(
            "✅ Admin post published and pinned.\n"
            f"🪙 Member reward: +{ADMIN_BONUS_COINS}. Admin cost: 0."
        )


async def lock(update, ctx):
    if not admin(update.effective_user.id):
        return
    if not GROUP_ID:
        await update.message.reply_text("GROUP_ID isn't configured.")
        return
    p = ChatPermissions(can_send_messages=False, can_send_audios=False, can_send_documents=False,
                        can_send_photos=False, can_send_videos=False, can_send_video_notes=False,
                        can_send_voice_notes=False, can_send_polls=False, can_send_other_messages=False,
                        can_add_web_page_previews=False)
    await ctx.bot.set_chat_permissions(GROUP_ID, p)
    await update.message.reply_text("🔒 Group locked.")


async def unlock(update, ctx):
    if not admin(update.effective_user.id):
        return
    if not GROUP_ID:
        await update.message.reply_text("GROUP_ID isn't configured.")
        return
    p = ChatPermissions(can_send_messages=True, can_send_audios=True, can_send_documents=True,
                        can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
                        can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
                        can_add_web_page_previews=True)
    await ctx.bot.set_chat_permissions(GROUP_ID, p)
    await update.message.reply_text("🔓 Group unlocked.")


def main():
    log.info("Connecting Growing Together V3 to Supabase at %s", SUPABASE_URL)
    # Verify that the schema is reachable before starting Telegram polling.
    supabase.table("settings").select("key").limit(1).execute()
    supabase.table("participation_sessions").select("id").limit(1).execute()
    app = Application.builder().token(TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(begin_submit, pattern="^submit$")],
        states={
            CHOOSE_PLATFORM: [CallbackQueryHandler(choose_platform, pattern="^p:")],
            GET_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_url)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    app.add_handler(conv)
    for cmd, fn in [("start", start), ("pending", pending), ("queue", queue_cmd),
                    ("approve", approve_cmd), ("reject", reject_cmd), ("publish", publish),
                    ("stats", stats), ("panel", panel), ("adminpost", adminpost),
                    ("lockgroup", lock), ("unlockgroup", unlock)]:
        app.add_handler(CommandHandler(cmd, fn))
    app.add_handler(CallbackQueryHandler(verify, pattern="^verify$"))
    app.add_handler(CallbackQueryHandler(coins, pattern="^coins$"))
    app.add_handler(CallbackQueryHandler(mine, pattern="^mine$"))
    app.add_handler(CallbackQueryHandler(rules, pattern="^rules$"))
    app.add_handler(CallbackQueryHandler(start_participation, pattern="^start:"))
    app.add_handler(CallbackQueryHandler(claim_participation, pattern="^claim:"))
    app.add_handler(CallbackQueryHandler(approve, pattern="^a:"))
    app.add_handler(CallbackQueryHandler(reject_button, pattern="^r:"))

    async def post_init(application):
        await ensure_admin_post(application)

    app.post_init = post_init
    app.job_queue.run_repeating(auto_publish, interval=INTERVAL * 60, first=60)
    print("Growing Together V3 running with Supabase.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
