#!/usr/bin/env python3
"""
publish_ci.py — Cloud version of publish.py for GitHub Actions.

Fetches MLB stats directly from the API (no Excel file needed),
computes scores from the roster data stored in rosters.json,
and generates index.html.

Run locally:   python publish_ci.py --local
Run in CI:     python publish_ci.py  (auto-detected via CI env var)
"""

import json
import os
import unicodedata
import requests
from datetime import date, timedelta, datetime
from pathlib import Path

MY_TEAM      = "evilbobdole"
SEASON_START = date(2026, 3, 25)
WEEK1_END    = date(2026, 4, 5)

# ── HELPERS ───────────────────────────────────────────────────────────────────

def strip_accents(name: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(name))
                   if unicodedata.category(c) != "Mn")

def ordinal(n):
    if n is None: return "—"
    s = ["th","st","nd","rd"] + ["th"] * 16
    return f"{n}{s[n % 100 if 11 <= n % 100 <= 13 else n % 10]}"

def week_num(d: date) -> int:
    if d < SEASON_START: return 0
    if d <= WEEK1_END:   return 1
    delta = (d - (WEEK1_END + timedelta(days=1))).days
    return 2 + delta // 7

def ip_to_decimal(ip_raw) -> float:
    ip   = float(ip_raw)
    full = int(ip)
    outs = round((ip - full) * 10)
    return full + outs / 3.0

def batting_dk(s: dict) -> float:
    H       = s.get("hits", 0)
    doubles = s.get("doubles", 0)
    triples = s.get("triples", 0)
    HR      = s.get("homeRuns", 0)
    singles = H - doubles - triples - HR
    return round(
        singles * 3 + doubles * 5 + triples * 8 + HR * 10 +
        s.get("rbi", 0) * 2 + s.get("runs", 0) * 2 +
        s.get("baseOnBalls", 0) * 2 + s.get("hitByPitch", 0) * 2 +
        s.get("stolenBases", 0) * 5, 2)

def pitching_dk(s: dict) -> float:
    IP = ip_to_decimal(s.get("inningsPitched", 0))
    return round(
        IP * 2.25 + s.get("strikeOuts", 0) * 2 + s.get("wins", 0) * 4 -
        s.get("earnedRuns", 0) * 2 - s.get("hits", 0) * 0.6 -
        s.get("baseOnBalls", 0) * 0.6 - s.get("hitBatsmen", 0) * 0.6, 2)

# ── STATS FETCHING ─────────────────────────────────────────────────────────────

API = "https://statsapi.mlb.com/api/v1"

def fetch(url):
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.json()

def fetch_stats_for_date(d: date):
    """Returns (batting_dict, pitching_dict) for a single date.
    Keys: (date_str, player_name, team_abbr) -> dk_pts
    Stats are keyed by SCHEDULED date regardless of when game ends.
    """
    batting  = {}
    pitching = {}
    date_str = str(d)

    data = fetch(f"{API}/schedule?sportId=1&date={date_str}")
    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            # Skip games not yet final
            if game.get("status", {}).get("abstractGameState") != "Final":
                continue
            pk = game["gamePk"]
            try:
                box = fetch(f"{API}/game/{pk}/boxscore")
            except Exception:
                continue

            for side in ("home", "away"):
                team_abbr = box["teams"][side]["team"]["abbreviation"]
                team_abbr = team_abbr.replace("AZ", "ARI")

                for p in box["teams"][side]["players"].values():
                    # Batting
                    bs = p.get("stats", {}).get("batting")
                    if bs and bs.get("atBats", 0) + bs.get("baseOnBalls", 0) + bs.get("hitByPitch", 0) > 0:
                        name = strip_accents(p["person"]["fullName"])
                        dk   = batting_dk(bs)
                        key  = (date_str, name, team_abbr)
                        batting[key] = batting.get(key, 0.0) + dk

                    # Pitching
                    ps = p.get("stats", {}).get("pitching")
                    if ps and float(ps.get("inningsPitched", 0)) > 0:
                        name = strip_accents(p["person"]["fullName"])
                        IP   = ip_to_decimal(ps.get("inningsPitched", 0))
                        if IP == 0: continue
                        dk   = pitching_dk(ps)
                        key  = (date_str, name, team_abbr)
                        pitching[key] = pitching.get(key, 0.0) + dk

    return batting, pitching

