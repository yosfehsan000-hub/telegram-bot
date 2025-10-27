import os
import json
import random
import threading
from datetime import datetime, timedelta, time as timeobj
from typing import Any, Dict, List, Optional
from io import BytesIO
import base64
import requests
import telebot
from telebot import types
import jdatetime
import pytz

# --------------------- CONFIG ---------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8468384781:AAH9MesLFZTZsokbn_2OO4Nf-w4fFH-DaZE")
DEFAULT_ADMIN_ID = 7050127262  # e.g. 123456789
BOT_NAME = "selnomoon_bot"     # used in captions

# AstronomyAPI (optional)
ASTRONOMY_APP_ID = os.environ.get("ASTRONOMY_APP_ID", "47ea7390-91a1-412d-916a-00ae2f3a109c")
ASTRONOMY_APP_SECRET = os.environ.get("ASTRONOMY_APP_SECRET", "0a375fb62247e1e51bd765088d509ec2a09f0f839c2e878f7c79065ce3012cf8815ef6679a791d7f4bcf613fff21979a26591421411fe9755c04d8e1373714fddc36732bdd54eb9aaa8d8f3ee7d0c408b955b54a5b113c0963b19974e29da41c830edde8cc128433072e9d269a5f4257")

# Tehran coords & TZ
TEHRAN_LAT = 35.6892
TEHRAN_LON = 51.3890
TEHRAN_TZ = pytz.timezone("Asia/Tehran")

# --------------------- INIT BOT & FILES ---------------------
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

os.makedirs("data", exist_ok=True)
os.makedirs("data/images", exist_ok=True)
os.makedirs("data/music", exist_ok=True)

def load_json(path: str, default: Any):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path: str, data: Any):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

# --------------------- PERSISTENT DATA ---------------------
admins: List[int] = load_json("data/admins.json", [DEFAULT_ADMIN_ID] if DEFAULT_ADMIN_ID else [])
admins = [a for a in admins if a]

FORTUNE_MONTHS = [
    "فروردین","اردیبهشت","خرداد","تیر","مرداد","شهریور",
    "مهر","آبان","آذر","دی","بهمن","اسفند"
]

def ensure_fortunes(data:any):
    # Normalize to {"فال روزانه": [...], "فال ماه تولد": {month: [..]}} structure.
    if isinstance(data, list):
        data = {"فال روزانه": data}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("فال روزانه", [])
    data.setdefault("فال ماه تولد", {})
    if not isinstance(data["فال ماه تولد"], dict):
        data["فال ماه تولد"] = {}
    for m in FORTUNE_MONTHS:
        data["فال ماه تولد"].setdefault(m, [])
    return data

fortunes = ensure_fortunes(load_json("data/fortunes.json", [
    "امروز روز خوبی برای شروع پروژه جدید است.",
    "امروز ممکن است با یک فرد مهم ملاقات کنید.",
    "امروز سعی کنید آرامش خود را حفظ کنید."
]))
save_json("data/fortunes.json", fortunes)

# images: category -> [filepaths]
moon_images: Dict[str, List[str]] = load_json("data/moon_images.json", {})
if isinstance(moon_images, list):
    moon_images = {"عمومی": moon_images}
    save_json("data/moon_images.json", moon_images)

# music: store saved local filenames or telegram file_ids
music_playlist: List[str] = load_json("data/music_playlist.json", [])

# texts
text_bio: List[str] = load_json("data/text_bio.json", [
    "سلام! این یک متن نمونه است.",
    "هر روز با Moon لحظه‌های زیبا داشته باشید."
])
save_json("data/text_bio.json", text_bio)

# new texts
new_texts: List[str] = load_json("data/new_texts.json", [])

# per-user playlist {str(user_id): [indexes]}
user_playlists: Dict[str, List[int]] = load_json("data/user_playlists.json", {})

# per-user listened music indexes {str(user_id): [indexes]}
user_listened: Dict[str, List[int]] = load_json("data/user_listened.json", {})

# likes: {section: {key: [user_ids]}}
likes: Dict[str, Dict[str, List[str]]] = load_json("data/likes.json", {})

# feedback inbox: {user_id: [messages]}
feedbacks: Dict[str, List[str]] = load_json("data/feedbacks.json", {})

# about us
about_us: List[str] = load_json("data/about_us.json", ["ربات Moon برای سرگرمی و مدیریت محتوا ساخته شده است."])
save_json("data/about_us.json", about_us)

# users registry for broadcasts & anon chat
users: Dict[str, Dict[str, str]] = load_json("data/users.json", {})

# anonymous chat structures
active_chats: Dict[str, Dict[str, Optional[int]]] = {}  # code -> {"user1": id, "user2": id or None}
user_chat_map: Dict[int, str] = {}  # user_id -> code (only when paired)

# simple state machine for admin (used minimally; most flows use next_step handlers)
user_states: Dict[int, str] = {}  # user_id -> state

def set_state(uid: int, state: str): user_states[uid] = state
def get_state(uid: int) -> str: return user_states.get(uid, "")
def clear_state(uid: int): user_states.pop(uid, None)

# --------------------- UTILITIES ---------------------
def is_admin(uid: int) -> bool:
    return uid in admins

def now_tehran() -> datetime:
    return datetime.now(TEHRAN_TZ)

def jalali_str(dt: datetime) -> str:
    j = jdatetime.date.fromgregorian(date=dt.date())
    return f"{j.year}/{j.month:02d}/{j.day:02d}"

def add_like(section: str, key: str, user_id: int) -> bool:
    s = likes.setdefault(section, {})
    lst = s.setdefault(key, [])
    uid = str(user_id)
    if uid in lst:
        return False
    lst.append(uid)
    save_json("data/likes.json", likes)
    return True

def get_likes(section: str, key: str) -> int:
    return len(likes.get(section, {}).get(key, []))

