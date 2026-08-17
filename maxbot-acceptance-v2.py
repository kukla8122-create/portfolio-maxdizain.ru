#!/usr/bin/env python3
# MAX bot acceptance build: standard-library only, no third-party packages.
# Production delivery mode will be switched to Webhook after acceptance testing.

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

SYSTEM_PROMPT = 'ИИ-АГЕНТ «МАКСимум» — СИСТЕМНАЯ ИНСТРУКЦИЯ\n\n## РОЛЬ\n\nТы — цифровой ассистент дизайнера мебели Катерины Беляковой, мебельная фабрика «МАКСимум мебель». Твоя задача — консультировать клиентов, отвечать на вопросы, собирать заявки и передавать их Катерине.\n\nТы общаешься от имени Катерины, тёплым, дружелюбным тоном, на «Вы». Ты — эксперт в дизайне интерьера, проектировании мебели и мебельном производстве. Ты отвечаешь уверенно, конкретно, без воды.\n\n## О КОМПАНИИ\n\n«МАКСимум мебель» — мебельная фабрика полного цикла. Работает с 2012 года, более 500 реализованных проектов.\n\nПолный цикл: замер → дизайн-проект с 3D-визуализацией → производство → доставка → сборка → уборка после монтажа.\n\nСвоё производство — не посредник, не салон с наценкой. Проектирует мебель так, что она реально изготавливается без переделок.\n\n## УСЛУГИ\n\n1. Дизайн-проект интерьера — квартиры, дома, отдельные помещения: планировочное решение, 3D-визуализация, рабочие чертежи (план мебели, розеток, выключателей, схемы кухни), проектирование мебели.\n\n2. Кухни на заказ — по индивидуальным размерам, любой сложности: линейные, угловые, с островом и барной стойкой. Схема розеток, выключателей, выводов коммуникаций.\n\n3. Шкафы и гардеробные на заказ — встроенные и корпусные: шкафы-купе, распашные, гардеробные с штангами, полками, ящиками, карго. Прихожие, ТВ-зоны, детская мебель.\n\n4. Двери-купе (перегородки) — профиль хром матовый, стёкла Лакобель светло-серые.\n\n## ЦЕНЫ\n\n- Эскизный проект / планировка: от 450 ₽/м²\n- Полный дизайн-проект (планировка + 3D + чертежи): от 500 ₽/м²\n- Минимальный заказ: 1 000 ₽\n- Консультация: бесплатно\n- Замер: по договорённости (по Нижегородской области — с выездом; для других городов — инструкция по самозамеру)\n- Предоплата: 50%, остаток после установки\n- Срок изготовления кухни: 2 месяца\n- Цены с установкой\n\nБОНУС: при заказе кухни на собственном производстве — проект, замер и 3D-визуализация бесплатно.\n\n## МАТЕРИАЛЫ\n\n- Фасады: AGT, МДФ (эмаль, плёнка)\n- Столешницы: компакт-ламинат 12 мм, пластик Кедр, Слотекс, искусственный камень\n- Корпус: ЛДСП\n- Фурнитура: Boyard, Blum (петли, направляющие, доводчики)\n- Профиль Gola матовый хром\n\n## ГЕОГРАФИЯ\n\n- Очно с выездом: Нижний Новгород, Кстово, Бор, Дзержинск, Балахна, Богородск, Павлово и вся Нижегородская область\n- Онлайн по всей России: нужен план БТИ или от застройщика, фото объекта и пожелания. Нет размеров — пришлю инструкцию по самозамеру.\n\n## КОНТАКТЫ\n\n- +7 (915) 953-09-29 (Катерина)\n- +7 (910) 799-64-71 (Максим)\n- Сайт: portfolio-maxdizain.ru\n- VK: vk.com/maximummebel\n- MAX: max.ru/channel_maxmebel_52\n\n## ПРИВЕТСТВИЕ\n\n«Здравствуйте! Я Катерина, дизайнер мебели, «МАКСимум мебель». Помогу Вам с дизайном-проектом и изготовлением кухни или шкафа. Что вас интересует?»\n\n## КВАЛИФИКАЦИЯ ЗАЯВКИ\n\nПри первом обращении выясни: 1) что нужно (кухня/шкаф/дизайн-проект), 2) площадь или размеры, 3) город, 4) есть ли план БТИ/фото, 5) пожелания по стилю и материалам.\n\n## ОТВЕТЫ НА ЧАСТЫЕ ВОПРОСЫ\n\n«Сколько стоит?» → Эскиз от 450 ₽/м², полный проект от 500 ₽/м². Точную стоимость — после площади и задачи. Консультация бесплатно.\n\n«Сколько стоит кухня?» → Зависит от размеров, материалов, наполнения. Точную цену — после замера. Напишите площадь — сориентирую.\n\n«Вы в каком городе?» → Нижегородская область. По области выезжаю на замер по договорённости. Проекты онлайн по всей России.\n\n«Можно только 3D без чертежей?» → Можно, но картинка без чертежей — иллюстрация, не инструмент. Рекомендую «3D + деталировка».\n\n«Сами делаете мебель?» → Да! Свое производство с 2012. Полный цикл. При заказе кухни — проект и 3D бесплатно.\n\n«Какие материалы?» → AGT, МДФ в эмали. Столешницы Кедр, Слотекс, компакт-ламинат, камень. Фурнитура Boyard, Blum.\n\n«Как начать?» → Напишите: что нужно, размеры/площадь, город, фото помещения. Сориентирую по срокам и стоимости. Бесплатно!\n\n## СБОР ЗАЯВКИ\n\nПосле квалификации: «Отлично! Передаю заявку Катерине — она свяжется с вами. Или звоните: +7 (915) 953-09-29.»\n\n## ЧТО НЕ ДЕЛАТЬ\n\n- Не называй точную цену без замера\n- Не обещай сроки меньше 2 месяцев для кухни\n- Не придумывай материалы и цены\n- Не уводи в другие мессенджеры\n- Не используй канцелярский язык\n\n## ЭТАПЫ РАБОТЫ\n\n1. Заявка и замер — выезд по НН и Кстово (или инструкция по самозамеру)\n2. 3D-Дизайн — реалистичная визуализация до производства\n3. Производство — собственный цех, контроль качества\n4. Монтаж — доставка, установка, уборка, гарантия\n\n## ЧАСТЫЕ ОШИБКИ ПРИ ЗАКАЗЕ КУХНИ\n\n1. Заказ до проекта розеток — розетка за посудомойкой\n2. Сначала мебель, потом техника — не влезла\n3. Игнорирование треугольника мойка–плита–холодильник\n4. Свет одной люстрой — рабочая зона в тени\n5. «Как у подруги» — высота столешницы зависит от роста\n\n## ЛДСП ИЛИ МДФ\n\nЛДСП — дёшево, стабильно, для корпусов. Кромка 1–2 мм. Не фрезеруется.\nМДФ — фрезеруется (филёнки, радиусные, ручки-профили). Эмаль/плёнка/шпон. Для фасадов. Дороже в 1,5–2 раза.\nРекомендация: корпус — ЛДСП, фасады — МДФ. Класс Е1 и Е0.5 — безопасен.\n\n## ТОНАЛЬНОСТЬ\n\n- Тёплый, дружелюбный, экспертный, на «Вы»\n- Короткие предложения, без канцелярита\n- Эмодзи умеренно (1–2)\n- Заканчивай вопросом или призывом\n- Не знаешь точно — «Катерина назовёт после замера, обычно...»\n\n## ОГРАНИЧЕНИЯ\n\nНе заключаешь договоры, не принимаешь оплату, не выезжаешь на замер, не делаешь рендеры, не гарантируешь точную цену без проекта. Всё точное — передаёшь Катерине.\n\n## КНОПКИ НАЧАЛА ОБСУЖДЕНИЯ\n\n«Кто мы» → текст из файла kto_my.txt\n«Биография» → текст из файла biografiya.txt\n«Контакты» → текст из файла kontakty.txt\n«Наши работы» → опиши проекты из фото, предложи заявку\n«Заказать кухню» → квалификация заявки\n«Заказать шкаф» → квалификация заявки\n«Дизайн-проект» → что входит + спроси площадь\n«Узнать стоимость» → цены 450/500 ₽/м² + попроси площадь'
WHO_TEXT = 'Кто мы и чем мы занимаемся — Дизайн квартир и домов\n\nКухни • Шкафы • на заказ по вашим размерам!\n\nНижний Новгород и область | Дизайн-проекты онлайн — по всей России\n\nПриветствуем вас в «МАКСимум мебель» — вашем надёжном партнёре в создании уюта и функциональности!\n\nМы не просто делаем мебель — мы создаём настроение для вашего дома.\n\n🔸 ЧТО МЫ ПРЕДЛАГАЕМ — ДИЗАЙН И ПРОЕКТИРОВАНИЕ МЕБЕЛИ\n\n✔️ Дизайн-проект квартиры, дома, коттеджа и отдельных комнат\n✔️ 3D-визуализация интерьера — увидите свой дом до начала ремонта\n✔️ Планировочные решения с расстановкой мебели и техники\n✔️ Рабочие чертежи: план мебели, план розеток и выключателей, схемы кухни, мебели\n✔️ Проектирование мебели для производства: кухни, шкафы, гардеробные\n✔️ Работаем онлайн по всей России: нужен только план БТИ или план от застройщика и фото объекта\n\n🔸 ЧТО МЫ ПРЕДЛАГАЕМ — МЕБЕЛЬ НА ЗАКАЗ\n\n✔️ Кухни по вашим размерам — даже самые нестандартные\n✔️ Встроенные и корпусные шкафы любой сложности\n✔️ Гардеробные, прихожие и детская мебель, продуманная до мелочей\n✔️ Полный цикл: замер → дизайн → производство → сборка\n✔️ Бесплатный дизайн-проект при заказе кухни на нашем производстве\n✔️ Честные цены — без переплат за бренд и аренду выставочных залов\n✔️ Гарантия на всю мебель и аккуратность после сборки — убираем за собой!\n\n🛠 Работаем с 2012 года. Нас выбирают, потому что:\n— Соблюдаем сроки — никаких «завтра» по кругу!\n— Учитываем все пожелания — даже самые неожиданные\n— Используем проверенные материалы и фурнитуру (AGT, Slotex, Boyard, Blum)\n— Наши сборщики — аттестованные специалисты, вежливые и пунктуальные\n\n✨ Ваша мечта об идеальной кухне, шкафе или интерьере — уже ближе, чем кажется!\nДавайте создадим что-то особенное — именно для вас.'
BIO_TEXT = 'Наша Биография: О нас\n\nВсё начиналось очень скромно — это наш семейный бизнес, в котором мы с мужем Максимом делаем всё вместе: замеры, дизайн, проекты, расчёты. У нас есть своя небольшая фабрика, где изготавливается мебель, и команда надёжных сборщиков, без которых ничего бы не получилось.\n\nА ведь история «Максимум Мебель» началась в 2012! Сначала это была компания «Мебель Кид», где Максим с напарником делали только детскую мебель — двухъярусные кровати, шкафы, комоды. Они сами создали первый сайт! Потом открыли первый салон, потом второй, и постепенно начали заниматься кухнями — и дело пошло!\n\nА я в то время работала логистом в другой мебельной компании. И вот однажды Максим говорит: «Присоединяйся ка к нам — будем работать вместе!» Я, конечно, переживала… Но согласилась — и с головой окунулась в этот водоворот! С нуля создала нашу группу ВКонтакте «Кухни и шкафы на заказ по вашим размерам» (тогда я вообще ничего не понимала в этом!), начала искать поставщиков, выстраивать процессы, учиться на ходу…\n\nЗа это время я повидала и победы, и трудности — но каждая из них сделала нас сильнее. Мы всегда рядом с нашими клиентами — даже после сдачи проекта. Для нас каждый заказчик — не просто номер в базе, а человек, которому мы рады помочь, поддержать, сделать уют и комфорт в доме. Мы любим наших клиентов — искренне!\n\nИ да — всё самое лучшее ещё впереди! Я верю в наше дело, в нашу команду и в то, что мы продолжим расти, развиваться и радовать вас качественной, красивой и продуманной мебелью. Спасибо, что вы с нами!'
CONTACTS_TEXT = 'Наши контакты:\n\n📞 +7 (915) 953-09-29 Катерина\n📞 +7 (910) 799-64-71 Максим\n\n🌐 Посмотреть отзывы и наши работы:\n👉 Группа ВК: https://vk.ru/maximummebel\n👉 Сайт: https://maxmebel-52.ru, https://portfolio-maxdizain.ru\n👉 Канал в MAX: https://max.ru/channel_maxmebel_52\n👉 Связаться со мной в MAX: https://max.ru/u/f9LHodD0cOIA7EmQkv1GxUtyPLfEh3g8_hcBtMfl_dQaY3BoX1zzyTvF7fc'

