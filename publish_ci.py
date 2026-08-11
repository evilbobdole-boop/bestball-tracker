#!/usr/bin/env python3
"""
publish_ci.py — Cloud version for GitHub Actions.
Fetches MLB stats from API, loads rosters.json, computes scores,
imports build_html from publish.py, generates index.html.
"""

import json
import sys
import unicodedata
import requests
from datetime import date, timedelta, datetime
from pathlib import Path

MY_TEAM       = "evilbobdole"
CHAMP_MY_TEAM      = "EBD"
CHAMP_START        = date(2026, 8, 10)
CHAMP_END          = date(2026, 8, 23)
STATS_FREEZE_DATE  = date(2026, 8, 9)  # Never re-fetch stats before this date
SEASON_START = date(2026, 3, 25)
WEEK1_END    = date(2026, 4, 5)   # Week 1: Mar 25 - Apr 5
API          = "https://statsapi.mlb.com/api/v1"

# ── HELPERS ───────────────────────────────────────────────────────────────────

def strip_accents(name: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(name))
                   if unicodedata.category(c) != "Mn")

def week_num(d):
    """Week calculation with All-Star break handling.
    ASB (Jul 13-15) = week 0 (no stats). Week 16 extended Jul 16-26."""
    if d < SEASON_START: return 0
    if d <= WEEK1_END:   return 1
    ASB_START   = __import__("datetime").date(2026, 7, 13)
    ASB_END     = __import__("datetime").date(2026, 7, 15)
    WEEK16_END  = __import__("datetime").date(2026, 7, 26)
    WEEK17_START= __import__("datetime").date(2026, 7, 27)
    if ASB_START <= d <= ASB_END: return 0
    if d > WEEK16_END:
        return 17 + (d - WEEK17_START).days // 7
    if d >= ASB_START:
        return 16
    delta = (d - (WEEK1_END + timedelta(days=1))).days
    return 2 + delta // 7


def ip_to_decimal(ip_raw) -> float:
    ip = float(ip_raw); full = int(ip); outs = round((ip - full) * 10)
    return full + outs / 3.0

def batting_dk(s: dict) -> float:
    H = s.get("hits", 0); doubles = s.get("doubles", 0)
    triples = s.get("triples", 0); HR = s.get("homeRuns", 0)
    singles = H - doubles - triples - HR
    return round(singles*3 + doubles*5 + triples*8 + HR*10 +
                 s.get("rbi",0)*2 + s.get("runs",0)*2 +
                 s.get("baseOnBalls",0)*2 + s.get("hitByPitch",0)*2 +
                 s.get("stolenBases",0)*5, 2)

def pitching_dk(s: dict) -> float:
    IP = ip_to_decimal(s.get("inningsPitched", 0))
    return round(IP*2.25 + s.get("strikeOuts",0)*2 + s.get("wins",0)*4 -
                 s.get("earnedRuns",0)*2 - s.get("hits",0)*0.6 -
                 s.get("baseOnBalls",0)*0.6 - s.get("hitBatsmen",0)*0.6, 2)

# ── STATS FETCHING ─────────────────────────────────────────────────────────────

def fetch(url):
    r = requests.get(url, timeout=20); r.raise_for_status(); return r.json()

