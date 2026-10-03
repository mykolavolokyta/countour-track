"""Generate server secrets for a fresh installation; never overwrite configuration."""
import argparse
import base64
import os
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.env', help='Destination env file (default: project .env)')
    args = parser.parse_args()
    template = (ROOT / '.env.example').read_text()
    content = template.replace('ENCRYPTION_KEY=\n', 'ENCRYPTION_KEY=' + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode() + '\n')
    content = content.replace('POSTGRES_PASSWORD=\n', 'POSTGRES_PASSWORD=' + secrets.token_hex(24) + '\n')
    try:
        fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise SystemExit('Configuration already exists. Edit it or choose --output; nothing was overwritten.')
    with os.fdopen(fd, 'w') as output:
        output.write(content)
    print('Configuration created. Set Google/Telegram client credentials before signing in.')


if __name__ == '__main__':
    main()
