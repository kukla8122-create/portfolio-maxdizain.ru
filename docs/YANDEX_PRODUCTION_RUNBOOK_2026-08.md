# MAX-бот «МАКСимум мебель» — Yandex Cloud production runbook (август 2026)

Дата проверки: 18.08.2026.

Этот runbook применяется только к ветке `maxbot-production` и дополняет `docs/MAX_BOT_IMPLEMENTATION_2026-08.md`. При расхождении со свежей официальной документацией MAX/Yandex Cloud сначала перепроверить официальную документацию и только потом менять production.

## 1. Целевая схема

```text
MAX
  -> HTTPS Webhook
  -> maximum-maxbot-ingress (public Serverless Container)
  -> Yandex Message Queue Standard
  -> YMQ Trigger
  -> maximum-maxbot-worker (private Serverless Container)
  -> YDB Serverless
  -> MAX Bot API https://platform-api2.max.ru
```

Один Docker-образ используется двумя Serverless Containers. Режим задаётся `APP_MODE=ingress` или `APP_MODE=worker`.

## 2. Жёсткие правила безопасности

- `MAX_BOT_TOKEN` никогда не хранить в GitHub, документах, URL, логах или чатах.
- MAX token передавать MAX API только через заголовок `Authorization`.
- `MAX_WEBHOOK_SECRET` — отдельное случайное значение; не выводить в логах.
- `MAX_ACTIVATE_WEBHOOK=0` на всех preflight-ревизиях.
- Webhook MAX активировать только после полного preflight инфраструктуры и `GET /me`.
- Старый Long Polling / старую подписку не отключать до controlled cutover.
- Ingress публичный; worker приватный.
- Ingress: только `GET /health`, `GET /ready`, `POST /webhook`.
- Worker: только YMQ trigger POST (`/` или `/trigger`) и служебные `GET /health`, `GET /ready`; `/webhook` на worker отсутствует.
- Секреты вводятся только внутри Yandex Cloud / Cloud Shell и не копируются в ChatGPT.

## 3. Требования MAX на август 2026

- API: `https://platform-api2.max.ru`.
- Production transport: Webhook.
- Webhook URL: HTTPS с доверенным сертификатом.
- При заданном secret MAX отправляет `X-Max-Bot-Api-Secret`.
- Callback подтверждается через `POST /answers`.
- `request_contact`: `hash` проверяется HMAC-SHA256 токеном бота по `vcf_info`; escaped CRLF нормализуются перед проверкой.
- Среда выполнения должна доверять актуальным сертификатам Минцифры.

Официальный источник истины: `dev.max.ru`.

## 4. Ресурсы Yandex Cloud

В одном Cloud/Folder:

1. `maximum-maxbot-db` — YDB Serverless.
2. `maximum-maxbot-events` — Message Queue Standard.
3. `maximum-maxbot-ingress` — public Serverless Container.
4. `maximum-maxbot-worker` — private Serverless Container.
5. `maximum-maxbot-worker-trigger` — YMQ -> worker.
6. `maximum-maxbot-registry` — Container Registry.

Для YDB использовать Serverless, не Dedicated. Provisioned RCU держать `0`, чтобы не включать почасовую provisioned-capacity оплату.

## 5. Service accounts и роли

### `maxbot-ingress-sa`

- `ymq.writer` — отправка Update в очередь.
- `container-registry.images.puller` — pull приватного Docker-образа.
- Для YMQ создаётся static access key. `key_id`/`secret` используются только в окружении ingress и не публикуются.

### `maxbot-worker-sa`

- `ydb.editor` — чтение/запись и инициализация таблиц.
- `container-registry.images.puller` — pull Docker-образа.
- YDB runtime использует metadata credentials сервисного аккаунта; отдельный пароль БД не нужен.

### `maxbot-trigger-sa`

Для YMQ trigger официальная YMQ-документация требует `editor` на Folder с очередью. Для вызова private worker использовать актуальную роль:

`serverless-containers.containerInvoker`

### Важное уточнение 18.08.2026

В Yandex Cloud сейчас есть несогласованность документации: YMQ-specific страница всё ещё может показывать устаревшее имя `serverless.containers.invoker`, но актуальный справочник ролей Serverless Containers и общий раздел triggers используют `serverless-containers.containerInvoker`. В нашем production использовать **новое имя** `serverless-containers.containerInvoker`.

## 6. YDB

Создание:

```bash
yc ydb database create maximum-maxbot-db \
  --serverless \
  --sls-provisioned-rcu 0
```

В worker передавать полный `YDB_CONNECTION_STRING` из созданной БД.

Таблицы создаются приложением:

```text
sessions
leads
channels
settings
processed_events
```

`processed_events` используется для идемпотентности при повторной доставке YMQ/MAX.

## 7. Message Queue

Очередь: `maximum-maxbot-events`, только **Standard**, не FIFO.

На старте:
- trigger batch size = `1`;
- batch cutoff = `1s`;
- visibility timeout > максимального времени обработки worker;
- после smoke-test добавить DLQ.

