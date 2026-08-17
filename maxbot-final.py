#!/usr/bin/env python3
import json
import re
import time
import uuid
import urllib.parse
import urllib.request
import urllib.error

MAX_BASE = "https://platform-api2.max.ru"
GIGA_BASE = "https://api.giga.chat"
GIGA_OAUTH = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGA_MODEL = "GigaChat-2-Pro"

MAX_TOKEN = open("/root/.max_token", "r", encoding="utf-8").read().strip()
GIGA_KEY = open("/root/.giga_key", "r", encoding="utf-8").read().strip()

SYSTEM_PROMPT = """Ты цифровой ассистент семейной мебельной фабрики «МАКСимум мебель».
Компания работает с 2012 года, реализовано более 500 проектов. Своё производство.
Полный цикл: замер → дизайн-проект с 3D-визуализацией → производство → доставка → сборка → уборка после монтажа.
Услуги: кухни на заказ, шкафы и гардеробные, корпусная мебель, дизайн интерьера.
Очно работаем по Нижнему Новгороду, Кстово и Нижегородской области. Дизайн-проекты делаем онлайн по всей России.

Цены на дизайн:
— эскизный проект / планировка — от 450 ₽/м²;
— полный дизайн-проект — от 500 ₽/м²;
— минимальный заказ — 1000 ₽;
— консультация бесплатная.

Никогда не придумывай точную стоимость мебели без размеров, материалов, фурнитуры и наполнения.
Для предварительного расчёта мебели уточняй только нужные данные: что требуется, город, размеры или план, фото помещения, пожелания по материалам/стилю, наполнение и технику.
Не придумывай акции, цены и условия.
Если нужен человек, предложи связаться с Катериной: +7 (915) 953-09-29.

Отвечай только по-русски, тепло, уверенно, профессионально и без воды. Клиенту — на «Вы».
Не используй Markdown: никаких звёздочек, решёток и обратных кавычек.
"""

WHO_TEXT = """«МАКСимум мебель» — семейная мебельная фабрика полного цикла.

Работаем с 2012 года, реализовали более 500 проектов. Своё производство.

Что делаем:
🍽 кухни на заказ;
🗄 шкафы, гардеробные и корпусную мебель;
🎨 дизайн интерьера и 3D-визуализацию.

Полный цикл: замер → проект и 3D → производство → доставка → сборка → уборка после монтажа."""

BIO_TEXT = """📖 Наша история началась в 2012 году.

«МАКСимум мебель» — семейный бизнес Катерины и Максима. Начинали очень скромно, постепенно развивали мебельное направление, открывали салоны, осваивали кухни и индивидуальную мебель.

Сегодня мы сами занимаемся замерами, дизайном, проектами и расчётами, а заказ сопровождаем от идеи до установленной мебели.

Для нас заказчик — не номер в базе, а человек, которому нужен удобный, красивый и реально выполнимый проект."""

KITCHEN_TEXT = """🍽 Для предварительного расчёта кухни пришлите, пожалуйста:

1. Ваш город.
2. Примерные размеры кухни или план помещения.
3. 2–4 фотографии помещения.
4. Желаемую планировку.
5. Какая встраиваемая техника планируется.
6. Если знаете — пожелания по фасадам, столешнице и фурнитуре.

Точную стоимость без размеров и комплектации я придумывать не буду. По исходным данным сможем сделать нормальный предварительный расчёт."""

WARDROBE_TEXT = """🗄 Для предварительного расчёта шкафа пришлите, пожалуйста:

1. Ваш город.
2. Размеры: ширина, высота и глубина.
3. Фото места установки.
4. Для чего нужен шкаф.
5. Желаемое наполнение: полки, штанги, ящики.
6. Пожелания по фасадам.

По этим данным можно подготовить предварительный расчёт."""

DESIGN_TEXT = """🎨 Дизайн интерьера

Эскизный проект / планировка — от 450 ₽/м².
Полный дизайн-проект с планировкой, 3D-визуализацией и рабочими чертежами — от 500 ₽/м².
Минимальный заказ — 1000 ₽.
Консультация бесплатная.

Дизайн-проекты делаем онлайн по всей России.

Напишите площадь помещения и какой проект Вам нужен — эскизный или полный."""

PRICE_TEXT = """💰 Стоимость зависит от того, что Вам нужно.

🎨 Дизайн интерьера:
— эскизный проект / планировка — от 450 ₽/м²;
— полный дизайн-проект — от 500 ₽/м²;
— минимальный заказ — 1000 ₽.

🍽 Кухни и 🗄 шкафы:
цена зависит от размеров, материалов, фасадов, столешницы, фурнитуры и наполнения. Без исходных данных точную стоимость не называем.

Напишите, что нужно рассчитать: дизайн, кухню или шкаф."""

