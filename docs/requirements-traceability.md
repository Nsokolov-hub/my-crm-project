# Матрица требований и трассируемости (Requirements Traceability Matrix)

Статус: **Все требования закрыты и верифицированы автоматическими тестами.**
Дата верификации: 23 сентября 2026 г.

| Код | Суть требования | Модуль | Реализация в кодовой базе | Автоматические тесты | Статус |
|---|---|---|---|---|---|
| **R01 / N01** | Неизменяемость исторических фактов (защита от UPDATE/DELETE закрытых сделок, платежей, проводок) | Core, Commerce, CRM | Триггерная функция `crm_protect_history()`, миграция `51eab019a784_protect_history.py` | `tests/test_postgres_history.py` | **CLOSED** |
| **R02 / N02** | Целостность схемы PostgreSQL, проверка внешних ключей, индексов и ограничений | Core, Alembic | Миграции Alembic (revision `51eab019a784`), strict foreign keys, checks | `tests/test_postgres_history.py`, `alembic check` | **CLOSED** |
| **R03 / N03** | Объектные права доступа (scope: all, shared, own) и разграничение по подразделениям | Core, Security | `app.core.security.can()`, `scope_for()`, `require_permission()`, ролевая модель | `tests/test_crm.py`, `tests/test_commerce.py` | **CLOSED** |
| **R04 / N03** | Финансовые права, защита сессий, CSRF-токены, защита от перебора (rate limiting) | Core, Security | Advisory locks на логин, HttpOnly cookies, CSRF header check, 15-мин блокировка | `tests/test_crm.py` | **CLOSED** |
| **R05 / F06-F08** | Сделка от запроса до КП: квоты, номенклатура, CAS-валидация, единый поставщик и валюта КП | Commerce, Procurement | `app.commerce.procurement`, `app.commerce.documents`, валидация CAS | `tests/test_commerce.py`, `tests/test_postgres_commerce.py` | **CLOSED** |
| **R06 / F09-F11** | Точный расчет наценки, пошлин, логистики, выпуск КП и счетов, идемпотентность | Commerce, Calculator | `app.commerce.calculator.calculate()`, Decimal DSL, ReportLab PDF, idempotency keys | `tests/test_commerce.py`, `tests/test_load_and_resilience.py` | **CLOSED** |
| **R07 / F12-F13** | Согласование руководителем и распределение по волнам поставки | Commerce, Fulfillment | `app.commerce.fulfillment`, Approval workflow, Wave allocation logic | `tests/test_commerce.py`, `tests/test_postgres_commerce.py` | **CLOSED** |
| **R08 / F13** | Логистика, перемещение партий, отслеживание статусов волн и списаний | Commerce, Fulfillment | `app.commerce.fulfillment` (`/waves/{id}/allocations`, `/transfer`, `/events`) | `tests/test_commerce.py` | **CLOSED** |
| **R09 / F11, F13** | Ревизии и корректировки исполнения при изменении объемов и отмене позиций | Commerce, Fulfillment | `revise_execution()`, `cancel_execution()`, частичные поставки | `tests/test_commerce.py` | **CLOSED** |
| **R10 / F14** | Аналитические дашборды, отчетность по менеджерам, конверсии, дебиторская задолженность | Analytics, Core | `app.analytics.routes`, `/analytics/dashboard`, `/analytics/manager-activity` | `tests/test_crm.py`, `scripts/load_test.py` | **CLOSED** |
| **R11 / F15-F16** | Внутренний чат, обмен файлами, антивирусное сканирование (ClamAV), индикатор непрочитанных | Communication | `app.communication.routes`, `app.communication.files`, ClamAV client, unread filter | `tests/test_jobs.py`, `Layout.tsx` | **CLOSED** |
| **R12 / N07** | Резервное копирование и восстановление (streaming pg_dump/restore, manifest v2.0, SHA-256) | Scripts, Devops | `scripts/recovery.py`, `--mode {host,docker}`, Tar Slip safe extraction, retention | `tests/test_recovery.py` | **CLOSED** |
| **R13 / S17** | Перенос исторических данных, строгий контракт дат, учет авансов, контрольные суммы | Migration | `scripts/migrate_legacy.py`, `--dry-run`, `--commit`, `--report`, currency control totals | `tests/test_migrate_legacy.py` | **CLOSED** |
| **R14 / N03, N07** | Ролевая изоляция БД: crm_api (DML only, no DDL) и crm_backup (read-only SELECT) | Core, Postgres | `app.core.init_roles.py`, `scripts/postgres/01-init-roles.sql`, docker-compose roles | `tests/test_recovery.py` | **CLOSED** |
| **R15 / S15, S18** | Нагрузочные тесты, 100 строк расчет, PDF, 10k импорт, graceful stop, RTO $\le 10$ с | Scripts, Resilience | `scripts/load_test.py`, `worker.py` SIGTERM handler, SLA enforcement (exit 1 on breach) | `tests/test_load_and_resilience.py` | **CLOSED** |
