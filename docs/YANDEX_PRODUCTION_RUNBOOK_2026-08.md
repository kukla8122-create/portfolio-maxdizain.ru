# MAX-бот «МАКСимум мебель» — Yandex Cloud production runbook (август 2026)

Этот runbook применяется только к ветке `maxbot-production` и дополняет основной стандарт `MAX_BOT_IMPLEMENTATION_2026-08.md`.

## 1. Целевая схема

```text
MAX
  -> HTTPS Webhook
  -> maximum-maxbot-ingress (public)
  -> Yandex Message Queue (Standard)
  -> YMQ Trigger
  -> maximum-maxbot-worker (private)
  -> YDB Serverless
  -> MAX Bot API platform-api2.max.ru
```

Один Docker-образ используется двумя Serverless Containers. Режим задается `APP_MODE=ingress` или `APP_MODE=worker`.

## 2. Зафиксированные правила безопасности

- `MAX_BOT_TOKEN` никогда не хранить в GitHub, документах, URL, логах и чатах.
- MAX token передавать MAX API только через `Authorization`.
- `MAX_WEBHOOK_SECRET` должен быть отдельным случайным значением и вводиться непосредственно в окружение/secret-хранилище Yandex Cloud.
- `MAX_ACTIVATE_WEBHOOK=0` на всех preflight-ревизиях.
- Webhook активируется только после успешной проверки ingress, queue, worker, YDB и MAX `/me`.
- Старый Long Polling не выключать до controlled cutover.
- Ingress публичный; worker не должен иметь публичный unauthenticated invoke.
- Ingress принимает только `GET /health`, `GET /ready`, `POST /webhook`.
- Worker принимает только YMQ trigger POST (`/` или `/trigger`) плюс служебные `GET /health`, `GET /ready`; `/webhook` на worker отсутствует.

## 3. Текущие официальные требования MAX

Проверено 18.08.2026 по MAX Developers:

- API: `https://platform-api2.max.ru`.
- Токен: только HTTP-заголовок `Authorization`.
- Production transport: Webhook; активная Webhook-подписка отключает Long Polling.
- Webhook URL: HTTPS, доверенный сертификат.
- При заданном `secret` MAX отправляет `X-Max-Bot-Api-Secret`.
- Callback после нажатия кнопки подтверждается через `POST /answers`.
- `request_contact`: `hash` проверяется как HMAC-SHA256 токеном бота по `vcf_info`; перед хешированием escaped CRLF должны быть приведены к реальным переносам строк.
- После изменений 19.07.2026 среда выполнения должна доверять сертификатам Минцифры.

Официальные источники:
- https://dev.max.ru/docs-api
- https://dev.max.ru/docs-api/methods/POST/subscriptions
- https://dev.max.ru/docs-api/methods/GET/subscriptions
- https://dev.max.ru/docs-api/methods/POST/answers
- https://dev.max.ru/docs-api/changelog-api

## 4. Ресурсы Yandex Cloud

Создать в одном Cloud и согласованном Folder:

1. `maximum-maxbot-db` — **YDB Serverless**.
2. `maximum-maxbot-events` — **Message Queue Standard**.
3. `maximum-maxbot-ingress` — публичный Serverless Container.
4. `maximum-maxbot-worker` — приватный Serverless Container.
5. `maximum-maxbot-worker-trigger` — Message Queue trigger -> worker.
6. Container Registry/repository для Docker-образа.

Не использовать Dedicated YDB для этой первой версии.

## 5. Service accounts и роли

### `maxbot-ingress-sa`

Нужно:
- `ymq.writer` на Folder с очередью — отправка сообщений в YMQ;
- `container-registry.images.puller` на registry/folder, если образ приватный и этот SA используется runtime/revision для pull.

Для Boto3/YMQ создается static access key этого SA. `key_id` и `secret` вводятся непосредственно в Yandex Cloud как `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`; в GitHub и чат не копируются.

### `maxbot-worker-sa`

Нужно:
- `ydb.editor` на YDB/Folder — чтение/запись и создание schema objects;
- `container-registry.images.puller` на registry/folder, если требуется pull приватного образа.

Worker использует metadata credentials service account для YDB; отдельный YDB-пароль не нужен.

### `maxbot-trigger-sa`

Для **работающего YMQ -> Serverless Container trigger** текущая официальная документация Yandex Cloud требует:
- `editor` на Folder с Message Queue;
- `serverless.containers.invoker` на Folder/worker-container, который вызывает trigger.

Это важное уточнение: хотя Message Queue имеет granular роли `ymq.reader`/`ymq.writer`, официальная страница YMQ-trigger для Serverless Containers на июль 2026 прямо указывает `editor` для service account, от имени которого trigger читает очередь. Используем текущую официальную схему, а не старое предположение `ymq.reader`.

Создающему trigger пользователю также нужны права использовать service account (`iam.serviceAccounts.user` или роль выше) и viewer-доступ к соответствующим Folder.

Официальные источники Yandex Cloud:
- https://yandex.cloud/en/docs/serverless-containers/concepts/trigger/ymq-trigger
- https://yandex.cloud/en/docs/serverless-containers/operations/ymq-trigger-create
- https://yandex.cloud/en/docs/message-queue/security/
- https://yandex.cloud/en/docs/ydb/security/
- https://yandex.cloud/en/docs/serverless-containers/security/

## 6. YDB

Создать Serverless database:

```bash
yc ydb database create maximum-maxbot-db --serverless
```