def fetch_stats_for_date(d: date):
    """Fetch stats for a date. Each game is processed exactly once.
    Final games take priority — if a game is both Live and cached as Final,
    we only process it once to avoid double-counting."""
    batting = {}; pitching = {}; date_str = str(d)
    data = fetch(f"{API}/schedule?sportId=1&date={date_str}")
    processed_games = set()  # track gamePks already processed
    
    # First pass: process all Final games
    # Exclude postponed, suspended, cancelled games
    skip_states = {"Postponed", "Cancelled", "Suspended", "Delayed"}
    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            state    = game.get("status", {}).get("abstractGameState", "")
            detailed = game.get("status", {}).get("detailedState", "")
            if state != "Final": continue
            if any(s.lower() in detailed.lower() for s in skip_states): continue
            pk = game["gamePk"]
            processed_games.add(pk)
            try:
                box = fetch(f"{API}/game/{pk}/boxscore")
            except Exception:
                continue
            for side in ("home", "away"):
                team_abbr = box["teams"][side]["team"]["abbreviation"].replace("AZ","ARI")
                for p in box["teams"][side]["players"].values():
                    bs = p.get("stats", {}).get("batting")
                    if bs and bs.get("atBats",0)+bs.get("baseOnBalls",0)+bs.get("hitByPitch",0) > 0:
                        name = strip_accents(p["person"]["fullName"])
                        key  = (date_str, name, team_abbr)
                        batting[key] = batting.get(key, 0.0) + batting_dk(bs)
                    ps = p.get("stats", {}).get("pitching")
                    if ps and float(ps.get("inningsPitched", 0)) > 0:
                        name = strip_accents(p["person"]["fullName"])
                        if ip_to_decimal(ps.get("inningsPitched",0)) == 0: continue
                        key  = (date_str, name, team_abbr)
                        pitching[key] = pitching.get(key, 0.0) + pitching_dk(ps)

    # Second pass: process Live games that aren't already Final
    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            state    = game.get("status", {}).get("abstractGameState", "")
            detailed = game.get("status", {}).get("detailedState", "")
            if state != "Live": continue
            if any(s.lower() in detailed.lower() for s in skip_states): continue
            pk = game["gamePk"]
            if pk in processed_games: continue  # already have Final version
            processed_games.add(pk)
            try:
                box = fetch(f"{API}/game/{pk}/boxscore")
            except Exception:
                continue
            for side in ("home", "away"):
                team_abbr = box["teams"][side]["team"]["abbreviation"].replace("AZ","ARI")
                for p in box["teams"][side]["players"].values():
                    bs = p.get("stats", {}).get("batting")
                    if bs and bs.get("atBats",0)+bs.get("baseOnBalls",0)+bs.get("hitByPitch",0) > 0:
                        name = strip_accents(p["person"]["fullName"])
                        key  = (date_str, name, team_abbr)
                        batting[key] = batting.get(key, 0.0) + batting_dk(bs)
                    ps = p.get("stats", {}).get("pitching")
                    if ps and float(ps.get("inningsPitched", 0)) > 0:
                        name = strip_accents(p["person"]["fullName"])
                        if ip_to_decimal(ps.get("inningsPitched",0)) == 0: continue
                        key  = (date_str, name, team_abbr)
                        pitching[key] = pitching.get(key, 0.0) + pitching_dk(ps)

    return batting, pitching

def load_all_stats():
    today = date.today(); yesterday = today - timedelta(days=1)
    all_batting = {}; all_pitching = {}

    # On Mondays, only remove previous week dates from cache
    import time as _t
    from datetime import timezone, timedelta as _tde
    _est_off  = _tde(hours=-4) if (_t.daylight and _t.localtime().tm_isdst) else _tde(hours=-5)
    _now_est  = datetime.utcnow() + _est_off
    if _now_est.weekday() == 0:  # Monday
        prev_end   = _now_est.date() - timedelta(days=1)
        prev_start = prev_end - timedelta(days=6)
        # Will be handled below when we re-fetch yesterday automatically
        # Only clear dates after the freeze date
        for k in list(all_batting.keys()):
            d_str = k[0] if isinstance(k, tuple) else k.split("|")[0]
            try:
                d_obj = date.fromisoformat(d_str)
                if prev_start <= d_obj <= prev_end and d_obj > STATS_FREEZE_DATE:
                    del all_batting[k]
            except: pass
        for k in list(all_pitching.keys()):
            d_str = k[0] if isinstance(k, tuple) else k.split("|")[0]
            try:
                d_obj = date.fromisoformat(d_str)
                if prev_start <= d_obj <= prev_end and d_obj > STATS_FREEZE_DATE:
                    del all_pitching[k]
            except: pass
        print(f"  Monday — previous week ({prev_start} to {prev_end}) cleared for re-fetch.")

    # Load cache
    cache_file = Path("stats_cache.json"); cached_dates = set()
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text())
            for k, v in cache.get("batting", {}).items():
                all_batting[tuple(k.split("|"))] = v
            for k, v in cache.get("pitching", {}).items():
                all_pitching[tuple(k.split("|"))] = v
            cached_dates = set(k.split("|")[0] for k in cache.get("batting", {}))
            print(f"  Cache loaded: {len(cached_dates)} dates")
        except Exception as e:
            print(f"  Cache load failed: {e}")

    # Fetch missing historical dates (skip yesterday — always re-fetch it)
    # Never re-fetch dates on or before STATS_FREEZE_DATE
    d = SEASON_START
    while d <= yesterday - timedelta(days=1):
        if str(d) not in cached_dates and d > STATS_FREEZE_DATE:
            print(f"  Fetching {d}...", end=" ", flush=True)
            try:
                b, p = fetch_stats_for_date(d)
                all_batting.update(b); all_pitching.update(p)
                print(f"✓ ({len(b)} batting, {len(p)} pitching)")
            except Exception as e:
                print(f"✗ {e}")
        d += timedelta(days=1)

    # Always re-fetch yesterday — catches late-finishing games
    print(f"  Fetching yesterday ({yesterday})...", end=" ", flush=True)
    try:
        b, p = fetch_stats_for_date(yesterday)
        # Remove old yesterday entries before updating
        for k in list(all_batting.keys()):
            if k[0] == str(yesterday): del all_batting[k]
        for k in list(all_pitching.keys()):
            if k[0] == str(yesterday): del all_pitching[k]
        all_batting.update(b); all_pitching.update(p)
        print(f"✓ ({len(b)} batting, {len(p)} pitching)")
    except Exception as e:
        print(f"✗ {e}")

    # Always fetch today
    print(f"  Fetching today ({today})...", end=" ", flush=True)
    try:
        b, p = fetch_stats_for_date(today)
        all_batting.update(b); all_pitching.update(p)
        print(f"✓ ({len(b)} batting, {len(p)} pitching)")
    except Exception as e:
        print(f"✗ {e}")

    # Save cache (exclude today and yesterday — both always re-fetched)
    try:
        exclude = {str(today), str(yesterday)}
        cb = {"|".join(k): v for k, v in all_batting.items()  if k[0] not in exclude}
        cp = {"|".join(k): v for k, v in all_pitching.items() if k[0] not in exclude}
        cache_file.write_text(json.dumps({"batting": cb, "pitching": cp}))
        print(f"  Cache saved.")
    except Exception as e:
        print(f"  Cache save failed: {e}")

    return all_batting, all_pitching

