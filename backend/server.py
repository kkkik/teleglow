#!/usr/bin/env python3
"""Gift Mini App backend: aiohttp API + payments bot in one process."""
import asyncio, hashlib, hmac, json, math, os, random, sqlite3, time, urllib.parse
from collections import defaultdict, deque
from aiohttp import web
from telebot import types
from telebot.async_telebot import AsyncTeleBot

TOKEN = os.environ["BOT_TOKEN"]
WEBAPP_URL = os.environ["WEBAPP_URL"]
PORT = int(os.getenv("PORT", "8080"))
DB = os.getenv("DB_PATH", "app.db")
WIN_CHANCE = float(os.getenv("WIN_CHANCE", "0.10"))
MARKUP = float(os.getenv("SHOP_MARKUP", "0.10"))   # withdraw price = gift Stars + markup
RATE = float(os.getenv("DEPOSIT_RATE", "0.85"))     # deposited gift credit = gift Stars x rate
SUPPORT = os.getenv("SUPPORT_USER", "GiftRollBank").lstrip("@")
TOPUPS = [50, 100, 250, 500]
LOW_STARS = int(os.getenv("LOW_STARS", "200"))     # alert admins when the bot holds fewer Stars
ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()}
HERE = os.path.dirname(os.path.abspath(__file__))
bot = AsyncTeleBot(TOKEN)
_cache = {"t": 0, "v": []}
BOT_USER = {"name": ""}
AUTO = {"conn": "", "owner": 0}   # business connection of the support account (auto deposits)
_bal = {"t": 0, "v": None}
RATE_HITS = defaultdict(deque)


def q(sql, args=(), one=False, many=False, last=False):
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(sql, args)
        con.commit()
        if last:
            return cur.lastrowid
        if one:
            r = cur.fetchone()
            return dict(r) if r else None
        if many:
            return [dict(r) for r in cur.fetchall()]
        return cur.rowcount
    finally:
        con.close()


def init():
    q("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, points INTEGER DEFAULT 0, last_spin REAL DEFAULT 0, balance INTEGER DEFAULT 0)")
    for col in ("balance INTEGER DEFAULT 0", "ref_by INTEGER", "streak INTEGER DEFAULT 0", "lang TEXT DEFAULT 'en'"):
        try:
            q("ALTER TABLE users ADD COLUMN " + col)
        except sqlite3.OperationalError:
            pass
    q("CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, gift_id TEXT, emoji TEXT, stars INTEGER, source TEXT, at REAL)")
    q("CREATE TABLE IF NOT EXISTS orders(payload TEXT PRIMARY KEY, user_id INTEGER, gift_id TEXT, price INTEGER, status TEXT, charge_id TEXT)")
    q("CREATE TABLE IF NOT EXISTS deposits(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, gift_id TEXT, emoji TEXT, stars INTEGER, credit INTEGER, status TEXT, at REAL)")
    q("CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, delta INTEGER, reason TEXT, at REAL)")
    try:
        q("ALTER TABLE deposits ADD COLUMN owned_id TEXT")
    except sqlite3.OperationalError:
        pass
    q("CREATE UNIQUE INDEX IF NOT EXISTS dep_owned ON deposits(owned_id)")
    q("CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT)")


def log(uid, delta, reason):
    q("INSERT INTO ledger(user_id, delta, reason, at) VALUES(?,?,?,?)", (uid, delta, reason, time.time()))


def kv_get(k, default=""):
    r = q("SELECT v FROM kv WHERE k = ?", (k,), one=True)
    return r["v"] if r else default


