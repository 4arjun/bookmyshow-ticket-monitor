#!/usr/bin/env python3
"""Watch BookMyShow with this checkout's Scrapling browser fetcher."""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
from datetime import datetime, date
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

from scrapling import Selector
from scrapling.fetchers import StealthyFetcher

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / '.ticket-watch'
URL = ('https://in.bookmyshow.com/movies/kochi/bachelor-party-deux/buytickets/'
       'ET00521598/20261119?etCodes=ET00521598&language=malayalam&refEventCode=ET00521598')
THEATRE = 'PVR: Forum Mall, Kochi'
SHOW_DATE = date(2026, 11, 19)
TZ = ZoneInfo('Asia/Kolkata')
TIME = re.compile(r'\b\d{1,2}:\d{2}\s*[AP]M\b', re.I)
STOP = threading.Event()


def inspect_page(page: Selector, status: int = 200) -> list[str]:
    """Return bookable showtime labels, or raise if the page cannot be trusted."""
    title = page.css('title::text').get() or ''
    if status != 200 or 'Bachelor Party' not in title:
        raise ValueError(f'Page unavailable or unexpected: HTTP {status}, {title!r}')
    main = page.css('#main-content')
    if not main or not main[0].css('[id="20261119"]'):
        raise ValueError('Expected movie/date controls missing')
    grids = main[0].css('[role="grid"], table')
    if not grids or not grids[0].css('a[href*="/cinemas/"]'):
        raise ValueError('Theatre listings missing; page may be incomplete or changed')
    grid = grids[0]
    # PVMF is the venue code from the live page's Forum Mall cinema link.
    # Never search the footer or the whole page for the theatre name.
    anchors = grid.css('a[href*="/cinemas/"]')
    # Showtime links can put the booking date after the venue code.
    targets = [
        anchor for anchor in anchors
        if 'PVMF' in urlsplit(anchor.attrib['href']).path.split('/')
    ]
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
    (STATE / 'alert.txt').write_text(message + '\n' + URL + '\n')
    print('\a' + message + '\n' + URL, flush=True)
    if sys.platform == 'darwin':
        script = ('on run argv\n'
                  'display notification (item 1 of argv) with title "BookMyShow ticket watch"\n'
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


def repeat_alert(message, args):
    """Latch the detection and alert until explicitly stopped, without more scraping."""
    while not STOP.is_set():
        notify(message, volume=args.alert_volume, bursts=3)
        if args.command == 'once' or STOP.wait(args.alert_interval):
            break


def fetch_shows(visible=False):
    response = StealthyFetcher.fetch(
        URL, headless=not visible, real_chrome=True, solve_cloudflare=False,
        timeout=45000, wait=3000, retries=1,
    )
    if '/ET00521598/20261119' not in response.url:
        raise ValueError(f'Unexpected redirect: {response.url}')
    return inspect_page(response, response.status)


def run(args):
    with (STATE / 'monitor.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('A ticket monitor is already running.')
        (STATE / 'monitor.pid').write_text(str(os.getpid()))
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: STOP.set())
        failures = 0
        logging.info('Watching %s on %s every %s seconds', THEATRE, SHOW_DATE, args.interval)
        try:
            while not STOP.is_set():
                if datetime.now(TZ).date() > SHOW_DATE:
                    save_status(state='expired', message='Show date has passed')
                    logging.info('Show date passed; stopping.')
                    return 0
                try:
                    shows = fetch_shows(args.visible)
                    failures = 0
                    save_status(state='open' if shows else 'waiting', showtimes=shows, url=URL)
                    if shows:
                        message = f"Tickets detected at {datetime.now(TZ):%H:%M}: {THEATRE}, Nov 19. " + ', '.join(shows)
                        logging.warning(message)
                        repeat_alert(message, args)
                        if STOP.is_set():
                            save_status(state='stopped', showtimes=shows, message='Ticket alert stopped')
                        return 0
                    logging.info('No Forum Mall showtimes yet.')
                except Exception as exc:
                    failures += 1
                    save_status(state='unknown', error=str(exc), consecutive_failures=failures)
                    logging.error('Check failed (%s): %s', failures, exc)
                    if failures == 3:
                        notify('Ticket monitor cannot read BookMyShow after 3 attempts. Check monitor.log; it will keep retrying.')
                    if args.command == 'once':
                        return 1
                if args.command == 'once':
                    return 0
                # Back off after errors instead of hammering a blocked site.
                STOP.wait(min(args.interval * 2 ** min(failures, 3), 1800))
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'run', 'once', 'status', 'stop', 'test-alert'])
    parser.add_argument('--interval', type=int, default=1, help='Seconds between checks (minimum 30)')
    parser.add_argument('--visible', action='store_true', help='Show the separate scraping browser')
    parser.add_argument('--alert-interval', type=int, default=15, help='Seconds between repeated alerts')
    parser.add_argument('--alert-volume', type=float, default=2.0, help='Sound gain, 0.1–2.0 (default 2.0)')
    args = parser.parse_args()
    if args.interval < 0:
        parser.error('--interval must be at least 0 seconds')
    if args.alert_interval < 1 or not 0.1 <= args.alert_volume <= 2.0:
        parser.error('--alert-interval must be positive and --alert-volume between 0.1 and 2.0')
    STATE.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if args.command == 'status':
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
