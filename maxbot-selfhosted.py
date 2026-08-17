#!/usr/bin/env python3
"""
Self-hosted production MAX bot for «МАКСимум мебель».

Goals:
- Production Webhook transport (MAX requires Webhook for production).
- No paid bot constructor.
- Works on a small Docker host with only Python stdlib.
- Secrets come only from environment variables.
- SQLite persists leads/sessions/channels.
- Uses current MAX API base: https://platform-api2.max.ru
"""

import hashlib
import hmac
import json
import os
import queue
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BASE = "https://platform-api2.max.ru"
MAX_TOKEN = os.environ.get("MAX_BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.environ.get("MAX_WEBHOOK_SECRET", "").strip()
PORT = int(os.environ.get("PORT", "8000"))
HOST = "0.0.0.0"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
DB_PATH = Path(os.environ.get("DATABASE_PATH", str(DATA_DIR / "maxbot.db")))
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
DOMAIN = os.environ.get("DOMAIN", "").strip()
AUTO_SUBSCRIBE = os.environ.get("MAX_AUTO_SUBSCRIBE", "1").strip().lower() not in {"0", "false", "no"}
ADMIN_CHAT_ID = os.environ.get("MAX_ADMIN_CHAT_ID", "").strip()
OWNER_USER_ID = os.environ.get("MAX_OWNER_USER_ID", "").strip()

if not PUBLIC_BASE_URL and DOMAIN:
    PUBLIC_BASE_URL = DOMAIN if DOMAIN.startswith("https://") else f"https://{DOMAIN}"

if not MAX_TOKEN:
    raise RuntimeError("MAX_BOT_TOKEN is required")
if not re.fullmatch(r"[A-Za-z0-9_-]{5,256}", WEBHOOK_SECRET or ""):
    raise RuntimeError(
        "MAX_WEBHOOK_SECRET is required and must contain 5-256 characters: A-Z a-z 0-9 _ -"
    )

DATA_DIR.mkdir(parents=True, exist_ok=True)

WELCOME = (
    "Здравствуйте! 👋\n"
    "Я помощник мебельной фабрики «МАКСимум мебель».\n\n"
    "Помогу узнать о нашей работе, посмотреть проекты, "
    "сориентироваться по заказу кухни или шкафа и оставить заявку.\n\n"
    "Выберите, что вас интересует 👇"
)

WHO_TEXT = (
    "🏠 «МАКСимум мебель» — мебельная фабрика полного цикла.\n\n"
    "Меня зовут Катерина Белякова. Я дизайнер интерьера и мебельный технолог. "
    "Работаем с 2012 года, реализовано более 500 проектов.\n\n"
    "Весь процесс в одних руках:\n"
    "замер → дизайн-проект и 3D-визуализация → производство → доставка → сборка → уборка после монтажа.\n\n"
    "У нас собственное производство. Мы не салон и не посредники."
)

DESIGN_TEXT = (
    "📐 Дизайн-проект\n\n"
    "Разрабатываем планировочное решение, 3D-визуализацию и рабочие чертежи. "
    "Проектируем мебель так, чтобы её можно было реально изготовить без переделок.\n\n"
    "По Нижегородской области работаем очно, дизайн-проекты можем вести удалённо по всей России."
)

PRICE_TEXT = (
    "💰 Стоимость\n\n"
    "Точная цена кухни или шкафа зависит от размеров, материалов, фасадов, "
    "фурнитуры и наполнения — без исходных данных точную сумму не называем.\n\n"
    "Дизайн-проект: стоимость зависит от состава проекта и площади. "
    "Для предварительного расчёта оставьте заявку — уточним задачу и исходные данные."
)

CONTACTS_TEXT = (
    "📞 Наши контакты\n\n"
    "Катерина: +7 (915) 953-09-29\n"
    "Максим: +7 (910) 799-64-71\n"
    "Сайт: portfolio-maxdizain.ru\n"
    "VK: vk.com/maximummebel"
)

PORTFOLIO_URLS = [
    f"https://portfolio-maxdizain.ru/images/kitchens/{i}.jpg"
    for i in range(1, 21)
]

MENU_BUTTONS = [
    [{"type": "callback", "text": "🏠 Кто мы", "payload": "menu:who"}],
    [{"type": "callback", "text": "🍽 Заказать кухню", "payload": "menu:kitchen"}],
    [{"type": "callback", "text": "🚪 Заказать шкаф", "payload": "menu:wardrobe"}],
    [{"type": "callback", "text": "📐 Дизайн-проект", "payload": "menu:design"}],
    [{"type": "callback", "text": "💰 Узнать стоимость", "payload": "menu:price"}],
    [{"type": "callback", "text": "🖼 Наши работы", "payload": "menu:works"}],
    [{"type": "callback", "text": "📞 Наши контакты", "payload": "menu:contacts"}],
    [{"type": "callback", "text": "💬 Задать вопрос", "payload": "menu:question"}],
]

BACK_BUTTONS = [[{"type": "callback", "text": "⬅️ В главное меню", "payload": "menu:main"}]]

CONTACT_BUTTONS = [
    [{"type": "request_contact", "text": "📱 Поделиться номером"}],
    [{"type": "callback", "text": "⬅️ В главное меню", "payload": "menu:main"}],
]

PRICE_BUTTONS = [
    [{"type": "callback", "text": "🍽 Рассчитать кухню", "payload": "menu:kitchen"}],
    [{"type": "callback", "text": "🚪 Рассчитать шкаф", "payload": "menu:wardrobe"}],
    [{"type": "callback", "text": "📐 Рассчитать дизайн", "payload": "menu:design"}],
    [{"type": "callback", "text": "⬅️ В главное меню", "payload": "menu:main"}],
]

FAQ = [
    (
        ("сколько стоит кухня", "цена кухни", "стоимость кухни"),
        "Стоимость кухни зависит от размеров, фасадов, столешницы, фурнитуры и наполнения. "
        "Для точного расчёта нужен замер или размеры помещения. Нажмите «Заказать кухню» — соберу данные.",
    ),
    (
        ("какой город", "где вы", "где находитесь"),
        "Мы работаем в Нижнем Новгороде и Нижегородской области. "
        "Дизайн-проекты можем выполнять удалённо по всей России.",
    ),
    (
        ("сами делаете", "свое производство", "своё производство"),
        "Да. У «МАКСимум мебель» собственное производство и полный цикл: "
        "замер, проект, производство, доставка и сборка.",
    ),
    (
        ("какие материалы", "материалы"),
        "Подбираем материалы под проект и бюджет: ЛДСП для корпусов, МДФ/эмаль и другие варианты фасадов, "
        "разные виды столешниц и фурнитуры. Точную комплектацию определяем после задачи и размеров.",
    ),
]


def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                chat_id TEXT PRIMARY KEY,
                flow TEXT NOT NULL,
                step TEXT NOT NULL,
                data_json TEXT NOT NULL DEFAULT '{}',
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at INTEGER NOT NULL,
                chat_id TEXT NOT NULL,
                user_id TEXT,
                kind TEXT NOT NULL,
                name TEXT,
                city TEXT,
                details TEXT,
                phone TEXT,
                phone_verified INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS channels (
                chat_id TEXT PRIMARY KEY,
                is_channel INTEGER NOT NULL DEFAULT 0,
                added_at INTEGER NOT NULL,
                added_by_user_id TEXT
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )


def set_session(chat_id, flow, step, data=None):
    payload = json.dumps(data or {}, ensure_ascii=False)
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions(chat_id, flow, step, data_json, updated_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(chat_id) DO UPDATE SET
              flow=excluded.flow, step=excluded.step, data_json=excluded.data_json, updated_at=excluded.updated_at
            """,
            (str(chat_id), flow, step, payload, int(time.time())),
        )


def get_session(chat_id):
    with db_connect() as conn:
        row = conn.execute(
            "SELECT flow, step, data_json FROM sessions WHERE chat_id=?",
            (str(chat_id),),
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["data_json"] or "{}")
    except Exception:
        data = {}
    return {"flow": row["flow"], "step": row["step"], "data": data}


def clear_session(chat_id):
    with db_connect() as conn:
        conn.execute("DELETE FROM sessions WHERE chat_id=?", (str(chat_id),))


def save_channel(chat_id, is_channel, user_id=None):
    if chat_id is None:
        return
    with db_connect() as conn:
        conn.execute(
            """
            INSERT INTO channels(chat_id, is_channel, added_at, added_by_user_id)
            VALUES(?,?,?,?)
            ON CONFLICT(chat_id) DO UPDATE SET
              is_channel=excluded.is_channel, added_at=excluded.added_at, added_by_user_id=excluded.added_by_user_id
            """,
            (str(chat_id), 1 if is_channel else 0, int(time.time()), str(user_id or "")),
        )


def save_lead(chat_id, user_id, kind, data, phone="", verified=False):
    details = data.copy()
    for k in ("name", "city", "phone"):
        details.pop(k, None)
    with db_connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO leads(created_at, chat_id, user_id, kind, name, city, details, phone, phone_verified)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                int(time.time()),
                str(chat_id),
                str(user_id or ""),
                kind,
                data.get("name", ""),
                data.get("city", ""),
                json.dumps(details, ensure_ascii=False),
                phone or data.get("phone", ""),
                1 if verified else 0,
            ),
        )
        return cur.lastrowid


def http_json(url, method="GET", obj=None, timeout=30):
    headers = {
        "Authorization": MAX_TOKEN,
        "Accept": "application/json",
        "User-Agent": "MaximumFurnitureBot/2.0",
    }
    data = None
    if obj is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                body = json.loads(raw) if raw else {}
            except Exception:
                body = {"raw": raw}
            if exc.code == 429 and attempt < 3:
                retry_after = exc.headers.get("Retry-After", "2")
                try:
                    delay = max(1.0, min(float(retry_after), 10.0))
                except Exception:
                    delay = 2.0
                time.sleep(delay)
                continue
            return exc.code, body
        except Exception:
            if attempt >= 3:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def keyboard(buttons):
    return {"type": "inline_keyboard", "payload": {"buttons": buttons}}


def send_message(chat_id, text, buttons=None, attachments=None, notify=True):
    payload = {"text": text, "notify": bool(notify)}
    out = list(attachments or [])
    if buttons:
        out.append(keyboard(buttons))
    if out:
        payload["attachments"] = out
    query = urllib.parse.urlencode({"chat_id": str(chat_id)})
    status, result = http_json(f"{MAX_BASE}/messages?{query}", method="POST", obj=payload, timeout=35)
    if not 200 <= status < 300:
        raise RuntimeError(f"MAX /messages HTTP {status}: {result}")
    return result


def answer_callback(callback_id):
    if not callback_id:
        return
    query = urllib.parse.urlencode({"callback_id": callback_id})
    status, result = http_json(f"{MAX_BASE}/answers?{query}", method="POST", obj={}, timeout=20)
    if not 200 <= status < 300:
        raise RuntimeError(f"MAX /answers HTTP {status}: {result}")


def send_menu(chat_id, welcome=False):
    clear_session(chat_id)
    send_message(chat_id, WELCOME if welcome else "Выберите раздел 👇", buttons=MENU_BUTTONS)


def send_works(chat_id):
    first = [{"type": "image", "payload": {"url": u}} for u in PORTFOLIO_URLS[:10]]
    second = [{"type": "image", "payload": {"url": u}} for u in PORTFOLIO_URLS[10:20]]
    try:
        send_message(chat_id, "🖼 Некоторые наши работы:", attachments=first)
        time.sleep(0.7)
        send_message(chat_id, "Ещё проекты 👇", buttons=BACK_BUTTONS, attachments=second)
    except Exception as exc:
        print("portfolio error:", repr(exc), flush=True)
        send_message(
            chat_id,
            "Фотографии сейчас не загрузились. Посмотреть работы можно на сайте:\n"
            "portfolio-maxdizain.ru",
            buttons=BACK_BUTTONS,
        )


def start_flow(chat_id, kind):
    clear_session(chat_id)
    if kind == "kitchen":
        set_session(chat_id, kind, "name", {})
        send_message(chat_id, "🍽 Начнём заявку на кухню.\n\nКак к Вам обращаться?", buttons=BACK_BUTTONS)
    elif kind == "wardrobe":
        set_session(chat_id, kind, "name", {})
        send_message(chat_id, "🚪 Начнём заявку на шкаф.\n\nКак к Вам обращаться?", buttons=BACK_BUTTONS)
    elif kind == "design":
        set_session(chat_id, kind, "name", {})
        send_message(chat_id, "📐 Начнём заявку на дизайн-проект.\n\nКак к Вам обращаться?", buttons=BACK_BUTTONS)
    elif kind == "question":
        set_session(chat_id, kind, "question", {})
        send_message(
            chat_id,
            "💬 Напишите Ваш вопрос одним сообщением. "
            "Если он требует уточнения специалиста, я сохраню его для Катерины.",
            buttons=BACK_BUTTONS,
        )


def ask_for_contact(chat_id):
    send_message(
        chat_id,
        "Остался номер телефона для связи.\n\n"
        "Нажмите «Поделиться номером» — MAX передаст номер, привязанный к Вашему аккаунту. "
        "Если удобнее, просто напишите номер сообщением.",
        buttons=CONTACT_BUTTONS,
    )


def next_flow_message(chat_id, session, text):
    flow, step, data = session["flow"], session["step"], dict(session["data"])
    value = text.strip()

    if step == "name":
        data["name"] = value[:120]
        set_session(chat_id, flow, "city", data)
        send_message(chat_id, "В каком городе находится объект?", buttons=BACK_BUTTONS)
        return

    if step == "city":
        data["city"] = value[:160]
        if flow == "kitchen":
            set_session(chat_id, flow, "dimensions", data)
            send_message(
                chat_id,
                "Есть размеры помещения или примерные размеры кухни? "
                "Напишите их. Если размеров пока нет — так и напишите.",
                buttons=BACK_BUTTONS,
            )
        elif flow == "wardrobe":
            set_session(chat_id, flow, "type_dims", data)
            send_message(
                chat_id,
                "Какой шкаф нужен и какие у него примерные размеры? "
                "Например: встроенный, 2400×600×2600 мм.",
                buttons=BACK_BUTTONS,
            )
        elif flow == "design":
            set_session(chat_id, flow, "area", data)
            send_message(
                chat_id,
                "Какая площадь и какие помещения нужно спроектировать?",
                buttons=BACK_BUTTONS,
            )
        return

    if flow == "kitchen" and step == "dimensions":
        data["dimensions"] = value[:700]
        set_session(chat_id, flow, "wishes", data)
        send_message(
            chat_id,
            "Расскажите коротко о пожеланиях: стиль, цвет, техника, особенности планировки.",
            buttons=BACK_BUTTONS,
        )
        return

    if flow == "wardrobe" and step == "type_dims":
        data["type_dims"] = value[:700]
        set_session(chat_id, flow, "filling", data)
        send_message(
            chat_id,
            "Что важно по наполнению: полки, ящики, штанги, обувь, хозяйственный блок и т. п.?",
            buttons=BACK_BUTTONS,
        )
        return

    if flow == "design" and step == "area":
        data["area"] = value[:700]
        set_session(chat_id, flow, "wishes", data)
        send_message(
            chat_id,
            "Что требуется от проекта и какие есть пожелания по интерьеру?",
            buttons=BACK_BUTTONS,
        )
        return

    if step in {"wishes", "filling"}:
        data[step] = value[:1500]
        set_session(chat_id, flow, "phone", data)
        ask_for_contact(chat_id)
        return

    if flow == "question" and step == "question":
        answer = faq_answer(value)
        if answer:
            clear_session(chat_id)
            send_message(chat_id, answer, buttons=BACK_BUTTONS)
            return
        data["question"] = value[:2500]
        set_session(chat_id, flow, "phone", data)
        send_message(
            chat_id,
            "Сохранила вопрос для Катерины. Оставьте номер для ответа:",
            buttons=CONTACT_BUTTONS,
        )
        return

    if step == "phone":
        phone = normalize_phone(value)
        if not phone:
            send_message(
                chat_id,
                "Не смогла распознать номер. Напишите его, например: +7 999 123-45-67, "
                "или нажмите «Поделиться номером».",
                buttons=CONTACT_BUTTONS,
            )
            return
        finish_lead(chat_id, None, flow, data, phone, False)
        return


def normalize_phone(text):
    digits = re.sub(r"\D+", "", text or "")
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    if len(digits) == 11 and digits.startswith("7"):
        return "+" + digits
    return ""


def parse_contact_attachment(message):
    body = message.get("body") or {}
    for item in body.get("attachments") or []:
        if item.get("type") != "contact":
            continue
        payload = item.get("payload") or {}
        vcf = payload.get("vcf_info") or ""
        claimed_hash = payload.get("hash") or ""
        phone = ""
        m = re.search(r"(?im)^TEL(?:;[^:]*)?:(.+)$", vcf)
        if m:
            phone = normalize_phone(m.group(1))
        verified = False
        if vcf and claimed_hash:
            calculated = hmac.new(
                MAX_TOKEN.encode("utf-8"),
                vcf.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            verified = hmac.compare_digest(calculated.lower(), str(claimed_hash).lower())
        return phone, verified
    return "", False


def faq_answer(text):
    key = (text or "").casefold()
    for phrases, answer in FAQ:
        if any(p in key for p in phrases):
            return answer
    return None


def finish_lead(chat_id, user_id, kind, data, phone, verified):
    lead_id = save_lead(chat_id, user_id, kind, data, phone, verified)
    clear_session(chat_id)
    label = {
        "kitchen": "кухню",
        "wardrobe": "шкаф",
        "design": "дизайн-проект",
        "question": "вопрос",
    }.get(kind, "заявку")
    send_message(
        chat_id,
        f"Спасибо! Заявка на {label} принята ✅\n"
        "Катерина свяжется с Вами и уточнит детали.",
        buttons=BACK_BUTTONS,
    )
    if ADMIN_CHAT_ID:
        detail_lines = [f"Новая заявка #{lead_id}", f"Тип: {kind}"]
        if data.get("name"):
            detail_lines.append(f"Имя: {data['name']}")
        if data.get("city"):
            detail_lines.append(f"Город: {data['city']}")
        detail_lines.append(f"Телефон: {phone or 'не указан'}")
        for k, v in data.items():
            if k not in {"name", "city", "phone"} and v:
                detail_lines.append(f"{k}: {v}")
        try:
            send_message(ADMIN_CHAT_ID, "\n".join(detail_lines)[:3900])
        except Exception as exc:
            print("admin notify error:", repr(exc), flush=True)


def extract_message(update):
    return update.get("message") or (update.get("callback") or {}).get("message") or {}


def extract_chat_id(update):
    if update.get("chat_id") is not None:
        return update.get("chat_id")
    message = extract_message(update)
    recipient = message.get("recipient") or {}
    if recipient.get("chat_id") is not None:
        return recipient.get("chat_id")
    callback = update.get("callback") or {}
    if callback.get("chat_id") is not None:
        return callback.get("chat_id")
    return None


def extract_user_id(update):
    user = update.get("user") or {}
    if user.get("user_id") is not None:
        return user.get("user_id")
    message = extract_message(update)
    sender = message.get("sender") or {}
    return sender.get("user_id")


def handle_callback(update):
    callback = update.get("callback") or {}
    callback_id = callback.get("callback_id")
    payload = callback.get("payload") or ""
    chat_id = extract_chat_id(update)
    if callback_id:
        try:
            answer_callback(callback_id)
        except Exception as exc:
            print("callback answer error:", repr(exc), flush=True)
    if chat_id is None:
        return

    if payload == "menu:main":
        send_menu(chat_id)
    elif payload == "menu:who":
        clear_session(chat_id)
        send_message(chat_id, WHO_TEXT, buttons=BACK_BUTTONS)
    elif payload == "menu:kitchen":
        start_flow(chat_id, "kitchen")
    elif payload == "menu:wardrobe":
        start_flow(chat_id, "wardrobe")
    elif payload == "menu:design":
        clear_session(chat_id)
        send_message(chat_id, DESIGN_TEXT, buttons=[
            [{"type": "callback", "text": "📐 Оставить заявку", "payload": "flow:design"}],
            [{"type": "callback", "text": "⬅️ В главное меню", "payload": "menu:main"}],
        ])
    elif payload == "flow:design":
        start_flow(chat_id, "design")
    elif payload == "menu:price":
        clear_session(chat_id)
        send_message(chat_id, PRICE_TEXT, buttons=PRICE_BUTTONS)
    elif payload == "menu:works":
        clear_session(chat_id)
        send_works(chat_id)
    elif payload == "menu:contacts":
        clear_session(chat_id)
        send_message(chat_id, CONTACTS_TEXT, buttons=BACK_BUTTONS)
    elif payload == "menu:question":
        start_flow(chat_id, "question")


def handle_message(update):
    message = extract_message(update)
    sender = message.get("sender") or {}
    if sender.get("is_bot"):
        return
    chat_id = extract_chat_id(update)
    user_id = extract_user_id(update)
    if chat_id is None:
        return

    phone, verified = parse_contact_attachment(message)
    session = get_session(chat_id)
    if phone and session and session["step"] == "phone":
        finish_lead(chat_id, user_id, session["flow"], session["data"], phone, verified)
        return

    body = message.get("body") or {}
    text = (body.get("text") or message.get("text") or "").strip()
    if not text:
        if body.get("attachments"):
            send_message(
                chat_id,
                "Файл или фото получила. Если Вы заполняете заявку, добавьте, пожалуйста, описание сообщением.",
                buttons=BACK_BUTTONS,
            )
        return

    low = text.casefold()
    if low in {"/start", "start", "меню", "/menu", "menu"}:
        send_menu(chat_id, welcome=(low in {"/start", "start"}))
        return

    session = get_session(chat_id)
    if session:
        next_flow_message(chat_id, session, text)
        return

    known = faq_answer(text)
    if known:
        send_message(chat_id, known, buttons=BACK_BUTTONS)
        return

    send_message(
        chat_id,
        "Чтобы я точнее помогла, выберите нужный раздел в меню. "
        "Если вопрос нестандартный — нажмите «Задать вопрос».",
        buttons=MENU_BUTTONS,
    )


def handle_update(update):
    update_type = update.get("update_type")
    if update_type == "bot_added":
        save_channel(
            update.get("chat_id"),
            bool(update.get("is_channel")),
            (update.get("user") or {}).get("user_id"),
        )
        print(
            "bot_added",
            update.get("chat_id"),
            "channel" if update.get("is_channel") else "chat",
            flush=True,
        )
        return
    if update_type == "bot_started":
        chat_id = extract_chat_id(update)
        if chat_id is not None:
            send_menu(chat_id, welcome=True)
        return
    if update_type == "message_callback":
        handle_callback(update)
        return
    if update_type == "message_created":
        handle_message(update)
        return


work_queue = queue.Queue(maxsize=1000)


def worker():
    while True:
        update = work_queue.get()
        try:
            handle_update(update)
        except Exception as exc:
            print("update error:", repr(exc), flush=True)
        finally:
            work_queue.task_done()


def subscriptions():
    return http_json(f"{MAX_BASE}/subscriptions", method="GET", timeout=25)


def subscribe_webhook():
    if not PUBLIC_BASE_URL:
        print("AUTO SUBSCRIBE skipped: PUBLIC_BASE_URL/DOMAIN is not set", flush=True)
        return False
    target = PUBLIC_BASE_URL.rstrip("/") + "/webhook"
    try:
        status, result = subscriptions()
        if 200 <= status < 300:
            for sub in result.get("subscriptions") or []:
                if isinstance(sub, dict) and sub.get("url") == target:
                    print("MAX webhook already subscribed:", target, flush=True)
                    return True
    except Exception as exc:
        print("GET subscriptions error:", repr(exc), flush=True)

    body = {
        "url": target,
        "update_types": [
            "bot_added",
            "bot_removed",
            "bot_started",
            "message_created",
            "message_callback",
        ],
        "secret": WEBHOOK_SECRET,
    }
    status, result = http_json(f"{MAX_BASE}/subscriptions", method="POST", obj=body, timeout=35)
    ok = 200 <= status < 300 and result.get("success", True) is not False
    print("MAX webhook subscribe:", status, result, flush=True)
    return ok


def auto_subscribe_worker():
    if not AUTO_SUBSCRIBE or not PUBLIC_BASE_URL:
        return
    time.sleep(15)
    for attempt in range(6):
        try:
            if subscribe_webhook():
                return
        except Exception as exc:
            print("AUTO SUBSCRIBE error:", repr(exc), flush=True)
        time.sleep(30 + attempt * 30)


class Handler(BaseHTTPRequestHandler):
    server_version = "MaximumFurnitureBot/2.0"

    def log_message(self, fmt, *args):
        print("WEB", fmt % args, flush=True)

    def respond(self, status, body=b"OK", content_type="text/plain; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            payload = json.dumps(
                {"ok": True, "service": "maximum-max-bot", "api": MAX_BASE},
                ensure_ascii=False,
            ).encode("utf-8")
            self.respond(200, payload, "application/json; charset=utf-8")
            return
        self.respond(404, b"Not found")

    def do_POST(self):
        if self.path != "/webhook":
            self.respond(404, b"Not found")
            return

        got = self.headers.get("X-Max-Bot-Api-Secret", "")
        if not hmac.compare_digest(got, WEBHOOK_SECRET):
            self.respond(403, b"Forbidden")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.respond(400, b"Bad Content-Length")
            return
        if length <= 0 or length > 1024 * 1024:
            self.respond(413, b"Invalid body size")
            return

        try:
            update = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self.respond(400, b"Bad JSON")
            return

        try:
            work_queue.put_nowait(update)
        except queue.Full:
            self.respond(503, b"Busy")
            return

        # MAX requires HTTP 200 quickly. Business logic runs in the worker thread.
        self.respond(200, b"OK")


def main():
    init_db()
    threading.Thread(target=worker, name="maxbot-worker", daemon=True).start()
    threading.Thread(target=auto_subscribe_worker, name="maxbot-subscribe", daemon=True).start()
    print(f"MAX bot starting on {HOST}:{PORT}; db={DB_PATH}", flush=True)
    print(f"MAX API: {MAX_BASE}", flush=True)
    if PUBLIC_BASE_URL:
        print(f"Public URL: {PUBLIC_BASE_URL}", flush=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
