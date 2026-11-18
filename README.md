# Movie ticket monitor

Watch BookMyShow and District for new start times at a specific cinema. The
monitor looks up movies in each site's public city catalogue. Discovery and
polling run locally without an AI subscription or paid search API.

From the project root on a Mac with Google Chrome and Python 3.11 installed:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python tickets.py start --movie "Avengers: Doomsday" --date 2026-12-17 --venue "PVR Forum Mall" --city Kochi
.venv/bin/python tickets.py status
.venv/bin/python tickets.py stop
```

Punctuation, spaces and capitalization are normalized. A unique title with a
single letter typo can also match. Include the mall or neighbourhood in the
venue name. Add `--codex-search` to let the signed-in Codex CLI look up harder
movie spellings when local matching fails. Each provider can request one search
per hour while unresolved. The suggested title must match a live city listing.
The monitor keeps checking the catalogue every minute while waiting. The city defaults
to Kochi. Each site runs a separate background monitor, so a missing listing
on one site doesn't stop the other. Discovery retries after a minute if a
movie isn't listed. After resolution each monitor waits one second between
successful checks. Set `--interval 60` to wait a minute. Stop both monitors
before changing the target.

BookMyShow uses the first listed language and its all-formats booking option
when available. District checks dated screening events with bookable offers
for the matching cinema. Ambiguous matches and unreadable pages are reported
as errors. Listings may differ across the two sites.

The first check alerts on available times not previously seen for the target.
Subsequent checks alert on new start times. Both sites share the same local
history. The Mac alert repeats while checking continues. Keep the Mac awake.
Private Telegram configuration, sessions, logs, and booking history live in
the Git-ignored `.ticket-watch/` directory.

The existing notification commands are available via
`.venv/bin/python tools/ticket_watch/watch.py --help`. Use `telegram-setup`
for bot alerts or `telegram-personal-setup` for a personal Telegram account.
Run tests with `.venv/bin/python -m unittest discover -s tools/ticket_watch`.

### Personal Telegram recipients

Run `.venv/bin/python tickets.py recipients` to list recipients. To replace the
list, pass all intended phone numbers together, including each country code:

```sh
.venv/bin/python tickets.py recipients +12025550101 +12025550102
```

This uses the session from `telegram-personal-setup`. The list is saved only
after Telegram resolves every number. If a lookup fails, check that the account
is reachable by phone from your Telegram contacts. Updated monitors read the
list for each new alert. Restart monitors launched before this feature once to
enable delivery to multiple recipients.
