#!/usr/bin/env python3
"""
run_local.py — Standalone local leaderboard generator.
No GitHub, no Excel dependency. Fetches stats directly from MLB API,
loads rosters from rosters.json, generates index.html.

Tracks last run time so it only fetches what's needed each time.
"""

import json, sys, time, unicodedata, requests
from datetime import date, timedelta, datetime
from pathlib import Path

# ── CONFIG ────────────────────────────────────────────────────────────────────
TRACKER_DIR  = Path(r"C:\Users\EvilBobFUCKINGDole\Downloads\BEST BALL 2026\bestball-tracker")
ROSTERS_FILE       = TRACKER_DIR / "rosters.json"
CHAMP_ROSTERS_FILE  = TRACKER_DIR / "championship_rosters.json"
CHAMP2_ROSTERS_FILE = TRACKER_DIR / "championship2_rosters.json"
CHAMP_START        = date(2026, 8, 10)
CHAMP_END          = date(2026, 8, 23)
STATS_FREEZE_DATE  = date(2026, 8, 9)  # Never re-fetch stats before this date
CHAMP_MY_TEAM      = "EBD"
DRAFT16_NUM        = 16  # Only calculate this regular draft alongside championship
CACHE_FILE   = TRACKER_DIR / "stats_cache.json"
SCORES_CACHE = TRACKER_DIR / "scores_cache.json"
OUTPUT_FILE  = TRACKER_DIR / "index.html"
MY_TEAM      = "evilbobdole"
SEASON_START = date(2026, 3, 25)
WEEK1_END    = date(2026, 4, 5)
API          = "https://statsapi.mlb.com/api/v1"
# ─────────────────────────────────────────────────────────────────────────────

def strip_accents(name):
    return "".join(c for c in unicodedata.normalize("NFD", str(name))
                   if unicodedata.category(c) != "Mn")

def week_num(d) -> int:
    """Week calculation with All-Star break handling.
    Jul 13-15 = All-Star break (week 0, no stats counted).
    Week 16 = Jul 16-26 (extended). Week 17+ resumes Jul 27.
    """
    ASB_START   = date(2026, 7, 13)
    ASB_END     = date(2026, 7, 15)
    WK16_START  = date(2026, 7, 16)
    WK16_END    = date(2026, 7, 26)
    WK17_START  = date(2026, 7, 27)
    if d < SEASON_START:             return 0
    if d <= WEEK1_END:               return 1
    if ASB_START <= d <= ASB_END:    return 0  # All-Star break
    if WK16_START <= d <= WK16_END:  return 16
    if d >= WK17_START:
        return 17 + (d - WK17_START).days // 7
    delta = (d - (WEEK1_END + timedelta(days=1))).days
    return 2 + delta // 7

def is_champ_date(d_str):
    try:
        d = date.fromisoformat(str(d_str)[:10])
        return CHAMP_START <= d <= CHAMP_END
    except: return False

def champ_player_score(name, mlb, pos, batting, pitching):
    """Match on name only — handles mid-season trades where team may have changed."""
    name_s = strip_accents(str(name).strip())
    src    = pitching if pos == "P" else batting
    return round(sum(v for k,v in src.items()
                     if is_champ_date(k[0]) and k[1]==name_s), 2)

def est_now():
    import time as _t
    from datetime import timedelta as _td
    off = _td(hours=-4) if (_t.daylight and _t.localtime().tm_isdst) else _td(hours=-5)
    return datetime.utcnow() + off

def ip_to_decimal(ip_raw):
    ip = float(ip_raw); full = int(ip); outs = round((ip - full) * 10)
    return full + outs / 3.0

def batting_dk(s):
    H = s.get("hits",0); d2=s.get("doubles",0); d3=s.get("triples",0); hr=s.get("homeRuns",0)
    sg = H - d2 - d3 - hr
    return round(sg*3 + d2*5 + d3*8 + hr*10 + s.get("rbi",0)*2 + s.get("runs",0)*2 +
                 s.get("baseOnBalls",0)*2 + s.get("hitByPitch",0)*2 + s.get("stolenBases",0)*5, 2)

def pitching_dk(s):
    IP = ip_to_decimal(s.get("inningsPitched",0))
    return round(IP*2.25 + s.get("strikeOuts",0)*2 + s.get("wins",0)*4 -
                 s.get("earnedRuns",0)*2 - s.get("hits",0)*0.6 -
                 s.get("baseOnBalls",0)*0.6 - s.get("hitBatsmen",0)*0.6, 2)

