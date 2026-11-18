import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace
import subprocess
import tempfile
from pathlib import Path

from scrapling import Selector
from watch import inspect_page, claim_new_shows, notify, fetch_shows, DateNotListed


def document(rows='', footer='', date='20261217'):
    return Selector(f'''<html><head><title>Avengers: Doomsday Movie Showtimes</title></head>
    <body><div id="main-content"><div id="{date}"></div><div role="grid">
    {rows}</div></div><footer>{footer}</footer></body></html>''')


def venue(code, buttons):
    return f'<div><div><a href="/cinemas/kochi/cinema/{code}"></a></div>{buttons}</div>'


BUTTON = '<div role="button" tabindex="0" aria-label="10:00 AM, 4K ATMOS">10:00 AM</div>'


class DetectionTests(unittest.TestCase):
    def test_requested_date_not_yet_listed(self):
        response = document(venue('PVMF', BUTTON), date='20261119')
        response.status = 200
        response.url = 'https://in.bookmyshow.com/movies/kochi/avengers-doomsday/buytickets/ET00526287/20261119'
        with patch('watch.StealthyFetcher.fetch', return_value=response):
            with self.assertRaises(DateNotListed):
                fetch_shows()

    def test_explicit_empty_showtime_page(self):
        page = document('There are no shows available for this date for the selected language format.')
        self.assertEqual(inspect_page(page), [])

    def test_dated_venue_booking_link(self):
        page = document(venue('buytickets/PVMF/20261217', BUTTON))
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
    def setUp(self):
        telegram = patch('watch.TelegramDelivery')
        telegram.start()
        self.addCleanup(telegram.stop)

    def test_seen_times_are_shared_between_providers_and_checks(self):
        with tempfile.TemporaryDirectory() as directory, patch('watch.CONFIG', Path(directory)):
            self.assertEqual(claim_new_shows(['12:05 AM, PXL']), ['12:05 AM, PXL'])
            self.assertEqual(claim_new_shows(['12:05 AM MS INFINITY VISION 3D', '1:35 PM']), ['1:35 PM'])
            self.assertEqual(claim_new_shows(['01:35 PM, PXL']), [])

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
