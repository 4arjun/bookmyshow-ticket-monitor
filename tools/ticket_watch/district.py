"""Read District's public ScreeningEvent offers."""
import json
from datetime import datetime
from urllib.request import urlopen

from lxml import html
from discovery import AwaitingListing, venue_matches

URL = 'https://www.district.in/movies/avengers-doomsday-movie-tickets-in-kochi-MV176735'


def inspect(body, show_date, timezone, url=URL, theatre=None):
    page = html.fromstring(body)
    events = []
    confirmed_movie = False
    for raw in page.xpath('//script[@type="application/ld+json"]/text()'):
        item = json.loads(raw)
        if isinstance(item, dict) and item.get('@type') == 'Movie' and item.get('url') == url.split('?')[0]:
            confirmed_movie = True
        if isinstance(item, dict) and item.get('@type') == 'ScreeningEvent' and item.get('workPresented', {}).get('url') == url.split('?')[0]:
            events.append(item)
    if not events:
        if confirmed_movie:
            raise AwaitingListing('District lists the movie but has no published screenings yet')
        raise ValueError('District movie screening data unavailable')
    venues = {e['location']['url'] for e in events if theatre and venue_matches(theatre, e['location']['name'])}
    if len(venues) > 1:
        raise ValueError(f'Ambiguous District venue: {theatre}. Include the mall or neighbourhood.')
    shows = []
    for event in events:
        start = datetime.fromisoformat(event['startDate']).astimezone(timezone)
        venue = event['location']['url'].rstrip('/').split('/')[-1]
        offer = event.get('offers', {})
        matches = event['location']['url'] in venues if theatre else venue == '1025902'
        if (matches and start.date() == show_date
                and offer.get('availability', '').split('/')[-1] in ('InStock', 'LimitedAvailability')
                and offer.get('url', '').startswith('https://www.district.in/movies/seat-layout/')):
            shows.append(f"{start:%I:%M %p} {event.get('videoFormat', '')}".strip())
    return list(dict.fromkeys(shows))


def fetch(show_date, timezone, url=URL, theatre=None):
    with urlopen(url, timeout=20) as response:
        return inspect(response.read(), show_date, timezone, url, theatre)