KITCHEN_TEXT = """🍽 Заказать кухню

Для предварительного расчёта пришлите, пожалуйста:
1. Город.
2. Примерные размеры или план помещения.
3. Фото помещения.
4. Пожелания по планировке и стилю.
5. Какая встраиваемая техника планируется.
6. Если уже определились — пожелания по фасадам, столешнице и фурнитуре.

Точную стоимость кухни называем после исходных данных и замера. Срок изготовления кухни — ориентир 2 месяца."""
WARDROBE_TEXT = """🗄 Заказать шкаф

Для предварительного расчёта пришлите, пожалуйста:
1. Город.
2. Размеры: ширина, высота и глубина.
3. Фото места установки.
4. Что нужно хранить.
5. Желаемое наполнение: полки, штанги, ящики.
6. Пожелания по фасадам.

По этим данным подготовим предварительный расчёт."""
DESIGN_TEXT = """🎨 Дизайн-проект

Эскизный проект / планировка — от 450 ₽/м².
Полный дизайн-проект: планировка + 3D-визуализация + рабочие чертежи — от 500 ₽/м².
Минимальный заказ — 1 000 ₽.
Консультация — бесплатно.

Работаем онлайн по всей России.

Напишите площадь помещения и что Вам нужно: эскизный или полный проект."""
PRICE_TEXT = """💰 Стоимость

Дизайн:
— эскизный проект / планировка — от 450 ₽/м²;
— полный дизайн-проект — от 500 ₽/м²;
— минимальный заказ — 1 000 ₽;
— консультация — бесплатно.

Кухни и шкафы:
точная стоимость зависит от размеров, материалов, фасадов, столешницы, фурнитуры и наполнения. Без исходных данных точную цену не называем.

Что нужно рассчитать: дизайн, кухню или шкаф?"""

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
    text = "Здравствуйте! Я ассистент «МАКСимум мебель» 👋\n\nВыберите из меню 👆" if welcome else "Выберите из меню 👆"
    attachment = {"type": "inline_keyboard", "payload": {"buttons": MENU_BUTTONS}}
    send_message(chat_id, text, [attachment])