Ingress кладёт в очередь исходный JSON Update MAX. Worker получает его через trigger.

## 8. Docker image

Использовать `Dockerfile.yandex` из `maxbot-production`.

Требования:
- без `curl -k` и других TLS bypass;
- сертификаты Минцифры установлены в trust store;
- `maxbot-yandex-entry.py` запускает split runtime;
- один image, два режима: ingress/worker.

Формат image в Yandex Container Registry:

`cr.yandex/<registry-id>/maximum-maxbot:<tag>`

## 9. Ingress revision

Обязательное окружение:

```text
APP_MODE=ingress
MAX_BOT_TOKEN=<только Yandex Cloud>
MAX_WEBHOOK_SECRET=<случайный secret>
MAX_ACTIVATE_WEBHOOK=0
PUBLIC_BASE_URL=https://<container-id>.containers.yandexcloud.net
YMQ_ENDPOINT=https://message-queue.api.cloud.yandex.net
YMQ_QUEUE_URL=<real queue URL>
YMQ_REGION=ru-central1
AWS_ACCESS_KEY_ID=<ingress SA static key id>
AWS_SECRET_ACCESS_KEY=<ingress SA static key secret>
```

Ingress сделать публичным. YDB-доступ ему не выдавать. `min-instances=0`.

## 10. Worker revision

```text
APP_MODE=worker
MAX_BOT_TOKEN=<только Yandex Cloud>
MAX_WEBHOOK_SECRET=<тот же webhook secret>
MAX_ACTIVATE_WEBHOOK=0
YDB_CONNECTION_STRING=<real YDB connection string>
```

Worker не делать публичным. YMQ producer credentials worker не нужны. `min-instances=0`.

## 11. Trigger

```bash
yc serverless trigger create message-queue \
  --name maximum-maxbot-worker-trigger \
  --queue <QUEUE_ARN> \
  --queue-service-account-id <TRIGGER_SA_ID> \
  --invoke-container-id <WORKER_CONTAINER_ID> \
  --invoke-container-service-account-id <TRIGGER_SA_ID> \
  --batch-size 1 \
  --batch-cutoff 1s
```

Для `--queue` использовать ARN очереди: именно ARN показан Yandex как Queue ID.

## 12. Preflight — MAX Webhook ещё НЕ включать

На ingress и worker: `MAX_ACTIVATE_WEBHOOK=0`.

Проверить:

1. ingress `GET /health` -> 200, `mode=ingress`;
2. ingress `POST /trigger` -> 404;
3. ingress `GET /ready`: `max_api=true`, `queue=true`, `activation_enabled=false`;
4. worker private `GET /health` -> 200, `mode=worker`;
5. worker `POST /webhook` -> 404;
6. worker `GET /ready`: `max_api=true`, `storage=true`;
7. YMQ synthetic probe проходит trigger -> worker и появляется точным ключом в `processed_events` YDB;
8. повторная доставка одного Update не создаёт вторую заявку.

До этого момента `POST /subscriptions` MAX не выполнять.

## 13. Controlled cutover

Только после полного preflight:

1. активировать новую MAX Webhook-подписку на ingress `/webhook`;
2. проверить `GET /subscriptions` и точное совпадение URL;
3. проверить `/start`/`bot_started`, 8 кнопок, callback, формы и `request_contact`;
4. проверить, что lead в YDB создаётся один раз;
5. проверить портфолио/fallback;
6. только после успешного E2E считать старый transport выведенным из эксплуатации.

Rollback: удалить production Webhook через официальный `DELETE /subscriptions?url=...`; после удаления Webhook Long Polling снова может быть использован.

## 14. Acceptance gate

Production готов только если:

```text
[ ] Python compile/unit tests OK
[ ] Docker image собирается без TLS bypass
[ ] ingress public / worker private
[ ] route isolation подтверждена
[ ] YDB Serverless RUNNING, provisioned RCU=0
[ ] YMQ Standard работает
[ ] trigger -> worker -> YDB probe доказан
[ ] MAX /me = 200
[ ] MAX webhook secret проверяется
[ ] callback /answers работает
[ ] request_contact/HMAC работает
[ ] ручной номер работает
[ ] retry не дублирует lead
[ ] 8 пунктов меню работают
[ ] портфолио работает
[ ] business texts сверены с MAX_BOT_IMPLEMENTATION_2026-08.md
[ ] неизвестный вопрос -> deterministic FAQ/fallback без выдуманных цен/сроков
[ ] политика/основание обработки ПД готовы до коммерческого запуска
[ ] rollback-план сохранён
```

## 15. Официальные источники Yandex Cloud, сверенные 18.08.2026

- Serverless Containers access management / role reference.
- Serverless Containers YMQ trigger docs and trigger overview.
- Message Queue access management.
- Container Registry access management and Docker authentication.
- YDB CLI/database create and authentication docs.
- IAM static access key docs.

При будущем изменении CLI/ролей сначала заново сверить эти официальные разделы, затем менять bootstrap/runbook.