def load_all_stats():
    """Load stats from season start through today."""
    today     = date.today()
    yesterday = today - timedelta(days=1)

    all_batting  = {}
    all_pitching = {}

    # Load cached stats if available
    cache_file = Path("stats_cache.json")
    cached_dates = set()
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text())
            for k, v in cache.get("batting", {}).items():
                all_batting[tuple(k.split("|"))] = v
            for k, v in cache.get("pitching", {}).items():
                all_pitching[tuple(k.split("|"))] = v
            cached_dates = set(k.split("|")[0] for k in cache.get("batting", {}))
            print(f"  Loaded cache: {len(cached_dates)} dates")
        except Exception as e:
            print(f"  Cache load failed: {e}")

    # Fetch any missing dates (yesterday and earlier not in cache)
    d = SEASON_START
    while d <= yesterday:
        if str(d) not in cached_dates:
            print(f"  Fetching {d}...", end=" ", flush=True)
            try:
                b, p = fetch_stats_for_date(d)
                all_batting.update(b)
                all_pitching.update(p)
                print(f"✓ ({len(b)} batting, {len(p)} pitching)")
            except Exception as e:
                print(f"✗ {e}")
        d += timedelta(days=1)

    # Always fetch today (games may still be finishing)
    print(f"  Fetching today ({today})...", end=" ", flush=True)
    try:
        b, p = fetch_stats_for_date(today)
        all_batting.update(b)
        all_pitching.update(p)
        print(f"✓ ({len(b)} batting, {len(p)} pitching)")
    except Exception as e:
        print(f"✗ {e}")

    # Save cache (everything except today — today may be incomplete)
    try:
        cache_batting  = {"|".join(k): v for k, v in all_batting.items()  if k[0] != str(today)}
        cache_pitching = {"|".join(k): v for k, v in all_pitching.items() if k[0] != str(today)}
        cache_file.write_text(json.dumps({"batting": cache_batting, "pitching": cache_pitching}))
        print(f"  Cache saved: {len(cache_batting)} batting, {len(cache_pitching)} pitching entries")
    except Exception as e:
        print(f"  Cache save failed: {e}")

    return all_batting, all_pitching

# ── SCORING ────────────────────────────────────────────────────────────────────

def player_score(name, mlb, pos, week, batting, pitching):
    name = strip_accents(str(name).strip())
    team = str(mlb).strip().upper().replace("AZ","ARI")
    total = 0.0
    for key, val in (batting if pos != "P" else pitching).items():
        if key[1] == name and key[2] == team and week_num(date.fromisoformat(key[0])) == week:
            total += val
    return round(total, 2)

def starters_score_for_date(roster, team_name, date_str, batting, pitching, week):
    """Today's score for the 9 week starters."""
    by_pos = {"P": [], "IF": [], "OF": []}
    for p in roster:
        if p["team_name"] != team_name or p["pos"] not in by_pos: continue
        week_pts = player_score(p["name"], p["mlb"], p["pos"], week, batting, pitching)
        by_pos[p["pos"]].append((p, week_pts))

    total = 0.0
    for pos, players in by_pos.items():
        starters = sorted(players, key=lambda x: x[1], reverse=True)[:3]
        for p, _ in starters:
            if not date_str: continue
            name = strip_accents(str(p["name"]).strip())
            team = str(p["mlb"]).strip().upper().replace("AZ","ARI")
            src  = pitching if p["pos"] == "P" else batting
            key  = (date_str, name, team)
            total += src.get(key, 0.0)
    return round(total, 2)

def top3_by_pos(players):
    by_pos = {"IF": [], "OF": [], "P": []}
    for p in players:
        if p["pos"] in by_pos:
            by_pos[p["pos"]].append(p)
    result = []
    for pos in ["P", "IF", "OF"]:
        sorted_pos = sorted(by_pos[pos], key=lambda x: x.get("week_total", 0), reverse=True)
        result.extend(sorted_pos[:3])
    return result

def team_week_score(roster, team_name, week, batting, pitching):
    by_pos = {"P": [], "IF": [], "OF": []}
    for p in roster:
        if p["team_name"] != team_name or p["pos"] not in by_pos: continue
        s = player_score(p["name"], p["mlb"], p["pos"], week, batting, pitching)
        by_pos[p["pos"]].append(s)
    total = 0.0
    for scores in by_pos.values():
        scores.sort(reverse=True)
        total += sum(scores[:3])
    return round(total, 2)

# ── ROSTERS ────────────────────────────────────────────────────────────────────

def load_rosters():
    """Load rosters from rosters.json (committed to repo)."""
    roster_file = Path("rosters.json")
    if not roster_file.exists():
        raise FileNotFoundError(
            "rosters.json not found. Run: python export_rosters.py "
            "to generate it from your Excel file."
        )
    return json.loads(roster_file.read_text())