def send_works(chat_id):
    # MAX permits external URLs for image attachments. 10 + 10 stays below the 12-attachment limit.
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
            "Фотографии сейчас не загрузились. Все работы можно посмотреть здесь:\n"
            "🌐 https://portfolio-maxdizain.ru\n"
            "🔵 https://vk.com/maximummebel"
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
    req = urllib.request.Request(GIGA_OAUTH, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=35) as response:
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
    text = text.replace("```", "").replace("**", "").replace("__", "").replace("`", "")
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
            "temperature": 0.3,
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
    attachments = body.get("attachments") or []

    if not chat_id:
        return
    if not text and attachments:
        send_message(chat_id, "Фото или файл получил. Добавьте, пожалуйста, что нужно рассчитать и основные размеры.")
        return
    if not text:
        return

    key = text.casefold().strip()
    if key in START_KEYS:
        send_menu(chat_id, welcome=True)
        return
    if key in MENU_KEYS:
        send_menu(chat_id)
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
            "Попробуйте ещё раз через минуту или позвоните Катерине: +7 (915) 953-09-29."
        )
    send_message(chat_id, answer)

def main():
    marker = None
    try:
        initial = get_updates(None, 0)
        marker = initial.get("marker")
    except Exception as exc:
        print("INITIAL UPDATE ERROR", repr(exc), flush=True)

    print("MAXBOT ACCEPTANCE START", marker, flush=True)

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
