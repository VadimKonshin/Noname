#!/usr/bin/env python3
"""Write a timestamped database dump and media archive into deploy/backups."""
import gzip
import subprocess
import sys
import time
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent
sys.path.insert(0, str(DEPLOY))

from envfile import read_env, sql_ident


def main():
    env = read_env(DEPLOY / '.env')
    try:
        user = sql_ident(env.get('DJANGO_DB_USER', ''))
        name = sql_ident(env.get('DJANGO_DB_NAME', ''))
    except ValueError:
        raise SystemExit('DJANGO_DB_NAME и DJANGO_DB_USER: буквы, цифры и _.')
    backups = DEPLOY / 'backups'
    backups.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M')
    db_path = backups / f'db-{stamp}.sql.gz'
    media_path = backups / f'media-{stamp}.tar.gz'

    dump = subprocess.run(
        ['docker', 'compose', 'exec', '-T', 'postgres', 'pg_dump', '-U', user, '-d', name, '--no-owner', '--no-acl'],
        cwd=DEPLOY,
        stdout=subprocess.PIPE,
        check=False,
    )
    if dump.returncode != 0 or not dump.stdout:
        sys.stderr.buffer.write(dump.stderr or b'')
        raise SystemExit('Не удалось снять базу.')
    db_path.write_bytes(gzip.compress(dump.stdout, mtime=0))

    with media_path.open('wb') as handle:
        packed = subprocess.run(
            ['docker', 'compose', 'exec', '-T', '-u', 'app', 'web', 'python', '/app/deploy/pack_media.py'],
            cwd=DEPLOY,
            stdout=handle,
            stderr=subprocess.PIPE,
            check=False,
        )
    if packed.returncode != 0:
        sys.stderr.buffer.write(packed.stderr or b'')
        raise SystemExit('Не удалось снять фотографии.')

    cutoff = time.time() - 14 * 24 * 60 * 60
    for path in backups.iterdir():
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink()
    print(db_path)
    print(media_path)


if __name__ == '__main__':
    main()