def kv_set(k, v):
    q("INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (k, v))


MSG = {
    "start": {"en": "Welcome! Tap the button to open the app.", "ar": "أهلاً بك! اضغط الزر لفتح التطبيق."},
    "open": {"en": "🎁 Open", "ar": "🎁 فتح"},
    "inv_title": {"en": "Add {n} ⭐", "ar": "إضافة {n} ⭐"},
    "inv_desc": {"en": "Stars are added to your in-app balance and can be used to withdraw gifts.",
                 "ar": "تُضاف النجوم إلى رصيدك داخل التطبيق ويمكنك استخدامها لسحب الهدايا."},
    "topup_ok": {"en": "✅ {n} ⭐ added to your balance.", "ar": "✅ تمت إضافة {n} ⭐ إلى رصيدك."},
    "dep_ok": {"en": "✅ Gift received: {e}\n+{c} ⭐ added to your balance.", "ar": "✅ تم استلام الهدية {e}\nأُضيف +{c} ⭐ إلى رصيدك."},
    "dep_unique": {"en": "🎁 Your collectible gift arrived. @{s} will credit your balance shortly.",
                   "ar": "🎁 وصلت هديتك النادرة. سيضيف @{s} رصيدها قريباً."},
    "dep_approved": {"en": "✅ Your {e} deposit was approved: +{c} ⭐", "ar": "✅ تمت الموافقة على إيداع {e}: +{c} ⭐"},
    "dep_rejected": {"en": "❌ Your {e} deposit was rejected. Contact @{s} if this is a mistake.",
                     "ar": "❌ تم رفض إيداع {e}. تواصل مع @{s} إذا كان هذا خطأ."},
}


def M(uid, key, **kw):
    """Message in the user's language (Telegram language_code: ar or en)."""
    r = q("SELECT lang FROM users WHERE id = ?", (uid,), one=True)
    lang = (r or {}).get("lang") or "en"
    return MSG[key].get(lang, MSG[key]["en"]).format(**kw)


def lang_of_code(code):
    return None if not code else ("ar" if code.startswith("ar") else "en")


def register(uid, name, ref=None, lang=None):
    """Create the user once; a valid referral gives the inviter 50 points and the new user 20."""
    if not q("INSERT OR IGNORE INTO users(id, name, lang) VALUES(?, ?, ?)", (uid, name, lang or "en")):
        if lang:
            q("UPDATE users SET name = ?, lang = ? WHERE id = ?", (name, lang, uid))
        else:
            q("UPDATE users SET name = ? WHERE id = ?", (name, uid))
        return
    if ref and ref != uid and q("SELECT id FROM users WHERE id = ?", (ref,), one=True):
        q("UPDATE users SET ref_by = ?, points = points + 20 WHERE id = ?", (ref, uid))
        q("UPDATE users SET points = points + 50 WHERE id = ?", (ref,))


def auth(request) -> int:
    data = dict(urllib.parse.parse_qsl(request.headers.get("X-Init", ""), keep_blank_values=True))
    got = data.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    good = hmac.compare_digest(hmac.new(key, check.encode(), hashlib.sha256).hexdigest(), got)
    if not good or time.time() - int(data.get("auth_date", 0) or 0) > 86400 or "user" not in data:
        raise web.HTTPUnauthorized()
    user = json.loads(data["user"])
    uid = user["id"]
    hits, now = RATE_HITS[uid], time.monotonic()
    while hits and now - hits[0] > 10:
        hits.popleft()
    if len(hits) >= 30:
        raise web.HTTPTooManyRequests()
    hits.append(now)
    sp = data.get("start_param", "")
    ref = int(sp[4:]) if sp.startswith("ref_") and sp[4:].isdigit() else None
    register(uid, user.get("first_name", ""), ref, lang_of_code(user.get("language_code")))
    return uid


async def gifts() -> list:
    if time.time() - _cache["t"] > 600 or not _cache["v"]:
        res = await bot.get_available_gifts()
        items = []
        for g in res.gifts:
            left = getattr(g, "remaining_count", None)
            if left is None or left > 0:
                items.append({"id": str(g.id), "emoji": (g.sticker.emoji if g.sticker else None) or "🎁", "stars": g.star_count})
        _cache.update(t=time.time(), v=sorted(items, key=lambda x: x["stars"]))
    return _cache["v"]


async def bot_stars():
    """Stars held by the bot (None if unknown). Cached for a minute."""
    if time.time() - _bal["t"] > 60:
        try:
            _bal.update(v=(await bot.get_my_star_balance()).amount, t=time.time())
        except Exception:
            _bal.update(v=None, t=time.time())
    return _bal["v"]


def price(g) -> int:
    return math.ceil(g["stars"] * (1 + MARKUP))


async def deliver(uid: int, g: dict, source: str):
    for attempt in (1, 2):
        try:
            await bot.send_gift(gift_id=g["id"], user_id=uid)
            break
        except Exception:
            if attempt == 2:
                raise
            await asyncio.sleep(1.5)
    _bal["t"] = 0
    q("INSERT INTO items(user_id, gift_id, emoji, stars, source, at) VALUES(?,?,?,?,?,?)",
      (uid, g["id"], g["emoji"], g["stars"], source, time.time()))


def jerr(code, status):
    return web.json_response({"error": code}, status=status)


async def api_state(request):
    uid = auth(request)
    u = q("SELECT * FROM users WHERE id = ?", (uid,), one=True)
    bs = await bot_stars()
    return web.json_response({
        "auto": bool(AUTO["conn"]), "balance": u["balance"], "points": u["points"], "next_spin": u["last_spin"] + 86400,
        "streak": u["streak"] or 0, "bot": BOT_USER["name"], "uid": uid,
        "refs": q("SELECT COUNT(*) AS c FROM users WHERE ref_by = ?", (uid,), one=True)["c"],
        "ledger": q("SELECT delta, reason, at FROM ledger WHERE user_id = ? ORDER BY id DESC LIMIT 20", (uid,), many=True),
        "win_chance": WIN_CHANCE, "topups": TOPUPS, "support": SUPPORT, "rate": RATE,
        "gifts": [dict(g, price=price(g), out=(bs is not None and g["stars"] > bs)) for g in await gifts()],
        "bag": q("SELECT emoji, stars, source, at FROM items WHERE user_id = ? ORDER BY id DESC LIMIT 50", (uid,), many=True),
        "deps": q("SELECT emoji, stars, credit, status, at FROM deposits WHERE user_id = ? ORDER BY id DESC LIMIT 20", (uid,), many=True),
        "top": q("SELECT name, points FROM users ORDER BY points DESC LIMIT 10", many=True),
    })


async def api_spin(request):
    uid = auth(request)
    now = time.time()
    u = q("SELECT last_spin, streak FROM users WHERE id = ?", (uid,), one=True)
    if not q("UPDATE users SET last_spin = ? WHERE id = ? AND last_spin <= ?", (now, uid, now - 86400)):
        return jerr("already_spun", 429)
    streak = (u["streak"] or 0) + 1 if 0 < now - u["last_spin"] <= 172800 else 1
    bonus = min(streak, 7) * 5
    gl = await gifts()
    result = {"win": False, "points": random.choice([5, 10, 15, 25])}
    if gl and random.random() < WIN_CHANCE:
        g = gl[0]  # cheapest gift
        try:
            await deliver(uid, g, "daily spin")
            result = {"win": True, "emoji": g["emoji"], "stars": g["stars"], "points": 0}
        except Exception as exc:
            print("spin delivery failed:", exc)
            q("UPDATE users SET last_spin = ? WHERE id = ?", (u["last_spin"], uid))
            return jerr("delivery_failed", 502)
    q("UPDATE users SET streak = ?, points = points + ? WHERE id = ?", (streak, bonus + result["points"], uid))
    return web.json_response(dict(result, streak=streak, bonus=bonus))


async def find_gift(gid):
    return next((x for x in await gifts() if x["id"] == str(gid)), None)


async def api_topup(request):
    uid = auth(request)
    amount = int((await request.json()).get("amount", 0) or 0)
    if amount not in TOPUPS:
        return jerr("bad_amount", 400)
    payload = f"top:{uid}:{amount}:{int(time.time())}"
    q("INSERT INTO orders(payload, user_id, gift_id, price, status) VALUES(?, ?, 'topup', ?, 'pending')", (payload, uid, amount))
    link = await bot.create_invoice_link(
        title=M(uid, "inv_title", n=amount), description=M(uid, "inv_desc"),
        payload=payload, provider_token="", currency="XTR",
        prices=[types.LabeledPrice(label="Top up", amount=amount)],
    )
    return web.json_response({"link": link})


async def api_buy(request):
    """Withdraw a gift: pay with in-app balance, gift is sent to the user."""
    uid = auth(request)
    g = await find_gift((await request.json()).get("gift_id"))
    if not g:
        return jerr("not_found", 404)
    p = price(g)
    bs = await bot_stars()
    if bs is not None and g["stars"] > bs:
        return jerr("out_of_stock", 503)
    if not q("UPDATE users SET balance = balance - ? WHERE id = ? AND balance >= ?", (p, uid, p)):
        return jerr("insufficient", 402)
    try:
        await deliver(uid, g, "withdraw")
    except Exception as exc:
        print("withdraw delivery failed:", exc)
        q("UPDATE users SET balance = balance + ? WHERE id = ?", (p, uid))
        return jerr("delivery_failed", 502)
    log(uid, -p, f"withdraw {g['emoji']}")
    return web.json_response({"ok": True, "emoji": g["emoji"]})


async def api_deposit(request):
    """User says they sent a gift to the support account; an admin confirms before credit is added."""
    uid = auth(request)
    g = await find_gift((await request.json()).get("gift_id"))
    if not g:
        return jerr("not_found", 404)
    if AUTO["conn"]:
        return jerr("auto_enabled", 409)
    if not ADMINS:
        return jerr("no_admin", 503)
    if q("SELECT COUNT(*) AS c FROM deposits WHERE user_id = ? AND status = 'pending'", (uid,), one=True)["c"] >= 3:
        return jerr("too_many_pending", 429)
    credit = int(g["stars"] * RATE)
    did = q("INSERT INTO deposits(user_id, gift_id, emoji, stars, credit, status, at) VALUES(?,?,?,?,?, 'pending', ?)",
            (uid, g["id"], g["emoji"], g["stars"], credit, time.time()), last=True)
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton(f"✅ Approve +{credit} ⭐", callback_data=f"dep:ok:{did}"),
           types.InlineKeyboardButton("❌ Reject", callback_data=f"dep:no:{did}"))
    for admin in ADMINS:
        try:
            await bot.send_message(admin, f"Deposit #{did}\nUser: {uid}\nGift: {g['emoji']} ({g['stars']} ⭐)\n"
                                   f"Credit: {credit} ⭐\nApprove only after @{SUPPORT} received the gift.", reply_markup=kb)
        except Exception as exc:
            print("admin notify failed:", exc)
    return web.json_response({"ok": True, "credit": credit})


