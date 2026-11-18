#!/usr/bin/env python3
"""Watch BookMyShow with this checkout's Scrapling browser fetcher."""
from __future__ import annotations

import argparse
from functools import partial
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import signal
import sqlite3
from queue import Queue, Empty
from contextlib import closing
import subprocess
import sys
import threading
from datetime import datetime, date
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

from scrapling import Selector
from scrapling.fetchers import StealthyFetcher
import discovery
import district
import telegram_personal
from telegram_alert import TelegramDelivery, TelegramError, setup as setup_telegram, send as send_telegram

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / '.ticket-watch'
STATE = CONFIG
URL = ('https://in.bookmyshow.com/movies/kochi/avengers-doomsday/buytickets/'
       'ET00526287/20261217?etCodes=*&language=english&refEventCode=ET00526287')
THEATRE = 'PVR: Forum Mall, Kochi'
CUSTOM = False
CITY = 'kochi'
MOVIE = 'Avengers: Doomsday'
SHOW_DATE = date(2026, 12, 17)
TZ = ZoneInfo('Asia/Kolkata')
TIME = re.compile(r'\b\d{1,2}:\d{2}\s*[AP]M\b', re.I)
STOP = threading.Event()


class DateNotListed(ValueError):
    """BookMyShow has not exposed the requested date yet."""


def matches_movie_title(title):
    listed = re.split(r' Movie Showtimes', title, flags=re.I)[0]
    expected = MOVIE
    if CUSTOM and URL:
        path = urlsplit(URL).path.partition('/buytickets/')[0]
        expected = path.rsplit('/', 1)[-1]
    return discovery.compact(listed) == discovery.compact(expected)


def inspect_page(page: Selector, status: int = 200) -> list[str]:
    """Return bookable showtime labels, or raise if the page cannot be trusted."""
    title = page.css('title::text').get() or ''
    if status != 200 or not matches_movie_title(title):
        raise ValueError(f'Page unavailable or unexpected: HTTP {status}, {title!r}')
    main = page.css('#main-content')
    if not main or not main[0].css(f'[id="{SHOW_DATE:%Y%m%d}"]'):
        raise ValueError('Expected movie/date controls missing')
    if 'There are no shows available for this date' in main[0].get_all_text():
        return []
    grids = main[0].css('[role="grid"], table')
    if not grids or not grids[0].css('a[href*="/cinemas/"]'):
        raise ValueError('Theatre listings missing; page may be incomplete or changed')
    grid = grids[0]
    # PVMF is the venue code from the live page's Forum Mall cinema link.
    # Never search the footer or the whole page for the theatre name.
    anchors = grid.css('a[href*="/cinemas/"]')
    # Showtime links can put the booking date after the venue code.
    targets = []
    venue_codes = set()
    for anchor in anchors:
        path = urlsplit(anchor.attrib['href']).path
        match = re.search(r'/cinemas/[^/]+/([^/]+)/(?:buytickets/)?([^/]+)', path)
        if match and (discovery.venue_matches(THEATRE, match[1]) or
                      (THEATRE == 'PVR: Forum Mall, Kochi' and match[2] == 'PVMF')):
            venue_codes.add(match[2])
            targets.append(anchor)
    if len(venue_codes) > 1:
        raise ValueError(f'Ambiguous BookMyShow venue: {THEATRE}. Include the mall or neighbourhood.')
    shows = []
    for anchor in targets:
        node = anchor.parent
        while node is not None:
            # Stop before reaching a shared container with other theatres.
            if len(node.css('a[href*="/cinemas/"]')) != 1:
                break
            buttons = node.css('[role="button"], button')
            timed = [b for b in buttons if TIME.search(b.attrib.get('aria-label', '') or b.get_all_text())]
            if timed:
                for button in timed:
                    label = button.attrib.get('aria-label', '') or button.get_all_text(separator=' ')
                    disabled = button.xpath('ancestor-or-self::*[@disabled or @aria-disabled="true" or @hidden]')
                    if (not disabled and button.attrib.get('tabindex') != '-1'
                            and not re.search(r'sold\s*out|unavailable|house\s*full|closed', label, re.I)):
                        shows.append(' '.join(label.split()))
                break
            node = node.parent
    return list(dict.fromkeys(shows))