# ── SCORING ────────────────────────────────────────────────────────────────────

def player_score_ci(name, mlb, pos, week, batting, pitching):
    """Match on name only — handles mid-season trades where team abbreviation may have changed."""
    name = strip_accents(str(name).strip())
    src  = pitching if pos == "P" else batting
    return round(sum(v for k, v in src.items()
                     if k[1]==name and week_num(date.fromisoformat(k[0]))==week), 2)

def starters_score_ci(roster, team_name, date_str, batting, pitching, week):
    by_pos = {"P": [], "IF": [], "OF": []}
    for p in roster:
        if p["team_name"] != team_name or p["pos"] not in by_pos: continue
        wk = player_score_ci(p["name"], p["mlb"], p["pos"], week, batting, pitching)
        by_pos[p["pos"]].append((p, wk))
    total = 0.0
    for pos, players in by_pos.items():
        starters = sorted(players, key=lambda x: x[1], reverse=True)[:3]
        for p, _ in starters:
            if not date_str: continue
            name = strip_accents(str(p["name"]).strip())
            team = str(p["mlb"]).strip().upper().replace("AZ","ARI")
            src  = pitching if p["pos"]=="P" else batting
            total += src.get((date_str, name, team), 0.0)
    return round(total, 2)

def top3_by_pos_ci(players):
    by_pos = {"IF": [], "OF": [], "P": []}
    for p in players:
        if p["pos"] in by_pos: by_pos[p["pos"]].append(p)
    result = []
    for pos in ["P", "IF", "OF"]:
        result.extend(sorted(by_pos[pos], key=lambda x: x.get("week_total",0), reverse=True)[:3])
    return result

