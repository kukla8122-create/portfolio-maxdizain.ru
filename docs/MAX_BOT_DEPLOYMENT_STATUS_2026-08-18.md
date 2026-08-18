# MAX bot — статус размещения 18.08.2026

Главный стандарт: `docs/MAX_BOT_IMPLEMENTATION_2026-08.md`.

## Bothost — отклонён после фактической проверки кабинета

Проверен реальный личный кабинет на бесплатном тарифе.

Фактический статус:
- тариф аккаунта: «Бесплатный»;
- в кабинете показано уведомление: «Бесплатный тариф пока отключен из-за отсутствия лишних мощностей»;
- раздел «Git репозитории» помечен как доступный на платных тарифах;
- поэтому Bothost сейчас не использовать для production;
- ничего не оплачивать только ради продолжения этой проверки.

Решение соответствует `MAX_BOT_IMPLEMENTATION_2026-08`: бесплатный кандидат сначала проверяется фактически; если нужные production-возможности недоступны — переходим к следующему варианту.

## Cloud.ru — исследован, но не выбран

Cloud.ru Evolution Container Apps технически подходит, однако для нашего малонагруженного webhook-бота предпочтительнее полностью serverless-схема с отдельной очередью событий и serverless-базой. Поэтому Cloud.ru оставляем запасным вариантом, а не текущей целью внедрения.

## Текущая целевая схема — Yandex Cloud Serverless

Проверены свежие официальные материалы Yandex Cloud и MAX на 18.08.2026.

Архитектура:

```text
MAX
  ↓ HTTPS Webhook
Yandex Serverless Container /webhook
  ↓ SendMessage
Yandex Message Queue (standard)
  ↓ trigger, batch size 1
тот же Serverless Container
  ↓
MAX Bot API + YDB Serverless
```

Почему так:
- MAX требует production Webhook и HTTP 200 максимум за 30 секунд;
- публичный `/webhook` только проверяет secret, кладёт Update в Message Queue и сразу отвечает;
- бизнес-логика не остаётся работать «фоном» после HTTP-ответа serverless-контейнера;
- Message Queue trigger удаляет сообщение только после успешной обработки; при ошибке сообщение возвращается для повторной попытки;
- заявки, сессии, каналы, настройки и обработанные event_id хранятся в YDB Serverless;
- это сохраняет архитектуру с очередью, требуемую нашим стандартом, и убирает необходимость в постоянно работающем VPS.

## Код

В `maxbot-production` добавлены:
- `maxbot-yandex.py` — YMQ + YDB адаптер существующей бизнес-логики;
- `maxbot-yandex-production.py` — защитный production-gate;
- `Dockerfile.yandex`;
- `requirements-yandex.txt`;
- расширенный CI для Yandex-образа.

`maxbot-yandex-production.py` разделяет два этапа:

### Preflight — безопасно, Webhook MAX не включается

```text
MAX_ACTIVATE_WEBHOOK=0
```

Проверяем:
- `/health`;
- `/storage` → YDB;
- `/ready` → MAX `/me`, YDB, Message Queue, публичный HTTPS URL;
- при этом `/ready` не выполняет `POST /subscriptions`.

Это важно: активный Webhook MAX отключает Long Polling, поэтому старый способ получения событий не должен прекращаться до завершения проверки новой инфраструктуры.

### Cutover — только после успешного preflight

Создаётся новая ревизия контейнера с:

```text
MAX_ACTIVATE_WEBHOOK=1
```

Только тогда `/ready` имеет право зарегистрировать точный URL `/webhook` через `POST /subscriptions`. После этого проверяются реальный `/start`, кнопки, заявки, `request_contact`, портфолио и канал.

## Что подтверждено CI

Первая Yandex-проверка прошла успешно:
- Python-код компилируется;
- существующие unit-тесты проходят;
- self-hosted образ продолжает собираться;
- Yandex Serverless образ собирается;
- размер Yandex-образа около 206 MB;
- parser формата события Yandex Message Queue trigger проходит;
- контейнер стартует при лимите 256 MB RAM и 0.5 CPU;
- `/health` отвечает HTTP 200.

После добавления `MAX_ACTIVATE_WEBHOOK` запускается повторный CI, который отдельно проверяет, что при значении `0` никакого `POST /subscriptions` нет, а при значении `1` регистрация разрешена.

## Стоимость и free tier — без обещания «вечные 0 ₽»

Для небольшого бота значительная часть serverless-нагрузки попадает в ежемесячный free tier Yandex Cloud:
- Serverless Containers — бесплатный месячный объём вызовов/CPU/RAM;
- YDB Serverless — бесплатный месячный объём RU и до 1 GB хранения;
- Message Queue — бесплатный месячный объём запросов.

Но Yandex Serverless Container может использовать Docker-образ только из Yandex Container Registry. Хранение Docker-образа в Container Registry тарифицируется по занимаемому объёму; free tier Container Registry относится к vulnerability scans, а не к хранению образа. Поэтому эту схему считаем **минимально затратной / с большим free tier**, но не обещаем абсолютные 0 ₽ после исчерпания гранта.

Для доступа к большинству Yandex Cloud сервисов нужен billing account. У нового аккаунта есть initial grant; для бизнеса также возможен trial при выполнении условий Yandex Cloud. Перед созданием платных ресурсов фактические условия кабинета проверяем ещё раз.

## Неизменные правила MAX

- API: `https://platform-api2.max.ru`;
- production — Webhook;
- Long Polling и Webhook одновременно не использовать;
- Webhook — HTTPS на внешнем 443 с доверенным TLS;
- если указан secret, проверять `X-Max-Bot-Api-Secret`;
- endpoint должен вернуть HTTP 200 в течение 30 секунд;
- токен — только заголовок `Authorization`, не query;
- сертификаты Минцифры добавлены в Docker image.

## Следующий неизбежный внешний шаг

После успешного повторного CI:
1. создать/открыть Yandex Cloud и billing account;
2. ничего не сообщать в чат из паролей, MAX token или static access key;
3. в облаке создать YDB Serverless, standard Message Queue, Container Registry, service account и Serverless Container;
4. сначала развернуть ревизию с `MAX_ACTIVATE_WEBHOOK=0`;
5. проверить `/health`, `/storage`, `/ready`;
6. создать YMQ trigger с batch size 1;
7. только после инфраструктурной проверки создать ревизию с `MAX_ACTIVATE_WEBHOOK=1` и выполнить cutover.
