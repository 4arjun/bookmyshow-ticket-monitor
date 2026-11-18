"""Send ticket alerts through an explicitly configured Telegram user account."""
import asyncio
import getpass
import json
import os
import re
import tempfile

if __package__:
    from .telegram_alert import TelegramError
else:
    from telegram_alert import TelegramError


def save_config(path, config):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as output:
        temporary = path.parent / os.path.basename(output.name)
        try:
            json.dump(config, output)
            output.flush()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def recipients(config):
    if 'recipients' in config:
        return config['recipients']
    return [dict(user_id=config['user_id'], access_hash=config['access_hash'],
                 label=f"Saved Telegram recipient (user ID ending in {str(config['user_id'])[-4:]}; phone number was not stored)")]


async def connect_and_run(config, message=None, numbers=None, delivered=None):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.types import InputPeerUser, User

    client = TelegramClient(
        StringSession(config.get('session', '')), config['api_id'], config['api_hash'],
        receive_updates=False, flood_sleep_threshold=0, connection_retries=0,
        # Login may need to resend its request after Telegram switches data centers.
        request_retries=2 if message is None and numbers is None else 0,
        raise_last_call_error=True, timeout=10,
    )
    try:
        if message is None and numbers is None:
            await client.start(
                phone=lambda: input('Your phone number with country code: ').strip(),
                code_callback=lambda: getpass.getpass('Telegram login code: '),
                password=lambda: getpass.getpass('Telegram two-step password: '),
            )
            recipient = input('Recipient Telegram @username or +countrycode phone: ').strip()
            if not re.fullmatch(r'@[A-Za-z0-9_]+|\+[1-9][0-9]{6,14}', recipient):
                raise TelegramError('Enter an @username or international phone number.')
            entity = await client.get_entity(recipient)
            if not isinstance(entity, User) or entity.bot or entity.access_hash is None:
                raise TelegramError('Select a personal Telegram account.')
            name = ' '.join(part for part in (entity.first_name, entity.last_name) if part)
            print(f'Recipient: {name} ({recipient})')
            if input('Type YES to enable ticket alerts to this recipient: ').strip() != 'YES':
                return False
            config.update(session=client.session.save(), recipients=[dict(
                user_id=entity.id, access_hash=entity.access_hash, label=recipient)])
        else:
            await client.connect()
            if not await client.is_user_authorized():
                raise TelegramError('Personal Telegram session expired. Run telegram-personal-setup.')
            if numbers is not None:
                selected = {}
                for number in numbers:
                    try:
                        entity = await client.get_entity(number)
                    except ValueError:
                        raise TelegramError(f'Telegram could not resolve {number}. Check the country code and your Telegram contacts.') from None
                    if not isinstance(entity, User) or entity.bot or entity.access_hash is None:
                        raise TelegramError(f'{number} did not resolve to a personal Telegram account.')
                    selected[entity.id] = dict(user_id=entity.id, access_hash=entity.access_hash, label=number)
                config['recipients'] = list(selected.values())
                # Older running monitors still read the single-recipient fields.
                first = config['recipients'][0]
                config.update(user_id=first['user_id'], access_hash=first['access_hash'])
            else:
                delivered = set() if delivered is None else delivered
                failure = None
                for recipient in recipients(config):
                    user_id = recipient['user_id']
                    if user_id in delivered:
                        continue
                    peer = InputPeerUser(user_id, recipient['access_hash'])
                    try:
                        await client.send_message(peer, message[:4096], link_preview=False, parse_mode=None)
                    except Exception as exc:
                        if getattr(exc, 'seconds', 0):
                            raise
                        failure = exc
                    else:
                        delivered.add(user_id)
                if failure:
                    raise failure
        return True
    finally:
        await client.disconnect()


def setup(path):
    try:
        config = {
            'api_id': int(input('Telegram app API ID: ').strip()),
            'api_hash': getpass.getpass('Telegram app API hash: ').strip(),
        }
        if asyncio.run(connect_and_run(config)):
            save_config(path, config)
            print('Personal Telegram delivery enabled.')
    except ImportError:
        raise TelegramError('Install Telethon with uv pip install --python .venv/bin/python "telethon<2".') from None
    except TelegramError:
        raise
    except Exception as exc:
        raise TelegramError(f'Personal Telegram setup failed ({type(exc).__name__}).') from None


def manage(path, numbers):
    if not path.exists():
        raise TelegramError('Run telegram-personal-setup first.')
    try:
        config = json.loads(path.read_text())
        if numbers:
            phones = list(dict.fromkeys(re.sub(r'[ ()-]', '', number) for number in numbers))
            if not all(re.fullmatch(r'\+[1-9][0-9]{6,14}', number) for number in phones):
                raise TelegramError('Use international numbers beginning with + and the country code.')
            asyncio.run(asyncio.wait_for(connect_and_run(config, numbers=phones), timeout=60))
            save_config(path, config)
        for recipient in recipients(config):
            print(recipient['label'])
    except TelegramError:
        raise
    except Exception as exc:
        raise TelegramError(f'Could not manage recipients ({type(exc).__name__}).') from None


def send(path, message, delivered=None):
    try:
        config = json.loads(path.read_text())
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        raise TelegramError('Cannot read personal Telegram configuration.') from None
    try:
        return asyncio.run(asyncio.wait_for(connect_and_run(config, message, delivered=delivered), timeout=20))
    except TelegramError:
        raise
    except Exception as exc:
        raise TelegramError(
            f'Personal Telegram delivery failed ({type(exc).__name__}).',
            getattr(exc, 'seconds', 60),
        ) from None
