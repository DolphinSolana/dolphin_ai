# bot.py — Dolphin AI (Knowledge + Channel Updates + Ready/Fallback)
import os, json, re, time, random
from pathlib import Path
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application, MessageHandler, CommandHandler, ContextTypes, filters
)

# ================== الإعدادات العامة ==================
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
if not TELEGRAM_TOKEN:
    raise SystemExit("❌ TELEGRAM_TOKEN مفقود في .env")

# مسارات الملفات
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

RESP_PATH = BASE_DIR / "responses.json"            # ردود جاهزة
PROFILE_PATH = BASE_DIR / "project_profile.json"   # ملف المعرفة
UPDATES_PATH = DATA_DIR / "updates.json"           # آخر الإعلانات من القناة

# ================== تحميل الردود الجاهزة ==================
with open(RESP_PATH, "r", encoding="utf-8") as f:
    RESP = json.load(f)

# ================== ملف المعرفة: قراءة وبناء سياق ==================
def load_profile() -> dict:
    try:
        with open(PROFILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

PROFILE = load_profile()

def build_context_from_profile(profile: dict) -> str:
    if not profile: return ""
    parts = []
    parts.append(f"Project: {profile.get('project_name','Dolphin Solana')}")
    parts.append(f"Chain: {profile.get('chain','Solana')}")
    parts.append(f"Mission: {profile.get('mission','')}")
    parts.append(f"Supply: {profile.get('supply','')}, Burn: {profile.get('burn','')}")
    ps = profile.get("presale", {})
    parts.append(f"Presale: date={ps.get('date','')}, platform={ps.get('platform','')}, reserved={ps.get('reserved_percent','')}")
    nft = profile.get("nft", {})
    parts.append(f"NFT: vision={nft.get('vision','')}, utility={', '.join(nft.get('utility', []))}")
    dao = profile.get("dao", {})
    parts.append(f"DAO: {dao.get('goal','')}")
    links = profile.get("links", {})
    parts.append(f"Official Links: website={links.get('website','')}, X={links.get('x','')}, TG(chat)={links.get('telegram_chat','')}, PresaleList={links.get('presale_list','')}")
    ca = profile.get("contract", {})
    parts.append(f"Contract: {ca.get('ca_status','')}")
    safety = profile.get("safety", [])
    if safety:
        parts.append("Safety: " + " | ".join(safety))
    return "\n".join(parts)

PROFILE_CTX = build_context_from_profile(PROFILE)

# ================== تحديثات القناة (Announcements) ==================
def load_updates():
    try:
        with open(UPDATES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"items": []}

def save_updates(store):
    with open(UPDATES_PATH, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)

UPDATES = load_updates()
MAX_UPDATES = 200

def recent_updates_snippet(n=5) -> str:
    items = sorted(UPDATES.get("items", []), key=lambda x: x.get("ts", 0), reverse=True)[:n]
    lines = []
    for it in items:
        ts = it.get("ts") or int(datetime.utcnow().timestamp())
        dt = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        text = it.get("text", "").strip()
        if text:
            lines.append(f"- [{dt} UTC] {text}")
    return "\n".join(lines)

async def channel_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التقاط منشورات القناة و تخزينها"""
    post = update.channel_post or update.edited_channel_post
    if not post or not post.text:
        return
    text = post.text.strip()
    ts = int((post.date or datetime.utcnow()).timestamp())
    item = {"ts": ts, "text": text, "chat_id": update.effective_chat.id}
    UPDATES["items"].append(item)
    UPDATES["items"] = UPDATES["items"][-MAX_UPDATES:]
    save_updates(UPDATES)

# ================== مرادفات + مطابقة الأوامر ==================
ALIASES = {
    "start": "/start", "/start": "/start",
    "help": "/help", "/help": "/help",
    "airdrop": "/airdrop", "/airdrop": "/airdrop",
    "giveaway": "/airdrop", "geveaway": "/airdrop", "give away": "/airdrop",
    "presale": "/presale", "/presale": "/presale",
    "buy": "/presale", "how to buy": "/presale", "price": "/presale",
    "nft": "/nft", "/nft": "/nft",
    "dao": "/dao", "/dao": "/dao",
    "ca": "/ca", "/ca": "/ca", "contract": "/ca",
    "website": "website", "/website": "website", "link": "website",
}

def normalize(text: str) -> str:
    t = (text or "").strip().lower()
    # إزالة mention للبوت
    t = re.sub(r"@[\w_]+", "", t).strip()
    # توحيد الأوامر: /help@Bot -> /help
    m = re.match(r"^/(\w+)", t)
    if m: return f"/{m.group(1)}"
    return re.sub(r"\s+", " ", t)

def get_reply(raw: str) -> Optional[str]:
    k = normalize(raw)
    if k in RESP: return RESP[k]
    if k.startswith("/") and k[1:] in RESP: return RESP[k[1:]]
    if k in ALIASES:
        canon = ALIASES[k]
        return RESP.get(canon) or RESP.get(canon.lstrip("/"))
    return None

# ================== OpenAI (اختياري) ==================
try:
    from openai import OpenAI
    ai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
except Exception:
    ai_client = None

AI_ON = bool(ai_client)
COOLDOWN = 8
_last_call: dict[int, float] = {}

async def ai_generate(prompt: str) -> str:
    if not AI_ON: return ""
    try:
        updates_txt = recent_updates_snippet(5)
        sys = (
            "You are Dolphin AI, assistant for the Dolphin Solana project.\n"
            "Always reply in the same language the user writes in.\n"
            "Use the following project profile and recent announcements as ground truth; "
            "if the user asks for the contract address, remind them it's not published until the presale date. "
            "Be concise, friendly, and avoid financial advice. Never ask for private keys/seed phrases.\n\n"
            f"== PROJECT PROFILE ==\n{PROFILE_CTX}\n"
            f"== RECENT ANNOUNCEMENTS (latest first) ==\n{updates_txt or '- (none)'}\n"
            "== END CONTEXT =="
        )
        r = ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system","content":sys},
                      {"role":"user","content":prompt}],
            temperature=0.5,
            max_tokens=450
        )
        return (r.choices[0].message.content or "").strip()
    except Exception:
        return "⚠️ Error: AI response failed."

# ================== Handlers ==================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(RESP.get("/start", "Welcome! Type /help"), disable_web_page_preview=True)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(RESP.get("/help", "Commands list"), disable_web_page_preview=True)

async def cmd_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not AI_ON:
        return await update.message.reply_text("⚠️ AI غير مفعّل (أضف OPENAI_API_KEY إلى .env).")
    prompt = " ".join(context.args).strip()
    if not prompt:
        return await update.message.reply_text("اكتب سؤالك بعد الأمر هكذا:\n/ai ما هي خطة الـ NFT؟")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
    text = await ai_generate(prompt)
    if len(text) > 3500: text = text[:3490] + "…"
    await update.message.reply_text(text, disable_web_page_preview=True)

async def router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    msg = update.message.text

    # 1) ردود ثابتة
    fixed = get_reply(msg)
    if fixed:
        return await update.message.reply_text(fixed, disable_web_page_preview=True)

    # 2) Fallback إلى AI
    if AI_ON:
        uid = update.message.from_user.id
        now = time.time()
        if now - _last_call.get(uid, 0) >= COOLDOWN:
            _last_call[uid] = now
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
            text = await ai_generate(msg)
            if text:
                if len(text) > 3500: text = text[:3490] + "…"
                return await update.message.reply_text(text, disable_web_page_preview=True)
    # وإلا تجاهل بهدوء

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # أوامر
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("ai", cmd_ai))

    # منشورات القناة (Announcements)
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, channel_post_handler))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_CHANNEL_POST, channel_post_handler))

    # رسائل المستخدمين (نصوص + أوامر غير معروفة)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, router))
    app.add_handler(MessageHandler(filters.COMMAND, router))

    print(f"🤖 Bot is running... (AI: {'ON' if AI_ON else 'OFF'}) | Knowledge + Announcements enabled")
    app.run_polling()

if __name__ == "__main__":
    main()