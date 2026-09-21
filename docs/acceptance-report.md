# Отчет о приемке (Acceptance Report)

## S00: Принять репозиторий и зафиксировать исходное состояние
- [x] Зафиксировать точный commit SHA в `docs/baseline.md`.
- [x] Проверить `.gitignore` и обновить.
- [x] Составить реестр фактических маршрутов и экранов в `docs/baseline.md`.
- [x] Запустить доступные baseline-проверки.
- [x] Создать `docs/acceptance-report.md`, `docs/defects.md`, `docs/progress.md`.
- [x] Создать фикстуры пользователей.

## S01: Исправить проверки качества и создать CI
- [x] Устранить импорты (Ruff)
- [x] Объединить селектор в stylelint
- [x] Зафиксировать команды
- [x] Добавить реальные frontend-регрессии
- [x] CI: сборка, линтеры, sqlite, postgres

## S02: Проверить миграции, ограничения и неизменяемую историю
- [x] Тесты триггеров на защиту данных в test_postgres_history.py
- [x] Генерация недостающих constraints (alembic check)
- [x] Разделение ролей PostgreSQL в init-скрипте и docker-compose