def build_bench_ci(roster, team_name, batting, pitching, batting_daily, pitching_daily,
                   batting_yest, pitching_yest, latest_date, yesterday_date, weeks):
    """Bench = all players NOT in top 3 per pos by week score."""
    all_players = []
    for p in roster:
        if p["team_name"] != team_name: continue
        wk_total = player_score_ci(p["name"], p["mlb"], p["pos"], weeks, batting, pitching)
        name_s   = strip_accents(str(p["name"]).strip())
        team_s   = str(p["mlb"]).strip().upper().replace("AZ","ARI")
        daily    = round((pitching_daily if p["pos"]=="P" else batting_daily).get((latest_date,    name_s, team_s), 0.0), 2)
        yest     = round((pitching_yest  if p["pos"]=="P" else batting_yest ).get((yesterday_date, name_s, team_s), 0.0), 2) if yesterday_date else 0.0
        wk_list  = [round(player_score_ci(p["name"], p["mlb"], p["pos"], w, batting, pitching), 2)
                    for w in range(1, weeks+1)]
        all_players.append({**p, "week_total": wk_total, "weeks": wk_list,
                            "total": round(sum(wk_list), 2), "daily": daily, "yesterday": yest})

    # Find starters (top 3 per pos by CURRENT week score)
    starters = set()
    by_pos = {"P": [], "IF": [], "OF": []}
    for p in all_players:
        if p["pos"] in by_pos: by_pos[p["pos"]].append(p)
    for pos, plist in by_pos.items():
        for p in sorted(plist, key=lambda x: x["weeks"][-1] if x["weeks"] else 0, reverse=True)[:3]:
            starters.add(p["name"])

    pos_order = {"P": 0, "IF": 1, "OF": 2}
    bench = [p for p in all_players if p["name"] not in starters]
    bench.sort(key=lambda x: (pos_order.get(x["pos"], 9), -x["total"]))
    return bench

def team_week_score_ci(roster, team_name, week, batting, pitching):
    by_pos = {"P": [], "IF": [], "OF": []}
    for p in roster:
        if p["team_name"] != team_name or p["pos"] not in by_pos: continue
        by_pos[p["pos"]].append(player_score_ci(p["name"], p["mlb"], p["pos"], week, batting, pitching))
    total = 0.0
    for scores in by_pos.values():
        scores.sort(reverse=True); total += sum(scores[:3])
    return round(total, 2)

# ── ROSTERS ────────────────────────────────────────────────────────────────────

def fetch_active_lineups() -> set:
    """Fetch confirmed starting lineups for today from the MLB API."""
    import urllib.request, json, unicodedata as _u
    active = set()
    try:
        import pytz
        est   = pytz.timezone("America/New_York")
        today = __import__("datetime").datetime.now(est).strftime("%Y-%m-%d")
        url   = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today}&hydrate=lineups"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        def clean(name):
            return "".join(c for c in _u.normalize("NFD", name) if _u.category(c) != "Mn")
        for date_entry in data.get("dates", []):
            for game in date_entry.get("games", []):
                lineups = game.get("lineups", {})
                for side, key in (("home","homePlayers"),("away","awayPlayers")):
                    team = game.get("teams",{}).get(side,{}).get("team",{}).get("abbreviation","").replace("AZ","ARI")
                    for player in lineups.get(key, []):
                        name = clean(player.get("fullName",""))
                        if name and team: active.add((name, team))
                for side in ("home","away"):
                    pp   = game.get("teams",{}).get(side,{}).get("probablePitcher",{})
                    team = game.get("teams",{}).get(side,{}).get("team",{}).get("abbreviation","").replace("AZ","ARI")
                    if pp:
                        name = clean(pp.get("fullName",""))
                        if name and team: active.add((name, team))
        print(f"  Active lineups: {len(active)} players confirmed")
    except Exception as e:
        print(f"  Lineup fetch failed (non-critical): {e}")
    return active


def is_champ_date(d_str):
    try:
        d = date.fromisoformat(str(d_str)[:10])
        return CHAMP_START <= d <= CHAMP_END
    except: return False

def champ_player_score_ci(name, mlb, pos, batting, pitching):
    """Match on name only — handles mid-season trades where team may have changed."""
    name_s = strip_accents(str(name).strip())
    src    = pitching if pos == "P" else batting
    return round(sum(v for k,v in src.items()
                     if is_champ_date(k[0]) and k[1]==name_s), 2)

def load_rosters():
    roster_file = Path("rosters.json")
    if not roster_file.exists():
        raise FileNotFoundError("rosters.json not found.")
    return json.loads(roster_file.read_text())

