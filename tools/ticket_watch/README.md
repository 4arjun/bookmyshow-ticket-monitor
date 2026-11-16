# Forum Mall ticket monitor

Uses this repository's **Scrapling `StealthyFetcher`** and CSS/XPath selectors.
It watches Bachelor Party D'eux (Malayalam), PVR: Forum Mall, Kochi, on
November 19, 2026. Google Chrome must be installed. It uses a separate temporary
browser profile, not your personal Chrome session.

From the repository root, install once:

```sh
python3.11 -m venv .venv
.venv/bin/pip install -e '.[fetchers]'
```

Start a detached monitor (current check interval default: 1 second after each fetch):

```sh
.venv/bin/python tools/ticket_watch/watch.py start
.venv/bin/python tools/ticket_watch/watch.py status
.venv/bin/python tools/ticket_watch/watch.py stop
```

Other commands:

```sh
.venv/bin/python tools/ticket_watch/watch.py once
.venv/bin/python tools/ticket_watch/watch.py run --interval 60
.venv/bin/python tools/ticket_watch/watch.py test-alert
tail -f .ticket-watch/monitor.log
```

`run` stays in the terminal; Ctrl+C stops it. `start` survives closing the
terminal, but not shutdown/reboot. The Mac must stay awake and connected to the
internet. If desired, run `caffeinate -i .venv/bin/python tools/ticket_watch/watch.py run`
to prevent idle sleep while monitoring (closing the lid can still suspend it).

On availability, the script posts a macOS notification, plays three Sosumi sounds
at 2× playback gain, and writes `.ticket-watch/alert.txt`. It repeats the alert
after a 15-second pause until you run `stop` (or Ctrl+C in foreground mode).
It stops scraping after detection; repeated alerts refer to the original
detection time and do not guarantee that seats remain available. `once` and
`test-alert` play only one three-sound burst.

Customize with `start --alert-interval 10 --alert-volume 2.0` (restart a running
monitor to apply changes). Playback gain is adjustable from 0.1 to 2.0. It does
not change system volume: keep the Mac unmuted and raise its output volume if
needed. Notifications are configured in `notify()` and `repeat_alert()` in
`watch.py`. Allow Script Editor/osascript notifications
in macOS if prompted; Focus mode can suppress banners. Use `test-alert` to check
your notification settings. On other platforms the alert is terminal output and
the alert file. It does not send a message to Codex, email, or your phone.

The detector only inspects the actual showtime grid and venue code `PVMF`, not
the footer link. It excludes disabled/sold-out buttons. An enabled showtime is
the booking-open signal; it does not select seats or guarantee remaining seats.
Failed/blocked loads are recorded as **unknown**, with bounded backoff and a
one-time error alert after three consecutive failures. CAPTCHA solving is off.
Searching ends after the show date in India; an already-triggered alert repeats
until stopped. Site markup changes may require
updating the selectors. The latest result is in `.ticket-watch/status.json`.

Parser regression checks:

```sh
.venv/bin/python -m unittest discover -s tools/ticket_watch -p 'test_*.py'
```
