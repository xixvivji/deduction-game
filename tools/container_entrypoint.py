"""Prepare persistent local operator credentials, then exec the requested process."""
import os
from pathlib import Path
import secrets
import sys


def prepare_token(path):
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        token = file.read_text().strip()
        if len(token) < 32:
            raise ValueError('운영 접근 키 파일이 올바르지 않습니다')
    else:
        with os.fdopen(fd, 'w') as handle:
            handle.write(secrets.token_urlsafe(32) + '\n')


def main():
    path = os.environ.get('GAME_OPS_TOKEN_FILE')
    if path:
        prepare_token(path)
    if len(sys.argv) < 2:
        raise SystemExit('실행 명령이 필요합니다')
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == '__main__': main()
