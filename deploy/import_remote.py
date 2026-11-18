#!/usr/bin/env python3
"""Load deploy/bundle into the public server. This replaces the server database."""
import subprocess
import sys
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent
sys.path.insert(0, str(DEPLOY))

from envfile import production_issues, read_env, sql_ident
from media_archive import file_sha256


def compose(args, check=True, **kwargs):
    print('+ docker compose ' + ' '.join(args), flush=True)
    try:
        return subprocess.run(['docker', 'compose', *args], cwd=DEPLOY, check=check, **kwargs)
    except FileNotFoundError:
        raise SystemExit('Нужна команда docker.')
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode)


def psql(user, statement):
    compose([
        'exec', '-T', 'postgres',
        'psql', '-U', user, '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-c', statement,
    ])


def verify(bundle):
    sums = bundle / 'SHA256SUMS'
    if not sums.is_file():
        return
    for line in sums.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        digest, name = line.split('  ', 1)
        path = bundle / name
        if file_sha256(path) != digest:
            raise SystemExit(f'Контрольная сумма не сходится: {name}. Скопируйте архив ещё раз.')


def main():
    if '--replace' not in sys.argv:
        raise SystemExit('Команда сотрёт базу на сервере. Запуск: python3 import_remote.py --replace')
    env = read_env(DEPLOY / '.env')
    errors, warnings = production_issues(env)
    for warning in warnings:
        print('Внимание: ' + warning, file=sys.stderr)
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        raise SystemExit(1)
    bundle = DEPLOY / 'bundle'
    sql_path = bundle / 'noname.sql'
    if not sql_path.is_file() or sql_path.stat().st_size == 0:
        raise SystemExit('Нет deploy/bundle/noname.sql. На своём компьютере: python3 deploy/export_local.py')
    verify(bundle)
    user = sql_ident(env['DJANGO_DB_USER'])
    name = sql_ident(env['DJANGO_DB_NAME'])

    compose(['build'])
    compose(['stop', 'web', 'bot', 'caddy'], check=False)
    compose(['up', '-d', '--wait', 'postgres'])
    psql(user, f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{name}' AND pid <> pg_backend_pid();")
    psql(user, f'DROP DATABASE IF EXISTS {name};')
    psql(user, f'CREATE DATABASE {name} OWNER {user};')
    print('Загружаю дамп.')
    with sql_path.open('rb') as handle:
        compose([
            'exec', '-T', 'postgres',
            'psql', '-U', user, '-d', name, '-v', 'ON_ERROR_STOP=1', '--single-transaction',
        ], stdin=handle)
    compose(['up', '-d', '--wait', 'web'])

    media_path = bundle / 'media.tar.gz'
    if media_path.is_file() and media_path.stat().st_size > 0:
        print('Раскладываю фотографии.')
        with media_path.open('rb') as handle:
            compose(['exec', '-T', '-u', 'root', 'web', 'python', '/app/deploy/extract_media.py'], stdin=handle)
        compose(['exec', '-u', 'root', 'web', 'chown', '-R', 'app:app', '/app/media'])
    compose(['up', '-d'])
    print('Сайт поднят. Откройте https://' + env['SITE_ADDRESS'].strip())


if __name__ == '__main__':
    main()