# ── MAIN ───────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--weeks", type=int, default=1)
    args = parser.parse_args()

    print("Loading rosters...")
    all_rosters = load_rosters()  # {sheet_name: [player_dicts]}

    print("Fetching MLB stats...")
    batting, pitching = load_all_stats()
    print(f"  Total batting:  {len(batting)}")
    print(f"  Total pitching: {len(pitching)}")

    # Build daily dicts
    all_dates     = sorted(set(k[0] for k in batting) | set(k[0] for k in pitching))
    latest_date   = all_dates[-1] if len(all_dates) >= 1 else None
    yesterday_date= all_dates[-2] if len(all_dates) >= 2 else None
    batting_daily  = {k: v for k, v in batting.items()  if k[0] == latest_date}
    pitching_daily = {k: v for k, v in pitching.items() if k[0] == latest_date}
    batting_yest   = {k: v for k, v in batting.items()  if k[0] == yesterday_date}
    pitching_yest  = {k: v for k, v in pitching.items() if k[0] == yesterday_date}

    print(f"  Latest date:    {latest_date}")
    print(f"  Yesterday:      {yesterday_date}")
    print("Computing scores...")

    # Import build_html from publish.py
    import importlib.util, sys
    spec   = importlib.util.spec_from_file_location("publish", "publish.py")
    pub    = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pub)

    # Build drafts using rosters + live stats
    drafts = []
    for sheet_name, roster in all_rosters.items():
        num = int(sheet_name.replace("draftboard_","").replace("_",""))
        teams = list(dict.fromkeys(p["team_name"] for p in roster))

        team_data = {}
        for team in teams:
            weekly = [team_week_score(roster, team, w, batting, pitching)
                      for w in range(1, args.weeks + 1)]
            team_data[team] = {"total": round(sum(weekly), 2), "weeks": weekly}

        ranked   = sorted(teams, key=lambda t: team_data[t]["total"], reverse=True)
        my_rank  = ranked.index(MY_TEAM) + 1 if MY_TEAM in ranked else None
        my_pts   = team_data.get(MY_TEAM, {}).get("total", 0.0)
        second_team = ranked[1] if len(ranked) > 1 else None

        def build_players_ci(team_name):
            players = []
            for p in roster:
                if p["team_name"] != team_name: continue
                wk_total = player_score(p["name"], p["mlb"], p["pos"], args.weeks, batting, pitching)
                name_s   = strip_accents(str(p["name"]).strip())
                team_s   = str(p["mlb"]).strip().upper().replace("AZ","ARI")
                src_d    = pitching_daily if p["pos"]=="P" else batting_daily
                src_y    = pitching_yest  if p["pos"]=="P" else batting_yest
                daily    = round(src_d.get((latest_date,    name_s, team_s), 0.0), 2)
                yest     = round(src_y.get((yesterday_date, name_s, team_s), 0.0), 2)
                weeks    = [round(player_score(p["name"], p["mlb"], p["pos"], w, batting, pitching), 2)
                            for w in range(1, args.weeks + 1)]
                players.append({**p, "week_total": wk_total, "weeks": weeks,
                                "total": round(sum(weeks), 2), "daily": daily, "yesterday": yest})
            return top3_by_pos(players)

        my_players     = build_players_ci(MY_TEAM)     if MY_TEAM in teams else []
        second_players = build_players_ci(second_team) if second_team       else []

        my_daily      = starters_score_for_date(roster, MY_TEAM,     latest_date,    batting, pitching, args.weeks) if MY_TEAM in teams else 0.0
        second_today  = starters_score_for_date(roster, second_team, latest_date,    batting, pitching, args.weeks) if second_team      else 0.0
        my_yesterday  = starters_score_for_date(roster, MY_TEAM,     yesterday_date, batting, pitching, args.weeks) if MY_TEAM in teams else 0.0
        second_yest   = starters_score_for_date(roster, second_team, yesterday_date, batting, pitching, args.weeks) if second_team      else 0.0

        daily_scores  = {team: starters_score_for_date(roster, team, latest_date, batting, pitching, args.weeks)
                         for team in teams} if latest_date else {}
        daily_ranked  = sorted(teams, key=lambda t: daily_scores.get(t, 0.0), reverse=True)

        drafts.append({
            "num": num, "sheet": sheet_name, "roster": roster,
            "teams": teams, "data": team_data, "ranked": ranked,
            "my_rank": my_rank, "my_pts": my_pts,
            "my_players": my_players, "second_team": second_team,
            "second_players": second_players, "daily_scores": daily_scores,
            "my_daily": my_daily, "second_today": second_today,
            "my_daily_gap": round(my_daily - second_today, 2),
            "my_yesterday": my_yesterday, "second_yesterday": second_yest,
            "my_yesterday_gap": round(my_yesterday - second_yest, 2),
            "latest_date": latest_date, "yesterday_date": yesterday_date,
        })

    drafts.sort(key=lambda d: d["num"])
    generated_at = datetime.now().strftime("%B %d, %Y at %I:%M %p UTC")
    html = pub.build_html(drafts, num_weeks=args.weeks, generated_at=generated_at)

    out = Path("index.html")
    out.write_text(html, encoding="utf-8")
    print(f"✓ Written: {out} ({len(html):,} bytes)")

if __name__ == "__main__":
    main()
