# Инструкция по развертыванию (Deployment Guide)

## 1. Требования
* Docker 24.0+
* Docker Compose v2.20+
* Оперативная память: от 4 ГБ (ClamAV потребляет около 2 ГБ ОЗУ)
* Свободное дисковое пространство: от 10 ГБ

## 2. Первоначальный запуск (Clean Installation)

Склонируйте репозиторий в пустую директорию. Убедитесь, что у вас нет файлов `node_modules` или `.venv`, которые могут быть прокинуты через volumes, хотя в `docker-compose.yml` мы собираем образы независимо.

1. Создайте `.env` файл на основе примера:
```bash
cp .env.example .env
# Сгенерируйте новые надежные пароли
# POSTGRES_PASSWORD=...
# CRM_SESSION_SECRET=...
# CRM_MFA_ENCRYPTION_KEY=...
```

2. Запустите инфраструктуру:
```bash
docker compose up -d --build
```
> **Внимание:** ClamAV стартует долго (около 180 секунд для загрузки сигнатур). API и Worker дождутся его готовности перед началом обработки файлов.

## 3. Разделение сред (Dev / Test / Prod)

При запуске в production среде:
* Измените `ALLOWED_ORIGINS` в `.env` на ваш реальный домен (например, `https://crm.example.com`).
* Убедитесь, что сервисы `api` и `web` не открывают порты `8000` и `8080` наружу напрямую (`127.0.0.1:8080:8080` обеспечивает безопасность).
* Настройте Reverse Proxy (Nginx/Traefik/Caddy) с SSL сертификатом.
* Включите MFA (второй фактор) для всех административных пользователей. 

## 4. Обновление (Update)

Для обновления продукта до новой версии выполните:
```bash
git pull origin main
docker compose build
docker compose up -d
```
Миграции базы данных (сервис `migrate`) запустятся автоматически перед стартом `api` и `worker`.

## 5. Резервное копирование и восстановление (Backup & Restore)

### Создание бекапа
```bash
docker exec -it crm-project-db-1 pg_dump -U crm -d crm > backup.sql
docker run --rm --volumes-from crm-project-api-1 -v $(pwd):/backup alpine tar cvf /backup/files.tar /data/files
```

### Восстановление
```bash
cat backup.sql | docker exec -i crm-project-db-1 psql -U crm -d crm
docker run --rm --volumes-from crm-project-api-1 -v $(pwd):/backup alpine tar xvf /backup/files.tar
```

## 6. Откат версии (Rollback)

В случае критической ошибки:
1. Восстановите дамп БД до момента миграции (см. пункт 5).
2. Переключитесь на предыдущий коммит Git: `git checkout <previous_tag>`
3. Пересоберите и запустите: `docker compose up -d --build`