@bot.callback_query_handler(func=lambda c: (c.data or "").startswith("dep:"))
async def on_deposit_review(c):
    if c.from_user.id not in ADMINS:
        await bot.answer_callback_query(c.id, "Not allowed", show_alert=True)
        return
    _, act, did = c.data.split(":")
    status = "approved" if act == "ok" else "rejected"
    if not q("UPDATE deposits SET status = ? WHERE id = ? AND status = 'pending'", (status, int(did))):
        await bot.answer_callback_query(c.id, "Already handled")
        return
    d = q("SELECT * FROM deposits WHERE id = ?", (int(did),), one=True)
    if status == "approved":
        q("UPDATE users SET balance = balance + ? WHERE id = ?", (d["credit"], d["user_id"]))
        log(d["user_id"], d["credit"], f"deposit {d['emoji']}")
        note = M(d["user_id"], "dep_approved", e=d["emoji"], c=d["credit"])
    else:
        note = M(d["user_id"], "dep_rejected", e=d["emoji"], s=SUPPORT)
    try:
        await bot.send_message(d["user_id"], note)
    except Exception as exc:
        print("user notify failed:", exc)
    await bot.answer_callback_query(c.id, "Done")
    await bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id, reply_markup=None)


@bot.business_connection_handler(func=lambda c: True)
async def on_biz_conn(c):
    """Remember the connection of the support account so gifts sent to it are credited automatically."""
    if (c.user.username or "").lower() != SUPPORT.lower():
        return
    on = bool(getattr(c, "is_enabled", True))
    AUTO.update(conn=c.id if on else "", owner=c.user.id)
    kv_set("biz_conn", AUTO["conn"])
    kv_set("biz_owner", str(c.user.id))
    print("Support account connection:", "enabled" if on else "disabled")


