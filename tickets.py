"""Start both ticket watchers or inspect their latest results."""
import argparse
from datetime import date
from pathlib import Path
import subprocess
import sys

SCRIPT = Path(__file__).resolve().parent / 'tools/ticket_watch/watch.py'


def main():
    if sys.argv[1:2] == ['recipients']:
        from tools.ticket_watch import telegram_personal
        parser = argparse.ArgumentParser(prog='tickets.py recipients', description='List recipients or replace the list with international phone numbers.')
        parser.add_argument('numbers', metavar='PHONE', nargs='*')
        args = parser.parse_args(sys.argv[2:])
        try:
            telegram_personal.manage(SCRIPT.parents[2] / '.ticket-watch/telegram-personal.json', args.numbers)
        except telegram_personal.TelegramError as exc:
            parser.exit(1, f'{exc}\n')
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'status', 'stop', 'recipients'])
    parser.add_argument('--movie')
    parser.add_argument('--date', type=date.fromisoformat)
    parser.add_argument('--venue')
    parser.add_argument('--city', default='Kochi')
    parser.add_argument('--codex-search', action='store_true', help='Use Codex web search for unresolved movie titles')
    parser.add_argument('--interval', type=int, default=1)
    args = parser.parse_args()
    base = [sys.executable, str(SCRIPT)]
    options = []
    if args.command == 'start':
        if not all((args.movie, args.date, args.venue)):
            parser.error('start requires --movie, --date YYYY-MM-DD, and --venue')
        if args.date < date.today() or args.interval < 1:
            parser.error('Choose today or a future date and an interval of at least 1 second')
        options = ['--movie', args.movie, '--date', str(args.date), '--venue', args.venue,
                   '--city', args.city, '--interval', str(args.interval)]
        if args.codex_search:
            options.append('--codex-search')
    results = []
    for provider, flags in [('BookMyShow', []), ('District', ['--district'])]:
        print(provider, flush=True)
        results.append(subprocess.run(base + [args.command] + options + flags).returncode)
    return max(results)


if __name__ == '__main__':
    sys.exit(main())
