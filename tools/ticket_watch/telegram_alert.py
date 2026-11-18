"""Optional Telegram delivery using locally stored bot credentials."""
import getpass
import json
import os
import re
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class TelegramError(Exception):
    def __init__(self, message, retry_after=60):
        super().__init__(message)
        self.retry_after = max(60, retry_after)


def api_call(token, method, payload):
    request = Request(
        f'https://api.telegram.org/bot{token}/{method}',
        data=json.dumps(payload).encode(),
        headers={'Content-Type': 'application/json'},
    )
    try:
        with urlopen(request, timeout=15) as response:
            result = json.load(response)
    except HTTPError as exc:
        retry_after = 60
        try:
            retry_after = int(json.load(exc).get('parameters', {}).get('retry_after', 60))
        except (ValueError, TypeError):
            pass
        raise TelegramError(f'Telegram returned HTTP {exc.code}', retry_after) from None
    except Exception as exc:
        # Exception text can contain the URL, which embeds the bot token.
        raise TelegramError(f'Telegram request failed ({type(exc).__name__})') from None
    if not result.get('ok'):
        raise TelegramError('Telegram rejected the request')
    return result['result']


def setup(path):
    token = getpass.getpass('Bot token from @BotFather (hidden): ').strip()
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token):
        raise TelegramError('Invalid bot token format')
    bot = api_call(token, 'getMe', {})
    input(f'Open https://t.me/{bot["username"]} on your phone, tap Start, then press Enter here. ')
    updates = api_call(token, 'getUpdates', {'limit': 100, 'timeout': 0})
    chats = {}
    for update in updates:
        chat = update.get('message', {}).get('chat', {})
        if chat.get('type') == 'private':
            chats[str(chat['id'])] = chat.get('first_name', 'Private chat')
    if not chats:
        raise TelegramError('No private chat found. Send a message to your bot and run telegram-setup again.')
    for chat_id, name in chats.items():
        print(f'{chat_id}: {name}')
    chat_id = input('Enter your chat ID from the list above: ').strip()
    if chat_id not in chats:
        raise TelegramError('Choose a chat ID from the displayed list')
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as output:
        os.fchmod(output.fileno(), 0o600)
        json.dump({'token': token, 'chat_id': chat_id}, output)
    temporary.replace(path)
    print('Telegram configured. Run telegram-test to send a test message to the selected chat.')


def send(path, message):
    if not path.exists():
        return False
    try:
        config = json.loads(path.read_text())
        token, chat_id = config['token'], config['chat_id']
    except (OSError, ValueError, KeyError, TypeError):
        raise TelegramError('Invalid Telegram configuration. Run telegram-setup again.') from None
    api_call(token, 'sendMessage', {
        'chat_id': chat_id,
        'text': message[:4096],
        'disable_notification': False,
        'link_preview_options': {'is_disabled': True},
    })
    return True


class TelegramDelivery:
    """Send once after success, retry failed deliveries without flooding the API."""
    def __init__(self, path, sender=None):
        self.path = path
        self.sender = sender or send
        self.sent = False
        self.next_attempt = 0

    def attempt(self, message):
        if self.sent or time.monotonic() < self.next_attempt:
            return
        self.next_attempt = time.monotonic() + 60
        try:
            self.sent = self.sender(self.path, message)
        except TelegramError as exc:
            self.next_attempt = time.monotonic() + exc.retry_after
            raise