@bot.business_message_handler(content_types=["gift", "unique_gift"])
async def on_gift(m):
    try:
        if not AUTO["conn"] or m.business_connection_id != AUTO["conn"]:
            return
        sender = m.from_user
        if sender is None or sender.is_bot or sender.id == AUTO["owner"]:
            return
        uid = sender.id
        register(uid, sender.first_name or "")
        info = getattr(m, "gift", None)
        if info is None:  # collectible gift: value unknown, an admin credits it manually
            for admin in ADMINS:
                try:
                    await bot.send_message(admin, f"🎁 Collectible gift received from user {uid}. Credit them with /credit {uid} <amount>.")
                except Exception:
                    pass
            await bot.send_message(uid, M(uid, "dep_unique", s=SUPPORT))
            return
        oid = str(getattr(info, "owned_gift_id", None) or f"{m.chat.id}:{m.message_id}")
        stars = int(info.gift.star_count)
        emoji = (info.gift.sticker.emoji if info.gift.sticker else None) or "🎁"
        credit = int(stars * RATE)
        if not q("INSERT OR IGNORE INTO deposits(user_id, gift_id, emoji, stars, credit, status, at, owned_id) "
                 "VALUES(?,?,?,?,?, 'approved', ?, ?)", (uid, str(info.gift.id), emoji, stars, credit, time.time(), oid)):
            return
        q("UPDATE users SET balance = balance + ? WHERE id = ?", (credit, uid))
        log(uid, credit, f"deposit {emoji}")
        await bot.send_message(uid, M(uid, "dep_ok", e=emoji, c=credit))
    except Exception as exc:
        print("gift handler failed:", exc)


