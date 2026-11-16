import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
import subprocess
import tempfile
from pathlib import Path

from scrapling import Selector
from watch import inspect_page, repeat_alert, notify


def document(rows='', footer='', date='20261119'):
    return Selector(f'''<html><head><title>Bachelor Party D'eux Movie Showtimes</title></head>
    <body><div id="main-content"><div id="{date}"></div><div role="grid">
    {rows}</div></div><footer>{footer}</footer></body></html>''')


def venue(code, buttons):
    return f'<div><div><a href="/cinemas/kochi/cinema/{code}"></a></div>{buttons}</div>'


BUTTON = '<div role="button" tabindex="0" aria-label="10:00 AM, 4K ATMOS">10:00 AM</div>'


class DetectionTests(unittest.TestCase):
    def test_dated_venue_booking_link(self):
        page = document(venue('buytickets/PVMF/20261119', BUTTON))
        self.assertEqual(inspect_page(page), ['10:00 AM, 4K ATMOS'])

    def test_footer_does_not_trigger(self):
        page = document(venue('SNYK', BUTTON), '<a href="/cinemas/kochi/forum/PVMF">PVR: Forum Mall, Kochi</a>')
        self.assertEqual(inspect_page(page), [])

    def test_target_showtime(self):
        page = document(venue('PVMF', BUTTON) + venue('SNYK', BUTTON.replace('10:00', '11:00')))
        self.assertEqual(inspect_page(page), ['10:00 AM, 4K ATMOS'])

    def test_other_theatre_buttons_not_inherited(self):
        self.assertEqual(inspect_page(document(venue('PVMF', '') + venue('SNYK', BUTTON))), [])

    def test_disabled_and_sold_out(self):
        for button in [BUTTON.replace('tabindex="0"', 'aria-disabled="true"'),
                       BUTTON.replace('tabindex="0"', 'tabindex="-1"'),
                       BUTTON.replace('4K ATMOS', 'Sold Out'),
                       '<div aria-disabled="true">' + BUTTON + '</div>']:
            with self.subTest(button=button):
                self.assertEqual(inspect_page(document(venue('PVMF', button))), [])

    def test_challenge_is_unknown(self):
        with self.assertRaises(ValueError):
            inspect_page(Selector('<title>Attention Required! | Cloudflare</title>'), 403)

    def test_missing_grid_or_wrong_date_is_unknown(self):
        for page in [document(), document(venue('SNYK', BUTTON), date='20261120')]:
            with self.assertRaises(ValueError):
                inspect_page(page)


class AlertTests(unittest.TestCase):
    def test_alert_repeats_until_stop(self):
        args = SimpleNamespace(command='run', alert_interval=15, alert_volume=2.0)
        with patch('watch.STOP') as stop, patch('watch.notify') as alert:
            stop.is_set.return_value = False
            stop.wait.side_effect = [False, True]
            repeat_alert('Tickets detected', args)
            self.assertEqual(alert.call_count, 2)
            alert.assert_called_with('Tickets detected', volume=2.0, bursts=3)
            stop.wait.assert_called_with(15)

    def test_once_does_not_repeat(self):
        args = SimpleNamespace(command='once', alert_interval=15, alert_volume=2.0)
        with patch('watch.STOP') as stop, patch('watch.notify') as alert:
            stop.is_set.return_value = False
            repeat_alert('Tickets detected', args)
            alert.assert_called_once()
            stop.wait.assert_not_called()

    def test_audio_still_plays_if_banner_fails(self):
        with tempfile.TemporaryDirectory() as directory, patch('watch.STATE', Path(directory)), \
                patch('watch.sys.platform', 'darwin'), patch('watch.STOP') as stop, \
                patch('watch.subprocess.run') as run, patch('builtins.print'), \
                self.assertLogs(level='ERROR'):
            stop.is_set.return_value = False
            run.side_effect = [subprocess.CalledProcessError(1, 'osascript'), MagicMock(), MagicMock(), MagicMock()]
            notify('Test', volume=2.0, bursts=3)
            self.assertEqual(run.call_count, 4)
            # The monitor's manually configured playback gain is currently 5.
            self.assertEqual(run.call_args.args[0][:3], ['afplay', '-v', '5'])


if __name__ == '__main__':
    unittest.main()
