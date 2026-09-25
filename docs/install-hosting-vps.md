# Установка CRM на сервере с доступом через интернет

Инструкция для небольшой компании и исполнителя, которому передан архив проекта. Пример рассчитан на **чистый VPS с Ubuntu 24.04 LTS, архитектурой x86_64/amd64, доменом и HTTPS**. CRM работает в Docker Compose; установленный на самом сервере Caddy принимает внешние запросы и передаёт их на локальный веб-контейнер. Команды выполняйте по порядку. Заменяйте `crm.example.com`, `SERVER_IP` и `SERVER_USER` своими значениями.

Если CRM **уже работает у клиента на HTTP-адресе с IP**, начните с раздела [«Обновление существующего сервера клиента»](#обновление-существующего-сервера-клиента). Повторная чистая установка создаст отдельную базу вместо обновления существующей.

## 1. Что подготовить

- VPS с Ubuntu 24.04 LTS **x86_64/amd64**, не менее 4 ГБ ОЗУ и 10 ГБ свободного места; для запаса под антивирусные базы, документы и копии разумно выделить 6 ГБ ОЗУ и 20 ГБ диска. Контейнер ClamAV в этом проекте закреплён за `linux/amd64`; ARM-серверу такая инструкция не подходит без отдельной проверки.
- Домен или поддомен, например `crm.example.com`, и доступ к его DNS. Создайте запись `A` на публичный IPv4 VPS. Запись `AAAA` добавляйте только при работающем IPv6 на сервере.
- Доступ по SSH к серверу и право выполнять `sudo`.
- Переданный ZIP с исходным кодом CRM. Он не содержит рабочую базу, документы компании, `.env` или готовые пароли.
- Открытые для интернета TCP-порты `80` и `443` и ваш SSH-порт. Порты `8080`, `8000`, `54329` и `3310` открывать наружу не нужно: Compose привязывает их к `127.0.0.1` сервера.

Если перед сервером есть панель хостинга с сетевым экраном, разрешите `80` и `443` также в ней. Caddy получает сертификат после того, как DNS указывает на сервер и эти порты доступны [по инструкции Caddy](https://caddyserver.com/docs/quick-starts/reverse-proxy#https-from-client-to-proxy).

## 2. Подключение и установка Docker

Подключитесь к серверу:

```bash
ssh SERVER_USER@SERVER_IP
uname -m
```

Команда `uname -m` должна вывести `x86_64`. Если Docker и `docker compose` уже установлены, проверьте их командами `sudo docker version` и `sudo docker compose version` и перейдите к следующему разделу. На чистом Ubuntu установите пакеты из [официального репозитория Docker](https://docs.docker.com/engine/install/ubuntu/#install-using-the-apt-repository):

```bash
sudo apt update
sudo apt install -y ca-certificates curl python3 unzip
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources > /dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo docker version
sudo docker compose version
```

Если Docker уже был установлен, убедитесь, что также есть `python3` и `unzip` (`python3 --version && unzip -v`) и при необходимости установите их через `sudo apt install -y python3 unzip`. Если на сервере уже установлены пакеты Docker другим способом, не удаляйте их автоматически: сначала оцените существующие контейнеры и способ обновления Docker. Далее команды используют `sudo docker`, поэтому добавлять пользователя в группу `docker` не требуется.

## 3. Загрузка архива и создание приватных настроек

На **своём компьютере** передайте полученный архив на сервер. Подставьте фактический путь к файлу:

```bash
scp /путь/к/полученному-архиву.zip SERVER_USER@SERVER_IP:~/reaktiv-crm.zip
```

Вернитесь в SSH-терминал сервера. Архив содержит папку `reaktiv-crm`; распакуйте его в домашний каталог и перейдите в проект:

```bash
unzip -l ~/reaktiv-crm.zip | head
unzip -q ~/reaktiv-crm.zip -d ~
cd ~/reaktiv-crm
python3 scripts/init-env.py
ls -l .env
```

Скрипт создаёт `.env` с новыми случайными секретами и правами доступа `0600`. Повторный запуск не заменит существующий `.env`. Держите этот файл только на сервере; не отправляйте его в чат, Git или архив проекта. В нём находятся ключ сессий и пароли базы.

Откройте `.env` в редакторе (`nano .env` или ваш редактор) и измените **только** следующие строки:

```dotenv
ENVIRONMENT=production
ALLOWED_ORIGINS=https://crm.example.com
```

Укажите свой точный адрес без завершающего `/` и без порта, если используется обычный HTTPS-порт `443`. Другие случайно созданные секреты оставьте как есть. `POSTGRES_PASSWORD`, `CRM_API_PASSWORD`, `CRM_BACKUP_PASSWORD`, `SECRET_KEY` и `COMPOSE_PROJECT_NAME` нельзя заново генерировать при обычном обновлении. `ENVIRONMENT=production` включает защищённую cookie и требование второго фактора для привилегированных пользователей; поэтому рабочий вход следует выполнять через HTTPS.

## 4. Запуск CRM

Из папки `~/reaktiv-crm` выполните:

```bash
sudo docker compose build api web
sudo docker compose up -d --no-build
sudo docker compose ps -a
```

Первая загрузка контейнеров и баз ClamAV может занять несколько минут. Дождитесь, пока `db`, `clamav`, `api` и `web` станут `healthy`, `worker` будет `Up`, а разовый `migrate` завершится с кодом `0`. Если сервис не запускается, сначала изучите его журнал, например:

```bash
sudo docker compose logs --tail=100 api
sudo docker compose logs --tail=100 migrate
sudo docker compose logs --tail=100 clamav
```

Проверка непосредственно на сервере:

```bash
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:8080/ready
sudo docker compose port web 8080
```

Первые две команды должны вернуть `ok` и `{"status":"ready"}`. Последняя должна показывать привязку веб-сервиса к `127.0.0.1:8080` (или к выбранному вами `CRM_HTTP_PORT`). Это внутренний порт: публичный доступ появится после настройки Caddy.

## 5. Домен, сетевой экран и HTTPS

Перед включением сетевого экрана убедитесь, что знаете **фактический SSH-порт**, чтобы не потерять доступ к серверу. Для стандартного порта `22`:

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

На нестандартном SSH-порту замените `22` на ваш номер. Это соответствует [инструкции Ubuntu по UFW](https://ubuntu.com/server/docs/how-to/security/firewalls/). Docker может обходить правила UFW для опубликованных портов; здесь порты контейнеров привязаны к `127.0.0.1`, поэтому не меняйте их на `0.0.0.0` ради внешнего доступа ([предупреждение Docker](https://docs.docker.com/engine/install/ubuntu/#firewall-limitations)).

Убедитесь, что на сервере нет другого сервиса, уже занимающего `80` и `443` (`sudo ss -ltnp`). Если прежний nginx или другой прокси уже работает на этих портах, сначала разберите его конфигурацию; устанавливать второй публичный прокси поверх него нельзя. На чистом сервере установите Caddy из [официального репозитория](https://caddyserver.com/docs/install#debian-ubuntu-raspbian):

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

Замените содержимое `/etc/caddy/Caddyfile` (например, `sudo nano /etc/caddy/Caddyfile`) на:

```caddyfile
crm.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Сохраните файл, проверьте конфигурацию и примените её:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo systemctl status caddy --no-pager
```

Caddy сам получает и продлевает HTTPS-сертификат для указанного домена, если запись DNS уже указывает на VPS и внешние `80/443` доступны ([документация Caddy](https://caddyserver.com/docs/automatic-https)). При ошибке сертификата посмотрите `sudo journalctl -u caddy -n 100 --no-pager`, проверьте DNS, внешние порты и отсутствие другого веб-сервера на `80/443`.

Откройте **`https://crm.example.com/`** с другого устройства. Страница должна открываться без предупреждения о сертификате. Проверьте:

```bash
curl -fsS https://crm.example.com/ready
```

Не используйте для обычной работы старую прямую ссылку `http://SERVER_IP:8080`: там нет TLS, а в режиме `production` сессионная cookie помечена `Secure`.

## 6. Первый владелец и первые данные

На **новой пустой базе** создайте первого владельца. Подставьте реальный служебный адрес; пароль не короче 12 символов будет запрошен интерактивно и не появится в журнале команды:

```bash
cd ~/reaktiv-crm
sudo docker compose exec api python -m app.bootstrap --email owner@company.example --name 'Владелец'
```

Войдите через HTTPS. Поскольку `ENVIRONMENT=production` требует второй фактор у привилегированного владельца, **сначала откройте «Настройки → Мой аккаунт → Защита аккаунта» и нажмите «Настроить второй фактор»**: привяжите код в приложении-аутентификаторе и подтвердите текущий код. После этого проверьте повторный вход с паролем и кодом. Храните резервный доступ к приложению-аутентификатору по внутренним правилам компании. Затем в «Настройках» добавьте организацию продавца, создайте сотрудников и назначьте им нужные роли. Перед выпуском реальных КП и счетов настройте финансовый профиль с правилами компании и проверьте контрольный расчёт. Подробные действия описаны в [руководстве пользователя](user-handbook.md).

Скрипт `scripts/setup_demo_roles.py` создаёт демонстрационные учётки только на отдельной **пустой тестовой базе**. Не запускайте его на базе компании. Для проверки семи ролей используйте отдельную установку по [инструкции передачи проекта](handover-deployment.md).

## 7. Проверка после установки

На публичном HTTPS-адресе выполните короткий сценарий под владельцем:

1. Войдите и откройте «Настройки». Добавьте тестовую организацию с названием и валютой `RUB`, затем обновите страницу — запись должна сохраниться.
2. Создайте сотрудника с отдельным адресом почты, паролем от 12 символов и нужной ролью; обновите список и проверьте вход этим сотрудником в отдельном приватном окне.
3. Проверьте, что разрешённые разделы видны роли, а закрытые не открываются.
4. Если сохранение не работает, откройте в браузере **DevTools → Network → Fetch/XHR**, нажмите «Сохранить» и найдите `POST /api/v1/sellers` или `POST /api/v1/admin/users`. Успешное создание возвращает `201`. При `403 ORIGIN_FORBIDDEN` сравните адрес браузера с `ALLOWED_ORIGINS` в `.env` символ в символ и выполните `sudo docker compose up -d --no-build --force-recreate api`. При `403 CSRF_INVALID` обновите страницу и войдите снова. При отсутствии POST проверьте, что веб-образ собран из актуального архива, затем обновите страницу браузера.

Не отправляйте технической поддержке скриншоты открытого `.env` или пароли. Для диагностики достаточно статуса запроса, кода ошибки и `X-Request-ID` из ответа.

## 8. Резервные копии и повседневные операции

База, файлы CRM и базы ClamAV находятся в Docker volumes. Обычная остановка не удаляет их:

```bash
cd ~/reaktiv-crm
sudo docker compose stop
sudo docker compose up -d
```

**Не выполняйте `docker compose down -v`** на рабочем сервере: флаг `-v` удаляет тома с данными. Перед первым рабочим использованием проверьте создание резервной копии:

```bash
cd ~/reaktiv-crm
sudo ./scripts/backup.sh --mode docker --compose-storage --retention 7
sudo ls -lh backups/
```

Команда создаёт архив `backups/backup_*.tar.gz` с дампом базы и файлами; `--retention 7` оставляет семь последних копий. Дамп базы и копия файлов создаются **последовательно**, поэтому для гарантированно согласованной копии проводите операцию в тихое окно, остановив `web` и `worker` на время создания архива:

```bash
sudo docker compose stop web worker
sudo ./scripts/backup.sh --mode docker --compose-storage --retention 7
sudo docker compose start worker web
```

Если копирование завершилось ошибкой, всё равно запустите `worker` и `web`, затем разберите причину. Настройте ежедневный запуск по расписанию и передачу копий **за пределы VPS**: копия на том же сервере не спасает при потере сервера. Для автоматического запуска создайте `/usr/local/sbin/reaktiv-crm-backup.sh` со следующим содержимым, заменив путь на фактический:

```bash
#!/bin/sh
set -eu
cd /home/SERVER_USER/reaktiv-crm
trap 'docker compose start worker web' EXIT
docker compose stop web worker
./scripts/backup.sh --mode docker --compose-storage --retention 7
```

Дайте файлу право на выполнение `sudo chmod 700 /usr/local/sbin/reaktiv-crm-backup.sh`. Затем добавьте задание через `sudo crontab -e`:

```cron
0 3 * * * /usr/local/sbin/reaktiv-crm-backup.sh >> /var/log/reaktiv-crm-backup.log 2>&1
```

Проверьте первый запуск задания вручную, архив, его время создания и что `web` и `worker` снова работают. Затем настройте защищённую передачу копий на другое хранилище и проверяйте её результат. Периодически проверяйте восстановление **на отдельном тестовом стенде**, а не на рабочей базе. Инструмент восстановления перезаписывает данные и требует `--force`; описание — в [технической инструкции](deployment.md). Локальный `backups/` не входит в архив исходного проекта.

Для обновления версии сначала сделайте резервную копию, затем обновите файлы приложения в той же папке и выполните:

```bash
cd ~/reaktiv-crm
sudo docker compose stop web worker
sudo ./scripts/backup.sh --mode docker --compose-storage --retention 7
sudo docker compose start worker web
sudo docker compose build api web
sudo docker compose up -d --no-build
sudo docker compose ps -a
```

Если резервное копирование завершилось ошибкой, всё равно запустите `worker` и `web`, но не продолжайте обновление до устранения ошибки. Если исходники получены через Git, после создания копии и перед сборкой выполните `git pull origin main`. Если пришёл новый ZIP, распакуйте его **во временную папку** и перенесите обновлённые исходники в существующую папку проекта, сохранив прежние `.env`, `COMPOSE_PROJECT_NAME`, `backups/` и Docker volumes. Не запускайте `init-env.py` и `bootstrap.py` повторно при обычном обновлении.

## Обновление существующего сервера клиента

Этот сценарий относится к уже работающей CRM по HTTP/IP. Сначала определите её **фактическую папку проекта** и текущее имя Compose-проекта: данные привязаны к этому проекту и его Docker volumes. Приведённые ниже команды выполняйте из существующей папки; не разворачивайте ZIP как новую чистую установку и не меняйте `COMPOSE_PROJECT_NAME`.

1. В тихое окно остановите доступ сотрудников и фоновую обработку, снимите копию данных и проверьте, что архив создан:

   ```bash
   sudo docker compose stop web worker
   sudo ./scripts/backup.sh --mode docker --compose-storage --retention 7
   sudo docker compose start worker web
   sudo ls -lh backups/ | tail
   sudo docker compose ps -a
   sudo docker compose port web 8080
   ```

   Если создание копии завершилось ошибкой, всё равно запустите `worker` и `web`; обновление отложите до получения проверенной копии.

2. Обновите исходники (`git pull origin main`, если сервер работает из Git, или переносом файлов нового архива в ту же папку с сохранением `.env`). Пересоберите **и веб, и API**, потому что исправление сохранения форм относится к интерфейсу:

   ```bash
   sudo docker compose build api web
   sudo docker compose up -d --no-build
   sudo docker compose ps -a
   ```

3. Если пока используется прямой HTTP/IP, оставьте фактический адрес вида `http://SERVER_IP:8080` в `ALLOWED_ORIGINS`, проверьте сохранение организации и сотрудника и **не переводите** `ENVIRONMENT` в `production` до появления HTTPS. При изменении `.env` примените его к контейнеру: `sudo docker compose up -d --no-build --force-recreate api`. Эта проверка HTTP/IP — временный этап, не целевой способ эксплуатации.
4. Для перехода на рабочий HTTPS сначала проверьте `sudo ss -ltnp`: если `80/443` уже заняты прежним nginx или другим прокси, не заменяйте его вслепую — настройте у него HTTPS и проксирование на `127.0.0.1:8080` либо согласуйте переход на Caddy. Если порты свободны, создайте DNS-запись и настройте Caddy по разделу 5. Перед переключением сохраните существующие `SECRET_KEY`, все пароли БД и `COMPOSE_PROJECT_NAME`. После открытия HTTPS измените `.env` на `ENVIRONMENT=production` и `ALLOWED_ORIGINS=https://crm.example.com`, затем выполните:

   ```bash
   sudo docker compose up -d --no-build --force-recreate api worker
   sudo docker compose ps -a
   ```

5. Сообщите всем сотрудникам новый адрес `https://crm.example.com/` и попросите обновить закладки. После переключения `ENVIRONMENT=production` вход через прежний `http://SERVER_IP:8080` перестанет работать из-за `Secure` cookie. Войдите по HTTPS. Владелец и другие привилегированные сотрудники должны включить второй фактор в «Настройки → Мой аккаунт → Защита аккаунта». Проверьте сохранение организации и новой учётки, обновление страницы и повторный вход. После этого закройте наружный HTTP-доступ на старый порт `8080` в панели хостинга и верните публикацию веб-контейнера только на `127.0.0.1` согласно `docker-compose.yml` этого проекта. Учтите, что если раньше `web` публиковался на `0.0.0.0:8080`, стандартный Compose после обновления может закрыть прямой IP-адрес ещё до этого шага; переход на домен готовьте заранее.

Для рабочего обновления **не нужны** повторные `init-env.py`, создание владельца, демонстрационные учётки и удаление volumes. Они могут нарушить доступ или создать отдельную пустую установку.

## Если что-то не открылось

| Симптом | Что проверить |
| --- | --- |
| `https://домен` не открывается | DNS `A`, внешние `80/443`, `sudo systemctl status caddy`, `sudo journalctl -u caddy -n 100 --no-pager`. |
| Caddy показывает `502` | `curl http://127.0.0.1:8080/ready`, `sudo docker compose ps -a`, журналы `api`, `migrate`, `clamav`. |
| Страница есть, но формы не сохраняются | DevTools → Network → POST, точный `ALLOWED_ORIGINS`, пересоздание `api`, версия `web`; подробнее в разделе 7. |
| После входа привилегированные разделы дают `MFA_REQUIRED` | Настройте второй фактор в «Моём аккаунте» и войдите снова. |
| Данные «пропали» после обновления | Проверьте текущую папку и `COMPOSE_PROJECT_NAME`: вероятно, запущен другой Compose-проект с новыми volumes. Не удаляйте старые volumes. |
