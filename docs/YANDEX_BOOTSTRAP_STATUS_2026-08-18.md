# Yandex Cloud bootstrap status — 18.08.2026

Проект: MAX-бот «МАКСимум мебель».
Ветка: `maxbot-production`.

## Проверено перед изменениями

- Cloud `maximum-maxbot` существует и в последнем сохранённом Yandex Cloud dashboard имеет статус `ACTIVE`.
- Cloud ID: `b1g91dbs94slnmrj3npv`.
- Folder ID: `b1g7u7p1qmhjvgtidp0i`.
- В аккаунте доступны YDB, Message Queue, Container Registry, Serverless Containers и Cloud Shell.
- Основной стандарт: `docs/MAX_BOT_IMPLEMENTATION_2026-08.md`.
- Production runbook: `docs/YANDEX_PRODUCTION_RUNBOOK_2026-08.md`.

## Актуализация официальных правил Yandex Cloud

18.08.2026 повторно сверены официальные разделы Yandex Cloud по Serverless Containers, YMQ triggers, Message Queue, Container Registry, YDB и IAM.

Обнаружена документационная несогласованность имени роли invoker: YMQ-specific страница может показывать старое `serverless.containers.invoker`, но актуальный role reference / trigger overview используют `serverless-containers.containerInvoker`. Runbook обновлён: production использует новое имя.

## Подготовлено

Добавлен `deploy/yandex-bootstrap.sh`.

Скрипт:
- сначала проверяет exact Cloud/Folder и статус `ACTIVE`;
- до cloud mutations компилирует Python, запускает unit tests и собирает `Dockerfile.yandex`;
- запрашивает MAX token только скрытым вводом внутри Cloud Shell;
- создаёт/переиспользует service accounts, Container Registry, YDB Serverless, YMQ Standard, ingress/worker containers и trigger;
- держит `min-instances=0`;
- держит `MAX_ACTIVATE_WEBHOOK=0`;
- делает ingress публичным, worker приватным;
- проверяет `/health`, `/ready` и route isolation;
- выполняет точный synthetic probe `YMQ -> trigger -> worker -> processed_events в YDB`;
- не выполняет `POST /subscriptions` MAX;
- не отключает старый MAX transport.

Локальная статическая проверка перед коммитом: `bash -n` — OK. Также проверено отсутствие `MAX_ACTIVATE_WEBHOOK=1`, `POST /subscriptions`, старого `platform-api.max.ru` и TLS bypass `curl -k`.

## Следующий gate

Запустить `deploy/yandex-bootstrap.sh` в аутентифицированном Yandex Cloud Shell. Только если скрипт завершится `YANDEX_INFRA_PREFLIGHT_OK`, переходить к отдельному controlled cutover Webhook MAX.

MAX token, webhook secret и static access key не должны попадать в ChatGPT, GitHub, скриншоты или логи.