#!/usr/bin/env python3
"""Dump the laptop database and product photos into deploy/bundle."""
import subprocess
import sys
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent
ROOT = DEPLOY.parent
sys.path.insert(0, str(DEPLOY))

from envfile import read_env, sql_ident
from media_archive import file_sha256, pack

COPY_PREFIXES = ('TELEGRAM_', 'CDEK_', 'DJANGO_EMAIL')
COPY_KEYS = {'DJANGO_ORDER_EMAIL', 'DJANGO_SITE_URL', 'DJANGO_DEFAULT_FROM_EMAIL'}


def main():
    env_path = ROOT / '.env'
    if not env_path.is_file():
        raise SystemExit('Нет .env в папке проекта. Локальная база описана там.')
    env = read_env(env_path)
    try:
        user = sql_ident(env.get('DJANGO_DB_USER', ''))
        name = sql_ident(env.get('DJANGO_DB_NAME', ''))
    except ValueError:
        raise SystemExit('В .env DJANGO_DB_NAME и DJANGO_DB_USER должны быть простыми именами.')
    if env.get('DJANGO_DB_ENGINE', '').lower() not in {'postgres', 'postgresql'}:
        raise SystemExit('Локальный .env должен указывать DJANGO_DB_ENGINE=postgresql.')

    bundle = DEPLOY / 'bundle'
    bundle.mkdir(parents=True, exist_ok=True)
    sql_path = bundle / 'noname.sql'
    print('Снимаю локальную PostgreSQL.')
    with sql_path.open('wb') as handle:
        result = subprocess.run(
            ['docker', 'compose', 'exec', '-T', 'postgres', 'pg_dump', '-U', user, '-d', name, '--no-owner', '--no-acl'],
            cwd=ROOT,
            stdout=handle,
            stderr=subprocess.PIPE,
        )
    if result.returncode != 0 or sql_path.stat().st_size == 0:
        sql_path.unlink(missing_ok=True)
        sys.stderr.buffer.write(result.stderr)
        raise SystemExit('Не удалось снять базу. Запустите в папке проекта: docker compose up -d')

    media_path = bundle / 'media.tar.gz'
    with media_path.open('wb') as handle:
        photos = pack(ROOT / 'media', handle)
    print(f'Фотографий в архиве: {photos}.')

    lines = [
        f'{file_sha256(sql_path)}  noname.sql',
        f'{file_sha256(media_path)}  media.tar.gz',
    ]
    (bundle / 'SHA256SUMS').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    present = sorted(
        key for key, value in env.items()
        if value and (key.startswith(COPY_PREFIXES) or key in COPY_KEYS)
    )
    print('Готово: deploy/bundle/noname.sql, media.tar.gz и SHA256SUMS.')
    print('Пароли и токены в архив не входят. В deploy/.env на сервере перепишите вручную:')
    for key in present:
        print(f'  {key}')
    print('Перед запуском бота на сервере остановите локальный runserver.')


if __name__ == '__main__':
    main()
