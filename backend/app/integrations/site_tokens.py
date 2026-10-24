"""Issue, list and revoke the site's service tokens.

Run inside the API container, e.g. ``docker compose exec api python -m app.integrations.site_tokens create
--name production``. The token and the signing secret are printed once: put them into the site's
CRM_TOKEN and CRM_SIGNING_SECRET. Rotation: issue a second token, switch the site to it, revoke the old one.
"""

import argparse

from sqlalchemy import select

from app.core.db import SessionLocal, utcnow
from app.core.service import audit
from app.integrations.models import IntegrationToken
from app.integrations.site_auth import issue_token, site_user


def main() -> None:
    import app.models  # noqa: F401  (all tables known before the first query)

    parser = argparse.ArgumentParser(description='Сервисные токены сайта')
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create', help='выпустить токен')
    create.add_argument('--name', required=True, help='например, production или staging')
    commands.add_parser('list', help='показать токены')
    revoke = commands.add_parser('revoke', help='отозвать токен')
    revoke.add_argument('token_id')
    args = parser.parse_args()

    with SessionLocal.begin() as db:
        if args.command == 'create':
            row, token, secret = issue_token(db, args.name)
            audit(db, site_user(db), 'integration_token', row.id, 'issued', after={'name': row.name})
            print(f'Токен {row.id} «{row.name}» выпущен. Сохраните значения — повторно их не показать.')
            print(f'CRM_TOKEN={token}')
            print(f'CRM_SIGNING_SECRET={secret}')
        elif args.command == 'list':
            for row in db.scalars(select(IntegrationToken).order_by(IntegrationToken.created_at)):
                state = 'активен' if row.active else f'отозван {row.revoked_at:%Y-%m-%d}'
                used = f'{row.last_used_at:%Y-%m-%d %H:%M}' if row.last_used_at else 'не использовался'
                print(f'{row.id}  {row.name:<20} {state:<20} последний вызов: {used}')
        else:
            target = db.get(IntegrationToken, args.token_id)
            if target is None:
                raise SystemExit('Токен не найден')
            target.active = False
            target.revoked_at = utcnow()
            target.version += 1
            audit(db, site_user(db), 'integration_token', target.id, 'revoked', after={'name': target.name})
            print(f'Токен «{target.name}» отозван.')


if __name__ == '__main__':
    main()