def save_status(**values):
    values['checked_at'] = datetime.now(TZ).isoformat()
    temp = STATE / 'status.tmp'
    temp.write_text(json.dumps(values, indent=2) + '\n')
    temp.replace(STATE / 'status.json')


def notify(message: str, volume: float = 1.0, bursts: int = 1):
    """Keep a durable alert even if desktop notification delivery is denied."""
    (STATE / 'alert.txt').write_text(message + '\n' + (URL or '') + '\n')
    print('\a' + message + '\n' + (URL or ''), flush=True)
    if sys.platform == 'darwin':
        script = ('on run argv\n'
                  'display notification (item 1 of argv) with title "Movie ticket watch"\n'
                  'end run')
        try:
            subprocess.run(['osascript', '-e', script, message], timeout=50, check=True,
                           capture_output=True)
        except (OSError, subprocess.SubprocessError) as exc:
            logging.error('Desktop alert failed; see alert.txt: %s', exc)
        # Audio is independent of banner permissions. Gain does not change the
        # Mac's master volume; mute and the selected output device still apply.
        for _ in range(bursts):
            if STOP.is_set():
                break
            try:
                subprocess.run(['afplay', '-v', '5', '/System/Library/Sounds/Sosumi.aiff'],
                               timeout=50, check=True, capture_output=True)
            except (OSError, subprocess.SubprocessError) as exc:
                logging.error('Alert sound failed: %s', exc)
                break