def fetch(url):
    r = requests.get(url, timeout=20); r.raise_for_status(); return r.json()

def fetch_date(d):
    batting = {}; pitching = {}; date_str = str(d)
    skip = {"postponed","cancelled","suspended","delayed"}
    data = fetch(f"{API}/schedule?sportId=1&date={date_str}")
    processed = set()

    for pass_state in ("Final", "Live"):
        for de in data.get("dates",[]):
            for game in de.get("games",[]):
                state    = game.get("status",{}).get("abstractGameState","")
                detailed = game.get("status",{}).get("detailedState","").lower()
                if state != pass_state: continue
                if any(s in detailed for s in skip): continue
                pk = game["gamePk"]
                if pk in processed: continue
                processed.add(pk)
                try:
                    box = fetch(f"{API}/game/{pk}/boxscore")
                except: continue
                for side in ("home","away"):
                    team = box["teams"][side]["team"]["abbreviation"].replace("AZ","ARI")
                    for p in box["teams"][side]["players"].values():
                        bs = p.get("stats",{}).get("batting")
                        if bs and bs.get("atBats",0)+bs.get("baseOnBalls",0)+bs.get("hitByPitch",0)>0:
                            name = strip_accents(p["person"]["fullName"])
                            key  = (date_str, name, team)
                            batting[key] = batting.get(key,0.0) + batting_dk(bs)
                        ps = p.get("stats",{}).get("pitching")
                        if ps and float(ps.get("inningsPitched",0))>0:
                            name = strip_accents(p["person"]["fullName"])
                            if ip_to_decimal(ps.get("inningsPitched",0))==0: continue
                            key  = (date_str, name, team)
                            pitching[key] = pitching.get(key,0.0) + pitching_dk(ps)
    return batting, pitching

def load_stats():
    now_est   = est_now()
    today     = now_est.date()
    yesterday = today - timedelta(days=1)

    all_bat = {}; all_pit = {}; cached_dates = set()

    if CACHE_FILE.exists():
        try:
            cache = json.loads(CACHE_FILE.read_text())
            for k,v in cache.get("batting",{}).items():
                all_bat[tuple(k.split("|"))] = v
            for k,v in cache.get("pitching",{}).items():
                all_pit[tuple(k.split("|"))] = v
            cached_dates = set(k.split("|")[0] for k in cache.get("batting",{}))
            print(f"  Cache: {len(cached_dates)} dates loaded")
        except Exception as e:
            print(f"  Cache load failed: {e}")

    # On Mondays, only remove previous week's dates so they get re-fetched fresh
    # Never clear frozen dates (before STATS_FREEZE_DATE)
    if now_est.weekday() == 0:
        prev_end   = now_est.date() - timedelta(days=1)
        prev_start = prev_end - timedelta(days=6)
        removed = 0
        for d_str in list(cached_dates):
            try:
                d_obj = date.fromisoformat(d_str)
                if prev_start <= d_obj <= prev_end and d_obj > STATS_FREEZE_DATE:
                    for k in [k for k in all_bat if k[0]==d_str]: del all_bat[k]
                    for k in [k for k in all_pit if k[0]==d_str]: del all_pit[k]
                    cached_dates.discard(d_str)
                    removed += 1
            except: pass
        print(f"  Monday — cleared {removed} previous week dates (will re-fetch fresh).")

    # Fetch missing historical dates (skip anything before STATS_FREEZE_DATE if cached)
    d = SEASON_START
    while d < yesterday:
        if str(d) not in cached_dates:
            if d <= STATS_FREEZE_DATE:
                # Don't re-fetch frozen dates - treat as already done
                pass
            else:
                print(f"  Fetching {d}...", end=" ", flush=True)
                try:
                    b, p = fetch_date(d)
                    all_bat.update(b); all_pit.update(p)
                    print(f"✓ ({len(b)} batting, {len(p)} pitching)")
                except Exception as e:
                    print(f"✗ {e}")
        d += timedelta(days=1)

    # Always fetch yesterday and today fresh
    for fetch_d in [yesterday, today]:
        label = "yesterday" if fetch_d == yesterday else "today"
        print(f"  Fetching {label} ({fetch_d})...", end=" ", flush=True)
        try:
            # Remove old entries for this date
            for k in [k for k in all_bat  if k[0]==str(fetch_d)]: del all_bat[k]
            for k in [k for k in all_pit  if k[0]==str(fetch_d)]: del all_pit[k]
            b, p = fetch_date(fetch_d)
            all_bat.update(b); all_pit.update(p)
            print(f"✓ ({len(b)} batting, {len(p)} pitching)")
        except Exception as e:
            print(f"✗ {e}")

    # Save cache (exclude today and yesterday)
    try:
        exclude = {str(today), str(yesterday)}
        cb = {"|".join(k):v for k,v in all_bat.items() if k[0] not in exclude}
        cp = {"|".join(k):v for k,v in all_pit.items() if k[0] not in exclude}
        CACHE_FILE.write_text(json.dumps({"batting":cb,"pitching":cp}))
    except Exception as e:
        print(f"  Cache save failed: {e}")

    all_dates = sorted(set(k[0] for k in all_bat)|set(k[0] for k in all_pit))
    latest    = all_dates[-1] if all_dates else None
    yest_date = all_dates[-2] if len(all_dates)>=2 else None
    print(f"  Total: {len(all_bat)} batting, {len(all_pit)} pitching rows")
    print(f"  Latest: {latest}, Yesterday: {yest_date}")
    return all_bat, all_pit, latest, yest_date