async def maintenance():
    """Expire stale manual deposits and warn admins when the bot is low on Stars."""
    last_alert = 0.0
    while True:
        try:
            q("UPDATE deposits SET status = 'expired' WHERE status = 'pending' AND at < ?", (time.time() - 172800,))
            bs = await bot_stars()
            if bs is not None and bs < LOW_STARS and time.time() - last_alert > 21600:
                last_alert = time.time()
                for admin in ADMINS:
                    try:
                        await bot.send_message(admin, f"⚠️ The bot holds only {bs} ⭐. Gifts above that are shown as out of stock.")
                    except Exception:
                        pass
        except Exception as exc:
            print("maintenance failed:", exc)
        await asyncio.sleep(1800)


def admin_only(m):
    return m.from_user is not None and m.from_user.id in ADMINS


@bot.message_handler(commands=["stats"], func=admin_only)
async def cmd_stats(m):
    u = q("SELECT COUNT(*) AS n, COALESCE(SUM(balance), 0) AS b FROM users", one=True)
    o = q("SELECT COUNT(*) AS c, COALESCE(SUM(price), 0) AS s FROM orders WHERE status = 'delivered'", one=True)
    d = q("SELECT COUNT(*) AS c FROM deposits WHERE status = 'pending'", one=True)["c"]
    n = q("SELECT COUNT(*) AS c FROM items", one=True)["c"]
    await bot.send_message(m.chat.id, f"Users: {u['n']}\nBalances held: {u['b']} ⭐\nTop-ups: {o['c']} ({o['s']} ⭐)\nGifts sent: {n}\nPending deposits: {d}")