CONTACTS_TEXT = """📞 Контакты «МАКСимум мебель»

Катерина: +7 (915) 953-09-29
Максим: +7 (910) 799-64-71

🌐 portfolio-maxdizain.ru
🌐 maxmebel-52.ru
🔵 vk.com/maximummebel
💬 MAX: max.ru/channel_maxmebel_52"""

PHOTO_URLS = ['https://sun9-83.vkuserphoto.ru/impg/c629318/v629318829/23d27/Or2MUjrcOlo.jpg?size=980x735&quality=96&sign=6aaf473c2225ae508f2ef9cd3552f28c&type=album', 'https://sun9-41.vkuserphoto.ru/impg/c631531/v631531829/501b/_X8fUxz1yPo.jpg?size=1280x960&quality=96&sign=0bdc826d79313a8665ae2f13d354f16b&type=album', 'https://sun9-18.vkuserphoto.ru/impg/c629318/v629318829/23cc1/Mc2JgdDOoas.jpg?size=960x720&quality=96&sign=1df90ba9c01b8e914321a688be9e9cf9&type=album', 'https://sun9-62.vkuserphoto.ru/impg/c629318/v629318829/23cca/0PzI0Id7y_I.jpg?size=807x605&quality=96&sign=d0f0b5664d0270ab6cb9226c08309b13&type=album', 'https://sun9-38.vkuserphoto.ru/impg/c633420/v633420829/f162/xuRS9RksTLs.jpg?size=1280x791&quality=96&sign=f51dc309afa616a4d5df99c3f95acbb8&type=album', 'https://sun9-76.vkuserphoto.ru/impg/c633420/v633420829/f16c/VOA3a_0ngL4.jpg?size=604x416&quality=96&sign=af9ad2fc07cc6f76685f89c5253bdc32&type=album', 'https://sun9-87.vkuserphoto.ru/impg/c633223/v633223829/9a79/DOQo5BAjn6w.jpg?size=1120x800&quality=96&sign=98ae554fc0c378b1eb577eae1ec8c69c&type=album', 'https://sun9-15.vkuserphoto.ru/impg/c631216/v631216829/f59c/BM2b0YGz7RU.jpg?size=289x381&quality=96&sign=66e2555ee4e5d23f998687657b4de3b8&type=album', 'https://sun9-20.vkuserphoto.ru/impg/c630327/v630327829/9de6/-4UOHLkjCPs.jpg?size=723x605&quality=96&sign=3a508974d3247a5f94510d6ace59b89e&type=album', 'https://sun9-51.vkuserphoto.ru/impg/c629318/v629318829/23c33/ssiVN2REWjg.jpg?size=421x561&quality=96&sign=5b470a589bf88ef231df2123ffc4c80e&type=album', 'https://sun9-71.vkuserphoto.ru/impg/c630327/v630327829/9e3f/AmI5wZ8ozqk.jpg?size=979x456&quality=96&sign=174b50cc3873e366d47594128c83c3d3&type=album', 'https://sun9-13.vkuserphoto.ru/impg/c628825/v628825829/3bcc9/buCtjEXbYz8.jpg?size=667x939&quality=96&sign=b332cbdbe1f11ba689560cdb32c405a1&type=album', 'https://sun9-33.vkuserphoto.ru/impg/c628825/v628825829/3bcd2/XLsdqCDWdbM.jpg?size=576x807&quality=96&sign=07c60dc3d4f8c3bb41f69e77f9540877&type=album', 'https://sun9-67.vkuserphoto.ru/impg/c631626/v631626829/8b4c/IIVbrbzcA6k.jpg?size=1081x1080&quality=96&sign=bbe1aefda3cfe491a9b0d3ef04948f15&type=album', 'https://sun9-9.vkuserphoto.ru/impg/c631218/v631218829/1d71e/gvyVOts8KrU.jpg?size=960x720&quality=96&sign=fd9818e0350bfe97316df66fd8803070&type=album', 'https://sun9-7.vkuserphoto.ru/impg/c629318/v629318829/23c9d/Y18VOPUw-J0.jpg?size=960x720&quality=96&sign=2b7cf51a95cf05b36be380310b45d9c2&type=album', 'https://sun9-32.vkuserphoto.ru/impg/c629318/v629318829/23ca6/0p7u2ojmbSI.jpg?size=604x453&quality=96&sign=fac65bc404d54805cb31e27acfba35fd&type=album', 'https://sun9-74.vkuserphoto.ru/impg/c629318/v629318829/23cd3/j_s6FTwZrcE.jpg?size=960x720&quality=96&sign=829620a0301e4af1f4402af1ed151991&type=album', 'https://sun9-13.vkuserphoto.ru/impg/c629318/v629318829/23cdc/wZh43R_d3Y0.jpg?size=604x453&quality=96&sign=2f8e8339b1f4ddac7c31717c6e8d71d0&type=album', 'https://sun9-87.vkuserphoto.ru/impg/c630327/v630327829/9e6d/N5vvKy8Ee5c.jpg?size=959x566&quality=96&sign=2043e76918717e382787f005830eb0ce&type=album']