В production worker передать `YDB_CONNECTION_STRING` из созданной БД. Код также умеет работать через `YDB_ENDPOINT` + `YDB_DATABASE`, но один connection string предпочтительнее.

Таблицы создаются приложением:

```text
sessions
leads
channels
settings
processed_events
```

## 7. Message Queue

Создать **Standard**, не FIFO:

```text
maximum-maxbot-events
```

Ingress отправляет туда оригинальный JSON Update от MAX. Worker получает его через trigger.

Рекомендуемые эксплуатационные параметры на старте:
- batch size trigger: `1`;
- batch cutoff: `1s`;
- visibility timeout очереди должен быть больше максимального времени обработки worker;
- после smoke-test добавить DLQ, чтобы неисправное сообщение не ретраилось бесконечно до истечения retention.

## 8. Docker image

Используется `Dockerfile.yandex` из `maxbot-production`.

Текущий Dockerfile:
- больше не использует `curl -k`;
- устанавливает сертификаты Минцифры;
- запускает `maxbot-yandex-entry.py`;
- entrypoint требует явный `PUBLIC_BASE_URL` в ingress mode;
- split runtime разделяет публичный ingress и private worker.

Собрать образ, проверить CI, затем push в Yandex Container Registry.

## 9. Ingress revision

Обязательные переменные:

```text
APP_MODE=ingress
MAX_BOT_TOKEN=<вводится только в Yandex Cloud>
MAX_WEBHOOK_SECRET=<отдельный случайный secret>
MAX_ACTIVATE_WEBHOOK=0
PUBLIC_BASE_URL=https://<реальный-id>.containers.yandexcloud.net
YMQ_ENDPOINT=https://message-queue.api.cloud.yandex.net
YMQ_QUEUE_URL=<реальный URL очереди>
YMQ_REGION=ru-central1
AWS_ACCESS_KEY_ID=<static key id ingress SA>
AWS_SECRET_ACCESS_KEY=<static key secret ingress SA>
```

Ingress делается публичным для вызова MAX. Никаких YDB credentials ему не выдавать.

## 10. Worker revision

Обязательные переменные:

```text
APP_MODE=worker
MAX_BOT_TOKEN=<вводится только в Yandex Cloud>
MAX_WEBHOOK_SECRET=<тот же webhook secret; нужен shared core>
YDB_CONNECTION_STRING=<реальный connection string YDB>
```

Worker **не** делать публичным. Никаких YMQ static producer keys worker не нужны.

## 11. Trigger

Создать YMQ trigger на private worker:

```bash
yc serverless trigger create message-queue \
  --name maximum-maxbot-worker-trigger \
  --queue <QUEUE_ID> \
  --queue-service-account-id <TRIGGER_SA_ID> \
  --invoke-container-id <WORKER_CONTAINER_ID> \
  --invoke-container-service-account-id <TRIGGER_SA_ID> \
  --batch-size 1 \
  --batch-cutoff 1s
```

Yandex Cloud указывает, что после успешной обработки trigger удаляет сообщение из очереди, а при ошибке возвращает его в очередь после visibility timeout.

## 12. Preflight — Webhook еще НЕ включать

На ingress обязательно оставить:

```text
MAX_ACTIVATE_WEBHOOK=0
```

Проверки:

```text
1. ingress GET /health -> 200, mode=ingress
2. ingress POST /trigger -> 404
3. worker GET /health -> 200, mode=worker (через авторизованный/private invoke)
4. worker POST /webhook -> 404
5. YDB /ready -> storage=true
6. ingress /ready -> max_api=true, queue=true, activation_enabled=false
7. MAX GET /me -> 200
8. очередь принимает тестовый update и trigger доставляет его worker
9. повторная доставка одного и того же MAX update не создает вторую lead row
```

До этого момента `POST /subscriptions` не выполнять.

## 13. Controlled cutover

Только после полного preflight:

1. создать новую ingress revision с `MAX_ACTIVATE_WEBHOOK=1` **или** выполнить однократный controlled `POST /subscriptions` вручную;
2. сразу проверить `GET /subscriptions` и точное совпадение production URL;
3. открыть личный диалог с ботом и проверить `/start`/bot_started, все 8 кнопок, callback, заявку и `request_contact`;
4. убедиться, что заявка появилась в YDB один раз;
5. проверить портфолио и fallback;
6. после успешного E2E старый Long Polling больше не используется.

Rollback: удалить production Webhook через официальный `DELETE /subscriptions?url=...`; после удаления Long Polling снова становится доступен.

## 14. Acceptance gate

Production считается готовым только если:

```text
[ ] GitHub CI прошел
[ ] Docker image собирается без TLS bypass
[ ] ingress public / worker private
[ ] route isolation подтверждена
[ ] YDB Serverless работает
[ ] YMQ Standard работает
[ ] trigger retry работает
[ ] MAX /me = 200
[ ] Webhook secret проверяется
[ ] callback /answers работает
[ ] request_contact и HMAC работают
[ ] ручной номер работает
[ ] заявка не дублируется при повторной доставке
[ ] 8 пунктов меню работают
[ ] портфолио работает
[ ] exact business texts сверены с MAX_BOT_IMPLEMENTATION_2026-08.md
[ ] неизвестный вопрос уходит в deterministic FAQ/fallback, без выдуманных цен и сроков
[ ] политика/основание обработки ПД готовы до коммерческого запуска
[ ] rollback проверен
```
