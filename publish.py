#!/usr/bin/env python3
"""
MLB Best Ball $600K Slugfest — Full Leaderboard Publisher

Reads rosters + stats from the xlsx, computes scores,
generates index.html with season summary + all 35 draft pages,
then pushes to GitHub Pages.

Usage:
    python publish.py                   # generate + push
    python publish.py --local           # generate only, no push
    python publish.py --weeks 2         # score through week 2

One-time setup:
    1. Create a PUBLIC GitHub repo e.g. "bestball-tracker"
    2. Repo Settings -> Pages -> Deploy from branch -> main -> / (root)
    3. Clone repo locally, put this script inside it
    4. Edit XLSX_PATH and REPO_DIR below
    5. Run: python publish.py
    6. Live at: https://YOUR_USERNAME.github.io/bestball-tracker/
"""

import argparse
import subprocess
import unicodedata
from datetime import datetime
from pathlib import Path
from openpyxl import load_workbook

# ── EDIT THESE TWO PATHS ─────────────────────────────────────────────────────
XLSX_PATH = Path(r"C:\Users\EvilBobFUCKINGDole\Downloads\BEST BALL 2026\all_draftboards_complete.xlsx")
REPO_DIR  = Path(r"C:\Users\EvilBobFUCKINGDole\Downloads\BEST BALL 2026\bestball-tracker")
# ─────────────────────────────────────────────────────────────────────────────

MY_TEAM = "evilbobdole"


# ── HELPERS ───────────────────────────────────────────────────────────────────

def strip_accents(name: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", str(name))
        if unicodedata.category(c) != "Mn"
    )

def ordinal(n):
    if n is None: return "—"
    s = ["th","st","nd","rd"] + ["th"] * 16
    return f"{n}{s[n % 100 if 11 <= n % 100 <= 13 else n % 10]}"

def load_stats(wb):
    batting  = {}
    pitching = {}
    ds = wb["Daily Stats"]
    for row in ds.iter_rows(min_row=2, values_only=True):
        if not row[0] or not row[2]: continue
        try:
            week = int(row[1])
            name = strip_accents(str(row[2]).strip())
            team = str(row[3]).strip().upper().replace("AZ","ARI")
            dk   = float(row[18]) if row[18] else 0.0
            key  = (week, name, team)
            batting[key] = batting.get(key, 0.0) + dk
        except (TypeError, ValueError): continue

    ps = wb["Pitching Stats"]
    for row in ps.iter_rows(min_row=2, values_only=True):
        if not row[0] or not row[2]: continue
        try:
            week = int(row[1])
            name = strip_accents(str(row[2]).strip())
            team = str(row[3]).strip().upper().replace("AZ","ARI")
            dk   = float(row[11]) if row[11] else 0.0
            key  = (week, name, team)
            pitching[key] = pitching.get(key, 0.0) + dk
        except (TypeError, ValueError): continue

    return batting, pitching

def player_score(name, mlb, pos, week, batting, pitching):
    name = strip_accents(str(name).strip())
    team = str(mlb).strip().upper().replace("AZ","ARI")
    if pos == "P":
        return pitching.get((week, name, team), 0.0)
    return batting.get((week, name, team), 0.0)

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

def load_all_drafts(wb, batting, pitching, num_weeks):
    drafts = []
    for sheet_name in wb.sheetnames:
        if not sheet_name.startswith("draftboard_"): continue
        ws  = wb[sheet_name]
        num = int(sheet_name.replace("draftboard_","").replace("_",""))

        # Roster
        roster = []
        for row in range(2, 242):
            name = ws.cell(row, 2).value
            team = ws.cell(row, 5).value
            if not name or not team: continue
            roster.append({
                "pick":      ws.cell(row, 1).value,
                "name":      str(name).strip(),
                "pos":       str(ws.cell(row, 3).value).strip(),
                "mlb":       str(ws.cell(row, 4).value).strip(),
                "team_name": str(team).strip(),
            })

        # Teams in draft order from leaderboard col G
        teams = []
        seen  = set()
        for row in range(2, 14):
            t = ws.cell(row, 7).value
            if t and str(t).strip() not in seen:
                teams.append(str(t).strip())
                seen.add(str(t).strip())

        # Score every team every week
        team_data = {}
        for team in teams:
            weekly = [team_week_score(roster, team, w, batting, pitching)
                      for w in range(1, num_weeks + 1)]
            team_data[team] = {
                "total":  round(sum(weekly), 2),
                "weeks":  weekly,
            }

        ranked   = sorted(teams, key=lambda t: team_data[t]["total"], reverse=True)
        my_rank  = ranked.index(MY_TEAM) + 1 if MY_TEAM in ranked else None
        my_pts   = team_data.get(MY_TEAM, {}).get("total", 0.0)

        # evilbobdole per-player scores
        pos_order = {"IF": 0, "OF": 1, "P": 2}
        my_players = []
        for p in roster:
            if p["team_name"] != MY_TEAM: continue
            weeks = [round(player_score(p["name"], p["mlb"], p["pos"], w, batting, pitching), 2)
                     for w in range(1, num_weeks + 1)]
            my_players.append({**p, "weeks": weeks, "total": round(sum(weeks), 2)})
        my_players.sort(key=lambda x: (pos_order.get(x["pos"], 9), x["name"]))

        drafts.append({
            "num":        num,
            "sheet":      sheet_name,
            "roster":     roster,
            "teams":      teams,
            "data":       team_data,
            "ranked":     ranked,
            "my_rank":    my_rank,
            "my_pts":     my_pts,
            "my_players": my_players,
        })

    drafts.sort(key=lambda d: d["num"])
    return drafts


