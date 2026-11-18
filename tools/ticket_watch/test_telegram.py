import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from telegram_alert import TelegramDelivery, TelegramError, api_call, send


class TelegramTests(unittest.TestCase):
    def test_unconfigured_sends_nothing(self):
        with tempfile.TemporaryDirectory() as directory, patch('telegram_alert.api_call') as api:
            self.assertFalse(send(Path(directory) / 'missing.json', 'message'))
            api.assert_not_called()

    def test_delivery_stops_after_success(self):
        with patch('telegram_alert.send', return_value=True) as sender:
            delivery = TelegramDelivery(Path('unused'))
            delivery.attempt('tickets')
            delivery.attempt('tickets')
            sender.assert_called_once()

    def test_retry_respects_server_delay(self):
        with patch('telegram_alert.send') as sender, patch('telegram_alert.time.monotonic') as clock:
            sender.side_effect = [TelegramError('rate limited', 120), True]
            clock.return_value = 0
            delivery = TelegramDelivery(Path('unused'))
            with self.assertRaises(TelegramError):
                delivery.attempt('tickets')
            clock.return_value = 60
            delivery.attempt('tickets')
            self.assertEqual(sender.call_count, 1)
            clock.return_value = 120
            delivery.attempt('tickets')
            self.assertTrue(delivery.sent)

    def test_errors_do_not_expose_token(self):
        with patch('telegram_alert.urlopen', side_effect=OSError('https://api.telegram.org/botSECRET/sendMessage')):
            with self.assertRaises(TelegramError) as error:
                api_call('SECRET', 'sendMessage', {})
            self.assertNotIn('SECRET', str(error.exception))

    def test_api_success_and_payload(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"ok":true,"result":{"message_id":5}}'
        with patch('telegram_alert.urlopen', return_value=response) as opener:
            self.assertEqual(api_call('test-token', 'sendMessage', {'chat_id': '123', 'text': 'hello'}), {'message_id': 5})
            self.assertIn(b'"chat_id": "123"', opener.call_args.args[0].data)


if __name__ == '__main__':
    unittest.main()


class PersonalRecipientTests(unittest.TestCase):
    def test_unresolved_number_preserves_legacy_configuration(self):
        import telegram_personal
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            original = '{"user_id":1,"access_hash":2}'
            path.write_text(original)
            with patch.object(telegram_personal, 'connect_and_run', side_effect=TelegramError('lookup failed')):
                with self.assertRaises(TelegramError):
                    telegram_personal.manage(path, ['+12025550101', '+12025550102'])
            self.assertEqual(path.read_text(), original)

    def test_delivery_retries_only_unsent_recipients(self):
        import asyncio
        from unittest.mock import AsyncMock
        import telegram_personal
        config = dict(api_id=1, api_hash='mock', recipients=[
            dict(user_id=i, access_hash=i) for i in (1, 2, 3)])
        client = AsyncMock()
        client.is_user_authorized.return_value = True
        client.send_message.side_effect = [None, ValueError('failed'), None, None]
        delivered = set()
        with patch('telethon.TelegramClient', return_value=client):
            with self.assertRaises(ValueError):
                asyncio.run(telegram_personal.connect_and_run(config, 'alert', delivered=delivered))
            asyncio.run(telegram_personal.connect_and_run(config, 'alert', delivered=delivered))
        self.assertEqual([args.args[0].user_id for args in client.send_message.await_args_list], [1, 2, 3, 2])
