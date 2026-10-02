"""Download real games for the rink replay: play-by-play, box score and shift charts.

    python tools/fetch_games.py                       # Calgary's last 10 regular-season games, 2025-26
    python tools/fetch_games.py --team EDM --last 5
    python tools/fetch_games.py --game 2025021310     # one game by ID

The app also downloads any game on demand when you pick it, so this is only needed to
pre-load games for offline use. Each game is saved to data/games/<game_id>.json.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nhl_api  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", default="CGY")
    ap.add_argument("--season", default="20252026")
    ap.add_argument("--last", type=int, default=10, help="most recent N completed regular-season games")
    ap.add_argument("--game", type=int, help="a single game ID instead")
    args = ap.parse_args()
    ids = [args.game] if args.game else [g["id"] for g in nhl_api.schedule(args.team, args.season)[: args.last]]
    for gid in ids:
        print("saved", nhl_api.fetch_game(gid))


if __name__ == "__main__":
    main()