# ── HTML ──────────────────────────────────────────────────────────────────────

CSS = """
:root {
  --navy:   #0D2D5E;
  --blue:   #1F4E79;
  --blue2:  #2E75B6;
  --gold:   #FFD700;
  --silver: #C0C0C0;
  --bronze: #CD7F32;
  --green:  #00CC00;
  --lgreen: #E6FFE6;
  --bg:     #F2F5FA;
  --card:   #FFFFFF;
  --text:   #222222;
  --muted:  #666666;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: Arial, sans-serif; background: var(--bg); color: var(--text); }

/* NAV */
nav {
  background: var(--navy);
  padding: 0 16px;
  display: flex;
  align-items: center;
  gap: 6px;
  height: 46px;
  position: sticky;
  top: 0;
  z-index: 200;
  overflow-x: auto;
  white-space: nowrap;
}
nav .brand { color: #fff; font-weight: bold; font-size: 14px; margin-right: 10px; flex-shrink: 0; }
nav a { color: #aac; text-decoration: none; font-size: 11px; padding: 3px 7px;
        border-radius: 4px; flex-shrink: 0; }
nav a:hover { background: rgba(255,255,255,0.15); color: #fff; }

/* HERO */
.hero {
  background: linear-gradient(135deg, var(--navy) 0%, var(--blue2) 100%);
  color: #fff; padding: 36px 20px 28px; text-align: center;
}
.hero h1 { font-size: 26px; margin-bottom: 6px; }
.hero .sub { opacity: .75; font-size: 13px; }
.hero .ts  { opacity: .55; font-size: 11px; margin-top: 5px; }

/* STAT PILLS */
.pills { display: flex; justify-content: center; gap: 16px; flex-wrap: wrap;
         margin: 20px 16px 0; }
.pill { background: var(--card); border-radius: 10px; padding: 14px 20px;
        text-align: center; min-width: 110px; box-shadow: 0 2px 8px rgba(0,0,0,.08); }
.pill .val { font-size: 22px; font-weight: bold; color: var(--blue); }
.pill .lbl { font-size: 11px; color: var(--muted); margin-top: 2px; }

/* SECTION WRAPPER */
.container { max-width: 960px; margin: 0 auto; padding: 0 14px 60px; }

/* SEASON SUMMARY */
.summary {
  background: var(--card); border-radius: 10px;
  box-shadow: 0 2px 10px rgba(0,0,0,.08); margin: 24px 0; overflow: hidden;
}
.summary .sec-title {
  background: var(--blue); color: #fff; padding: 11px 18px; font-size: 15px; font-weight: bold;
}

/* DRAFT CARD */
.draft-card {
  background: var(--card); border-radius: 10px;
  box-shadow: 0 2px 8px rgba(0,0,0,.07); margin-bottom: 24px; overflow: hidden;
}
.draft-header {
  background: var(--blue); color: #fff; padding: 11px 18px;
  display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px;
}
.draft-header h2 { font-size: 15px; }
.badge {
  background: var(--green); color: #000; padding: 3px 11px;
  border-radius: 12px; font-size: 12px; font-weight: bold;
}

/* TABLES */
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { background: #E8EEF7; padding: 8px 12px; text-align: left; font-weight: bold; color: #333; }
td { padding: 7px 12px; border-bottom: 1px solid #F0F0F0; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: #F7F9FC; }
.num  { font-family: monospace; }
.bold { font-weight: bold; }

/* RANK BADGES */
.r1 { background: var(--gold)   !important; color: #333; font-weight: bold; }
.r2 { background: var(--silver) !important; color: #333; font-weight: bold; }
.r3 { background: var(--bronze) !important; color: #fff; font-weight: bold; }
.rank-cell { text-align: center; width: 36px; font-weight: bold; }

/* MY TEAM ROW */
.my-row td { background: var(--lgreen) !important; font-weight: bold; }
.my-row:hover td { background: #d0f5d0 !important; }

/* PLAYER SECTION */
.player-section { border-top: 3px solid var(--green); }
.player-header  { background: var(--green); color: #000; padding: 8px 16px; font-size: 13px; font-weight: bold; }
.pos-IF td { background: #FFF0E8; }
.pos-OF td { background: #F0F8EE; }
.pos-P  td { background: #EAF2FB; }
.pos-break td { border-top: 2px solid #aaa !important; }

/* RESPONSIVE */
@media(max-width:600px){
  .hero h1 { font-size: 20px; }
  td, th   { padding: 6px 8px; font-size: 12px; }
  .pills   { gap: 10px; }
  .pill    { min-width: 90px; padding: 10px 14px; }
  .pill .val { font-size: 18px; }
}
"""