@bot.message_handler(commands=["credit"], func=admin_only)
async def cmd_credit(m):
    a = (m.text or "").split()
    try:
        uid, amt = int(a[1]), int(a[2])
    except (IndexError, ValueError):
        await bot.send_message(m.chat.id, "Usage: /credit <user_id> <amount> (negative to deduct)")
        return
    if not q("UPDATE users SET balance = balance + ? WHERE id = ? AND balance + ? >= 0", (amt, uid, amt)):
        await bot.send_message(m.chat.id, "User not found or the balance would go negative.")
        return
    log(uid, amt, "admin adjustment")
    await bot.send_message(m.chat.id, f"Done: {amt:+d} ⭐ for {uid}.")


@bot.message_handler(commands=["broadcast"], func=admin_only)
async def cmd_broadcast(m):
    text = (m.text or "").partition(" ")[2].strip()
    if not text:
        await bot.send_message(m.chat.id, "Usage: /broadcast <message>")
        return
    sent = 0
    for r in q("SELECT id FROM users", many=True):
        try:
            await bot.send_message(r["id"], text)
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await bot.send_message(m.chat.id, f"Sent to {sent} users.")


@bot.message_handler(commands=["start"])
async def on_start(m):
    parts = (m.text or "").split(maxsplit=1)
    arg = parts[1] if len(parts) > 1 else ""
    ref = int(arg[4:]) if arg.startswith("ref_") and arg[4:].isdigit() else None
    uid = m.from_user.id
    register(uid, m.from_user.first_name or "", ref, lang_of_code(m.from_user.language_code))
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton(M(uid, "open"), web_app=types.WebAppInfo(WEBAPP_URL)))
    await bot.send_message(m.chat.id, M(uid, "start"), reply_markup=kb)


@bot.pre_checkout_query_handler(func=lambda c: True)
async def on_pre_checkout(c):
    o = q("SELECT * FROM orders WHERE payload = ? AND status = 'pending'", (c.invoice_payload,), one=True)
    ok = bool(o) and o["user_id"] == c.from_user.id and c.total_amount == o["price"] and c.currency == "XTR"
    await bot.answer_pre_checkout_query(c.id, ok=ok, error_message=None if ok else "This order is no longer valid.")


@bot.message_handler(content_types=["successful_payment"])
async def on_paid(m):
    sp = m.successful_payment
    o = q("SELECT * FROM orders WHERE payload = ? AND status = 'pending'", (sp.invoice_payload,), one=True)
    if not o:
        return
    q("UPDATE orders SET status = 'delivered', charge_id = ? WHERE payload = ?", (sp.telegram_payment_charge_id, o["payload"]))
    q("UPDATE users SET balance = balance + ? WHERE id = ?", (o["price"], o["user_id"]))
    log(o["user_id"], o["price"], "top-up")
    await bot.send_message(m.chat.id, M(o["user_id"], "topup_ok", n=o["price"]))


async def main():
    init()
    BOT_USER["name"] = (await bot.get_me()).username or ""
    AUTO["conn"], AUTO["owner"] = kv_get("biz_conn"), int(kv_get("biz_owner", "0") or 0)
    if AUTO["conn"]:
        try:
            if not getattr(await bot.get_business_connection(AUTO["conn"]), "is_enabled", True):
                AUTO["conn"] = ""
        except Exception:
            AUTO["conn"] = ""
    print("Auto gift deposits:", "ON" if AUTO["conn"] else "OFF (connect @%s in Telegram Business > Chatbots)" % SUPPORT)
    asyncio.create_task(maintenance())
    if not ADMINS:
        print("WARNING: ADMIN_IDS is empty, gift deposits are disabled.")
    app = web.Application()
    app.add_routes([
        web.get("/api/state", api_state), web.post("/api/spin", api_spin), web.post("/api/buy", api_buy),
        web.post("/api/topup", api_topup), web.post("/api/deposit", api_deposit),
        web.get("/health", lambda r: web.json_response({"ok": True})),
        web.get("/", lambda r: web.HTTPFound(WEBAPP_URL)),
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    await bot.infinity_polling(allowed_updates=["message", "pre_checkout_query", "callback_query", "business_connection", "business_message"])


if __name__ == "__main__":
    asyncio.run(main())
