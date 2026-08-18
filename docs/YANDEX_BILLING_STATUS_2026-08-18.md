# Yandex Cloud Billing — verified state for MAX bot deployment

Verified from the user's Yandex Cloud Billing page saved on 18.08.2026.

## Current state

- Billing account exists and is `Active`.
- Account type: personal.
- Consumption mode: paid version.
- Payment method is attached in Yandex Billing. Do not copy payment details into GitHub, logs or chat.
- Current personal-account balance shown by Billing: `0.00 RUB`.
- Active initial grant: `4,000 RUB / 4,000 RUB`.
- Grant validity: `18.08.2026` through `17.10.2026` (60 days).
- The grant applies to all Yandex Cloud services except the exclusions listed in the current official grant rules. The MAX bot target services (Serverless Containers, Managed Service for YDB, Message Queue and Container Registry) are not in the stated excluded categories.
- No cloud/service is currently shown as linked to this billing account on the saved overview page; the interface offers `Link`.

## Cost-safety rule

The user does not want unexpected paid consumption.

Yandex Cloud budgets are notifications/automation triggers, not a hard spending stop. Reaching a budget threshold does not itself stop resources. Therefore deployment must use conservative resource settings and no provisioned Serverless Container instances. We must monitor the grant/free-tier and either migrate/disable/delete billable resources before the grant/free allocation can produce a payable amount.

For the initial deployment:

- use Serverless Containers with provisioned instances = 0;
- cap maximum instances/concurrency conservatively;
- use Serverless YDB, not Dedicated;
- use one Standard YMQ queue;
- keep only the minimum required Docker images in Container Registry;
- do not enable paid support or Marketplace products;
- do not use GPU resources or Cloud Postbox;
- keep `MAX_ACTIVATE_WEBHOOK=0` until the entire preflight is green.

## Next external step

Open **Cloud Console** for the same organization. If an automatically created cloud already exists, link it to this billing account. If there is no cloud, create a dedicated cloud named `maximum-maxbot` and link it to this active billing account.

Only after a linked cloud/folder exists should Cloud Shell/CLI be used to create YDB, YMQ, service accounts, Container Registry, ingress/worker Serverless Containers and the YMQ trigger.