# ── MAIN ───────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true")
    # Auto-detect current week from today's date
    import time as _t2
    from datetime import timedelta as _td2
    _est2 = timedelta(hours=-4) if (_t2.daylight and _t2.localtime().tm_isdst) else timedelta(hours=-5)
    _today_est = (datetime.utcnow() + _est2).date()
    _auto_weeks = week_num(_today_est)
    parser.add_argument("--weeks", type=int, default=_auto_weeks)
    args = parser.parse_args()

    print("Loading rosters...")
    all_rosters = load_rosters()

    print("Fetching MLB stats...")
    batting, pitching = load_all_stats()
    print(f"  Total batting:  {len(batting)}")
    print(f"  Total pitching: {len(pitching)}")

    all_dates      = sorted(set(k[0] for k in batting) | set(k[0] for k in pitching))
    latest_date    = all_dates[-1] if len(all_dates) >= 1 else None
    yesterday_date = all_dates[-2] if len(all_dates) >= 2 else None
    batting_daily  = {k: v for k, v in batting.items()  if k[0] == latest_date}
    pitching_daily = {k: v for k, v in pitching.items() if k[0] == latest_date}
    batting_yest   = {k: v for k, v in batting.items()  if k[0] == yesterday_date}
    pitching_yest  = {k: v for k, v in pitching.items() if k[0] == yesterday_date}
    print(f"  Latest date:    {latest_date}")
    print(f"  Yesterday:      {yesterday_date}")

    print("Computing scores...")
    drafts = []
    for sheet_name, roster in all_rosters.items():
        num   = int(sheet_name.replace("draftboard_","").replace("_",""))
        if num != 16: continue  # Only calculate draft 16 alongside championship
        teams = list(dict.fromkeys(p["team_name"] for p in roster))

        team_data = {}
        for team in teams:
            weekly = [team_week_score_ci(roster, team, w, batting, pitching)
                      for w in range(1, args.weeks+1)]
            team_data[team] = {"total": round(sum(weekly),2), "weeks": weekly}

        ranked      = sorted(teams, key=lambda t: team_data[t]["total"], reverse=True)
        my_rank     = ranked.index(MY_TEAM)+1 if MY_TEAM in ranked else None
        my_pts      = team_data.get(MY_TEAM, {}).get("total", 0.0)
        # Opponent: 3rd if evilbobdole is in top 2, else 2nd
        opp_idx     = 2 if (my_rank and my_rank <= 2) else 1
        second_team = ranked[opp_idx] if len(ranked) > opp_idx else None

        # Weekly gap
        my_week_pts  = team_data.get(MY_TEAM, {}).get("weeks", [0.0])[-1] if MY_TEAM in teams else 0.0
        opp_week_pts = team_data.get(second_team, {}).get("weeks", [0.0])[-1] if second_team else 0.0
        my_week_gap  = round(my_week_pts - opp_week_pts, 2)

        def build_players_ci(team_name):
            players = []
            for p in roster:
                if p["team_name"] != team_name: continue
                wk_total = player_score_ci(p["name"], p["mlb"], p["pos"], args.weeks, batting, pitching)
                name_s   = strip_accents(str(p["name"]).strip())
                team_s   = str(p["mlb"]).strip().upper().replace("AZ","ARI")
                daily    = round((pitching_daily if p["pos"]=="P" else batting_daily).get((latest_date, name_s, team_s), 0.0), 2)
                yest     = round((pitching_yest  if p["pos"]=="P" else batting_yest ).get((yesterday_date, name_s, team_s), 0.0), 2)
                weeks    = [round(player_score_ci(p["name"], p["mlb"], p["pos"], w, batting, pitching), 2)
                            for w in range(1, args.weeks+1)]
                players.append({**p, "week_total": wk_total, "weeks": weeks,
                                "total": round(sum(weeks),2), "daily": daily, "yesterday": yest})
            return top3_by_pos_ci(players)

        my_players     = build_players_ci(MY_TEAM)     if MY_TEAM in teams else []
        second_players = build_players_ci(second_team) if second_team      else []
        my_bench       = build_bench_ci(roster, MY_TEAM,     batting, pitching, batting_daily, pitching_daily, batting_yest, pitching_yest, latest_date, yesterday_date, args.weeks) if MY_TEAM in teams else []
        second_bench   = build_bench_ci(roster, second_team, batting, pitching, batting_daily, pitching_daily, batting_yest, pitching_yest, latest_date, yesterday_date, args.weeks) if second_team      else []

        my_daily      = starters_score_ci(roster, MY_TEAM,     latest_date,    batting, pitching, args.weeks) if MY_TEAM in teams else 0.0
        second_today  = starters_score_ci(roster, second_team, latest_date,    batting, pitching, args.weeks) if second_team      else 0.0
        my_yesterday  = starters_score_ci(roster, MY_TEAM,     yesterday_date, batting, pitching, args.weeks) if MY_TEAM in teams else 0.0
        second_yest   = starters_score_ci(roster, second_team, yesterday_date, batting, pitching, args.weeks) if second_team      else 0.0

        daily_scores = {team: starters_score_ci(roster, team, latest_date, batting, pitching, args.weeks)
                        for team in teams} if latest_date else {}
        daily_ranked = sorted(teams, key=lambda t: daily_scores.get(t,0.0), reverse=True)

        # Player analytics
        player_analytics = []
        seen = set()
        for p in roster:
            key = (p["name"], p["pos"])
            if key not in seen:
                seen.add(key)

        drafts.append({
            "num": num, "sheet": sheet_name, "roster": roster,
            "teams": teams, "data": team_data, "ranked": ranked,
            "my_rank": my_rank, "my_pts": my_pts,
            "my_week_pts": my_week_pts, "my_week_gap": my_week_gap,
            "my_players": my_players, "my_bench": my_bench,
            "second_team": second_team, "second_players": second_players,
            "second_bench": second_bench, "daily_scores": daily_scores,
            "my_daily": my_daily, "second_today": second_today,
            "my_daily_gap": round(my_daily - second_today, 2),
            "my_yesterday": my_yesterday, "second_yesterday": second_yest,
            "my_yesterday_gap": round(my_yesterday - second_yest, 2),
            "latest_date": latest_date, "yesterday_date": yesterday_date,
        })

    drafts.sort(key=lambda d: d["num"])

    # Build player analytics from all rosters
    all_players = {}
    for d in drafts:
        for p in d["roster"]:
            key = (p["name"], p["pos"], p["mlb"])
            if key not in all_players:
                all_players[key] = {"name": p["name"], "pos": p["pos"], "mlb": p["mlb"],
                                    "drafted_by_me": 0, "cashing": 0, "season_total": 0.0,
                                    "week_totals": []}
            if p["team_name"] == MY_TEAM:
                all_players[key]["drafted_by_me"] += 1
            top2 = set(d["ranked"][:2]) if len(d["ranked"]) >= 2 else set(d["ranked"])
            if p["team_name"] in top2:
                all_players[key]["cashing"] += 1

    player_analytics = []
    for key, pa in all_players.items():
        name, pos, mlb = key
        season_total = round(sum(
            v for k, v in (pitching if pos=="P" else batting).items()
            if k[1]==name and k[2]==mlb.upper().replace("AZ","ARI")
        ), 2)
        week_totals = [round(player_score_ci(name, mlb, pos, w, batting, pitching), 2)
                       for w in range(1, args.weeks+1)]
        pa["season_total"]  = season_total
        pa["week_totals"]   = week_totals
        if pa["drafted_by_me"] > 0 or pa["cashing"] > 0:
            player_analytics.append(pa)

    # Import build_html from publish.py
    import importlib.util
    spec = importlib.util.spec_from_file_location("publish", Path(__file__).parent / "publish.py")
    pub  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pub)

    import time as _t
    from datetime import timezone, timedelta as _tde
    # EDT = UTC-4 (Mar-Nov), EST = UTC-5 (Nov-Mar)
    # Use tm_isdst from localtime as a proxy — works on most systems
    _is_dst  = bool(_t.localtime().tm_isdst)
    _est_off = _tde(hours=-4) if _is_dst else _tde(hours=-5)
    _now_est = datetime.utcnow() + _est_off
    _tz_lbl  = "EDT" if _is_dst else "EST"
    generated_at = _now_est.strftime(f"%B %d, %Y at %I:%M %p {_tz_lbl}")
    # ── CHAMPIONSHIP ────────────────────────────────────────────────────────
    champ_draft   = None
    champ_ros_path = Path("championship_rosters.json")
    if champ_ros_path.exists():
        champ_data   = json.loads(champ_ros_path.read_text())
        champ_roster = champ_data.get("championship", [])
        champ_teams  = list(dict.fromkeys(p["team_name"] for p in champ_roster))
        today_d      = datetime.utcnow().date()

        champ_team_data = {}
        for team in champ_teams:
            by_pos = {"P":[], "IF":[], "OF":[]}
            for p in champ_roster:
                if p["team_name"]!=team or p["pos"] not in by_pos: continue
                score = champ_player_score_ci(p["name"],p["mlb"],p["pos"],batting,pitching)
                by_pos[p["pos"]].append(score)
            total = sum(sum(sorted(scores,reverse=True)[:3]) for scores in by_pos.values())
            champ_team_data[team] = {"total": round(total,2)}

        champ_ranked   = sorted(champ_teams, key=lambda t: champ_team_data[t]["total"], reverse=True)
        my_champ_rank  = champ_ranked.index(CHAMP_MY_TEAM)+1 if CHAMP_MY_TEAM in champ_ranked else None
        my_champ_pts   = champ_team_data.get(CHAMP_MY_TEAM,{}).get("total",0.0)
        opp_idx        = 2 if (my_champ_rank and my_champ_rank<=2) else 1
        champ_opp      = champ_ranked[opp_idx] if len(champ_ranked)>opp_idx else None
        champ_opp_pts  = champ_team_data.get(champ_opp,{}).get("total",0.0)

        def build_champ_team_ci(team_name):
            by_pos = {"P":[],"IF":[],"OF":[]}
            for p in champ_roster:
                if p["team_name"]!=team_name or p["pos"] not in by_pos: continue
                score  = champ_player_score_ci(p["name"],p["mlb"],p["pos"],batting,pitching)
                name_s = strip_accents(str(p["name"]).strip())
                team_s = str(p["mlb"]).strip().upper().replace("AZ","ARI")
                daily  = round((batting_daily if p["pos"]!="P" else pitching_daily).get((latest_date,name_s,team_s),0.0),2)
                yest   = round((batting_yest  if p["pos"]!="P" else pitching_yest ).get((yesterday_date,name_s,team_s),0.0),2) if yesterday_date else 0.0
                by_pos[p["pos"]].append({**p,"total":score,"daily":daily,"yesterday":yest})
            starters=[]; bench=[]
            for pos in ["P","IF","OF"]:
                s = sorted(by_pos[pos],key=lambda x:x["total"],reverse=True)
                starters.extend(s[:3]); bench.extend(s[3:])
            return starters, bench

        my_st,  my_bn  = build_champ_team_ci(CHAMP_MY_TEAM) if CHAMP_MY_TEAM in champ_teams else ([],[])
        opp_st, opp_bn = build_champ_team_ci(champ_opp)     if champ_opp                    else ([],[])

        champ_draft = {
            "num":"CHAMP","sheet":"championship","is_champ":True,
            "roster":champ_roster,"teams":champ_teams,
            "data":champ_team_data,"ranked":champ_ranked,
            "my_rank":my_champ_rank,"my_pts":my_champ_pts,
            "my_week_pts":my_champ_pts,
            "my_week_gap":round(my_champ_pts-champ_opp_pts,2),
            "my_players":my_st,"my_bench":my_bn,
            "second_team":champ_opp,"second_players":opp_st,"second_bench":opp_bn,
            "daily_scores":{},"my_daily":sum(p["daily"] for p in my_st),
            "second_today":sum(p["daily"] for p in opp_st),
            "my_daily_gap":round(sum(p["daily"] for p in my_st)-sum(p["daily"] for p in opp_st),2),
            "my_yesterday":sum(p["yesterday"] for p in my_st),
            "second_yesterday":sum(p["yesterday"] for p in opp_st),
            "my_yesterday_gap":round(sum(p["yesterday"] for p in my_st)-sum(p["yesterday"] for p in opp_st),2),
            "latest_date":latest_date,"yesterday_date":yesterday_date,
            "champ_start":str(CHAMP_START),"champ_end":str(CHAMP_END),
            "champ_active": CHAMP_START <= today_d <= CHAMP_END,
        }
        print(f"  Championship: {CHAMP_MY_TEAM} {my_champ_pts:.2f} pts (rank {my_champ_rank})")
    else:
        print("  championship_rosters.json not found — skipping")

    print("Building HTML...")
    html = pub.build_html(drafts, player_analytics, args.weeks, generated_at,
                          champ_draft=champ_draft)

    out = Path("index.html")
    out.write_text(html, encoding="utf-8")
    print(f"✓ Written: {out} ({len(html):,} bytes)")

if __name__ == "__main__":
    main()