MENU_BUTTONS = [
    [
        {"type": "message", "text": "ℹ️ Кто мы и чем занимаемся"},
        {"type": "message", "text": "📖 Наша биография"}
    ],
    [
        {"type": "message", "text": "🍽 Заказать Кухню"},
        {"type": "message", "text": "🗄 Заказать Шкаф"}
    ],
    [
        {"type": "message", "text": "🎨 Заказать Дизайн-проект"},
        {"type": "message", "text": "💰 Узнать стоимость"}
    ],
    [
        {"type": "message", "text": "📞 Наши Контакты"},
        {"type": "message", "text": "🏆 Наши Работы"}
    ],
    [
        {"type": "link", "text": "🔵 Мы ВКонтакте", "url": "https://vk.com/maximummebel"}
    ]
]

CANNED = {
    "ℹ️ кто мы и чем занимаемся": WHO_TEXT,
    "кто мы и чем занимаемся": WHO_TEXT,
    "кто мы": WHO_TEXT,
    "📖 наша биография": BIO_TEXT,
    "наша биография": BIO_TEXT,
    "биография": BIO_TEXT,
    "🍽 заказать кухню": KITCHEN_TEXT,
    "заказать кухню": KITCHEN_TEXT,
    "🗄 заказать шкаф": WARDROBE_TEXT,
    "заказать шкаф": WARDROBE_TEXT,
    "🎨 заказать дизайн-проект": DESIGN_TEXT,
    "заказать дизайн-проект": DESIGN_TEXT,
    "дизайн-проект": DESIGN_TEXT,
    "💰 узнать стоимость": PRICE_TEXT,
    "узнать стоимость": PRICE_TEXT,
    "стоимость": PRICE_TEXT,
    "📞 наши контакты": CONTACTS_TEXT,
    "наши контакты": CONTACTS_TEXT,
    "контакты": CONTACTS_TEXT,
}

WORK_KEYS = {"🏆 наши работы", "наши работы", "работы"}
MENU_KEYS = {"меню", "/menu", "menu", "кнопки"}
START_KEYS = {"/start", "start"}

history = {}
giga_token = None
giga_token_time = 0.0

def http_json(url, method="GET", headers=None, obj=None, raw=None, timeout=40):
    hdr = dict(headers or {})
    data = raw
    if obj is not None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        hdr.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdr, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(body) if body else {}
        except Exception:
            parsed = {"raw": body}
        return exc.code, parsed

def max_headers():
    return {"Authorization": MAX_TOKEN}

def send_message(chat_id, text, attachments=None):
    query = urllib.parse.urlencode({"chat_id": str(chat_id)})
    payload = {"text": text}
    if attachments:
        payload["attachments"] = attachments
    status, answer = http_json(
        MAX_BASE + "/messages?" + query,
        method="POST",
        headers=max_headers(),
        obj=payload,
        timeout=35,
    )
    if not 200 <= status < 300:
        raise RuntimeError("MAX send error %s %s" % (status, answer))
    return answer

def send_menu(chat_id, welcome=False):
    if welcome:
        text = "Здравствуйте! Я ассистент «МАКСимум мебель» 👋\n\nВыберите из меню 👆"
    else:
        text = "Выберите из меню 👆"
    attachment = {
        "type": "inline_keyboard",
        "payload": {"buttons": MENU_BUTTONS}
    }
    send_message(chat_id, text, [attachment])

def send_works(chat_id):
    first = [{"type": "image", "payload": {"url": url}} for url in PHOTO_URLS[:10]]
    second = [{"type": "image", "payload": {"url": url}} for url in PHOTO_URLS[10:20]]
    try:
        send_message(chat_id, "🏆 Наши работы 👇", first)
        time.sleep(0.8)
        send_message(chat_id, "Ещё работы 👇", second)
    except Exception as exc:
        print("PHOTO ERROR", repr(exc), flush=True)
        send_message(
            chat_id,
            "Часть фотографий сейчас не загрузилась. Все работы можно посмотреть здесь:\n"
            "🌐 portfolio-maxdizain.ru\n"
            "🔵 vk.com/maximummebel"
        )