def load_scores_cache():
    """Load cached week scores. Returns dict with completed week data."""
    if SCORES_CACHE.exists():
        try:
            return json.loads(SCORES_CACHE.read_text())
        except Exception:
            pass
    return {"completed_through_week": 0, "player_weeks": {}, "team_weeks": {}}

def save_scores_cache(cache):
    try:
        SCORES_CACHE.write_text(json.dumps(cache))
    except Exception as e:
        print(f"  Scores cache save failed: {e}")

def player_week_score_cached(name, mlb, pos, week, batting, pitching, scores_cache):
    """Get player week score — from cache if completed week, compute if current."""
    key = f"{name}|{mlb}|{pos}|{week}"
    if week < scores_cache.get("completed_through_week", 0) + 1:
        cached = scores_cache.get("player_weeks", {}).get(key)
        if cached is not None:
            return cached
    # Compute fresh
    name_s = strip_accents(str(name).strip())
    team_s = str(mlb).strip().upper().replace("AZ","ARI")
    src    = pitching if pos == "P" else batting
    score  = round(sum(v for k,v in src.items()
                       if k[1]==name_s and k[2]==team_s and week_num(date.fromisoformat(k[0]))==week), 2)
    return score


def main():
    if not ROSTERS_FILE.exists():
        print(f"ERROR: {ROSTERS_FILE} not found.")
        print("Run export_rosters_ci.py first to generate rosters.json")
        sys.exit(1)

    print("Loading rosters...")
    rosters = json.loads(ROSTERS_FILE.read_text())
    # No regular drafts — championship only
    rosters = {}

    # Load championship rosters
    champ_rosters = {}
    if CHAMP_ROSTERS_FILE.exists():
        champ_rosters = json.loads(CHAMP_ROSTERS_FILE.read_text())
        print(f"  Championship rosters loaded: {len(champ_rosters.get('championship', []))} players")
    else:
        print(f"  WARNING: {CHAMP_ROSTERS_FILE} not found")

    # Load championship 2 rosters
    champ2_rosters = {}
    if CHAMP2_ROSTERS_FILE.exists():
        champ2_rosters = json.loads(CHAMP2_ROSTERS_FILE.read_text())
        print(f"  Championship 2 rosters loaded: {len(champ2_rosters.get('championship2', []))} players")

    print("Fetching MLB stats...")
    batting, pitching, latest_date, yesterday_date = load_stats()

    # Auto-detect current week
    now_est    = est_now()
    num_weeks  = week_num(now_est.date())
    print(f"  Current week: {num_weeks}")

    # Load scores cache for completed weeks
    scores_cache   = load_scores_cache()
    cached_through = scores_cache.get("completed_through_week", 0)
    completed_weeks = num_weeks - 1
    has_cache       = bool(scores_cache.get("team_weeks"))
    # Only recompute completed weeks if cache doesn't cover them
    # Never recompute if cached_through >= completed_weeks (data is frozen)
    needs_recompute = not has_cache or (completed_weeks > cached_through)
    if has_cache and not needs_recompute:
        print(f"  ✓ Using cached scores for weeks 1-{cached_through}, computing week {num_weeks} only")
    elif has_cache and needs_recompute:
        # Only recompute the newly completed weeks, not all of them
        print(f"  Updating cache: weeks {cached_through+1}-{completed_weeks} are newly complete")
    else:
        print(f"  First run — computing all weeks and caching")

    # Import build_html from publish.py
    import importlib.util
    spec = importlib.util.spec_from_file_location("publish", TRACKER_DIR/"publish.py")
    pub  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pub)

    print("Computing scores...")
    # Use publish_ci.py scoring functions by importing them
    spec2 = importlib.util.spec_from_file_location("publish_ci", TRACKER_DIR/"publish_ci.py")
    ci    = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(ci)

    # Build daily dicts
    batting_daily  = {k:v for k,v in batting.items()  if k[0]==latest_date}
    pitching_daily = {k:v for k,v in pitching.items() if k[0]==latest_date}
    batting_yest   = {k:v for k,v in batting.items()  if k[0]==yesterday_date}
    pitching_yest  = {k:v for k,v in pitching.items() if k[0]==yesterday_date}

    drafts = []
    for sheet_name, roster in rosters.items():
        num   = int(sheet_name.replace("draftboard_","").replace("_",""))
        teams = list(dict.fromkeys(p["team_name"] for p in roster))

        team_data = {}
        for team in teams:
            weekly = []
            for w in range(1, num_weeks+1):
                if w < num_weeks:
                    # Use cache if available for this specific week
                    tk = f"{sheet_name}|{team}|{w}"
                    cached_score = scores_cache.get("team_weeks", {}).get(tk)
                    if cached_score is not None:
                        weekly.append(cached_score)
                        continue
                # Compute fresh — current week OR missing from cache
                weekly.append(ci.team_week_score_ci(roster, team, w, batting, pitching))
            team_data[team] = {"total": round(sum(weekly),2), "weeks": weekly}

        # Cache any completed weeks not yet cached
        for team in teams:
            for w in range(1, num_weeks):
                tk = f"{sheet_name}|{team}|{w}"
                if tk not in scores_cache.get("team_weeks", {}):
                    scores_cache.setdefault("team_weeks", {})[tk] = team_data[team]["weeks"][w-1]

        ranked      = sorted(teams, key=lambda t: team_data[t]["total"], reverse=True)
        my_rank     = ranked.index(MY_TEAM)+1 if MY_TEAM in ranked else None
        my_pts      = team_data.get(MY_TEAM,{}).get("total",0.0)
        opp_idx     = 2 if (my_rank and my_rank<=2) else 1
        second_team = ranked[opp_idx] if len(ranked)>opp_idx else None
        my_week_pts = team_data.get(MY_TEAM,{}).get("weeks",[0.0])[-1] if MY_TEAM in teams else 0.0
        opp_week    = team_data.get(second_team,{}).get("weeks",[0.0])[-1] if second_team else 0.0
        my_week_gap = round(my_week_pts - opp_week, 2)

        def build_players_local(team_name):
            players = []
            for p in roster:
                if p["team_name"] != team_name: continue
                name_s   = strip_accents(str(p["name"]).strip())
                team_s   = str(p["mlb"]).strip().upper().replace("AZ","ARI")
                weeks    = []
                for w in range(1, num_weeks+1):
                    if w < num_weeks:
                        pk = f"{name_s}|{team_s}|{p['pos']}|{w}"
                        cached = scores_cache.get("player_weeks",{}).get(pk)
                        if cached is not None:
                            weeks.append(cached)
                            continue
                    weeks.append(round(ci.player_score_ci(p["name"],p["mlb"],p["pos"],w,batting,pitching),2))
                # Cache any completed weeks not yet cached
                for w_idx, w in enumerate(range(1, num_weeks)):
                    pk = f"{name_s}|{team_s}|{p['pos']}|{w}"
                    if pk not in scores_cache.get("player_weeks", {}):
                        scores_cache.setdefault("player_weeks",{})[pk] = weeks[w_idx]
                wk_total = weeks[-1] if weeks else 0.0
                src_d  = pitching_daily if p["pos"]=="P" else batting_daily
                src_y  = pitching_yest  if p["pos"]=="P" else batting_yest
                daily  = round(sum(v for k,v in src_d.items() if k[0]==latest_date    and k[1]==name_s), 2) if latest_date    else 0.0
                yest   = round(sum(v for k,v in src_y.items() if k[0]==yesterday_date and k[1]==name_s), 2) if yesterday_date else 0.0
                players.append({**p,"week_total":wk_total,"weeks":weeks,
                                "total":round(sum(weeks),2),"daily":daily,"yesterday":yest})
            return ci.top3_by_pos_ci(players)

        def build_bench_local(team_name):
            all_p = []
            for p in roster:
                if p["team_name"] != team_name: continue
                wk_total = ci.player_score_ci(p["name"],p["mlb"],p["pos"],num_weeks,batting,pitching)
                name_s   = strip_accents(str(p["name"]).strip())
                team_s   = str(p["mlb"]).strip().upper().replace("AZ","ARI")
                src_d  = pitching_daily if p["pos"]=="P" else batting_daily
                src_y  = pitching_yest  if p["pos"]=="P" else batting_yest
                daily  = round(sum(v for k,v in src_d.items() if k[0]==latest_date    and k[1]==name_s), 2) if latest_date    else 0.0
                yest   = round(sum(v for k,v in src_y.items() if k[0]==yesterday_date and k[1]==name_s), 2) if yesterday_date else 0.0
                weeks    = [round(ci.player_score_ci(p["name"],p["mlb"],p["pos"],w,batting,pitching),2)
                            for w in range(1,num_weeks+1)]
                all_p.append({**p,"week_total":wk_total,"weeks":weeks,
                              "total":round(sum(weeks),2),"daily":daily,"yesterday":yest})
            starters = set()
            by_pos   = {"P":[],"IF":[],"OF":[]}
            for p in all_p:
                if p["pos"] in by_pos: by_pos[p["pos"]].append(p)
            for pos,plist in by_pos.items():
                for p in sorted(plist,key=lambda x:x["weeks"][-1] if x["weeks"] else 0,reverse=True)[:3]:
                    starters.add(p["name"])
            pos_order = {"P":0,"IF":1,"OF":2}
            bench = [p for p in all_p if p["name"] not in starters]
            bench.sort(key=lambda x:(pos_order.get(x["pos"],9),-x["total"]))
            return bench

        my_players     = build_players_local(MY_TEAM)     if MY_TEAM in teams else []
        second_players = build_players_local(second_team) if second_team      else []
        my_bench       = build_bench_local(MY_TEAM)       if MY_TEAM in teams else []
        second_bench   = build_bench_local(second_team)   if second_team      else []

        def starters_daily_local(team_name, date_str):
            by_pos = {"P":[],"IF":[],"OF":[]}
            for p in roster:
                if p["team_name"]!=team_name or p["pos"] not in by_pos: continue
                wk = ci.player_score_ci(p["name"],p["mlb"],p["pos"],num_weeks,batting,pitching)
                by_pos[p["pos"]].append((p,wk))
            total = 0.0
            for pos,players in by_pos.items():
                for p,_ in sorted(players,key=lambda x:x[1],reverse=True)[:3]:
                    if not date_str: continue
                    name_s = strip_accents(str(p["name"]).strip())
                    src    = pitching if p["pos"]=="P" else batting
                    # Match name only — handles trades
                    total += sum(v for k,v in src.items()
                                 if k[0]==date_str and k[1]==name_s)
            return round(total,2)

        my_daily      = starters_daily_local(MY_TEAM,     latest_date)    if MY_TEAM in teams else 0.0
        second_today  = starters_daily_local(second_team, latest_date)    if second_team      else 0.0
        my_yesterday  = starters_daily_local(MY_TEAM,     yesterday_date) if MY_TEAM in teams else 0.0
        second_yest   = starters_daily_local(second_team, yesterday_date) if second_team      else 0.0
        daily_scores  = {team: starters_daily_local(team, latest_date) for team in teams} if latest_date else {}
        daily_ranked  = sorted(teams, key=lambda t: daily_scores.get(t,0.0), reverse=True)

        drafts.append({
            "num":num,"sheet":sheet_name,"roster":roster,"teams":teams,
            "data":team_data,"ranked":ranked,"my_rank":my_rank,"my_pts":my_pts,
            "my_week_pts":my_week_pts,"my_week_gap":my_week_gap,
            "my_players":my_players,"my_bench":my_bench,
            "second_team":second_team,"second_players":second_players,"second_bench":second_bench,
            "daily_scores":daily_scores,"my_daily":my_daily,"second_today":second_today,
            "my_daily_gap":round(my_daily-second_today,2),
            "my_yesterday":my_yesterday,"second_yesterday":second_yest,
            "my_yesterday_gap":round(my_yesterday-second_yest,2),
            "latest_date":latest_date,"yesterday_date":yesterday_date,
        })

    drafts.sort(key=lambda d: d["num"])

    # ── CHAMPIONSHIP DRAFT ────────────────────────────────────────────────────
    champ_draft = None
    champ_roster_raw = champ_rosters.get("championship", [])
    # Pre-score all championship players before building panels
    champ_roster = []
    for _p in champ_roster_raw:
        _ns  = strip_accents(str(_p["name"]).strip())
        _src = pitching if _p["pos"]=="P" else batting
        _srd = pitching_daily if _p["pos"]=="P" else batting_daily
        _sry = pitching_yest  if _p["pos"]=="P" else batting_yest
        _tot = round(sum(v for k,v in _src.items() if is_champ_date(k[0]) and k[1]==_ns), 2)
        _day = round(sum(v for k,v in _srd.items() if k[0]==latest_date    and k[1]==_ns), 2) if latest_date    else 0.0
        _yst = round(sum(v for k,v in _sry.items() if k[0]==yesterday_date and k[1]==_ns), 2) if yesterday_date else 0.0
        champ_roster.append({**_p, "total":_tot, "daily":_day, "yesterday":_yst})
    if champ_roster:
        today_date = est_now().date()
        champ_active = CHAMP_START <= today_date <= CHAMP_END
        champ_teams  = list(dict.fromkeys(p["team_name"] for p in champ_roster))

        # Score each team for the championship period
        champ_team_data = {}
        for team in champ_teams:
            total = 0.0
            by_pos = {"P": [], "IF": [], "OF": []}
            for p in champ_roster:
                if p["team_name"] != team or p["pos"] not in by_pos: continue
                score = champ_player_score(p["name"], p["mlb"], p["pos"], batting, pitching)
                by_pos[p["pos"]].append(score)
            for pos, scores in by_pos.items():
                total += sum(sorted(scores, reverse=True)[:3])
            champ_team_data[team] = {"total": round(total, 2)}

        champ_ranked    = sorted(champ_teams, key=lambda t: champ_team_data[t]["total"], reverse=True)
        my_champ_rank   = champ_ranked.index(CHAMP_MY_TEAM)+1 if CHAMP_MY_TEAM in champ_ranked else None
        my_champ_pts    = champ_team_data.get(CHAMP_MY_TEAM, {}).get("total", 0.0)
        opp_idx         = 2 if (my_champ_rank and my_champ_rank <= 2) else 1
        champ_opp_team  = champ_ranked[opp_idx] if len(champ_ranked) > opp_idx else None
        champ_opp_pts   = champ_team_data.get(champ_opp_team, {}).get("total", 0.0)

        # Build player lists for EBD and opponent
        def build_champ_team(team_name):
            by_pos = {"P": [], "IF": [], "OF": []}
            for p in champ_roster:
                if p["team_name"] != team_name or p["pos"] not in by_pos: continue
                score = champ_player_score(p["name"], p["mlb"], p["pos"], batting, pitching)
                name_s = strip_accents(str(p["name"]).strip())
                team_s = str(p["mlb"]).strip().upper().replace("AZ","ARI")
                src_d  = pitching_daily if p["pos"]=="P" else batting_daily
                src_y  = pitching_yest  if p["pos"]=="P" else batting_yest
                daily  = round(sum(v for k,v in src_d.items() if k[0]==latest_date    and k[1]==name_s), 2) if latest_date    else 0.0
                yest   = round(sum(v for k,v in src_y.items() if k[0]==yesterday_date and k[1]==name_s), 2) if yesterday_date else 0.0
                by_pos[p["pos"]].append({**p, "total": score, "daily": daily, "yesterday": yest})
            starters = []; bench = []
            for pos in ["P", "IF", "OF"]:
                sorted_p = sorted(by_pos[pos], key=lambda x: x["total"], reverse=True)
                starters.extend(sorted_p[:3])
                bench.extend(sorted_p[3:])
            return starters, bench

        my_starters,  my_bench   = build_champ_team(CHAMP_MY_TEAM)   if CHAMP_MY_TEAM in champ_teams else ([], [])
        opp_starters, opp_bench  = build_champ_team(champ_opp_team)   if champ_opp_team               else ([], [])

        champ_draft = {
            "num": "CHAMP", "sheet": "championship", "is_champ": True,
            "roster": champ_roster, "teams": champ_teams,
            "data": champ_team_data, "ranked": champ_ranked,
            "my_rank": my_champ_rank, "my_pts": my_champ_pts,
            "my_week_pts": my_champ_pts, "my_week_gap": round(my_champ_pts - champ_opp_pts, 2),
            "my_players": my_starters, "my_bench": my_bench,
            "second_team": champ_opp_team, "second_players": opp_starters, "second_bench": opp_bench,
            "daily_scores": {t: sum(
                sorted([champ_player_score(p["name"],p["mlb"],p["pos"],batting_daily,pitching_daily)
                        for p in champ_roster if p["team_name"]==t], reverse=True)[:9]
            ) for t in champ_teams},
            "my_daily": sum(p["daily"] for p in my_starters),
            "second_today": sum(p["daily"] for p in opp_starters),
            "my_daily_gap": round(sum(p["daily"] for p in my_starters) - sum(p["daily"] for p in opp_starters), 2),
            "my_yesterday": sum(p["yesterday"] for p in my_starters),
            "second_yesterday": sum(p["yesterday"] for p in opp_starters),
            "my_yesterday_gap": round(sum(p["yesterday"] for p in my_starters) - sum(p["yesterday"] for p in opp_starters), 2),
            "latest_date": latest_date, "yesterday_date": yesterday_date,
            "champ_start": str(CHAMP_START), "champ_end": str(CHAMP_END),
            "champ_active": champ_active,
        }
        print(f"  Championship: {CHAMP_MY_TEAM} {my_champ_pts:.2f} pts (rank {my_champ_rank})")

    # ── CHAMPIONSHIP 2 ────────────────────────────────────────────────────────
    champ2_roster_raw = champ2_rosters.get("championship2", [])
    # Pre-score all championship2 players before building panels
    champ2_roster = []
    for _p in champ2_roster_raw:
        _ns  = strip_accents(str(_p["name"]).strip())
        _src = pitching if _p["pos"]=="P" else batting
        _srd = pitching_daily if _p["pos"]=="P" else batting_daily
        _sry = pitching_yest  if _p["pos"]=="P" else batting_yest
        _tot = round(sum(v for k,v in _src.items() if is_champ_date(k[0]) and k[1]==_ns), 2)
        _day = round(sum(v for k,v in _srd.items() if k[0]==latest_date    and k[1]==_ns), 2) if latest_date    else 0.0
        _yst = round(sum(v for k,v in _sry.items() if k[0]==yesterday_date and k[1]==_ns), 2) if yesterday_date else 0.0
        champ2_roster.append({**_p, "total":_tot, "daily":_day, "yesterday":_yst})
    if champ2_roster:
        c2_teams = list(dict.fromkeys(p["team_name"] for p in champ2_roster))
        c2_team_data = {}
        for team in c2_teams:
            by_pos = {"P":[],"IF":[],"OF":[]}
            for p in champ2_roster:
                if p["team_name"]!=team or p["pos"] not in by_pos: continue
                score = champ_player_score(p["name"],p["mlb"],p["pos"],batting,pitching)
                by_pos[p["pos"]].append(score)
            total = sum(sum(sorted(s,reverse=True)[:3]) for s in by_pos.values())
            c2_team_data[team] = {"total": round(total,2)}

        c2_ranked   = sorted(c2_teams, key=lambda t: c2_team_data[t]["total"], reverse=True)
        my_c2_rank  = c2_ranked.index(CHAMP_MY_TEAM)+1 if CHAMP_MY_TEAM in c2_ranked else None
        my_c2_pts   = c2_team_data.get(CHAMP_MY_TEAM,{}).get("total",0.0)
        c2_opp_idx  = 2 if (my_c2_rank and my_c2_rank<=2) else 1
        c2_opp      = c2_ranked[c2_opp_idx] if len(c2_ranked)>c2_opp_idx else None
        c2_opp_pts  = c2_team_data.get(c2_opp,{}).get("total",0.0)

        def build_champ2_team(team_name):
            by_pos = {"P":[],"IF":[],"OF":[]}
            for p in champ2_roster:
                if p["team_name"]!=team_name or p["pos"] not in by_pos: continue
                score  = champ_player_score(p["name"],p["mlb"],p["pos"],batting,pitching)
                name_s = strip_accents(str(p["name"]).strip())
                src_d  = pitching_daily if p["pos"]=="P" else batting_daily
                src_y  = pitching_yest  if p["pos"]=="P" else batting_yest
                daily  = round(sum(v for k,v in src_d.items() if k[0]==latest_date    and k[1]==name_s),2) if latest_date    else 0.0
                yest   = round(sum(v for k,v in src_y.items() if k[0]==yesterday_date and k[1]==name_s),2) if yesterday_date else 0.0
                by_pos[p["pos"]].append({**p,"total":score,"daily":daily,"yesterday":yest})
            starters=[]; bench=[]
            for pos in ["P","IF","OF"]:
                s = sorted(by_pos[pos],key=lambda x:x["total"],reverse=True)
                starters.extend(s[:3]); bench.extend(s[3:])
            return starters, bench

        my2_st, my2_bn = build_champ2_team(CHAMP_MY_TEAM) if CHAMP_MY_TEAM in c2_teams else ([],[])

        champ2_draft = {
            "num":"CHAMP2","sheet":"championship2","is_champ":True,
            "roster":champ2_roster,"teams":c2_teams,
            "data":c2_team_data,"ranked":c2_ranked,
            "my_rank":my_c2_rank,"my_pts":my_c2_pts,
            "my_week_pts":my_c2_pts,"my_week_gap":round(my_c2_pts-c2_opp_pts,2),
            "my_players":my2_st,"my_bench":my2_bn,
            "second_team":c2_opp,"second_players":[],"second_bench":[],
            "daily_scores":{},"my_daily":sum(p["daily"] for p in my2_st),
            "second_today":0.0,"my_daily_gap":sum(p["daily"] for p in my2_st),
            "my_yesterday":sum(p["yesterday"] for p in my2_st),
            "second_yesterday":0.0,"my_yesterday_gap":sum(p["yesterday"] for p in my2_st),
            "latest_date":latest_date,"yesterday_date":yesterday_date,
            "champ_start":str(CHAMP_START),"champ_end":str(CHAMP_END),
            "champ_active": CHAMP_START <= est_now().date() <= CHAMP_END,
        }
        print(f"  Championship 2: {CHAMP_MY_TEAM} {my_c2_pts:.2f} pts (rank {my_c2_rank}/{len(c2_teams)})")

    # Build player analytics
    player_analytics = []
    seen = {}
    for d in drafts:
        top2 = set(d["ranked"][:2]) if len(d["ranked"])>=2 else set(d["ranked"])
        for p in d["roster"]:
            key = (strip_accents(p["name"]), p["pos"], p["mlb"])
            if key not in seen:
                seen[key] = {"name":p["name"],"pos":p["pos"],"mlb":p["mlb"],
                             "drafted_by_me":0,"cashing":0,"season_total":0.0,"week_totals":[]}
            if p["team_name"]==MY_TEAM:
                seen[key]["drafted_by_me"] += 1
            if p["team_name"] in top2:
                seen[key]["cashing"] += 1
    for key,pa in seen.items():
        name,pos,mlb = key
        pa["season_total"] = round(sum(
            v for k,v in (pitching if pos=="P" else batting).items()
            if k[1]==name and k[2]==mlb.upper().replace("AZ","ARI")
        ),2)
        pa["week_totals"] = [round(ci.player_score_ci(name,mlb,pos,w,batting,pitching),2)
                             for w in range(1,num_weeks+1)]
        if pa["drafted_by_me"]>0 or pa["cashing"]>0:
            player_analytics.append(pa)

    # Always save cache to capture any newly computed weeks
    scores_cache["completed_through_week"] = num_weeks - 1
    save_scores_cache(scores_cache)
    print(f"  Scores cache saved through week {num_weeks-1}")

    import time as _t
    from datetime import timedelta as _tde
    _est_off  = _tde(hours=-4) if (_t.daylight and _t.localtime().tm_isdst) else _tde(hours=-5)
    _now_est  = datetime.utcnow() + _est_off
    _tz_lbl   = "EDT" if _t.daylight and _t.localtime().tm_isdst else "EST"
    generated_at = _now_est.strftime(f"%B %d, %Y at %I:%M %p {_tz_lbl}")

    print("Building HTML...")
    html = pub.build_html(drafts, player_analytics, num_weeks, generated_at,
                          xlsx=TRACKER_DIR, champ_draft=champ_draft, champ2_draft=champ2_draft)
    OUTPUT_FILE.write_text(html, encoding="utf-8")
    print(f"✓ Written: {OUTPUT_FILE} ({len(html):,} bytes)")

if __name__ == "__main__":
    main()
