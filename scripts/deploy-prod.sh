#!/usr/bin/env bash
# Выкатка CRM на прод одной командой, с машины разработчика.
#
#   ./scripts/deploy-prod.sh           выкатить origin/main на прод
#   ./scripts/deploy-prod.sh --check   только проверить: что на проде, что уйдёт, есть ли связь
#
# Шаги: проверки (ветка main, всё закоммичено и запушено) → сборка фронта здесь (на сервере нет npm)
# → бэкап на сервере → код до origin/main → текущие образы помечаются rollback-<коммит> → сборка api
# → образ web из собранного фронта → docker compose up → проверки (миграции, /ready, новый фронт)
# → старые rollback-образы удаляются, кроме трёх последних. Откат печатается в конце.
set -euo pipefail

HOST=${CRM_DEPLOY_HOST:-kpuser@185.237.186.58}
KEY=${CRM_DEPLOY_KEY:-$HOME/.ssh/crm_deploy}
APP=${CRM_DEPLOY_DIR:-kp_app/reaktiv-crm}
URL=${CRM_DEPLOY_URL:-https://crm.ogk-chem.ru}
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ServerAliveInterval=10 "$HOST")

step() { printf '\n== %s\n' "$*"; }
die() { printf '\nОШИБКА: %s\n' "$*" >&2; exit 1; }
remote() { "${SSH[@]}" "cd $APP && $1"; }

# Long jobs run detached on the server: a silent ssh session gets cut. Waits for "EXIT:<code>" in the log.
remote_job() {
  local name=$1 command=$2 log="/tmp/crm-deploy-$1.log"
  remote "rm -f $log; (setsid nohup bash -c '$command; echo EXIT:\$?' > $log 2>&1 < /dev/null &)"
  local waited=0
  while :; do
    sleep 10
    waited=$((waited + 10))
    local status
    status=$(remote "grep -o 'EXIT:[0-9]*' $log | tail -1" || true)
    if [[ -n $status ]]; then
      remote "tail -5 $log" || true
      [[ $status == EXIT:0 ]] || die "$name завершился с ошибкой ($status), журнал на сервере: $log"
      return
    fi
    ((waited % 60 == 0)) && echo "… $name идёт ${waited} с"
    ((waited < 1800)) || die "$name идёт дольше 30 минут, журнал на сервере: $log"
  done
}

mode=deploy
[[ ${1:-} == --check ]] && mode=check

cd "$(git rev-parse --show-toplevel)"
step "Проверки"
[[ $(git rev-parse --abbrev-ref HEAD) == main ]] || die "выкатывается только ветка main"
git diff --quiet && git diff --cached --quiet || die "есть незакоммиченные изменения в отслеживаемых файлах"
git fetch -q origin main
[[ $(git rev-parse HEAD) == $(git rev-parse origin/main) ]] || die "локальный main не совпадает с origin/main: запушьте или подтяните"
sha=$(git rev-parse --short HEAD)
live=$(remote "git rev-parse --short HEAD") || die "нет связи с сервером $HOST"
echo "На проде: $live · выкатываем: $sha"
git log --oneline "$live..$sha" 2>/dev/null | sed 's/^/  /' || true
remote "docker compose ps --format '{{.Name}} {{.Status}}'" | sed 's/^/  /'
remote "df -h / | tail -1" | sed 's/^/  диск: /'
if [[ $mode == check ]]; then
  echo "Проверка закончена, ничего не менялось."
  exit 0
fi
[[ $live != "$sha" ]] || echo "Этот коммит уже на проде: пересобираю и перезапускаю."

step "Сборка фронта здесь"
(cd frontend && npm run build >/dev/null)
bundle=$(grep -o 'assets/index-[^"]*\.js' frontend/dist/index.html | head -1)
[[ -n $bundle ]] || die "в собранном index.html нет основного скрипта"

step "Бэкап на сервере"
remote_job backup "./scripts/backup.sh --mode docker --compose-storage"

step "Код до origin/main, образы для отката: rollback-$live"
remote "git fetch -q origin && git merge --ff-only origin/main && git log --oneline -1"
remote "docker tag reaktiv-crm-backend:local reaktiv-crm-backend:rollback-$live && docker tag reaktiv-crm-web:local reaktiv-crm-web:rollback-$live"

step "Образ web из собранного фронта"
tmp=/tmp/crm-web-$sha
tar czf - -C frontend dist nginx.conf | "${SSH[@]}" "rm -rf $tmp && mkdir -p $tmp && tar xzf - -C $tmp"
# The runtime stage of frontend/Dockerfile, with the dist built here instead of in a node stage.
sed -n '/^FROM nginx/,$p' frontend/Dockerfile |
  sed 's#--from=build --chown=nginx:nginx /app/dist#--chown=nginx:nginx dist#' |
  "${SSH[@]}" "cat > $tmp/Dockerfile"
remote_job web "docker build -q -t reaktiv-crm-web:local $tmp && rm -rf $tmp"

step "Сборка api (несколько минут)"
remote_job api "docker compose build api"

step "Перезапуск"
remote_job up "docker compose up -d --no-build"

step "Проверки после выкатки"
for _ in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "$URL/ready" || true)
  [[ $code == 200 ]] && break
  sleep 5
done
[[ $code == 200 ]] || die "$URL/ready отвечает $code"
echo "$URL/ready: 200"
remote "docker compose ps -a migrate --format '{{.State}} {{.ExitCode}}'" | grep -q 'exited 0' || die "миграции не завершились успешно: docker compose logs migrate"
remote "docker compose exec -T api alembic current 2>/dev/null | tail -1" | sed 's/^/alembic: /'
curl -s "$URL/" | grep -q "$bundle" || die "сайт CRM отдаёт не новый фронт (ждали $bundle)"
echo "фронт: $bundle"
remote "docker compose ps --format '{{.Name}} {{.Status}}'" | sed 's/^/  /'

step "Уборка старых образов отката (остаются три последних)"
remote "for repo in reaktiv-crm-backend reaktiv-crm-web; do docker images \$repo --format '{{.CreatedAt}}|{{.Tag}}' | grep '|rollback-' | sort -r | tail -n +4 | cut -d'|' -f2 | while read -r tag; do docker rmi -f \$repo:\$tag >/dev/null; done; done; docker image prune -f >/dev/null; df -h / | tail -1"

cat <<EOF

ГОТОВО: на проде $sha (было $live).
Откат, если понадобится (если в выкатке была миграция базы, сначала откатите её):
  ssh $HOST 'cd $APP && docker tag reaktiv-crm-backend:rollback-$live reaktiv-crm-backend:local && docker tag reaktiv-crm-web:rollback-$live reaktiv-crm-web:local && docker compose up -d --no-build'
EOF
