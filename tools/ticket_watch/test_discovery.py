import unittest
from datetime import date
from unittest.mock import patch

import discovery


class DiscoveryTests(unittest.TestCase):
    def test_initials_typo_and_agent_suggestion(self):
        url = 'https://in.bookmyshow.com/movies/kochi/om-operation-mole/ET1'
        body = f'<a href="{url}">Movie</a>'
        def resolve(title, lookup=None):
            return discovery.movie_link(body, 'https://in.bookmyshow.com', 'kochi', title, 'bookmyshow', lookup)
        for title in ['O.M: Operation Mole', 'OM Opreation Mole']:
            self.assertEqual(resolve(title), url)
        with self.assertRaises(discovery.AwaitingListing):
            resolve('OM Operasion Moel')
        self.assertEqual(resolve('OM Operasion Moel', lambda *_: 'O.M Operation Mole'), url)
        with self.assertRaises(discovery.AwaitingListing):
            resolve('OM Operasion Moel', lambda *_: 'Another Movie')

    def test_typo_in_venue(self):
        self.assertTrue(discovery.venue_matches('new cntral talkies', 'New Central Talkies RGB Laser'))
        self.assertFalse(discovery.venue_matches('new central talkies', 'Central Talkies'))

    def test_uses_booking_format_code_from_matching_movie(self):
        catalogue = b'''<a href="/movies/kochi/a-movie-sequel/ET8">Sequel</a>
            <a href="https://example.org/movies/kochi/a-movie/ET9">External</a>
            <a href="/movies/kochi/a-movie/ET1">A Movie</a>'''
        details = b'''<script>window.data={"formats":[
            {"language":"English","refEventCode":"ET2","eventCode":"*"}]}</script>'''
        with patch.object(discovery, 'browser', side_effect=[catalogue, details]):
            url = discovery.resolve('A: Movie', 'Kochi', date(2026, 12, 17), 'bookmyshow')
        self.assertIn('/a-movie/buytickets/ET2/20261217?', url)
        self.assertIn('refEventCode=ET2', url)

    def test_district_ambiguous_title(self):
        body = b'''<a href="/movies/a-movie-movie-tickets-in-kochi-MV1">One</a>
            <a href="/movies/a-movie-movie-tickets-in-kochi-MV2">Two</a>'''
        with self.assertRaisesRegex(ValueError, '2 distinct listings'):
            discovery.movie_link(body, 'https://www.district.in', 'kochi', 'A Movie', 'district')