def get_giga_token(force=False):
    global giga_token, giga_token_time
    if not force and giga_token and time.time() - giga_token_time < 1500:
        return giga_token

    data = urllib.parse.urlencode({"scope": "GIGACHAT_API_PERS"}).encode("utf-8")
    headers = {
        "Authorization": "Basic " + GIGA_KEY,
        "RqUID": str(uuid.uuid4()),
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    request = urllib.request.Request(GIGA_OAUTH, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError("Giga OAuth error %s %s" % (exc.code, body))

    token = result.get("access_token")
    if not token:
        raise RuntimeError("Giga OAuth: no access_token")
    giga_token = token
    giga_token_time = time.time()
    return token

def clean_ai_text(text):
    text = (text or "").strip()
    text = text.replace("```", "")
    text = text.replace("**", "")
    text = text.replace("__", "")
    text = text.replace("`", "")
    text = re.sub(r"(?m)^\s*#{1,6}\s*", "", text)
    return text.strip()

def ask_giga(chat_id, user_text):
    items = history.setdefault(chat_id, [])
    items.append({"role": "user", "content": user_text})
    items[:] = items[-12:]

    def call(token):
        payload = {
            "model": GIGA_MODEL,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + items,
            "stream": False,
            "temperature": 0.35,
            "max_tokens": 900,
        }
        return http_json(
            GIGA_BASE + "/v1/chat/completions",
            method="POST",
            headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
            obj=payload,
            timeout=60,
        )

    status, result = call(get_giga_token(False))
    if status == 401:
        status, result = call(get_giga_token(True))
    if not 200 <= status < 300:
        raise RuntimeError("Giga chat error %s %s" % (status, result))

    answer = clean_ai_text(result["choices"][0]["message"]["content"])
    items.append({"role": "assistant", "content": answer})
    items[:] = items[-12:]
    return answer

def get_updates(marker=None, timeout=30):
    params = {"timeout": str(timeout), "types": "message_created,bot_started"}
    if marker is not None:
        params["marker"] = str(marker)
    url = MAX_BASE + "/updates?" + urllib.parse.urlencode(params)
    status, result = http_json(url, headers=max_headers(), timeout=timeout + 10)
    if not 200 <= status < 300:
        raise RuntimeError("MAX updates error %s %s" % (status, result))
    return result

def chat_id_from(update):
    if update.get("chat_id"):
        return update.get("chat_id")
    message = update.get("message") or {}
    recipient = message.get("recipient") or {}
    return recipient.get("chat_id")

def handle_update(update):
    update_type = update.get("update_type")

    if update_type == "bot_started":
        chat_id = chat_id_from(update)
        if chat_id:
            send_menu(chat_id, welcome=True)
        return

    if update_type != "message_created":
        return

    message = update.get("message") or {}
    sender = message.get("sender") or {}
    if sender.get("is_bot"):
        return

    chat_id = chat_id_from(update)
    body = message.get("body") or {}
    text = (body.get("text") or message.get("text") or "").strip()
    if not chat_id or not text:
        return

    key = text.casefold().strip()

    if key in START_KEYS:
        send_menu(chat_id, welcome=True)
        return

    if key in MENU_KEYS:
        send_menu(chat_id, welcome=False)
        return

    if key in WORK_KEYS:
        send_works(chat_id)
        return

    if key in CANNED:
        send_message(chat_id, CANNED[key])
        return

    try:
        answer = ask_giga(chat_id, text)
    except Exception as exc:
        print("GIGA ERROR", repr(exc), flush=True)
        answer = (
            "Сейчас не получилось получить ответ от ИИ. "
            "Попробуйте ещё раз через минуту или напишите Катерине: +7 (915) 953-09-29."
        )
    send_message(chat_id, answer)

def main():
    marker = None

    try:
        initial = get_updates(None, 0)
        marker = initial.get("marker")
    except Exception as exc:
        print("INITIAL UPDATE ERROR", repr(exc), flush=True)

    print("MAXBOT FINAL START", marker, flush=True)

    while True:
        try:
            result = get_updates(marker, 30)
            next_marker = result.get("marker")
            for update in result.get("updates") or []:
                try:
                    handle_update(update)
                except Exception as exc:
                    print("UPDATE ERROR", repr(exc), flush=True)
                time.sleep(0.55)
            if next_marker is not None:
                marker = next_marker
        except Exception as exc:
            print("LOOP ERROR", repr(exc), flush=True)
            time.sleep(5)

if __name__ == "__main__":
    main()