def claim_new_shows(shows):
    """Atomically share known start times across both providers and restarts."""
    new = []
    with closing(sqlite3.connect(CONFIG / 'seen-shows.db', timeout=10)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS seen (event TEXT, clock TEXT, PRIMARY KEY(event, clock))')
        venue = discovery.words(THEATRE)
        if CITY and venue[-1:] == [CITY]:
            venue.pop()
        target = (discovery.slug(MOVIE), CITY, '-'.join(venue), str(SHOW_DATE))
        if target == ('avengers-doomsday', 'kochi', 'pvr-forum-mall', '2026-12-17'):
            event = 'Avengers: Doomsday|PVR: Forum Mall, Kochi|2026-12-17|English'
        else:
            event = '|'.join(target)
        for label in shows:
            match = TIME.search(label)
            if match:
                clock = datetime.strptime(match[0].replace(' ', '').upper(), '%I:%M%p').strftime('%H:%M')
                if db.execute('INSERT OR IGNORE INTO seen VALUES (?, ?)', (event, clock)).rowcount:
                    new.append(label)
    return new


def alert_worker(messages, args):
    message = None
    pending = []
    while not STOP.is_set():
        try:
            message = messages.get(timeout=args.alert_interval if message else 1)
            pending.extend([
                (TelegramDelivery(CONFIG / 'telegram.json'), message),
                (TelegramDelivery(CONFIG / 'telegram-personal.json', partial(telegram_personal.send, delivered=set())), message),
            ])
        except Empty:
            if message is None:
                continue
        if STOP.is_set():
            break
        retry = []
        for delivery, text in pending:
            try:
                delivery.attempt(text + '\n' + URL)
                if not delivery.sent and delivery.path.exists():
                    retry.append((delivery, text))
            except TelegramError as exc:
                logging.error('%s', exc)
                retry.append((delivery, text))
        pending = retry
        notify(message, volume=args.alert_volume, bursts=3)
        if args.command == 'once':
            break


def fetch_shows(visible=False):
    response = StealthyFetcher.fetch(
        URL, headless=not visible, real_chrome=True, solve_cloudflare=False,
        timeout=45000, wait=3000, retries=1,
    )
    expected_path = urlsplit(URL).path
    actual_path = urlsplit(response.url).path
    actual_date = actual_path.rsplit('/', 1)[-1]
    title = response.css('title::text').get() or ''
    main = response.css('#main-content')
    if (response.status == 200 and matches_movie_title(title)
            and actual_path.rsplit('/', 1)[0] == expected_path.rsplit('/', 1)[0]
            and re.fullmatch(r'\d{8}', actual_date)
            and main and main[0].css(f'[id="{actual_date}"]')
            and not main[0].css(f'[id="{SHOW_DATE:%Y%m%d}"]')):
        raise DateNotListed(f'{SHOW_DATE} is not listed yet on BookMyShow')
    if actual_path != expected_path:
        raise ValueError(f'Unexpected redirect: {response.url}')
    return inspect_page(response, response.status)


def run(args):
    global URL
    with (STATE / 'monitor.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('A ticket monitor is already running.')
        save_status(state='starting', movie=MOVIE, venue=THEATRE, date=str(SHOW_DATE))
        (STATE / 'monitor.pid').write_text(str(os.getpid()))
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: STOP.set())
        messages = Queue()
        worker = threading.Thread(target=alert_worker, args=(messages, args), daemon=True)
        worker.start()
        failures = 0
        lookup = discovery.TitleLookup() if args.codex_search else None
        logging.info('Watching %s on %s every %s seconds', THEATRE, SHOW_DATE, args.interval)
        try:
            while not STOP.is_set():
                if datetime.now(TZ).date() > SHOW_DATE:
                    save_status(state='expired', message='Show date has passed')
                    logging.info('Show date passed; stopping.')
                    return 0
                try:
                    if not URL:
                        source = 'district' if args.district else 'bookmyshow'
                        URL = discovery.resolve(MOVIE, CITY, SHOW_DATE, source, lookup=lookup)
                        logging.info('Resolved %s listing: %s', source, URL)
                    shows = district.fetch(SHOW_DATE, TZ, URL, THEATRE if CUSTOM else None) if args.district else fetch_shows(args.visible)
                    failures = 0
                    new = claim_new_shows(shows)
                    save_status(state='watching_for_new', showtimes=shows, new_showtimes=new, url=URL)
                    if new:
                        source = "District" if args.district else "BookMyShow"
                        message = f"{source} — {MOVIE} tickets detected at {datetime.now(TZ):%H:%M}: {THEATRE}, {SHOW_DATE:%b %d}. " + ', '.join(new)
                        logging.warning(message)
                        messages.put(message)
                        if args.command == 'once':
                            worker.join()
                    else:
                        logging.info('No new %s showtimes.', THEATRE)
                except discovery.AwaitingListing as exc:
                    failures = 0
                    save_status(state='awaiting_listing', message=str(exc), movie=MOVIE,
                                venue=THEATRE, date=str(SHOW_DATE), url=URL)
                    logging.info('%s; will check again', exc)
                except DateNotListed as exc:
                    failures = 0
                    save_status(state='awaiting_date', message=str(exc), showtimes=[], url=URL)
                    logging.info('%s', exc)
                except Exception as exc:
                    failures += 1
                    save_status(state='unknown', error=str(exc), consecutive_failures=failures)
                    logging.error('Check failed (%s): %s', failures, exc)
                    if failures == 3 and URL:
                        notify('Ticket monitor cannot read the booking page after 3 attempts. Check monitor.log; it will keep retrying.')
                    if args.command == 'once':
                        return 1
                if args.command == 'once':
                    return 0
                # Back off after errors instead of hammering a blocked site.
                STOP.wait(60 if not URL else min(args.interval * 2 ** min(failures, 3), 1800))
            save_status(state='stopped')
            return 0
        finally:
            (STATE / 'monitor.pid').unlink(missing_ok=True)


def active_pid():
    with (STATE / 'monitor.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return None
        except BlockingIOError:
            try:
                return int((STATE / 'monitor.pid').read_text())
            except (FileNotFoundError, ValueError):
                raise SystemExit('Monitor is starting; retry in a moment.')


def main():
    global STATE, URL, MOVIE, THEATRE, SHOW_DATE, CITY, CUSTOM
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'run', 'once', 'status', 'stop', 'test-alert',
                                            'telegram-setup', 'telegram-test',
                                            'telegram-personal-setup', 'telegram-personal-test'])
    parser.add_argument('--interval', type=int, default=1, help='Seconds to wait after each successful check (default: 1)')
    parser.add_argument('--visible', action='store_true', help='Show the separate scraping browser')
    parser.add_argument('--alert-interval', type=int, default=15, help='Seconds between repeated alerts')
    parser.add_argument('--alert-volume', type=float, default=2.0, help='Sound gain, 0.1–2.0 (default 2.0)')
    parser.add_argument('--district', action='store_true', help='Use the independent District monitor')
    parser.add_argument('--movie')
    parser.add_argument('--venue')
    parser.add_argument('--date', type=date.fromisoformat)
    parser.add_argument('--city', default='Kochi')
    parser.add_argument('--codex-search', action='store_true')
    args = parser.parse_args()
    if args.district:
        STATE = CONFIG / 'district'
        URL = district.URL
    if any((args.movie, args.venue, args.date)):
        if not all((args.movie, args.venue, args.date, args.city)):
            parser.error('Supply --movie, --venue, and --date together.')
        CUSTOM = True
        MOVIE, THEATRE, SHOW_DATE, CITY = args.movie, args.venue, args.date, discovery.slug(args.city)
        URL = None
    if args.interval < 0:
        parser.error('--interval must be at least 0 seconds')
    if args.alert_interval < 1 or not 0.1 <= args.alert_volume <= 2.0:
        parser.error('--alert-interval must be positive and --alert-volume between 0.1 and 2.0')
    STATE.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if args.command in ('telegram-personal-setup', 'telegram-personal-test'):
        path = CONFIG / 'telegram-personal.json'
        try:
            if args.command == 'telegram-personal-setup':
                telegram_personal.setup(path)
            elif telegram_personal.send(path, 'Test: personal Telegram ticket alerts are connected.'):
                print('Telegram accepted the test message.')
            else:
                raise TelegramError('Run telegram-personal-setup first.')
        except TelegramError as exc:
            logging.error('%s', exc)
            return 1
    elif args.command in ('telegram-setup', 'telegram-test'):
        try:
            if args.command == 'telegram-setup':
                setup_telegram(CONFIG / 'telegram.json')
            elif send_telegram(CONFIG / 'telegram.json', 'Test: your ticket monitor can send Telegram alerts.\n' + URL):
                print('Telegram accepted the test message. Check your phone.')
            else:
                raise TelegramError('Run telegram-setup first.')
        except TelegramError as exc:
            logging.error('%s', exc)
            return 1
    elif args.command == 'status':
        print(f'Running PID: {active_pid() or "not running"}')
        path = STATE / 'status.json'
        print(path.read_text() if path.exists() else 'No check recorded yet.')
    elif args.command == 'stop':
        pid = active_pid()
        if pid:
            os.kill(pid, signal.SIGTERM)
            print('Stop requested; any active fetch will finish first.')
        else:
            print('Monitor is not running.')
    elif args.command == 'test-alert':
        notify('Test alert: the Forum Mall ticket monitor is configured.', args.alert_volume, bursts=3)
    elif args.command == 'start':
        if active_pid():
            raise SystemExit('A ticket monitor is already running.')
        command = [sys.executable, str(Path(__file__).resolve()), 'run', '--interval', str(args.interval),
                   '--alert-interval', str(args.alert_interval), '--alert-volume', str(args.alert_volume)]
        if CUSTOM:
            command.extend(['--movie', MOVIE, '--venue', THEATRE, '--date', SHOW_DATE.isoformat(), '--city', CITY])
        if args.district:
            command.append('--district')
        if args.codex_search:
            command.append('--codex-search')
        if args.visible:
            command.append('--visible')
        with (STATE / 'monitor.log').open('a') as log:
            child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=log, start_new_session=True)
        print(f'Started process {child.pid}. Check status to confirm the first successful fetch. Logs: {STATE / "monitor.log"}')
    else:
        return run(args)
    return 0


if __name__ == '__main__':
    sys.exit(main())