def build_html(drafts, num_weeks, generated_at):
    # Aggregate stats
    my_drafts  = [d for d in drafts if MY_TEAM in d["teams"]]
    total_pts  = sum(d["my_pts"] for d in my_drafts)
    wins       = sum(1 for d in my_drafts if d["my_rank"] == 1)
    top3       = sum(1 for d in my_drafts if d["my_rank"] and d["my_rank"] <= 3)
    avg_rank   = sum(d["my_rank"] for d in my_drafts if d["my_rank"]) / max(len(my_drafts), 1)

    wk_hdrs = "".join(f"<th>Wk {w}</th>" for w in range(1, num_weeks + 1))

    # ── NAV ──
    nav_links = "".join(f'<a href="#d{d["num"]}">D{d["num"]}</a>' for d in drafts)
    nav = f'<nav><span class="brand">⚾ Best Ball $600K</span><a href="#summary">Summary</a>{nav_links}</nav>'

    # ── SEASON SUMMARY TABLE ──
    sum_rows = ""
    for d in drafts:
        rank     = d["my_rank"]
        my_pts   = d["my_pts"]
        leader   = d["ranked"][0] if d["ranked"] else "—"
        ldr_pts  = d["data"].get(leader, {}).get("total", 0.0)
        diff     = round(my_pts - ldr_pts, 2)
        diff_str = (f'<span style="color:#1a7a1a">+{diff:.2f}</span>' if diff > 0
                    else f'<span style="color:#b00">{diff:.2f}</span>' if diff < 0
                    else "0.00")
        r_cls    = f"r{rank}" if rank in (1,2,3) else ""
        me_cls   = "my-row" if MY_TEAM in d["teams"] else ""
        sum_rows += f"""
        <tr class="{me_cls}">
          <td><a href="#d{d['num']}">Draft {d['num']}</a></td>
          <td class="num">{my_pts:.2f}</td>
          <td class="rank-cell {r_cls}">{ordinal(rank)}</td>
          <td>{leader}</td>
          <td class="num">{ldr_pts:.2f}</td>
          <td class="num">{diff_str}</td>
        </tr>"""

    summary = f"""
    <div id="summary" class="summary">
      <div class="sec-title">📊 Season Summary — {MY_TEAM}</div>
      <table>
        <thead>
          <tr><th>Draft</th><th>My Points</th><th>Rank</th>
              <th>Leader</th><th>Leader Pts</th><th>Gap</th></tr>
        </thead>
        <tbody>{sum_rows}</tbody>
      </table>
    </div>"""

    # ── PER-DRAFT CARDS ──
    draft_cards = ""
    for d in drafts:
        # Leaderboard rows
        ldr_rows = ""
        for rank, team in enumerate(d["ranked"], 1):
            sc      = d["data"].get(team, {})
            total   = sc.get("total", 0.0)
            weeks   = "".join(f'<td class="num">{w:.2f}</td>' for w in sc.get("weeks", []))
            r_cls   = f"r{rank}" if rank in (1,2,3) else ""
            me_cls  = "my-row" if team == MY_TEAM else ""
            ldr_rows += f"""
            <tr class="{me_cls}">
              <td class="rank-cell {r_cls}">{rank}</td>
              <td class="bold">{team}</td>
              <td class="num bold">{total:.2f}</td>
              {weeks}
            </tr>"""

        # Player section (evilbobdole only)
        player_html = ""
        if MY_TEAM in d["teams"] and d["my_players"]:
            p_rows  = ""
            prev    = None
            for p in d["my_players"]:
                brk  = "pos-break" if prev and p["pos"] != prev else ""
                prev = p["pos"]
                wkd  = "".join(f'<td class="num">{w:.2f}</td>' for w in p["weeks"])
                p_rows += f"""
                <tr class="pos-{p['pos']} {brk}">
                  <td>{p['name']}</td>
                  <td>{p['pos']}</td>
                  <td>{p['mlb']}</td>
                  <td class="num bold">{p['total']:.2f}</td>
                  {wkd}
                </tr>"""
            player_html = f"""
            <div class="player-section">
              <div class="player-header">⚾ {MY_TEAM} — Player Scores</div>
              <table>
                <thead><tr><th>Player</th><th>Pos</th><th>MLB</th><th>Total</th>{wk_hdrs}</tr></thead>
                <tbody>{p_rows}</tbody>
              </table>
            </div>"""

        badge = (f'<span class="badge">{MY_TEAM}: {d["my_pts"]:.2f} pts &nbsp;|&nbsp; {ordinal(d["my_rank"])}</span>'
                 if MY_TEAM in d["teams"] else "")

        draft_cards += f"""
        <div id="d{d['num']}" class="draft-card">
          <div class="draft-header">
            <h2>Draft {d['num']}</h2>
            {badge}
          </div>
          <table>
            <thead><tr><th>#</th><th>Team</th><th>Total</th>{wk_hdrs}</tr></thead>
            <tbody>{ldr_rows}</tbody>
          </table>
          {player_html}
        </div>"""

    # ── FULL PAGE ──
    pills = f"""
    <div class="pills">
      <div class="pill"><div class="val">{total_pts:,.2f}</div><div class="lbl">Total Points</div></div>
      <div class="pill"><div class="val">{wins}</div><div class="lbl">Draft Wins</div></div>
      <div class="pill"><div class="val">{top3}</div><div class="lbl">Top-3 Finishes</div></div>
      <div class="pill"><div class="val">{avg_rank:.1f}</div><div class="lbl">Avg Rank</div></div>
      <div class="pill"><div class="val">{len(drafts)}</div><div class="lbl">Drafts</div></div>
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>⚾ MLB Best Ball $600K Slugfest</title>
<style>{CSS}</style>
</head>
<body>
{nav}
<div class="hero">
  <h1>⚾ MLB Best Ball $600K Slugfest</h1>
  <div class="sub">35 Drafts &nbsp;·&nbsp; DraftKings Scoring &nbsp;·&nbsp; Tracking: {MY_TEAM}</div>
  <div class="ts">Updated: {generated_at}</div>
</div>
{pills}
<div class="container">
{summary}
{draft_cards}
</div>
<script>
document.querySelectorAll('a[href^="#"]').forEach(a => {{
  a.addEventListener('click', e => {{
    e.preventDefault();
    const el = document.querySelector(a.getAttribute('href'));
    if (el) el.scrollIntoView({{behavior:'smooth', block:'start'}});
  }});
}});
</script>
</body>
</html>"""