def ensure_user(uid: int, from_user=None):
    """Ensure the user entry exists. If `from_user` (telegram User) is provided,
    update name, username, joined_at and set active=True."""

    su = str(uid)
    changed = False
    if su not in users:
        users[su] = {"anon_code": su}
        users[su]["joined_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        changed = True
    if from_user:
        name = (getattr(from_user, 'first_name', '') or '')
        if getattr(from_user, 'last_name', None):
            name = (name + ' ' + from_user.last_name).strip()
        if users[su].get("name") != name:
            users[su]["name"] = name
            changed = True
        uname = getattr(from_user, 'username', '') or ''
        if users[su].get("username") != uname:
            users[su]["username"] = uname
            changed = True
        if users[su].get("active") is not True:
            users[su]["active"] = True
            changed = True
        users[su].setdefault("joined_at", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
    if changed:
        save_json("data/users.json", users)

def end_chat_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("❌ پایان چت")
    return kb

def main_menu_kb(user_id: int) -> types.ReplyKeyboardMarkup:
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    buttons = [
        "تصویر زنده ماه", "تاریخ امروز",
        "عکس ها", "فال",
        "موزیک و پلی‌لیست", "تکست و بیو",
        "چت ناشناس", "انتقادات و پیشنهادات",
        "درباره ما"
    ]
    if is_admin(user_id):
        buttons.append("پنل مدیریت")
    kb.add(*buttons)
    return kb

def back_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("⬅️ بازگشت")
    return kb

# --------------------- START HANDLER ---------------------
@bot.message_handler(commands=["start"])
def cmd_start(m: types.Message):
    uid = m.chat.id
    ensure_user(uid, m.from_user)
    bot.send_message(uid, f"سلام {m.from_user.first_name or ''} \nبه ربات ✨🌙Moon خوش آمدید.", reply_markup=main_menu_kb(uid))

    # handle anon param if provided (joining another user's chat)
    parts = (m.text or "").split()
    if len(parts) > 1 and parts[1].startswith("anon_"):
        code = parts[1][5:]
        chat = active_chats.get(code)
        if chat and chat.get("user2") is None:
            if chat["user1"] == uid:
                bot.send_message(uid, "❌ نمی‌توانید با خودتان چت کنید.", reply_markup=main_menu_kb(uid))
                return
            chat["user2"] = uid
            user_chat_map[uid] = code
            user_chat_map[chat["user1"]] = code
            bot.send_message(chat["user1"], "✅ یک کاربر به چت ناشناس شما متصل شد.", reply_markup=end_chat_kb())
            bot.send_message(uid, "✅ به چت ناشناس متصل شدی.", reply_markup=end_chat_kb())
        else:
            bot.send_message(uid, "لینک نامعتبر یا استفاده شده.", reply_markup=main_menu_kb(uid))

# --------------------- GENERAL MESSAGE HANDLER ---------------------
@bot.message_handler(func=lambda m: True, content_types=['text','photo','audio','document','voice','video','sticker','animation'])
def handle_all(m: types.Message):
    uid = m.chat.id
    ensure_user(uid, m.from_user)

    # 1) If user is currently in an active anonymous chat, forward messages
    if uid in user_chat_map:
        code = user_chat_map.get(uid)
        chat = active_chats.get(code)
        if not chat:
            user_chat_map.pop(uid, None)
            bot.send_message(uid, "چت پیدا نشد.", reply_markup=main_menu_kb(uid))
            return

        # end chat command/button
        if m.content_type == 'text' and (m.text or "").strip() == "❌ پایان چت":
            leave_chat(uid)
            return

        partner = chat["user1"] if chat["user1"] != uid else chat.get("user2")
        if partner:
            try:
                bot.copy_message(partner, uid, m.message_id)
            except Exception:
                pass
        return  # do not process menu while in chat

    # 2) Non-text content not in anon chat: ignore for menus
    if m.content_type != 'text':
        return

    text = (m.text or "").strip()

    # 3) MAIN MENU HANDLERS (prioritized)
    if text == "تاریخ امروز":
        send_today(uid); return
    elif text == "تصویر زنده ماه":
        send_moon_image(uid); return
    elif text == "فال":
        show_fortune_categories(uid); return
    elif text == "عکس ها":
        show_image_categories(uid); return
    elif text == "تکست و بیو":
        show_text_menu(uid); return
    elif text == "موزیک و پلی‌لیست":
        show_music_menu(uid); return
    elif text == "چت ناشناس":
        start_anon(uid); return
    elif text == "انتقادات و پیشنهادات":
        bot.send_message(uid, "متن انتقاد/پیشنهاد خود را ارسال کنید (برای لغو از دکمه ⬅️ بازگشت استفاده کن).", reply_markup=back_kb())
        bot.register_next_step_handler_by_chat_id(uid, receive_feedback); return
    elif text == "درباره ما":
        about = load_json("data/about_us.json", ["ربات Moon — ساخته شده با ❤️"])
        bot.send_message(uid, "\n".join(about), reply_markup=main_menu_kb(uid)); return
    elif text == "پنل مدیریت" and is_admin(uid):
        show_admin_panel(uid); return

    # 4) ADMIN PANEL STATE (if still active, handle admin choices)
    state = get_state(uid)
    if state == "admin_panel":
        admin_panel_choice(m)
        return

    # 5) fallback
    bot.send_message(uid, "گزینه نامعتبر — منو را انتخاب کن.", reply_markup=main_menu_kb(uid))

def send_today(uid: int):
    now = now_tehran()
    jalali = jalali_str(now)
    greg = now.strftime("%d %B %Y")
    t = now.strftime("%H:%M:%S")
    weekday_names = ["دوشنبه","سه‌شنبه","چهارشنبه","پنجشنبه","جمعه","شنبه","یکشنبه"]
    weekday = weekday_names[now.weekday()]
    msg = (
        "━━━━━━━ ‌‌ ‌📅 تقویم روز ━━━━━━━━\n\n"
        f"✨ ساعت: {t}  \n\n"
        f"🪐 تاریخ میلادی: {greg} \n\n"
        f"🌙 تاریخ شمسی: {jalali}  \n\n"
        f"☀️ روز هفته: {weekday}  \n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━  \n"
        "روز خوبی داشته باشید✨\n"
        f"@{BOT_NAME}"
    )
    bot.send_message(uid, msg, reply_markup=main_menu_kb(uid))

# --------------------- FORTUNES (User-side) ---------------------
# --------------------- FORTUNES (User-side) ---------------------
def show_fortune_categories(uid: int):
    ikb = types.InlineKeyboardMarkup(row_width=2)
    ikb.add(
        types.InlineKeyboardButton("📅 فال روزانه", callback_data="fortune::daily"),
        types.InlineKeyboardButton("🎂 فال ماه تولد", callback_data="fortune::monthly")
    )
    bot.send_message(uid, "✨ نوع فال را انتخاب کنید:", reply_markup=ikb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("fortune::"))
def cb_fortune(c: types.CallbackQuery):
    uid = c.message.chat.id
    if c.data == "fortune::daily":
        show_daily_fortune(uid)
    elif c.data == "fortune::monthly":
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for month in FORTUNE_MONTHS:
            kb.add(month)
        kb.add("⬅️ بازگشت")
        bot.send_message(uid, "ماه تولد خود را انتخاب کنید:", reply_markup=kb)
        bot.register_next_step_handler_by_chat_id(uid, show_monthly_fortune)
    bot.answer_callback_query(c.id)

def show_daily_fortune(uid: int):
    lst = fortunes.get("فال روزانه", [])
    if not lst:
        bot.send_message(uid, "فال روزانه موجود نیست.", reply_markup=main_menu_kb(uid))
        return
    text = random.choice(lst)
    bot.send_message(uid, f"✨ فال امروز:\n\n{text}\n\n@{BOT_NAME}", reply_markup=main_menu_kb(uid))

def show_monthly_fortune(m: types.Message):
    uid = m.chat.id
    month = (m.text or "").strip()
    if month == "⬅️ بازگشت":
        show_fortune_categories(uid)
        return
    lst = fortunes.get("فال ماه تولد", {}).get(month, [])
    if not lst:
        bot.send_message(uid, f"برای {month} فال ثبت نشده.", reply_markup=main_menu_kb(uid))
        return
    text = random.choice(lst)
    bot.send_message(uid, f"✨ فال {month}:\n\n{text}\n\n@{BOT_NAME}", reply_markup=main_menu_kb(uid))

# --------------------- ADMIN: FORTUNE MANAGEMENT ---------------------
def manage_fortunes_root(uid: int):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📅 مدیریت فال روزانه", "🎂 مدیریت فال ماه تولد", "⬅️ بازگشت")
    bot.send_message(uid, "مدیریت فال‌ها:", reply_markup=kb)
    bot.register_next_step_handler_by_chat_id(uid, manage_fortunes_choice)

def manage_fortunes_choice(m: types.Message):
    uid = m.chat.id
    txt = (m.text or "").strip()
    if txt == "📅 مدیریت فال روزانه":
        manage_specific_fortunes(uid, "فال روزانه")
    elif txt == "🎂 مدیریت فال ماه تولد":
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for month in FORTUNE_MONTHS: kb.add(month)
        kb.add("⬅️ بازگشت")
        bot.send_message(uid, "یک ماه را انتخاب کنید:", reply_markup=kb)
        bot.register_next_step_handler_by_chat_id(uid, choose_birth_month)
    else:
        show_admin_panel(uid)

def manage_specific_fortunes(uid: int, category: str):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ افزودن", "➕ افزودن چندتا", "🗑 حذف", "⬅️ بازگشت")
    bot.send_message(uid, f"مدیریت {category}:", reply_markup=kb)
    bot.register_next_step_handler_by_chat_id(uid, lambda m: specific_fortunes_choice(m, category))

def specific_fortunes_choice(m: types.Message, category: str):
    uid = m.chat.id
    txt = (m.text or "").strip()
    if txt == "➕ افزودن":
        bot.send_message(uid, f"متن {category} جدید را ارسال کنید:", reply_markup=back_kb())
        bot.register_next_step_handler_by_chat_id(uid, lambda m: add_new_fortune(m, category))
    elif txt == "➕ افزودن چندتا":
        bot.send_message(uid, f"چند متن {category} را یکی‌یکی بفرست. وقتی تمام شد، کلمه «اتمام» را بفرست.", reply_markup=back_kb())
        bot.register_next_step_handler_by_chat_id(uid, lambda m: add_new_fortune_bulk(m, category))
    elif txt == "🗑 حذف":
        # Build list to delete from based on category
        if category.startswith("فال ماه تولد/"):
            month = category.split("/",1)[1]
            entries = fortunes.get("فال ماه تولد", {}).get(month, [])
        else:
            entries = fortunes.get(category, [])
        if not entries:
            bot.send_message(uid, f"{category} خالی است.", reply_markup=main_menu_kb(uid))
            return
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for i, ftxt in enumerate(entries, start=1):
            preview = ftxt if len(ftxt) <= 50 else ftxt[:47]+"..."
            kb.add(f"{i}. {preview}")
        kb.add("⬅️ بازگشت")
        bot.send_message(uid, f"کدام {category} حذف شود؟", reply_markup=kb)
        bot.register_next_step_handler_by_chat_id(uid, lambda m: delete_fortune(m, category))
    else:
        manage_fortunes_root(uid)

def choose_birth_month(m: types.Message):
    uid = m.chat.id
    month = (m.text or "").strip()
    if month == "⬅️ بازگشت":
        manage_fortunes_root(uid)
        return
    fortunes.setdefault("فال ماه تولد", {}).setdefault(month, [])
    manage_specific_fortunes(uid, f"فال ماه تولد/{month}")

def add_new_fortune(m: types.Message, category: str):
    uid = m.chat.id
    txt = (m.text or "").strip()
    if txt == "⬅️ بازگشت":
        manage_fortunes_root(uid)
        return
    if category.startswith("فال ماه تولد/"):
        month = category.split("/",1)[1]
        fortunes.setdefault("فال ماه تولد", {}).setdefault(month, []).append(txt)
    else:
        fortunes.setdefault(category, []).append(txt)
    save_json("data/fortunes.json", fortunes)
    bot.send_message(uid, "فال اضافه شد.", reply_markup=main_menu_kb(uid))

def add_new_fortune_bulk(m: types.Message, category: str):
    uid = m.chat.id
    txt = (m.text or "").strip()
    if txt in ["⬅️ بازگشت", "اتمام"]:
        bot.send_message(uid, "پایان افزودن گروهی.", reply_markup=main_menu_kb(uid))
        save_json("data/fortunes.json", fortunes)
        return
    if category.startswith("فال ماه تولد/"):
        month = category.split("/",1)[1]
        fortunes.setdefault("فال ماه تولد", {}).setdefault(month, []).append(txt)
    else:
        fortunes.setdefault(category, []).append(txt)
    # keep listening
    bot.send_message(uid, "✅ ثبت شد. مورد بعدی را بفرست یا «اتمام».")
    bot.register_next_step_handler_by_chat_id(uid, lambda mm: add_new_fortune_bulk(mm, category))

def delete_fortune(m: types.Message, category: str):
    uid = m.chat.id
    txt = (m.text or "").strip()
    if txt == "⬅️ بازگشت":
        manage_fortunes_root(uid)
        return
    try:
        idx = int(txt.split(".")[0]) - 1
        if category.startswith("فال ماه تولد/"):
            month = category.split("/",1)[1]
            lst = fortunes.get("فال ماه تولد", {}).get(month, [])
            if 0 <= idx < len(lst):
                lst.pop(idx)
        else:
            lst = fortunes.get(category, [])
            if 0 <= idx < len(lst):
                lst.pop(idx)
        save_json("data/fortunes.json", fortunes)
        bot.send_message(uid, "فال حذف شد.", reply_markup=main_menu_kb(uid))
        return
    except Exception:
        pass
    bot.send_message(uid, "خطا یا مورد یافت نشد.", reply_markup=main_menu_kb(uid))

# --------------------- TEXT/BIO ---------------------
def show_text_menu(uid: int):
    ikb = types.InlineKeyboardMarkup()
    ikb.add(types.InlineKeyboardButton("📂 همه‌ی تکست‌ها", callback_data="text::all::0"))
    ikb.add(types.InlineKeyboardButton("🆕 تکست‌های جدید", callback_data="text::new::0"))
    ikb.add(types.InlineKeyboardButton("📤 ارسال تمامی تکست‌ها", callback_data="text::sendall::0"))
    bot.send_message(uid, "یکی را انتخاب کنید:", reply_markup=ikb)


def send_text_all(uid: int, index: int):
    if not text_bio:
        bot.send_message(uid, "هنوز متنی ثبت نشده.", reply_markup=main_menu_kb(uid))
        return
    if index >= len(text_bio):
        bot.send_message(uid, "پایان لیست تکست‌ها.", reply_markup=main_menu_kb(uid))
        return

    content = text_bio[index]
    likes_count = get_likes("text", str(index))

    ikb = types.InlineKeyboardMarkup()
    ikb.add(types.InlineKeyboardButton(f"🤍({likes_count})", callback_data=f"like::text::{index}"))
    if index + 1 < len(text_bio):
        ikb.add(types.InlineKeyboardButton("➡️ بعدی", callback_data=f"text::all::{index + 1}"))
    bot.send_message(uid, f"{content}\n\n@{BOT_NAME}", reply_markup=ikb)


def send_text_new(uid: int, index: int = 0):
    if not new_texts:
        bot.send_message(uid, "تکست جدیدی وجود ندارد.", reply_markup=main_menu_kb(uid))
        return

    for i, content in enumerate(new_texts):
        likes_count = get_likes("newtext", str(i))
        ikb = types.InlineKeyboardMarkup()
        ikb.add(types.InlineKeyboardButton(f"🤍({likes_count})", callback_data=f"like::newtext::{i}"))
        bot.send_message(uid, f"{content}\n\n@{BOT_NAME}", reply_markup=ikb)

    text_bio.extend(new_texts)
    save_json("data/text_bio.json", text_bio)
    new_texts.clear()
    save_json("data/new_texts.json", new_texts)
    bot.send_message(uid, "✅ همه‌ی تکست‌های جدید نمایش داده شد.", reply_markup=main_menu_kb(uid))


# 🆕 تابع جدید برای ارسال تمامی تکست‌ها یکجا
def send_all_texts(uid: int):
    if not text_bio:
        bot.send_message(uid, "هنوز هیچ متنی ثبت نشده.", reply_markup=main_menu_kb(uid))
        return

    bot.send_message(uid, f"📤 در حال ارسال {len(text_bio)} تکست ...")
    for i, content in enumerate(text_bio):
        likes_count = get_likes("text", str(i))
        ikb = types.InlineKeyboardMarkup()
        ikb.add(types.InlineKeyboardButton(f"🤍({likes_count})", callback_data=f"like::text::{i}"))
        bot.send_message(uid, f"{content}\n\n@{BOT_NAME}", reply_markup=ikb)

    bot.send_message(uid, "✅ همه‌ی تکست‌ها ارسال شدند.", reply_markup=main_menu_kb(uid))


@bot.callback_query_handler(func=lambda c: c.data.startswith("text::"))
def cb_text(c: types.CallbackQuery):
    _, mode, idx = c.data.split("::")
    idx = int(idx)
    if mode == "all":
        send_text_all(c.message.chat.id, idx)
    elif mode == "new":
        send_text_new(c.message.chat.id, idx)
    elif mode == "sendall":
        send_all_texts(c.message.chat.id)

    bot.answer_callback_query(c.id)


# --------------------- MUSIC & PLAYLIST ---------------------
def show_music_menu(uid:int):
    ikb = types.InlineKeyboardMarkup(row_width=2)
    ikb.add(types.InlineKeyboardButton("🎶 همه موزیک‌ها", callback_data="music::all"))
    ikb.add(types.InlineKeyboardButton("🎧 پلی‌لیست من", callback_data="music::playlist"))
    ikb.add(types.InlineKeyboardButton("🎲 موزیک تصادفی", callback_data="music::random"))
    bot.send_message(uid, "یک گزینه را انتخاب کنید:", reply_markup=ikb)

def send_music_with_buttons(uid:int, idx:int, from_playlist:bool=False):
    if idx < 0 or idx >= len(music_playlist):
        return
    item = music_playlist[idx]
    likes_count = get_likes("music", str(idx))
    ikb = types.InlineKeyboardMarkup()
    ikb.add(types.InlineKeyboardButton(f"🤍({likes_count})", callback_data=f"like::music::{idx}"))
    if from_playlist:
        ikb.add(types.InlineKeyboardButton("🗑 حذف از پلی‌لیست", callback_data=f"playlist::remove::{idx}"))
    else:
        ikb.add(types.InlineKeyboardButton("➕ افزودن به پلی‌لیست", callback_data=f"playlist::add::{idx}"))
    try:
        if os.path.exists(item):
            with open(item, "rb") as f:
                bot.send_audio(uid, f, reply_markup=ikb)
        else:
            bot.send_audio(uid, item, reply_markup=ikb)  # assume file_id
    except Exception:
        pass

@bot.callback_query_handler(func=lambda c: c.data.startswith("music::"))
def cb_music(c: types.CallbackQuery):
    action = c.data.split("::")[1]
    uid = c.message.chat.id
    su = str(c.from_user.id)
    if action == "all":
        if not music_playlist:
            bot.send_message(uid, "هیچ موزیکی موجود نیست.")
            bot.answer_callback_query(c.id); return
        for i in range(len(music_playlist)):
            send_music_with_buttons(uid, i)
    elif action == "new":
        listened = set(user_listened.get(su, []))
        new_idxs = [i for i in range(len(music_playlist)) if i not in listened]
        if not new_idxs:
            bot.send_message(uid, " شما همه موزیک‌های جدید را شنیده‌اید،روی همه ی موزیک ها کلیک کنید!", reply_markup=main_menu_kb(uid))
            bot.answer_callback_query(c.id); return
        for i in new_idxs:
            send_music_with_buttons(uid, i); listened.add(i)
        user_listened[su] = sorted(list(listened))
        save_json("data/user_listened.json", user_listened)
        bot.send_message(uid, "✅ موزیک‌های جدید برای شما ارسال شد.", reply_markup=main_menu_kb(uid))
    elif action == "playlist":
        pl = user_playlists.get(su, [])
        if not pl:
            bot.send_message(uid, "پلی‌لیست شما خالی است.", reply_markup=main_menu_kb(uid))
            bot.answer_callback_query(c.id); return
        for i in pl:
            send_music_with_buttons(uid, i, from_playlist=True)
    elif action == "random":
        if not music_playlist:
            bot.send_message(uid, "هیچ موزیکی موجود نیست.")
            bot.answer_callback_query(c.id); return
        idx = random.randint(0, len(music_playlist)-1)
        send_music_with_buttons(uid, idx)
    bot.answer_callback_query(c.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith("playlist::"))
def cb_playlist(c: types.CallbackQuery):
    parts = c.data.split("::")
    action = parts[1]
    idx = int(parts[2])
    su = str(c.from_user.id)
    if action == "add":
        user_playlists.setdefault(su, [])
        if idx not in user_playlists[su]:
            user_playlists[su].append(idx)
            save_json("data/user_playlists.json", user_playlists)
            bot.answer_callback_query(c.id, "به پلی‌لیست اضافه شد ✅")
        else:
            bot.answer_callback_query(c.id, "این موزیک قبلاً در پلی‌لیست شما هست.")
    elif action == "remove":
        if idx in user_playlists.get(su, []):
            user_playlists[su].remove(idx)
            save_json("data/user_playlists.json", user_playlists)
            bot.answer_callback_query(c.id, "از پلی‌لیست شما حذف شد ✅")
        else:
            bot.answer_callback_query(c.id, "این موزیک در پلی‌لیست شما نیست.")

# --------------------- IMAGES ---------------------
def show_image_categories(uid: int):
    if not moon_images:
        bot.send_message(uid, "هیچ دسته‌ای موجود نیست.", reply_markup=main_menu_kb(uid))
        return
    ikb = types.InlineKeyboardMarkup()
    for cat in moon_images.keys():
        ikb.add(types.InlineKeyboardButton(cat, callback_data=f"img::cat::{cat}"))
    bot.send_message(uid, "دسته‌ای انتخاب کنید:", reply_markup=ikb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("img::"))
def cb_img(c: types.CallbackQuery):
    parts = c.data.split("::")

    if parts[1] == "cat":
        cat = parts[2]
        send_image_from_category(c.message.chat.id, cat, 0)

    elif parts[1] == "next":
        cat = parts[2]
        idx = int(parts[3])
        send_image_from_category(c.message.chat.id, cat, idx)

    elif parts[1] == "sendall":
        cat = parts[2]
        send_all_images_from_category(c.message.chat.id, cat)

    bot.answer_callback_query(c.id)


def send_image_from_category(uid: int, category: str, idx: int):
    imgs = [p for p in moon_images.get(category, []) if os.path.exists(p)]
    if not imgs:
        bot.send_message(uid, "عکسی در این دسته موجود نیست.", reply_markup=main_menu_kb(uid))
        return
    if idx >= len(imgs):
        bot.send_message(uid, "پایان تصاویر.", reply_markup=main_menu_kb(uid))
        return

    ikb = types.InlineKeyboardMarkup()
    # اگر عکس اول دسته است، دکمه ارسال کل عکس‌های دسته را هم اضافه کن
    if idx == 0:
        ikb.add(types.InlineKeyboardButton("📸 ارسال کل عکس‌های این دسته", callback_data=f"img::sendall::{category}"))

    if idx + 1 < len(imgs):
        ikb.add(types.InlineKeyboardButton("➡️ عکس بعدی", callback_data=f"img::next::{category}::{idx + 1}"))

    key = f"{category}::{idx}"
    ikb.add(types.InlineKeyboardButton(f"🤍({get_likes('image', key)})", callback_data=f"like::image::{key}"))

    try:
        with open(imgs[idx], "rb") as f:
            bot.send_photo(uid, f, caption=f"عکس {idx + 1} از {len(imgs)} — دسته: {category}", reply_markup=ikb)
    except Exception:
        bot.send_message(uid, "خطا در ارسال تصویر.", reply_markup=main_menu_kb(uid))


# 🆕 تابع جدید برای ارسال کل عکس‌های یک دسته
def send_all_images_from_category(uid: int, category: str):
    imgs = [p for p in moon_images.get(category, []) if os.path.exists(p)]
    if not imgs:
        bot.send_message(uid, "هیچ عکسی در این دسته وجود ندارد.", reply_markup=main_menu_kb(uid))
        return

    bot.send_message(uid, f"📤 در حال ارسال {len(imgs)} عکس از دسته: {category}")

    for idx, img_path in enumerate(imgs):
        try:
            with open(img_path, "rb") as f:
                key = f"{category}::{idx}"
                ikb = types.InlineKeyboardMarkup()
                ikb.add(types.InlineKeyboardButton(f"🤍({get_likes('image', key)})", callback_data=f"like::image::{key}"))
                bot.send_photo(uid, f, caption=f"📸 عکس {idx + 1} از {len(imgs)} — دسته: {category}", reply_markup=ikb)
        except Exception:
            bot.send_message(uid, f"❌ خطا در ارسال عکس {idx + 1}.", reply_markup=main_menu_kb(uid))

    bot.send_message(uid, "✅ تمام عکس‌های این دسته ارسال شدند.", reply_markup=main_menu_kb(uid))


# --------------------- LIKES GENERAL ---------------------
@bot.callback_query_handler(func=lambda c: c.data.startswith("like::"))
def cb_like(c: types.CallbackQuery):
    try:
        _, section, key = c.data.split("::", 2)
    except Exception:
        bot.answer_callback_query(c.id, "خطا در داده‌ها.")
        return

    added = add_like(section, key, c.from_user.id)
    if not added:
        bot.answer_callback_query(c.id, "شما قبلاً لایک کرده‌اید.")
        return

    # best-effort: update the label if possible
    try:
        msg = c.message
        new_markup = types.InlineKeyboardMarkup()
        if msg.reply_markup and hasattr(msg.reply_markup, "keyboard"):
            for row in msg.reply_markup.keyboard:
                buttons = []
                for b in row:
                    cbd = getattr(b, "callback_data", None)
                    text = getattr(b, "text", "")
                    if cbd == c.data:
                        text = f"🤍({get_likes(section, key)})"
                    buttons.append(types.InlineKeyboardButton(text, callback_data=cbd))
                new_markup.add(*buttons)
            bot.edit_message_reply_markup(chat_id=msg.chat.id, message_id=msg.message_id, reply_markup=new_markup)
    except Exception:
        pass

    bot.answer_callback_query(c.id, "🤍 لایک شما با موفقیت ثبت شد")

# --------------------- ANONYMOUS CHAT ---------------------
def start_anon(uid:int):
    """Generate link and mark the user as the waiting owner (without trapping them)."""
    ensure_user(uid)
    code = users[str(uid)]["anon_code"]
    # prepare waiting room for this code
    if code not in active_chats:
        active_chats[code] = {"user1": uid, "user2": None}
    else:
        active_chats[code]["user1"] = uid
        active_chats[code]["user2"] = active_chats[code].get("user2", None)

    username = bot.get_me().username or BOT_NAME
    link = f"https://t.me/{username}?start=anon_{code}"
    bot.send_message(uid, f"لینک ناشناس شما:\n{link}\nبا اشتراک‌گذاری این لینک، دیگران می‌توانند با شما به طور ناشناس چت کنند.\n"f"وقتی کسی وصل شود، دکمه پایان چت برای هر دو نمایش داده می‌شود.", reply_markup=main_menu_kb(uid))

def leave_chat(uid:int):
    """End the chat for both sides and clean maps safely."""
    code = user_chat_map.pop(uid, None)
    if not code:
        bot.send_message(uid, "شما در چتی نیستید.", reply_markup=main_menu_kb(uid))
        return
    chat = active_chats.pop(code, None)
    other = None
    if chat:
        if chat.get("user1") and chat["user1"] != uid:
            other = chat["user1"]
        elif chat.get("user2") and chat["user2"] != uid:
            other = chat["user2"]
    if other:
        try:
            user_chat_map.pop(other, None)
            bot.send_message(other, "طرف مقابل چت را پایان داد.", reply_markup=main_menu_kb(other))
        except Exception:
            pass
    bot.send_message(uid, "چت تمام شد.", reply_markup=main_menu_kb(uid))

# --------------------- FEEDBACK ---------------------
def receive_feedback(m: types.Message):
    uid = m.chat.id
    txt = (m.text or "").strip()
    if txt == "⬅️ بازگشت" or txt == "❌ لغو":
        bot.send_message(uid, "لغو شد.", reply_markup=main_menu_kb(uid))
        return
    feedbacks.setdefault(str(uid), []).append(txt)
    save_json("data/feedbacks.json", feedbacks)
    bot.send_message(uid, "پیام شما ثبت شد. متشکریم!", reply_markup=main_menu_kb(uid))

def show_feedbacks_for_admin(uid:int):
    if not feedbacks:
        bot.send_message(uid, "صندوق پیام‌ها خالی است.", reply_markup=main_menu_kb(uid))
        return
    for user_id, msgs in feedbacks.items():
        for i, m in enumerate(msgs):
            k = types.InlineKeyboardMarkup()
            k.add(types.InlineKeyboardButton("✉️ پاسخ", callback_data=f"fb::reply::{user_id}"),
                  types.InlineKeyboardButton("🗑 حذف", callback_data=f"fb::del::{user_id}::{i}"))
            bot.send_message(uid, f"پیام از {user_id}:\n{m}", reply_markup=k)

@bot.callback_query_handler(func=lambda c: c.data.startswith("fb::"))
def cb_fb(c: types.CallbackQuery):
    parts = c.data.split("::")
    cmd = parts[1]
    if cmd == "reply":
        target = parts[2]
        bot.send_message(c.message.chat.id, f"متن پاسخ را برای کاربر {target} بنویسید:", reply_markup=back_kb())
        bot.register_next_step_handler_by_chat_id(c.message.chat.id, lambda m: send_feedback_reply(m, target))
    elif cmd == "del":
        target = parts[2]; idx = int(parts[3])
        lst = feedbacks.get(target, [])
        if 0 <= idx < len(lst):
            lst.pop(idx)
            if not lst:
                feedbacks.pop(target, None)
            save_json("data/feedbacks.json", feedbacks)
            bot.edit_message_text("پیام حذف شد.", c.message.chat.id, c.message.message_id)
            bot.answer_callback_query(c.id, "حذف شد.")
        else:
            bot.answer_callback_query(c.id, "خطا.")

def send_feedback_reply(m: types.Message, target_uid: str):
    text = (m.text or "").strip()
    if not text:
        bot.send_message(m.chat.id, "پیام خالی است.", reply_markup=main_menu_kb(m.chat.id))
        return
    try:
        bot.send_message(int(target_uid), f"📩 پاسخ مدیریت:\n{text}")
        bot.send_message(m.chat.id, "پاسخ ارسال شد.", reply_markup=main_menu_kb(m.chat.id))
    except Exception:
        bot.send_message(m.chat.id, "ارسال موفق نبود.", reply_markup=main_menu_kb(m.chat.id))

# --------------------- ADMIN PANEL (Full) ---------------------
def show_admin_panel(uid:int):
    if not is_admin(uid):
        bot.send_message(uid, "دسترسی ادمین ندارید.", reply_markup=main_menu_kb(uid))
        return
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("مدیریت فال", "مدیریت عکس", "مدیریت موزیک", "مدیریت تکست و بیو",
           "مدیریت درباره ما", "آمار کاربران", "📢 ارسال پیام همگانی", "👤 مدیریت ادمین‌ها",
           "📩 صندوق پیام‌ها", "بازگشت به منو اصلی")
    set_state(uid, "admin_panel")
    bot.send_message(uid, "پنل مدیریت:", reply_markup=kb)

# --- Bulk helpers UI (for admin) ---
def bulk_hint_finish() -> str:
    return "🔁 حالت افزودن گروهی فعال است.\n- آیتم‌ها را یکی‌یکی بفرست.\n- برای پایان، کلمه «اتمام» را بفرست.\n- برای لغو، «⬅️ بازگشت» را بفرست."

def admin_panel_choice(m: types.Message):
    uid = m.chat.id
    txt = (m.text or "").strip()

    if txt == "مدیریت فال":
        manage_fortunes_root(uid)
    elif txt == "مدیریت عکس":
        manage_images_root(uid)
    elif txt == "مدیریت موزیک":
        admin_music_root(uid)
    elif txt == "مدیریت تکست و بیو":
        admin_text_root(uid)
    elif txt == "مدیریت درباره ما":
        manage_about_root(uid)
    elif txt == "آمار کاربران":
        from telebot import types as _types
        items = list(users.items())
        total = len(items)
        if total == 0:
            bot.send_message(uid, "کاربری ثبت نشده است.", reply_markup=main_menu_kb(uid)); return
        kb = _types.InlineKeyboardMarkup()
        limit = 200
        for su, info in items[:limit]:
            label = info.get('name') or info.get('username') or ("کاربر " + su)
            if len(label) > 30:
                label = label[:27] + '...'
            kb.add(_types.InlineKeyboardButton(label, callback_data=f"stats_{su}" ))
        if total > limit:
            kb.add(_types.InlineKeyboardButton(f"نمایش {total-limit} کاربر دیگر...", callback_data="stats_more" ))
        bot.send_message(uid, f"تعداد کاربران ثبت‌شده: {total}\nبرای دیدن جزئیات روی نام کاربر ضربه بزنید.", reply_markup=kb)
    elif txt == "📢 ارسال پیام همگانی":
        bot.send_message(uid, "متن پیام همگانی را ارسال کنید (برای لغو ⬅️ بازگشت):", reply_markup=back_kb())
        bot.register_next_step_handler_by_chat_id(uid, admin_broadcast)
    elif txt == "👤 مدیریت ادمین‌ها":
        manage_admins_root(uid)
    elif txt == "📩 صندوق پیام‌ها":
        show_feedbacks_for_admin(uid)
    elif txt == "بازگشت به منو اصلی":
        clear_state(uid)
        bot.send_message(uid, "بازگشت شد.", reply_markup=main_menu_kb(uid))
    else:
        bot.send_message(uid, "گزینه نامعتبر.", reply_markup=main_menu_kb(uid))

# Admin: broadcast
def admin_broadcast(m: types.Message):
    uid = m.chat.id
    txt = (m.text or "").strip()
    if txt == "⬅️ بازگشت":
        bot.send_message(uid, "لغو شد.", reply_markup=main_menu_kb(uid)); return
    count = 0
    for su in list(users.keys()):
        try:
            bot.send_message(int(su), f"{txt}")
            count += 1
        except Exception:
            pass
    bot.send_message(uid, f"اعلامیه به {count} کاربر ارسال شد.", reply_markup=main_menu_kb(uid))

# Admin: manage admins
def manage_admins_root(uid:int):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ افزودن ادمین", "🗑 حذف ادمین", "📋 لیست ادمین‌ها", "بازگشت")
    bot.send_message(uid, "مدیریت ادمین‌ها:", reply_markup=kb)
    bot.register_next_step_handler_by_chat_id(uid, manage_admins_choice)

def manage_admins_choice(m: types.Message):
    uid = m.chat.id
    txt = m.text
    if txt == "➕ افزودن ادمین":
        bot.send_message(uid, "آی‌دی عددی کاربر را بفرستید:", reply_markup=back_kb())
        bot.register_next_step_handler_by_chat_id(uid, add_admin_step)
    elif txt == "🗑 حذف ادمین":
        bot.send_message(uid, "آی‌دی عددی ادمین را ارسال کنید:", reply_markup=back_kb())
        bot.register_next_step_handler_by_chat_id(uid, remove_admin_step)
    elif txt == "📋 لیست ادمین‌ها":
        bot.send_message(uid, "لیست ادمین‌ها:\n" + "\n".join(str(a) for a in admins), reply_markup=main_menu_kb(uid))
    else:
        show_admin_panel(uid)

def add_admin_step(m: types.Message):
    uid = m.chat.id
    try:
        nid = int((m.text or "").strip())
        if nid not in admins:
            admins.append(nid)
            save_json("data/admins.json", admins)
            bot.send_message(uid, "ادمین اضافه شد.", reply_markup=main_menu_kb(uid))
        else:
            bot.send_message(uid, "این کاربر قبلاً ادمین است.", reply_markup=main_menu_kb(uid))
    except Exception:
        bot.send_message(uid, "آی‌دی نامعتبر.", reply_markup=main_menu_kb(uid))

def remove_admin_step(m: types.Message):
    uid = m.chat.id
    try:
        nid = int((m.text or "").strip())
        if nid in admins:
            admins.remove(nid)
            save_json("data/admins.json", admins)
            bot.send_message(uid, "ادمین حذف شد.", reply_markup=main_menu_kb(uid))
        else:
            bot.send_message(uid, "چنین ادمینی وجود ندارد.", reply_markup=main_menu_kb(uid))
    except Exception:
        bot.send_message(uid, "آی‌دی نامعتبر.", reply_markup=main_menu_kb(uid))

# Admin: manage texts
def admin_text_root(uid:int):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ افزودن تکست جدید", "➕ افزودن چند تکست", "🗑 حذف تکست", "بازگشت")
    bot.send_message(uid, "مدیریت تکست:", reply_markup=kb)
    bot.register_next_step_handler_by_chat_id(uid, admin_text_choice)

def admin_text_choice(m: types.Message):
    uid = m.chat.id
    txt = m.text
    if txt == "➕ افزودن تکست جدید":
        bot.send_message(uid, "متن جدید را بنویسید:", reply_markup=back_kb())
        bot.register_next_step_handler_by_chat_id(uid, admin_add_newtext)
    elif txt == "➕ افزودن چند تکست":
        bot.send_message(uid, bulk_hint_finish(), reply_markup=back_kb())
        bot.register_next_step_handler_by_chat_id(uid, admin_add_newtext_bulk)
    elif txt == "🗑 حذف تکست":
        if not text_bio:
            bot.send_message(uid, "لیست خالی است.", reply_markup=main_menu_kb(uid)); return
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for i, t in enumerate(text_bio, start=1):
            preview = t if len(t)<=50 else t[:47]+"..."
            kb.add(f"{i}. {preview}")
        kb.add("بازگشت")
        bot.send_message(uid, "کدام را حذف کنیم؟ (ارسال عدد)", reply_markup=kb)
        bot.register_next_step_handler_by_chat_id(uid, admin_delete_text)
    else:
        show_admin_panel(uid)

def admin_add_newtext(m: types.Message):
    uid = m.chat.id
    txt = (m.text or "").strip()
    if txt == "⬅️ بازگشت":
        show_admin_panel(uid); return
    new_texts.append(txt)
    save_json("data/new_texts.json", new_texts)
    bot.send_message(uid, "تکست به بخش تکست‌های جدید اضافه شد.", reply_markup=main_menu_kb(uid))

def admin_add_newtext_bulk(m: types.Message):
    uid = m.chat.id
    txt = (m.text or "").strip()
    if txt in ["⬅️ بازگشت", "اتمام"]:
        save_json("data/new_texts.json", new_texts)
        bot.send_message(uid, "پایان افزودن گروهی.", reply_markup=main_menu_kb(uid))
        return
    new_texts.append(txt)
    bot.send_message(uid, "✅ ثبت شد. مورد بعدی را بفرست یا «اتمام».")
    bot.register_next_step_handler_by_chat_id(uid, admin_add_newtext_bulk)

def admin_delete_text(m: types.Message):
    uid = m.chat.id
    txt = m.text
    if txt == "بازگشت":
        admin_text_root(uid); return
    try:
        idx = int(txt.split(".")[0]) - 1
        if 0 <= idx < len(text_bio):
            text_bio.pop(idx)
            save_json("data/text_bio.json", text_bio)
            bot.send_message(uid, "حذف شد.", reply_markup=main_menu_kb(uid)); return
    except Exception:
        pass
    bot.send_message(uid, "خطا یا مورد یافت نشد.", reply_markup=main_menu_kb(uid))

# Admin: music root
def admin_music_root(uid:int):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("ارسال موزیک (فایل صوتی)", "ارسال چند موزیک", "حذف موزیک", "بازگشت")
    bot.send_message(uid, "مدیریت موزیک:", reply_markup=kb)
    bot.register_next_step_handler_by_chat_id(uid, admin_music_choice)

def admin_music_choice(m: types.Message):
    uid = m.chat.id
    txt = m.text
    if txt == "ارسال موزیک (فایل صوتی)":
        bot.send_message(uid, "لطفاً فایل صوتی (Audio یا Document) را ارسال کنید:", reply_markup=back_kb())
        bot.register_next_step_handler_by_chat_id(uid, admin_receive_music_file)
    elif txt == "ارسال چند موزیک":
        bot.send_message(uid, bulk_hint_finish(), reply_markup=back_kb())
        bot.register_next_step_handler_by_chat_id(uid, admin_receive_music_bulk)
    elif txt == "حذف موزیک":
        if not music_playlist:
            bot.send_message(uid, "هیچ موزیکی نیست.", reply_markup=main_menu_kb(uid)); return
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for i,p in enumerate(music_playlist, start=1):
            kb.add(f"{i}. {os.path.basename(p)}")
        kb.add("بازگشت")
        bot.send_message(uid, "کدام موزیک حذف شود؟", reply_markup=kb)
        bot.register_next_step_handler_by_chat_id(uid, admin_delete_music)
    else:
        show_admin_panel(uid)

def admin_receive_music_file(m: types.Message):
    uid = m.chat.id
    if m.content_type not in ["audio","document"]:
        bot.send_message(uid, "فایل معتبر ارسال نشده.", reply_markup=main_menu_kb(uid)); return
    file_id = m.audio.file_id if m.content_type=="audio" else m.document.file_id
    try:
        info = bot.get_file(file_id)
        data = bot.download_file(info.file_path)
        fname = f"data/music/{os.path.basename(info.file_path)}"
        with open(fname, "wb") as f:
            f.write(data)
        music_playlist.append(fname)
        save_json("data/music_playlist.json", music_playlist)
        bot.send_message(uid, "موزیک ذخیره شد و به لیست اضافه شد.", reply_markup=main_menu_kb(uid))
    except Exception:
        # fallback: save file_id only
        music_playlist.append(file_id)
        save_json("data/music_playlist.json", music_playlist)
        bot.send_message(uid, "موزیک اضافه شد (با file_id).", reply_markup=main_menu_kb(uid))

def admin_receive_music_bulk(m: types.Message):
    uid = m.chat.id
    if m.content_type == "text":
        txt = (m.text or "").strip()
        if txt in ["⬅️ بازگشت", "اتمام"]:
            save_json("data/music_playlist.json", music_playlist)
            bot.send_message(uid, "پایان افزودن گروهی موزیک.", reply_markup=main_menu_kb(uid)); return
        # ignore any other text; ask again
        bot.send_message(uid, "فایل صوتی بفرست یا «اتمام».")
        bot.register_next_step_handler_by_chat_id(uid, admin_receive_music_bulk)
        return

    if m.content_type in ["audio","document"]:
        try:
            file_id = m.audio.file_id if m.content_type=="audio" else m.document.file_id
            info = bot.get_file(file_id)
            data = bot.download_file(info.file_path)
            fname = f"data/music/{os.path.basename(info.file_path)}"
            with open(fname, "wb") as f:
                f.write(data)
            music_playlist.append(fname)
        except Exception:
            # fallback store file_id
            try:
                music_playlist.append(file_id)
            except Exception:
                pass
        bot.send_message(uid, "✅ ذخیره شد. مورد بعدی را بفرست یا «اتمام».")
        bot.register_next_step_handler_by_chat_id(uid, admin_receive_music_bulk)
        return

    bot.send_message(uid, "لطفاً Audio/Document یا «اتمام» ارسال کن.")
    bot.register_next_step_handler_by_chat_id(uid, admin_receive_music_bulk)

def admin_delete_music(m: types.Message):
    uid = m.chat.id
    txt = m.text
    if txt == "بازگشت":
        admin_music_root(uid); return
    try:
        idx = int(txt.split(".")[0]) - 1
        if 0 <= idx < len(music_playlist):
            removed = music_playlist.pop(idx)
            save_json("data/music_playlist.json", music_playlist)
            try:
                if os.path.exists(removed):
                    os.remove(removed)
            except Exception:
                pass
            bot.send_message(uid, "حذف شد.", reply_markup=main_menu_kb(uid)); return
    except Exception:
        pass
    bot.send_message(uid, "خطا یا مورد یافت نشد.", reply_markup=main_menu_kb(uid))

# Admin: images management (full)
def manage_images_root(uid:int):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ افزودن دسته", "🗑 حذف دسته", "📂 مدیریت یک دسته", "بازگشت")
    bot.send_message(uid, "مدیریت عکس‌ها:", reply_markup=kb)
    bot.register_next_step_handler_by_chat_id(uid, manage_images_root_choice)

def manage_images_root_choice(m: types.Message):
    uid = m.chat.id
    txt = m.text
    if txt == "➕ افزودن دسته":
        bot.send_message(uid, "نام دسته را ارسال کنید:", reply_markup=back_kb())
        bot.register_next_step_handler_by_chat_id(uid, add_image_category)
    elif txt == "🗑 حذف دسته":
        if not moon_images:
            bot.send_message(uid, "هیچ دسته‌ای نیست.", reply_markup=main_menu_kb(uid)); return
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for k in moon_images.keys(): kb.add(k)
        kb.add("بازگشت")
        bot.send_message(uid, "کدام دسته حذف شود؟", reply_markup=kb)
        bot.register_next_step_handler_by_chat_id(uid, delete_image_category)
    elif txt == "📂 مدیریت یک دسته":
        if not moon_images:
            bot.send_message(uid, "هیچ دسته‌ای نیست.", reply_markup=main_menu_kb(uid)); return
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for k in moon_images.keys(): kb.add(k)
        kb.add("بازگشت")
        bot.send_message(uid, "کدام دسته را مدیریت کنیم؟", reply_markup=kb)
        bot.register_next_step_handler_by_chat_id(uid, manage_image_category_choice)
    else:
        show_admin_panel(uid)

def add_image_category(m: types.Message):
    uid = m.chat.id
    name = (m.text or "").strip()
    if not name or name == "⬅️ بازگشت":
        manage_images_root(uid); return
    if name in moon_images:
        bot.send_message(uid, "این دسته از قبل وجود دارد.", reply_markup=main_menu_kb(uid)); return
    moon_images[name] = []
    save_json("data/moon_images.json", moon_images)
    bot.send_message(uid, "دسته اضافه شد.", reply_markup=main_menu_kb(uid))

def delete_image_category(m: types.Message):
    uid = m.chat.id
    name = m.text
    if name == "بازگشت":
        manage_images_root(uid); return
    if name in moon_images:
        for p in moon_images[name]:
            try:
                if os.path.exists(p): os.remove(p)
            except Exception:
                pass
        moon_images.pop(name, None)
        save_json("data/moon_images.json", moon_images)
        bot.send_message(uid, "دسته حذف شد.", reply_markup=main_menu_kb(uid))
    else:
        bot.send_message(uid, "دسته یافت نشد.", reply_markup=main_menu_kb(uid))

def manage_image_category_choice(m: types.Message):
    uid = m.chat.id
    cat = m.text
    if cat == "بازگشت":
        manage_images_root(uid); return
    if cat not in moon_images:
        bot.send_message(uid, "دسته نامعتبر.", reply_markup=main_menu_kb(uid)); return
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("➕ افزودن عکس", "➕ افزودن چند عکس", "🗑 حذف عکس", "بازگشت")
    bot.send_message(uid, f"مدیریت دسته {cat}:", reply_markup=kb)
    bot.register_next_step_handler_by_chat_id(uid, lambda mm: manage_image_category_action(mm, cat))

def manage_image_category_action(m: types.Message, category: str):
    uid = m.chat.id
    txt = m.text
    if txt == "➕ افزودن عکس":
        bot.send_message(uid, "عکس را به صورت Photo یا Document ارسال کنید:", reply_markup=back_kb())
        bot.register_next_step_handler_by_chat_id(uid, lambda mm: receive_image_for_category(mm, category))
    elif txt == "➕ افزودن چند عکس":
        bot.send_message(uid, bulk_hint_finish(), reply_markup=back_kb())
        bot.register_next_step_handler_by_chat_id(uid, lambda mm: receive_images_bulk(mm, category))
    elif txt == "🗑 حذف عکس":
        imgs = moon_images.get(category, [])
        if not imgs:
            bot.send_message(uid, "عکسی وجود ندارد.", reply_markup=main_menu_kb(uid)); return
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for p in imgs: kb.add(os.path.basename(p))
        kb.add("بازگشت")
        bot.send_message(uid, "کدام عکس حذف شود؟", reply_markup=kb)
        bot.register_next_step_handler_by_chat_id(uid, lambda mm: delete_image_from_category(mm, category))
    else:
        manage_images_root(uid)

def receive_image_for_category(m: types.Message, category: str):
    uid = m.chat.id
    if m.content_type not in ["photo","document"]:
        bot.send_message(uid, "فرمت نامعتبر.", reply_markup=main_menu_kb(uid)); return
    file_id = m.photo[-1].file_id if m.content_type=="photo" else m.document.file_id
    try:
        info = bot.get_file(file_id)
        data = bot.download_file(info.file_path)
        fname = f"data/images/{os.path.basename(info.file_path)}"
        with open(fname, "wb") as f:
            f.write(data)
        moon_images.setdefault(category, []).append(fname)
        save_json("data/moon_images.json", moon_images)
        bot.send_message(uid, "عکس اضافه شد.", reply_markup=main_menu_kb(uid))
    except Exception:
        bot.send_message(uid, "خطا در دریافت فایل.", reply_markup=main_menu_kb(uid))

def receive_images_bulk(m: types.Message, category: str):
    uid = m.chat.id
    if m.content_type == "text":
        txt = (m.text or "").strip()
        if txt in ["⬅️ بازگشت", "اتمام"]:
            save_json("data/moon_images.json", moon_images)
            bot.send_message(uid, "پایان افزودن گروهی عکس.", reply_markup=main_menu_kb(uid)); return
        bot.send_message(uid, "Photo/Document بفرست یا «اتمام».")
        bot.register_next_step_handler_by_chat_id(uid, lambda mm: receive_images_bulk(mm, category))
        return

    if m.content_type in ["photo","document"]:
        try:
            file_id = m.photo[-1].file_id if m.content_type=="photo" else m.document.file_id
            info = bot.get_file(file_id)
            data = bot.download_file(info.file_path)
            fname = f"data/images/{os.path.basename(info.file_path)}"
            with open(fname, "wb") as f:
                f.write(data)
            moon_images.setdefault(category, []).append(fname)
            bot.send_message(uid, "✅ ذخیره شد. مورد بعدی را بفرست یا «اتمام».")
        except Exception:
            bot.send_message(uid, "❌ خطا در ذخیره. مورد بعدی یا «اتمام».")
        bot.register_next_step_handler_by_chat_id(uid, lambda mm: receive_images_bulk(mm, category))
        return

    bot.send_message(uid, "فرمت نامعتبر. Photo/Document یا «اتمام».")
    bot.register_next_step_handler_by_chat_id(uid, lambda mm: receive_images_bulk(mm, category))

def delete_image_from_category(m: types.Message, category: str):
    uid = m.chat.id
    name = m.text
    if name == "بازگشت":
        manage_images_root(uid); return
    img_list = moon_images.get(category, [])
    for p in list(img_list):
        if os.path.basename(p) == name:
            img_list.remove(p)
            try:
                if os.path.exists(p): os.remove(p)
            except Exception:
                pass
            save_json("data/moon_images.json", moon_images)
            bot.send_message(uid, "عکس حذف شد.", reply_markup=main_menu_kb(uid))
            return
    bot.send_message(uid, "عکس یافت نشد.", reply_markup=main_menu_kb(uid))

# Admin: about us management
def manage_about_root(uid:int):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("نمایش درباره ما", "افزودن پاراگراف", "حذف پاراگراف", "بازگشت")
    bot.send_message(uid, "مدیریت درباره ما:", reply_markup=kb)
    bot.register_next_step_handler_by_chat_id(uid, manage_about_choice)

def manage_about_choice(m: types.Message):
    uid = m.chat.id
    txt = m.text
    if txt == "نمایش درباره ما":
        about = load_json("data/about_us.json", ["ربات Moon"])
        bot.send_message(uid, "\n".join(about), reply_markup=main_menu_kb(uid))
    elif txt == "افزودن پاراگراف":
        bot.send_message(uid, "متن پاراگراف را ارسال کنید:", reply_markup=back_kb())
        bot.register_next_step_handler_by_chat_id(uid, manage_about_add)
    elif txt == "حذف پاراگراف":
        about = load_json("data/about_us.json", [])
        if not about:
            bot.send_message(uid, "خالی است.", reply_markup=main_menu_kb(uid)); return
        kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        for p in about:
            kb.add(p if len(p)<=50 else p[:47]+"...")
        kb.add("بازگشت")
        bot.send_message(uid, "کدام را حذف کنیم؟", reply_markup=kb)
        bot.register_next_step_handler_by_chat_id(uid, manage_about_delete)
    else:
        show_admin_panel(uid)

def manage_about_add(m: types.Message):
    uid = m.chat.id
    txt = (m.text or "").strip()
    if txt == "⬅️ بازگشت":
        show_admin_panel(uid); return
    about = load_json("data/about_us.json", [])
    about.append(txt)
    save_json("data/about_us.json", about)
    bot.send_message(uid, "افزوده شد.", reply_markup=main_menu_kb(uid))

def manage_about_delete(m: types.Message):
    uid = m.chat.id
    txt = m.text
    if txt == "بازگشت":
        manage_about_root(uid); return
    about = load_json("data/about_us.json", [])
    for p in list(about):
        if p.startswith(txt[:30]):
            about.remove(p)
            save_json("data/about_us.json", about)
            bot.send_message(uid, "حذف شد.", reply_markup=main_menu_kb(uid))
            return
    bot.send_message(uid, "پاراگراف یافت نشد.", reply_markup=main_menu_kb(uid))

# --------------------- SCHEDULE: DAILY CALENDAR & MOON IMAGE ---------------------
def schedule_daily_tasks():
    now = now_tehran()
    tomorrow = (now + timedelta(days=1)).date()
    next_midnight = TEHRAN_TZ.localize(datetime.combine(tomorrow, timeobj(0,0,5)))
    delay = max(60, (next_midnight - now).total_seconds())
    threading.Timer(delay, run_daily_tasks).start()

def run_daily_tasks():
    for su in list(users.keys()):
        try:
            send_today(int(su))
        except Exception:
            pass
    schedule_nightly_moon()
    schedule_daily_tasks()

def schedule_nightly_moon():
    now = now_tehran()
    tomorrow = (now + timedelta(days=1)).date()
    next_midnight = TEHRAN_TZ.localize(datetime.combine(tomorrow, timeobj(0,30,0)))
    delay = max(60, (next_midnight - now).total_seconds())
    threading.Timer(delay, run_nightly_moon).start()

def run_nightly_moon():
    date_str = now_tehran().strftime("%Y-%m-%d")
    for su in list(users.keys()):
        try:
            img = fetch_moon_image_url(date_str)
            if img:
                r = requests.get(img, timeout=15)
                if r.status_code == 200:
                    bio = BytesIO(r.content)
                    bio.name = "moon.png"
                    bot.send_photo(int(su), bio, caption=f"تصویر ماه — {date_str}\n@{BOT_NAME}")
        except Exception:
            pass
    schedule_nightly_moon()

# --------------------- AstronomyAPI MOON IMAGE ---------------------
def fetch_moon_image_url(date_str: str) -> Optional[str]:
    if not ASTRONOMY_APP_ID or not ASTRONOMY_APP_SECRET:
        return None
    url = "https://api.astronomyapi.com/api/v2/studio/moon-phase"
    token = base64.b64encode(f"{ASTRONOMY_APP_ID}:{ASTRONOMY_APP_SECRET}".encode()).decode()
    headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}
    payload = {
        "format": "png",
        "style":{"moonStyle":"default","backgroundStyle":"solid"},
        "observer":{"latitude":TEHRAN_LAT,"longitude":TEHRAN_LON,"date":date_str},
        "view":{"type":"portrait-simple"}
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        if r.status_code == 200:
            data = r.json()
            return data.get("data", {}).get("imageUrl")
    except Exception:
        pass
    return None

def send_moon_image(uid:int):
    date_str = now_tehran().strftime("%Y-%m-%d")
    url = fetch_moon_image_url(date_str)
    if not url:
        bot.send_message(uid, "دسترسی به AstronomyAPI مقدور نیست یا تنظیم نشده.", reply_markup=main_menu_kb(uid))
        return
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            bio = BytesIO(r.content); bio.name="moon.png"; bio.seek(0)
            bot.send_photo(uid, bio, caption=f"تصویر ماه — {date_str}\n@{BOT_NAME}", reply_markup=main_menu_kb(uid)); return
    except Exception:
        pass
    bot.send_message(uid, f"تصویر ماه زنده:{url}", reply_markup=main_menu_kb(uid))

# --------------------- HELPER COMMANDS ---------------------
@bot.message_handler(commands=["images"])
def cmd_images(m: types.Message):
    show_image_categories(m.chat.id)

@bot.message_handler(commands=["end"])
def cmd_end(m: types.Message):
    leave_chat(m.chat.id)

# --------------------- FILE SAVE ON EXIT (optional) ---------------------
def save_all():
    save_json("data/fortunes.json", fortunes)
    save_json("data/moon_images.json", moon_images)
    save_json("data/music_playlist.json", music_playlist)
    save_json("data/text_bio.json", text_bio)
    save_json("data/new_texts.json", new_texts)
    save_json("data/user_playlists.json", user_playlists)
    save_json("data/user_listened.json", user_listened)
    save_json("data/likes.json", likes)
    save_json("data/feedbacks.json", feedbacks)
    save_json("data/about_us.json", about_us)
    save_json("data/users.json", users)
    save_json("data/admins.json", admins)

# --------------------- RUN BOT ---------------------

# ----------------- ADMIN: user stats callbacks -----------------
@bot.callback_query_handler(func=lambda c: c.data and (c.data.startswith('stats_') or c.data == 'stats_more'))
def handle_stats_callback(c):
    data = c.data
    admin_id = c.from_user.id
    if admin_id not in admins:
        bot.answer_callback_query(c.id, "دسترسی ندارد.", show_alert=True)
        return
    if data == 'stats_more':
        bot.answer_callback_query(c.id, "برای مشاهده همه کاربران بهتر است از فایل داده‌ها استفاده کنید.")
        return
    su = data.split('_',1)[1]
    info = users.get(su)
    if not info:
        bot.answer_callback_query(c.id, "اطلاعات یافت نشد.", show_alert=True)
        return
    name = info.get('name','-')
    uname = info.get('username','-')
    joined = info.get('joined_at','-')
    active = info.get('active', False)
    status = 'فعال' if active else 'غیرفعال'
    text = f"اطلاعات کاربر:\nآیدی: {su}\nنام: {name}\nیوزرنیم: @{uname if uname else '-'}\nتاریخ عضویت (UTC): {joined}\nوضعیت: {status}"
    try:
        bot.send_message(admin_id, text)
        bot.answer_callback_query(c.id, "اطلاعات ارسال شد.")
    except Exception:
        bot.answer_callback_query(c.id, "خطا در ارسال اطلاعات.", show_alert=True)

if __name__ == "__main__":
    try:
        schedule_daily_tasks()
    except Exception:
        pass
    try:
        schedule_nightly_moon()
    except Exception:
        pass
    print("Bot started.")
    bot.infinity_polling(skip_pending=True)