# ── MAIN ──────────────────────────────────────────────────────────────────────

def git_push(repo_dir, message):
    for cmd in [
        ["git", "-C", str(repo_dir), "add", "index.html"],
        ["git", "-C", str(repo_dir), "commit", "-m", message],
        ["git", "-C", str(repo_dir), "push"],
    ]:
        r = subprocess.run(cmd, capture_output=True, text=True)
        combined = r.stdout + r.stderr
        if r.returncode != 0:
            if "nothing to commit" in combined:
                print("No changes since last push.")
                return
            # Print full output so we can diagnose
            print(f"stdout: {r.stdout}")
            print(f"stderr: {r.stderr}")
            raise RuntimeError(f"Git command failed: {' '.join(cmd)}")
    print("✓ Pushed to GitHub Pages")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local",  action="store_true", help="Skip git push")
    parser.add_argument("--xlsx",   default=str(XLSX_PATH))
    parser.add_argument("--weeks",  type=int, default=1)
    args = parser.parse_args()

    xlsx = Path(args.xlsx)
    if not xlsx.exists():
        raise FileNotFoundError(f"Not found: {xlsx}")

    print(f"Loading {xlsx.name}...")
    wb = load_workbook(xlsx, data_only=True)

    print("Reading stats...")
    batting, pitching = load_stats(wb)
    print(f"  Batting rows:  {len(batting)}")
    print(f"  Pitching rows: {len(pitching)}")

    print("Computing scores for all 35 drafts...")
    drafts = load_all_drafts(wb, batting, pitching, num_weeks=args.weeks)

    generated_at = datetime.now().strftime("%B %d, %Y at %I:%M %p")
    print("Building HTML...")
    html = build_html(drafts, num_weeks=args.weeks, generated_at=generated_at)

    out = Path("index.html") if args.local else REPO_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"✓ Written: {out}  ({len(html):,} bytes)")

    if not args.local:
        print("Pushing to GitHub...")
        git_push(REPO_DIR, f"Update scores {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        username = subprocess.run(
            ["git", "-C", str(REPO_DIR), "remote", "get-url", "origin"],
            capture_output=True, text=True
        ).stdout.strip()
        repo_name = Path(username).stem
        print(f"\n✓ Live at: https://YOUR_USERNAME.github.io/{repo_name}/")
    else:
        print(f"\n✓ Open index.html in your browser to preview.")

if __name__ == "__main__":
    main()
