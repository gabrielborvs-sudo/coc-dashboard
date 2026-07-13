"""Clash of Clans clan dashboard core.

Clan war room: clan stats, live war, sortable member browser with full
per-member profiles.

- Run directly (python dashboard.py [--open]) to generate a local dashboard.html.
- Imported by app.py to serve the same page as a live website.
"""
import json
import html
import os
import sys
import urllib.request
import urllib.error
import urllib.parse
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from th_caps import UNIT_TH_CAPS, EQUIP_BS_CAPS, EQUIP_HERO, EQUIP_CURVE

# ----------------------------- configuration -------------------------------
KEY_FILE = Path(r"C:\Users\Borvs\Pictures\coc-key.txt")
CLAN_TAG = "#2JYRQ0UPC"
OUT_FILE = Path(__file__).with_name("dashboard.html")

# Base URL can be overridden for proxy setups (e.g. RoyaleAPI proxy on Render):
#   COC_API_BASE=https://cocproxy.royaleapi.dev/v1
API_BASE = os.environ.get("COC_API_BASE", "https://api.clashofclans.com/v1")

# Town-hall icons (community CDN, verified TH8-TH18); a styled fallback chip
# appears if an image is missing or the CDN is offline.
TH_ICON = "https://www.clash.ninja/images/entities/1_{n}.png"


def _norm(name):
    """Normalize a unit name for icon lookup: lowercase, alphanumerics only."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


# Unit icons scraped from coc.guide's index pages (their filenames are legacy
# internal names - e.g. Rage Spell is haste.png - so this is an explicit map,
# not a slug pattern). Units missing here (e.g. pets) just render without an
# icon via the onerror fallback.
_UNIT_ICON_RAW = {
    # home troops
    "Barbarian": "troop/barbarian", "Archer": "troop/archer",
    "Giant": "troop/giant", "Goblin": "troop/goblin",
    "Wall Breaker": "troop/wall-breaker", "Balloon": "troop/balloon",
    "Wizard": "troop/wizard", "Healer": "troop/healer",
    "Dragon": "troop/dragon", "P.E.K.K.A": "troop/pekka",
    "Baby Dragon": "troop/babydragon", "Miner": "troop/miner",
    "Electro Dragon": "troop/electro-dragon", "Yeti": "troop/yeti",
    "Dragon Rider": "troop/dragon-rider", "Electro Titan": "troop/electro-titan",
    "Root Rider": "troop/root-rider", "Thrower": "troop/thrower",
    "Minion": "troop/gargoyle", "Hog Rider": "troop/boar-rider",
    "Valkyrie": "troop/warrior-girl", "Golem": "troop/golem",
    "Witch": "troop/warlock", "Lava Hound": "troop/airdefenceseeker",
    "Bowler": "troop/bowler", "Ice Golem": "troop/ice-golem",
    "Headhunter": "troop/headhunter", "Apprentice Warden": "troop/apprentice-warden",
    "Druid": "troop/druid_healer", "Firecracker": "troop/firecracker",
    "Ice Minion": "troop/ice-minion", "Skeleton Barrel": "troop/skeleton-barrel",
    "Snake Barrel": "troop/snake-barrel",
    # siege machines
    "Wall Wrecker": "troop/siege-machine-ram",
    "Battle Blimp": "troop/siege-machine-flyer",
    "Stone Slammer": "troop/siege-bowler-balloon",
    "Siege Barracks": "troop/siege-machine-carrier",
    "Log Launcher": "troop/siege-log-launcher",
    "Flame Flinger": "troop/siege-catapult",
    "Battle Drill": "troop/battle-drill",
    # spells
    "Lightning Spell": "spell/lighningstorm", "Healing Spell": "spell/healingwave",
    "Rage Spell": "spell/haste", "Jump Spell": "spell/jump",
    "Freeze Spell": "spell/freeze", "Clone Spell": "spell/duplicate",
    "Invisibility Spell": "spell/invisibility", "Recall Spell": "spell/recall",
    "Revive Spell": "spell/revive", "Poison Spell": "spell/poison",
    "Earthquake Spell": "spell/earthquake", "Haste Spell": "spell/speedup",
    "Skeleton Spell": "spell/spawnskele", "Bat Spell": "spell/spawnbats",
    "Overgrowth Spell": "spell/overgrowth",
}
UNIT_ICONS = {_norm(k): f"https://coc.guide/static/imgs/{v}.png"
              for k, v in _UNIT_ICON_RAW.items()}


def fetch(path, key):
    url = API_BASE + path
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        # A real UA matters: Cloudflare blocks default Python-urllib from
        # datacenter IPs, which breaks proxy calls from cloud hosts.
        "User-Agent": "coc-clan-dashboard/1.0 (+https://github.com/gabrielborvs-sudo/coc-dashboard)",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        try:
            reason = json.loads(body).get("reason", "")
        except Exception:
            reason = body[:150].strip()   # surface non-JSON blocks (e.g. Cloudflare)
        return None, f"{e.code} {reason}"
    except Exception as e:
        return None, str(e)


def _resolve_cwl_war(lg, key):
    """During CWL, find our clan's most relevant round war.

    Prefers a live (inWar) round, then preparation, then the latest ended one.
    Returns (war_dict_normalised_to_our_side, round_number) or (None, None).
    """
    best = {"inWar": None, "preparation": None, "warEnded": None}
    rounds = lg.get("rounds", [])
    for idx in range(len(rounds) - 1, -1, -1):
        tags = [t for t in rounds[idx].get("warTags", []) if t and t != "#0"]
        for t in tags:
            w, err = fetch(f"/clanwarleagues/wars/{enc(t)}", key)
            if not w:
                continue
            sides = (w.get("clan", {}).get("tag"), w.get("opponent", {}).get("tag"))
            if CLAN_TAG not in sides:
                continue
            if w["clan"].get("tag") != CLAN_TAG:
                w["clan"], w["opponent"] = w["opponent"], w["clan"]
            w.setdefault("attacksPerMember", 1)   # CWL: one attack each
            state = w.get("state")
            if state in best and best[state] is None:
                best[state] = (w, idx + 1)
            break  # our war for this round found; move to earlier round
        if best["inWar"]:
            break
    return best["inWar"] or best["preparation"] or best["warEnded"] or (None, None)


def fetch_all(key):
    """Fetch clan, war (incl. CWL), war log, capital raids, member profiles."""
    clan, c_err = fetch(f"/clans/{enc(CLAN_TAG)}", key)
    war, w_err = fetch(f"/clans/{enc(CLAN_TAG)}/currentwar", key)

    # CWL: during league week the normal war endpoint reports notInWar
    cwl_round = None
    if (war is None) or war.get("state") == "notInWar":
        lg, _lg_err = fetch(f"/clans/{enc(CLAN_TAG)}/currentwar/leaguegroup", key)
        if lg and lg.get("rounds"):
            cw, rnd = _resolve_cwl_war(lg, key)
            if cw:
                war, w_err, cwl_round = cw, None, rnd

    warlog, wl_err = fetch(f"/clans/{enc(CLAN_TAG)}/warlog?limit=12", key)
    raids, r_err = fetch(f"/clans/{enc(CLAN_TAG)}/capitalraidseasons?limit=6", key)

    profiles = {}
    if clan:
        tags = [m["tag"] for m in clan.get("memberList", [])]

        def grab(tag):
            p, err = fetch(f"/players/{enc(tag)}", key)
            return tag, p

        with ThreadPoolExecutor(max_workers=10) as pool:
            for tag, p in pool.map(grab, tags):
                if p:
                    profiles[tag] = p
    return {"clan": clan, "c_err": c_err, "war": war, "w_err": w_err,
            "cwl_round": cwl_round, "warlog": warlog, "wl_err": wl_err,
            "raids": raids, "r_err": r_err, "profiles": profiles}


def enc(tag):
    return urllib.parse.quote(tag)


def esc(s):
    return html.escape(str(s), quote=True)


def now_str():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Manila")).strftime("%d %b %Y, %I:%M %p")
    except Exception:
        return datetime.now().strftime("%d %b %Y, %I:%M %p")


ROLE_NAMES = {"leader": "Leader", "coLeader": "Co-leader",
              "admin": "Elder", "member": "Member"}

# Max hero level reachable at each Town Hall (tune when game balance changes).
# Rush % = how far a member's heroes lag behind their own TH's caps.
HERO_CAPS = {
    7:  {"Barbarian King": 5},
    8:  {"Barbarian King": 10},
    9:  {"Barbarian King": 30, "Archer Queen": 30, "Minion Prince": 10},
    10: {"Barbarian King": 40, "Archer Queen": 40, "Minion Prince": 20},
    11: {"Barbarian King": 50, "Archer Queen": 50, "Minion Prince": 30,
         "Grand Warden": 20},
    12: {"Barbarian King": 65, "Archer Queen": 65, "Minion Prince": 40,
         "Grand Warden": 40},
    13: {"Barbarian King": 75, "Archer Queen": 75, "Minion Prince": 50,
         "Grand Warden": 50, "Royal Champion": 25},
    14: {"Barbarian King": 80, "Archer Queen": 80, "Minion Prince": 60,
         "Grand Warden": 55, "Royal Champion": 30},
    15: {"Barbarian King": 90, "Archer Queen": 90, "Minion Prince": 70,
         "Grand Warden": 65, "Royal Champion": 40},
    16: {"Barbarian King": 95, "Archer Queen": 95, "Minion Prince": 80,
         "Grand Warden": 70, "Royal Champion": 45},
    17: {"Barbarian King": 105, "Archer Queen": 105, "Minion Prince": 90,
         "Grand Warden": 80, "Royal Champion": 50, "Dragon Duke": 15},
    18: {"Barbarian King": 110, "Archer Queen": 110, "Minion Prince": 95,
         "Grand Warden": 85, "Royal Champion": 55, "Dragon Duke": 25},
}


TOP_TH = max(HERO_CAPS)

# Super troops are temporary boosts, not lab progression - excluded from rush.
SUPER_TROOPS = {"Sneaky Goblin", "Rocket Balloon", "Inferno Dragon", "Ice Hound"}

# Blacksmith level available at each TH (equipment caps); TH16+ = 9.
BS_AT_TH = {8: 1, 9: 2, 10: 3, 11: 4, 12: 5, 13: 6, 14: 7, 15: 8}

# Equipment newer than the game-data files - hero attribution fallback.
# (The live clan data refines this: anything a member has equipped maps
# to that hero automatically in _equip_hero_map.)
_EQUIP_HERO_EXTRA = {
    "spikyball": "Barbarian King", "actionfigure": "Archer Queen",
    "darkcrown": "Minion Prince", "metalpants": "Minion Prince",
    "nobleiron": "Minion Prince", "heroictorch": "Grand Warden",
    "fireheart": "Dragon Duke", "flameblower": "Dragon Duke",
}


def unit_cap(name, th, api_max):
    """Exact max level for a troop/spell/pet/siege at this TH (th_caps.py).

    TH18+ and unknown units fall back to the API's global max. 0 means the
    table says 'locked here' - callers clamp with the member's real level.
    """
    if not isinstance(th, int) or th >= TOP_TH:
        return api_max
    caps = UNIT_TH_CAPS.get(_norm(name))
    if not caps:
        return api_max
    return min(caps[min(th, len(caps)) - 1], api_max)


def equip_cap(name, th, api_max):
    """Max hero-equipment level at this TH via its Blacksmith level."""
    if not isinstance(th, int) or th >= TOP_TH:
        return api_max
    bs = BS_AT_TH.get(th, 9 if th >= 16 else 0)
    if not bs:
        return 0
    caps = EQUIP_BS_CAPS.get(_norm(name))
    if caps is None:  # item newer than the data files: generic rarity curve
        caps = EQUIP_CURVE["Epic" if api_max > 18 else "Common"]
    return min(caps[bs - 1], api_max)


def hero_cap(name, th, api_max):
    """Max hero level at this TH (exact HERO_CAPS table)."""
    cap = HERO_CAPS.get(th, {}).get(name, 0) if isinstance(th, int) else 0
    return cap or api_max


def _equip_hero_map(profiles):
    """equipment name (normalized) -> owning hero. Game data + manual extras,
    refined by whatever clan members actually have equipped right now."""
    owner = dict(EQUIP_HERO)
    owner.update(_EQUIP_HERO_EXTRA)
    for p in (profiles or {}).values():
        for h in p.get("heroes", []):
            if h.get("village") != "home":
                continue
            for e in h.get("equipment", []):
                owner[_norm(e["name"])] = h["name"]
    return owner


def _lab_completion(units, th):
    """Lab completion vs this TH's exact caps (th_caps.py, from game data)."""
    have = total = 0
    for u in units:
        if u.get("village") != "home":
            continue
        name = u.get("name", "")
        if name in SUPER_TROOPS or name.startswith("Super "):
            continue
        cap = max(1, unit_cap(name, th, u.get("maxLevel", 1)))
        have += min(u.get("level", 0), cap)
        total += cap
    return (have / total) if total else None


def rush_score(th, profile):
    """Composite rush index: 0% = maxed for this TH, 100% = untouched.

    Weights: heroes 50%, troops 35%, spells 15% - all against exact per-TH
    caps from the game's data files. Missing components redistribute weight.
    """
    if not isinstance(th, int) or not profile:
        return None
    parts = []
    caps = HERO_CAPS.get(th)
    if caps:
        levels = {h["name"]: h["level"] for h in profile.get("heroes", [])
                  if h.get("village") == "home"}
        total = sum(caps.values())
        have = sum(min(levels.get(n, 0), c) for n, c in caps.items())
        if total:
            parts.append((have / total, 0.50))
    tc = _lab_completion(profile.get("troops", []), th)
    if tc is not None:
        parts.append((tc, 0.35))
    sc = _lab_completion(profile.get("spells", []), th)
    if sc is not None:
        parts.append((sc, 0.15))
    if not parts:
        return None
    completion = sum(c * w for c, w in parts) / sum(w for _, w in parts)
    return max(0, min(100, 100 - round(completion * 100)))


def parse_coc_time(s):
    return datetime.strptime(s, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc)


def th_avatar(th, size=30, cls=""):
    """Town-hall icon with a graceful fallback chip when the CDN is missing."""
    if not isinstance(th, int):
        return f'<span class="thchip" style="display:inline-grid">?</span>'
    src = TH_ICON.format(n=th)
    cls = f" {cls}" if cls else ""
    return (f'<span class="th-av{cls}" style="--s:{size}px">'
            f'<img src="{src}" alt="TH{th}" loading="lazy" '
            f'onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'inline-grid\'">'
            f'<span class="thchip" style="display:none">{th}</span></span>')


# --------------------------------- style ------------------------------------
CSS = """
  :root {
    --page:#0e1116; --card:#151a22; --card-2:#1b212b; --card-3:#222a36;
    --ink:#e8ecf2; --ink-2:#9aa5b4; --muted:#5d6878;
    --line:rgba(255,255,255,.07); --line-2:rgba(255,255,255,.13);
    --accent:#e8a33d; --accent-dim:rgba(232,163,61,.14);
    --blue:#4d8fe0; --aqua:#2ea583; --violet:#8f87d8; --red:#e06060;
    --magenta:#c76490; --orange:#dd7b45; --green:#48b865; --gold:#e8a33d;
    --display:'Space Grotesk','Segoe UI',system-ui,sans-serif;
    --body:'Inter',system-ui,-apple-system,'Segoe UI',sans-serif;
  }
  * { box-sizing:border-box; margin:0; }
  [hidden] { display:none !important; }
  html { color-scheme:dark; }
  body {
    background:var(--page); color:var(--ink);
    background-image:
      radial-gradient(1000px 400px at 20% -140px, rgba(77,143,224,.07), transparent 65%),
      radial-gradient(1000px 400px at 85% -140px, rgba(232,163,61,.05), transparent 65%);
    background-attachment:fixed;
    font:14.5px/1.55 var(--body);
    padding:28px 20px 48px; max-width:940px; margin:0 auto;
    -webkit-font-smoothing:antialiased;
  }

  /* ---------- banner ---------- */
  .banner {
    display:flex; align-items:center; gap:20px;
    border:1px solid var(--line-2); border-radius:16px;
    padding:22px 26px; margin-bottom:18px;
    background:linear-gradient(140deg, var(--card-2), var(--card) 60%);
    box-shadow:0 16px 44px rgba(0,0,0,.4);
  }
  .banner .crest { width:64px; height:64px; flex:none;
                   filter:drop-shadow(0 6px 14px rgba(0,0,0,.5)); }
  .who { min-width:0; }
  .kicker { font-size:10.5px; letter-spacing:2.4px; text-transform:uppercase;
            color:var(--accent); font-weight:600; margin-bottom:3px; }
  .who h1 { font-family:var(--display); font-size:30px; font-weight:700;
            letter-spacing:-.4px; line-height:1.1; }
  /* slow golden shine sweeping the clan name every ~9s */
  @supports (-webkit-background-clip:text) {
    .who h1 {
      background:linear-gradient(100deg, var(--ink) 0%, var(--ink) 42%,
                 #f2d491 50%, var(--ink) 58%, var(--ink) 100%);
      background-size:230% 100%; background-position:120% 0;
      -webkit-background-clip:text; background-clip:text;
      -webkit-text-fill-color:transparent;
      animation:titleshine 9s ease-in-out infinite;
    }
  }
  @keyframes titleshine {
    0%, 55% { background-position:120% 0; }
    90%, 100% { background-position:-70% 0; }
  }
  .who .tag { color:var(--muted); font-size:12.5px; margin-top:3px;
              font-variant-numeric:tabular-nums; }
  .banner-right { margin-left:auto; text-align:right; flex:none; }
  .lvl-chip { display:inline-block; font-family:var(--display); font-weight:700;
              font-size:13px; letter-spacing:.5px; color:var(--accent);
              border:1px solid rgba(232,163,61,.35); background:var(--accent-dim);
              border-radius:8px; padding:5px 12px; }
  .updated { color:var(--muted); font-size:11.5px; margin-top:8px; }
  .live-dot { display:inline-block; width:7px; height:7px; border-radius:50%;
              background:var(--green); margin-right:5px;
              animation:pulse 2.4s infinite; }
  @keyframes pulse { 50% { opacity:.3; } }

  /* ---------- tabs ---------- */
  nav {
    position:sticky; top:12px; z-index:5;
    display:flex; gap:2px; width:fit-content; margin:0 auto 20px;
    background:rgba(17,21,28,.92); backdrop-filter:blur(14px);
    border:1px solid var(--line-2); border-radius:12px; padding:4px;
    box-shadow:0 10px 28px rgba(0,0,0,.4);
  }
  .tab {
    position:relative; border:none; background:transparent; color:var(--ink-2);
    font:600 13.5px var(--display); letter-spacing:.3px;
    padding:10px 20px 12px; border-radius:9px; cursor:pointer;
    transition:color .15s, background .15s;
  }
  .tab:hover { color:var(--ink); background:var(--card-2); }
  .tab.active { color:var(--ink); background:var(--card-3); }
  .tab.active::after {
    content:''; position:absolute; left:20px; right:20px; bottom:6px;
    height:2px; border-radius:1px; background:var(--accent);
  }

  .panel { display:none; }
  .panel.active { display:block; animation:fadeUp .22s ease; }
  @keyframes fadeUp { from { opacity:0; transform:translateY(6px); } to { opacity:1; } }

  .card {
    background:var(--card); border:1px solid var(--line);
    border-radius:14px; padding:20px 22px; margin-bottom:14px;
    box-shadow:0 10px 30px rgba(0,0,0,.3);
  }
  h2 {
    font-family:var(--display); font-size:12px; font-weight:600;
    letter-spacing:1.8px; text-transform:uppercase; color:var(--ink-2);
    margin-bottom:14px; display:flex; align-items:center; gap:9px;
  }
  h2::before { content:''; width:5px; height:14px; border-radius:2px;
               background:var(--accent); }
  .quiet { color:var(--muted); font-size:12.5px; }

  /* ---------- stat tiles ---------- */
  .tiles { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }
  @media (max-width:640px) { .tiles { grid-template-columns:repeat(2,1fr); } }
  /* staggered entrance on first load - subtle, one pass */
  .tiles > .tile, .mvps > .mvp { animation:fadeUp .45s ease-out backwards; }
  .tiles > .tile:nth-child(2), .mvps > .mvp:nth-child(2) { animation-delay:.06s; }
  .tiles > .tile:nth-child(3) { animation-delay:.12s; }
  .tiles > .tile:nth-child(4) { animation-delay:.18s; }
  .tiles > .tile:nth-child(5) { animation-delay:.24s; }
  .tiles > .tile:nth-child(6) { animation-delay:.30s; }
  .tile {
    position:relative; overflow:hidden;
    background:var(--card-2); border:1px solid var(--line);
    border-radius:12px; padding:14px 16px;
    transition:border-color .15s, transform .15s;
  }
  .tile:hover { border-color:color-mix(in srgb, var(--tint) 45%, transparent);
                transform:translateY(-2px); }

  /* ---------- themed overview tiles ---------- */
  .tile-gold { border-color:rgba(232,194,90,.28); }
  .tile-gold::before { content:''; position:absolute; top:0; bottom:0; left:-70%;
    width:45%; transform:skewX(-18deg); pointer-events:none;
    background:linear-gradient(90deg, transparent, rgba(232,194,90,.10), transparent);
    animation:sheen 7.5s ease-in-out infinite .8s; }
  .tile-gold .tile-value { color:#e8c25a;
    text-shadow:0 0 16px rgba(232,194,90,.25); }
  .tile-war { border-color:rgba(224,96,96,.26); }
  .tile-war .tile-value { color:#e57272;
    text-shadow:0 0 14px rgba(224,96,96,.25); }
  .tile-mark { position:absolute; right:6px; bottom:-4px; font-size:46px;
    opacity:.08; pointer-events:none; transform:rotate(-14deg);
    filter:grayscale(1) brightness(1.6); }
  .tile-streak { border-color:rgba(255,100,70,.32); }
  .tile-streak::before { content:''; position:absolute; right:-30px; bottom:-46px;
    width:140px; height:100px; border-radius:50%; pointer-events:none;
    background:radial-gradient(closest-side, rgba(255,90,50,.24), transparent 72%);
    animation:smolder 3.2s ease-in-out infinite; }
  .tile-streak .tile-value { color:#ff6b57;
    text-shadow:0 0 16px rgba(255,90,50,.35); }
  .tile-streak .tile-value::before { content:'\\01F525'; display:inline-block;
    font-size:.68em; margin-right:7px; vertical-align:3px;
    transform-origin:50% 90%; animation:flick 1.4s infinite ease-in-out;
    filter:drop-shadow(0 0 5px rgba(255,140,0,.65)); }
  .tile-league .tile-value.txt { color:var(--tint);
    text-shadow:0 0 14px color-mix(in srgb, var(--tint) 35%, transparent); }
  .tile-league { border-color:color-mix(in srgb, var(--tint) 30%, transparent); }
  .tile-label { font-size:10.5px; color:var(--ink-2); letter-spacing:1.4px;
                text-transform:uppercase; font-weight:600;
                display:flex; align-items:center; gap:7px; }
  .tile-label::before { content:''; width:7px; height:7px; border-radius:2px;
                        background:var(--tint); flex:none; }
  .tile-value { font-family:var(--display); font-weight:700; font-size:26px;
                letter-spacing:-.5px; margin-top:6px;
                font-variant-numeric:tabular-nums; }
  .tile-value.txt { font-size:16px; padding-top:5px; line-height:1.25; }
  .tile-sub { font-size:11px; color:var(--muted); margin-top:1px; }

  /* ---------- MVP cards ---------- */
  .mvps { display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:10px; }
  .mvp { display:flex; align-items:center; gap:13px;
         background:var(--card-2); border:1px solid var(--line);
         border-radius:12px; padding:13px 16px; }
  .mvp .t { font-size:10px; letter-spacing:1.6px; text-transform:uppercase;
            color:var(--muted); font-weight:600; }
  .mvp .n { font-family:var(--display); font-weight:700; font-size:15.5px; }
  .mvp .v { margin-left:auto; font-family:var(--display); font-weight:700;
            font-size:19px; color:var(--accent);
            font-variant-numeric:tabular-nums; }

  /* ---------- MVP effects: smolder for the donator, storm for the leader --- */
  .mvp { position:relative; overflow:hidden;
         transition:border-color .15s, transform .15s; }
  .mvp:hover { transform:translateY(-2px); }
  .mvp .vic { display:inline-block; margin-right:7px; font-size:14px; }
  .mvp-fire { border-color:rgba(255,130,70,.30); }
  .mvp-fire::before { content:''; position:absolute; right:-34px; bottom:-52px;
    width:160px; height:115px; border-radius:50%; pointer-events:none;
    background:radial-gradient(closest-side, rgba(255,118,45,.26), transparent 72%);
    animation:smolder 3.4s ease-in-out infinite; }
  .mvp-fire:hover { border-color:rgba(255,130,70,.55); }
  .mvp-fire .v { color:#ff9757; text-shadow:0 0 16px rgba(255,120,45,.35); }
  .mvp-fire .vic { transform-origin:50% 90%;
    animation:flick 1.4s infinite ease-in-out;
    filter:drop-shadow(0 0 5px rgba(255,140,0,.65)); }
  .ember { position:absolute; bottom:-4px; width:4px; height:4px;
    border-radius:50%; background:#ffb066; opacity:0; pointer-events:none;
    box-shadow:0 0 6px 1px rgba(255,150,70,.55);
    animation:ember 3.8s infinite ease-out; }
  .ember.e1 { right:36px; }
  .ember.e2 { right:70px; width:3px; height:3px; animation-delay:1.4s; }
  .ember.e3 { right:104px; width:3px; height:3px; animation-delay:2.5s; }
  @keyframes smolder { 50% { opacity:.5; transform:scale(1.14); } }
  @keyframes ember {
    0%   { opacity:0; transform:translateY(0) scale(1); }
    12%  { opacity:.95; }
    100% { opacity:0; transform:translateY(-58px) translateX(-9px) scale(.35); }
  }
  .mvp-bolt { border-color:rgba(232,194,90,.30); }
  .mvp-bolt::before { content:''; position:absolute; top:0; bottom:0; left:-70%;
    width:45%; transform:skewX(-18deg); pointer-events:none;
    background:linear-gradient(90deg, transparent, rgba(232,194,90,.11), transparent);
    animation:sheen 6.5s ease-in-out infinite; }
  .mvp-bolt:hover { border-color:rgba(232,194,90,.55); }
  .mvp-bolt .v { color:#e8c25a; text-shadow:0 0 16px rgba(232,194,90,.3); }
  .mvp-bolt .vic { animation:boltflick 4.6s infinite;
    filter:drop-shadow(0 0 6px rgba(255,224,130,.75)); }
  @keyframes sheen { 0%, 55% { left:-70%; } 78%, 100% { left:135%; } }
  @keyframes boltflick {
    0%, 100% { opacity:1; }
    6%  { opacity:.3; }  9%  { opacity:1; } 12% { opacity:.45; } 15% { opacity:1; }
    52% { opacity:1; }  55% { opacity:.35; } 58% { opacity:1; }
  }

  /* ---------- TH avatar ---------- */
  .th-av { display:inline-block; width:var(--s,30px); height:var(--s,30px);
           margin-right:10px; vertical-align:middle; }
  .th-av img { width:100%; height:100%; object-fit:contain;
               filter:drop-shadow(0 2px 5px rgba(0,0,0,.5)); }
  .thchip { place-items:center; width:var(--s,30px); height:var(--s,30px);
            border-radius:8px; background:var(--card-3);
            border:1px solid var(--line-2); color:var(--accent);
            font-family:var(--display); font-weight:700; font-size:12px;
            vertical-align:middle; margin-right:0; }

  /* ---------- heroes / meters ---------- */
  .hero-row { display:grid; grid-template-columns:34px 175px 1fr 84px;
              align-items:center; gap:14px; padding:10px 0; }
  .hero-row + .hero-row { border-top:1px solid var(--line); }
  .hero-name { font-size:13.5px; font-weight:600; }
  .hero-ico { width:34px; height:34px; object-fit:contain; justify-self:center;
              filter:drop-shadow(0 2px 5px rgba(0,0,0,.45)); }
  .hero-fb { display:inline-grid; place-items:center; width:34px; height:34px;
             border-radius:50%; font:700 12px var(--display); color:var(--ink-2);
             background:color-mix(in srgb, var(--hue) 22%, transparent);
             border:1px solid color-mix(in srgb, var(--hue) 40%, transparent); }
  .hero-eq { grid-column:1 / -1; display:flex; flex-wrap:wrap; gap:5px;
             padding:1px 0 3px 48px; }
  .eqchip { font-size:11px; padding:3px 9px; border-radius:6px; line-height:1.5;
            border:1px solid var(--line-2); color:var(--ink-2); white-space:nowrap; }
  .eqchip b { color:var(--ink); font-weight:600; }
  .eqchip.eq-on { border-color:color-mix(in srgb, var(--hue) 55%, transparent);
                  background:color-mix(in srgb, var(--hue) 10%, transparent);
                  color:var(--ink); }
  .eqchip.eq-on::before { content:'\\2605'; margin-right:5px; font-size:9.5px;
                          color:var(--hue); }
  .eqchip.eq-max b { color:var(--green); }
  .hero-lvl { font-size:12.5px; text-align:right; color:var(--ink-2);
              font-variant-numeric:tabular-nums; }
  .hero-lvl b { color:var(--ink); font-family:var(--display); font-size:15px; }
  .meter { height:8px; background:rgba(255,255,255,.06); border-radius:4px;
           overflow:hidden; }
  .meter-fill { height:100%; border-radius:4px; background:var(--hue);
                animation:grow .8s cubic-bezier(.2,.7,.3,1); }
  .meter-max { --hue:var(--green) !important; }
  @keyframes grow { from { width:0; } }

  /* ---------- war ---------- */
  /* overview war-at-a-glance card, tinted by state */
  .card.glance-live { border-color:rgba(224,96,96,.5);
    background:linear-gradient(165deg, rgba(224,96,96,.15), var(--card) 62%); }
  .card.glance-prep { border-color:rgba(72,184,101,.5);
    background:linear-gradient(165deg, rgba(72,184,101,.14), var(--card) 62%); }
  .card.glance-won { border-color:rgba(232,194,90,.55);
    background:linear-gradient(165deg, rgba(232,194,90,.14), var(--card) 62%); }
  .card.glance-lost { border-color:rgba(148,158,172,.45);
    background:linear-gradient(165deg, rgba(148,158,172,.10), var(--card) 62%); }
  /* war result tint - whole card goes green on victory, red on defeat */
  .card.war-won { border-color:rgba(72,184,101,.45);
    background:linear-gradient(165deg, rgba(72,184,101,.13), var(--card) 60%); }
  .card.war-lost { border-color:rgba(224,96,96,.45);
    background:linear-gradient(165deg, rgba(224,96,96,.13), var(--card) 60%); }
  .tab.tab-won { color:var(--green); }
  .tab.tab-lost { color:var(--red); }
  .tab.active.tab-won { color:var(--green); }
  .tab.active.tab-lost { color:var(--red); }
  .tab.active.tab-won::after { background:var(--green); }
  .tab.active.tab-lost::after { background:var(--red); }

  .war-head { display:flex; align-items:center; gap:12px; margin-bottom:16px; flex-wrap:wrap; }
  .war-badge { font-family:var(--display); font-weight:700; font-size:11.5px;
               letter-spacing:1.5px; text-transform:uppercase;
               padding:6px 14px; border-radius:8px;
               border:1px solid var(--line-2); background:var(--card-2); }
  .war-badge-live { color:var(--red); border-color:rgba(224,96,96,.45);
                    animation:livepulse 2.4s ease-in-out infinite; }
  @keyframes livepulse {
    50% { box-shadow:0 0 14px rgba(224,96,96,.30);
          border-color:rgba(224,96,96,.75); }
  }
  .war-badge-won  { color:var(--green); }
  .war-badge-lost { color:var(--red); }
  .war-badge-prep { color:var(--accent); }
  .war-grid { display:grid; grid-template-columns:1fr auto 1fr; gap:14px;
              align-items:stretch; text-align:center; }
  .war-side { border-radius:12px; padding:18px 14px; background:var(--card-2);
              border:1px solid var(--line); border-top:3px solid var(--blue); }
  .war-side.them { border-top-color:var(--red); }
  .war-side img { width:46px; height:46px; filter:drop-shadow(0 3px 8px rgba(0,0,0,.5)); }
  .war-clan { font-family:var(--display); font-weight:700; font-size:15px; margin-top:8px; }
  .war-stars { font-family:var(--display); font-weight:700; font-size:38px;
               letter-spacing:-1px; margin:4px 0 2px;
               font-variant-numeric:tabular-nums; }
  .war-stars .st-ico { color:var(--accent); font-size:26px; vertical-align:6px; }
  .war-vs { align-self:center; font-family:var(--display); font-weight:700;
            font-size:12px; letter-spacing:1px; color:var(--muted);
            border:1px solid var(--line-2); border-radius:8px; padding:8px 11px; }
  .destr { height:6px; background:rgba(255,255,255,.06); border-radius:3px;
           margin:10px 22px 8px; overflow:hidden; }
  .destr div { height:100%; border-radius:3px; background:var(--blue);
               animation:grow .8s cubic-bezier(.2,.7,.3,1); }
  .war-side.them .destr div { background:var(--red); }
  .war-pending { margin-top:14px; font-size:13px; color:var(--ink-2);
                 background:var(--accent-dim); border:1px solid rgba(232,163,61,.3);
                 border-radius:10px; padding:11px 14px; }
  .glance-top { display:flex; align-items:center; gap:10px; flex-wrap:wrap;
                margin-bottom:10px; }
  .glance-score { font-size:14.5px; line-height:1.55; }
  .glance-score b { font-family:var(--display); font-size:19px; }
  .g-name { font-weight:600; }
  .st { color:var(--accent); letter-spacing:.5px; }
  .st-off { color:rgba(255,255,255,.12); letter-spacing:.5px; }
  .att { font-variant-numeric:tabular-nums; white-space:nowrap; }
  .att-empty { color:var(--muted); }
  .att-pct { color:var(--ink-2); font-size:12px; margin-left:4px; }
  .att-target { color:var(--muted); font-size:10.5px; }
  /* roster member cell: TH icon + name (works for both team and enemy) */
  .rmem { display:flex; align-items:center; gap:8px; }
  .rmem .th-av { margin-right:0; flex:none; }
  .ritem > .th-av { margin-right:0; flex:none; margin-top:2px; }
  .th-av.th-sm { --s:15px !important; margin-right:3px;
                 vertical-align:-3px; }
  /* roster row highlight by completion */
  .roster tr.rst-full td { background:rgba(72,184,101,.07); }
  .roster tr.rst-full td:first-child { box-shadow:inset 3px 0 0 var(--green); }
  .roster tr.rst-part td { background:rgba(232,163,61,.07); }
  .roster tr.rst-part td:first-child { box-shadow:inset 3px 0 0 var(--accent); }
  .roster tr.rst-none td { background:rgba(224,96,96,.08); }
  .roster tr.rst-none td:first-child { box-shadow:inset 3px 0 0 var(--red); }
  /* attack cell stars colored by result */
  .att.st3 .st { color:var(--green); text-shadow:0 0 8px rgba(72,184,101,.45); }
  .att.st3 .att-pct { color:var(--green); }
  .att.st2 .st { color:var(--gold); }
  .att.st1 .st { color:var(--orange); }
  .att.st0 .st-off { color:rgba(224,96,96,.5); }
  .att.st0 .att-pct { color:var(--red); }
  .status-done { color:var(--green); font-weight:600; font-size:12px; }
  .status-left { color:var(--red); font-weight:600; font-size:12px; }
  .status-missed { color:var(--red); font-size:12px; }
  .status-wait { color:var(--muted); font-size:12px; }
  .empty { text-align:center; padding:32px 0; color:var(--muted); }
  .empty .big { font-size:30px; opacity:.7; }

  /* ---------- members ---------- */
  .chips { display:flex; flex-wrap:wrap; gap:7px; margin-bottom:14px; }
  .chip { font-size:12px; color:var(--ink-2); background:var(--card-2);
          border:1px solid var(--line); border-radius:7px; padding:4px 11px; }
  .chip-c { border-color:color-mix(in srgb, var(--tint) 35%, transparent);
            background:color-mix(in srgb, var(--tint) 9%, transparent);
            color:var(--ink); }
  .chip .ci { margin-right:6px; font-size:11.5px; }
  .sortbar { display:flex; align-items:center; gap:6px; flex-wrap:wrap;
             margin-bottom:14px; }
  .sortbar .lbl { font-size:11px; letter-spacing:1.4px; text-transform:uppercase;
                  color:var(--muted); font-weight:600; margin-right:4px; }
  .sortbtn { --tint:var(--accent);
             border:1px solid var(--line-2); background:transparent;
             color:var(--ink-2); font:600 12.5px var(--body);
             border-radius:8px; padding:6px 13px; cursor:pointer;
             transition:all .15s; }
  .sortbtn .si { margin-right:5px; font-size:11.5px;
                 filter:grayscale(.55) opacity(.75); }
  .sortbtn:hover { color:var(--ink); background:var(--card-2); }
  .sortbtn:hover .si, .sortbtn.active .si { filter:none; }
  .sortbtn.active { color:var(--tint);
                    border-color:color-mix(in srgb, var(--tint) 50%, transparent);
                    background:color-mix(in srgb, var(--tint) 13%, transparent); }
  .sortbtn .dir { font-weight:700; }
  /* war roster team/enemy switch */
  .roster-head { display:flex; align-items:center; justify-content:space-between;
                 gap:8px 14px; flex-wrap:wrap; }
  .rseg { display:flex; gap:6px; margin-bottom:14px; }
  .rseg .sortbtn { max-width:44vw; overflow:hidden; text-overflow:ellipsis;
                   white-space:nowrap; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th { text-align:left; color:var(--muted); font-weight:600; font-size:10.5px;
       text-transform:uppercase; letter-spacing:1.2px;
       border-bottom:1px solid var(--line-2); padding:9px 8px; }
  td { padding:9px 8px; border-bottom:1px solid var(--line); }
  tr:last-child td { border-bottom:none; }
  #members-table tbody tr { cursor:pointer; }
  #members-table tbody tr:hover { background:var(--card-2); }
  .roster tbody tr:hover { background:var(--card-2); }
  .num { text-align:right; font-variant-numeric:tabular-nums; }
  th.num { text-align:right; }
  /* member names never wrap - the table scrolls sideways instead */
  #members-table td:nth-child(2), .roster td:nth-child(2) { white-space:nowrap; }
  .rk { font-family:var(--display); font-weight:700; color:var(--muted); }
  .rk.rk1 { color:#e8c25a; } .rk.rk2 { color:#b9c2cf; } .rk.rk3 { color:#c98d5f; }
  .role-chip { font-size:10.5px; padding:3px 9px; border-radius:6px;
               background:transparent; color:var(--ink-2); white-space:nowrap;
               border:1px solid var(--line-2); letter-spacing:.3px; }
  .role-leader   { color:#e8c25a; border-color:rgba(232,194,90,.45); }
  .role-coLeader { color:var(--violet); border-color:rgba(143,135,216,.45); }
  .role-admin    { color:var(--aqua); border-color:rgba(46,165,131,.45); }
  .cellbar { height:2px; margin-top:5px; }
  .cellbar div { height:100%; background:var(--accent); opacity:.65;
                 border-radius:1px; animation:grow .8s ease-out; }
  .fire { white-space:nowrap; }
  .fire .fl { display:inline-block; font-size:11px; margin-right:4px;
              transform-origin:50% 90%;
              animation:flick 1.4s infinite ease-in-out;
              filter:drop-shadow(0 0 4px rgba(255,140,0,.6)); }
  .fire-1 .val { color:#ffb066; font-weight:600; }   /* light orange */
  .fire-2 .val { color:#ff8a70; font-weight:600; }   /* light red */
  .fire-3 .val { color:#ff5252; font-weight:700;     /* red - top tier */
                 animation:burn 1.6s infinite ease-in-out; }
  @keyframes burn {
    0%, 100% { filter:drop-shadow(0 0 3px rgba(255,80,60,.5)); }
    50% { filter:drop-shadow(0 0 8px rgba(255,80,60,.9)) brightness(1.12); }
  }
  @keyframes flick {
    0%, 100% { transform:scale(1); }
    50% { transform:scale(1.2) rotate(-4deg); filter:brightness(1.25); }
  }

  /* ---------- mobile member cards ---------- */
  .mlist { display:none; }
  .mitem { display:flex; align-items:center; gap:11px; padding:11px 2px;
           border-bottom:1px solid var(--line); cursor:pointer; }
  .mitem:last-child { border-bottom:none; }
  .mitem:active { background:var(--card-2); }
  .mitem > .rk { width:20px; text-align:right; flex:none; font-size:12px; }
  .mitem .th-av { margin-right:0; flex:none; }
  .mi-main { min-width:0; flex:1; }
  .mi-name { font-weight:600; font-size:14px; white-space:nowrap;
             overflow:hidden; text-overflow:ellipsis; }
  .mi-sub { color:var(--muted); font-size:10.5px; margin-top:3px;
            white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .mi-sub .role-chip { font-size:9px; padding:1px 6px; margin-right:2px; }
  .mi-right { text-align:right; flex:none; }
  .mi-don { font-size:13.5px; font-variant-numeric:tabular-nums; font-weight:600; }
  .mi-right .mi-sub { text-align:right; }

  /* ---------- member detail ---------- */
  #member-detail { display:none; }
  .detail-head { display:flex; align-items:center; gap:14px; margin-bottom:16px; }
  .detail-head .n { font-family:var(--display); font-weight:700; font-size:21px;
                    letter-spacing:-.3px; }
  .detail-head .quiet { font-size:12px; }
  .detail-close {
    margin-left:auto; border:1px solid var(--line-2); background:transparent;
    color:var(--ink-2); border-radius:8px; padding:7px 13px; cursor:pointer;
    font:600 12px var(--body);
  }
  .detail-close:hover { color:var(--ink); background:var(--card-2); }
  .detail-tiles { display:grid; grid-template-columns:repeat(3,1fr);
                  gap:9px; margin-bottom:16px; }
  .unit-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(128px,1fr));
               gap:6px; }
  .unit { display:flex; align-items:center; gap:7px;
          background:var(--card-2); border:1px solid var(--line);
          border-radius:8px; padding:5px 9px; font-size:11.5px; }
  .unit img.ui { width:22px; height:22px; object-fit:contain; flex:none;
                 filter:drop-shadow(0 1px 3px rgba(0,0,0,.5)); }
  .unit .un { white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
              color:var(--ink-2); flex:1; }
  .unit .ul { font-variant-numeric:tabular-nums; color:var(--ink); flex:none; }
  .unit.maxed { border-color:rgba(72,184,101,.35); }
  .unit.maxed .ul { color:var(--green); font-weight:600; }
  @media (max-width:640px) { .detail-tiles { grid-template-columns:repeat(2,1fr); } }

  footer { color:var(--muted); font-size:11.5px; margin-top:18px; text-align:center; }
  code { background:var(--card-2); padding:1px 6px; border-radius:5px; font-size:11px; }

  /* ---------- info button & modal ---------- */
  .info-fab { position:fixed; right:18px; bottom:18px; z-index:20;
              width:42px; height:42px; border-radius:50%;
              border:1px solid var(--line-2); background:var(--card-3);
              color:var(--accent); font:700 17px var(--display); cursor:pointer;
              box-shadow:0 8px 24px rgba(0,0,0,.5); }
  .info-fab:hover { background:var(--accent-dim); }
  .im { cursor:pointer; color:var(--muted); font-size:11px; }
  .im:hover { color:var(--accent); }
  .h2-info { margin-left:auto; font-size:15px; line-height:1; }
  .modal-back { position:fixed; inset:0; z-index:30; background:rgba(5,8,12,.72);
                backdrop-filter:blur(4px); display:flex; align-items:center;
                justify-content:center; padding:18px; }
  .modal { background:var(--card); border:1px solid var(--line-2);
           border-radius:16px; max-width:620px; width:100%; max-height:84vh;
           overflow-y:auto; padding:22px 26px 26px;
           box-shadow:0 24px 70px rgba(0,0,0,.6); }
  .modal-head { display:flex; align-items:center; justify-content:space-between; }
  .modal-head h2 { margin:0; }
  .modal h3 { font-family:var(--display); font-size:12.5px; letter-spacing:1.4px;
              text-transform:uppercase; color:var(--accent); margin:20px 0 6px; }
  .modal p, .modal li { font-size:13.5px; color:var(--ink-2); line-height:1.6; }
  .modal ul { margin:4px 0 0 18px; padding:0; }

  /* ---------- install button ---------- */
  .install-btn { margin-top:8px; border:1px solid rgba(232,163,61,.45);
                 background:var(--accent-dim); color:var(--accent);
                 border-radius:8px; padding:6px 12px; cursor:pointer;
                 font:600 12px var(--body); }
  .install-btn:hover { background:rgba(232,163,61,.22); }
  .ios-tip { display:flex; align-items:center; gap:12px;
             background:var(--card-2); border:1px solid var(--line-2);
             border-radius:12px; padding:12px 16px; margin-bottom:16px;
             font-size:13px; color:var(--ink-2); }

  /* ---------- rush-o-meter ---------- */
  .rz-ok { color:var(--green); font-weight:600; }
  .rz-mid { color:var(--accent); font-weight:600; }
  .rz-bad { color:var(--red); font-weight:600; }

  /* ---------- compare ---------- */
  .cmp-bar { display:flex; align-items:center; gap:10px; margin-bottom:14px; }
  .cmpsel { flex:1; min-width:0; background:var(--card-2); color:var(--ink);
            border:1px solid var(--line-2); border-radius:9px; padding:9px 10px;
            font:600 13px var(--body); cursor:pointer; }
  .cmp-vs { font-family:var(--display); font-weight:700; font-size:11px;
            letter-spacing:1px; color:var(--muted);
            border:1px solid var(--line-2); border-radius:7px; padding:6px 9px; }
  .cmp-table { table-layout:fixed; width:100%; }
  .cmp-table th:first-child, .cmp-table .cl { width:30%; }
  .cmp-table .cl { color:var(--ink-2); font-size:12px;
                   overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .cmp-table .cv { text-align:center; font-variant-numeric:tabular-nums; }
  .cmp-h { text-align:center; padding-bottom:10px; }
  .cmp-h .th-av { display:block; margin:0 auto 5px; }
  .cmp-hname { font-size:10.5px; letter-spacing:.6px; color:var(--ink);
               white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .cmp-win { color:var(--green); font-weight:700; }

  /* ---------- history & capital ---------- */
  .res { font-family:var(--display); font-weight:700; font-size:10.5px;
         letter-spacing:1px; padding:3px 9px; border-radius:6px;
         border:1px solid var(--line-2); }
  .res-w { color:var(--green); border-color:rgba(72,184,101,.45); }
  .res-l { color:var(--red); border-color:rgba(224,96,96,.45); }
  .res-t { color:var(--muted); }
  .wl-opp { white-space:nowrap; }
  /* war history rows washed by result */
  tr.hr-win td { background:rgba(72,184,101,.10); }
  tr.hr-win td:first-child { box-shadow:inset 3px 0 0 var(--green); }
  tr.hr-lose td { background:rgba(224,96,96,.11); }
  tr.hr-lose td:first-child { box-shadow:inset 3px 0 0 var(--red); }
  .hitem.hr-win { background:rgba(72,184,101,.09);
                  box-shadow:inset 3px 0 0 var(--green); }
  .hitem.hr-lose { background:rgba(224,96,96,.10);
                   box-shadow:inset 3px 0 0 var(--red); }
  .wl-badge { width:22px; height:22px; vertical-align:middle; margin-right:8px;
              filter:drop-shadow(0 2px 4px rgba(0,0,0,.5)); }
  .c-green { color:var(--green); } .c-red { color:var(--red); }

  /* ---------- wide tables scroll inside their card ---------- */
  .table-scroll { overflow-x:auto; -webkit-overflow-scrolling:touch;
                  margin:0 -22px; padding:0 22px; }

  /* ---------- mobile ---------- */
  @media (max-width:700px) {
    body { padding:14px 12px 40px; font-size:14px; }
    .banner { padding:16px 16px 12px; gap:13px; border-radius:14px; flex-wrap:wrap; }
    .banner .crest { width:46px; height:46px; }
    .who h1 { font-size:21px; }
    .kicker { font-size:9.5px; letter-spacing:1.8px; }
    .banner-right { margin-left:0; width:100%; display:flex; align-items:center;
                    justify-content:space-between; gap:10px; text-align:left;
                    border-top:1px solid var(--line); padding-top:10px; }
    .lvl-chip { font-size:11px; padding:4px 9px; }
    .updated { font-size:10px; margin-top:0; text-align:right; }
    .install-btn { margin-top:0; }
    nav { width:100%; }
    .tab { flex:1; padding:9px 4px 11px; font-size:11.5px; letter-spacing:0; }
    .tab.active::after { left:10px; right:10px; }
    .card { padding:14px 14px; border-radius:12px; }
    .table-scroll { margin:0 -14px; padding:0 14px; }
    .tile { padding:11px 12px; }
    .tile-value { font-size:20px; }
    .tile-value.txt { font-size:13.5px; }
    .tile-label { font-size:9px; letter-spacing:1px; }
    .mvp { padding:11px 12px; }
    .mvp .n { font-size:14px; }
    .mvp .v { font-size:16px; }
    .war-grid { grid-template-columns:1fr; gap:10px; }
    .war-vs { justify-self:center; padding:5px 18px; }
    .war-stars { font-size:30px; }
    .hero-row { grid-template-columns:26px 104px 1fr 62px; gap:9px; padding:8px 0; }
    .hero-name { font-size:12px; }
    .hero-ico, .hero-fb { width:26px; height:26px; font-size:10px; }
    .hero-eq { padding-left:35px; }
    .eqchip { font-size:10px; padding:2px 7px; }
    .hero-lvl { font-size:11px; }
    .hero-lvl b { font-size:13px; }
    th, td { padding:8px 6px; }
    table { font-size:12.5px; }
    .th-av { --s:26px !important; margin-right:7px; }
    .sortbtn { padding:5px 10px; font-size:11.5px; }
    .detail-head .n { font-size:17px; }
    .detail-head .quiet { font-size:10.5px; }
  }
  /* ---------- mobile war-history cards ---------- */
  .hlist { display:none; }
  .hitem { display:flex; align-items:center; gap:11px; padding:11px 2px;
           border-bottom:1px solid var(--line); }
  .hitem:last-child { border-bottom:none; }
  .hitem .wl-badge { width:26px; height:26px; margin:0; flex:none; }
  .hi-main { flex:1; min-width:0; }
  .hi-name { font-weight:600; font-size:13.5px; white-space:nowrap;
             overflow:hidden; text-overflow:ellipsis; }
  .hi-right { text-align:right; flex:none; }
  .hi-stars { font-size:13px; font-variant-numeric:tabular-nums; }
  .hi-stars .res { margin-left:5px; font-size:9.5px; padding:2px 7px; }

  /* ---------- mobile war-roster cards ---------- */
  .rlist { display:none; }
  .ritem { display:flex; align-items:flex-start; gap:10px;
           padding:10px 4px 10px 8px; border-bottom:1px solid var(--line); }
  .ritem:last-child { border-bottom:none; }
  .ritem > .rk { width:18px; text-align:right; flex:none; font-size:12px;
                 margin-top:2px; }
  .ri-main { flex:1; min-width:0; }
  .ri-atks { display:flex; flex-wrap:wrap; gap:3px 16px; margin-top:4px;
             font-size:12px; }
  .ratt { white-space:nowrap; }
  .ri-status { flex:none; margin-top:2px; }
  .ritem.rst-full { background:rgba(72,184,101,.07);
                    box-shadow:inset 3px 0 0 var(--green); }
  .ritem.rst-part { background:rgba(232,163,61,.07);
                    box-shadow:inset 3px 0 0 var(--accent); }
  .ritem.rst-none { background:rgba(224,96,96,.08);
                    box-shadow:inset 3px 0 0 var(--red); }

  /* phones: swap wide tables for card lists - no sideways scrolling */
  @media (max-width:620px) {
    .members-desktop { display:none; }
    .mlist { display:block; }
    .hist-desktop { display:none; }
    .hlist { display:block; }
    .roster-desktop { display:none; }
    .rlist { display:block; }
  }
  /* thin, unobtrusive scrollbar for the tables that still scroll (war roster) */
  .table-scroll::-webkit-scrollbar { height:5px; }
  .table-scroll::-webkit-scrollbar-track { background:transparent; }
  .table-scroll::-webkit-scrollbar-thumb { background:var(--card-3); border-radius:3px; }
  .table-scroll { scrollbar-width:thin; scrollbar-color:var(--card-3) transparent; }

  /* accessibility: users who prefer reduced motion get a still page */
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration:.01ms !important; animation-iteration-count:1 !important;
      transition-duration:.01ms !important;
    }
  }
"""

INFO_MODAL = """
<button class="info-fab" id="info-fab" title="How this works">i</button>
<div class="modal-back" id="info-back" hidden>
  <div class="modal">
    <div class="modal-head"><h2>How this works</h2>
    <button class="detail-close" id="info-close">&#10005; close</button></div>

    <h3>Data &amp; refresh</h3>
    <p>All numbers come from the official Clash of Clans API. Supercell updates
    its data every 1&ndash;2 minutes, so the site is as close to live as anyone
    can get. The live site refreshes itself automatically; the timestamp is in
    the top-right corner.</p>

    <h3>&#128293; Donation fire</h3>
    <p>The flame marks active donators; the number's color shows the tier,
    relative to the clan's top donator this season:</p>
    <ul>
      <li><span style="color:#ff5252">Red, glowing</span> &mdash; 66%+ of the top donator</li>
      <li><span style="color:#ff8a70">Light red</span> &mdash; 33%+</li>
      <li><span style="color:#ffb066">Light orange</span> &mdash; 8%+</li>
      <li>Plain gray, no flame &mdash; below 8% or zero donated</li>
    </ul>

    <h3>Rush %</h3>
    <p>How far behind a member's <b>heroes, troops and spells</b> are compared
    to what their own Town Hall allows. 0% = fully maxed for their TH,
    100% = nothing upgraded. Weighting: heroes 50%, troops 35%, spells 15%.
    Colors: <span class="rz-ok">&le;15% fine</span> &middot;
    <span class="rz-mid">16&ndash;40% somewhat rushed</span> &middot;
    <span class="rz-bad">41%+ rushed</span>.</p>
    <p>Note: someone who just upgraded their Town Hall will spike temporarily
    &mdash; the score measures against their <i>new</i> TH's caps. All caps
    (heroes, troops, spells, equipment) are exact per-TH values taken from the
    game's own data files.</p>
    <p>In member profiles every level is shown against the max for that
    member's <b>current Town Hall</b>, not the game-wide max &mdash; so a green
    "maxed" mark means maxed <i>for their TH</i>. Equipment is listed under its
    hero; <b>&#9733;</b> marks what they have equipped right now.</p>

    <h3>War roster colors</h3>
    <ul>
      <li>Row: <span class="c-green">green</span> = all attacks used &middot;
      <span style="color:var(--accent)">amber</span> = partial &middot;
      <span class="c-red">red</span> = not attacked yet</li>
      <li>Stars per attack: <span class="c-green">3&#9733; green</span> &middot;
      <span style="color:var(--gold)">2&#9733; gold</span> &middot;
      <span style="color:var(--orange)">1&#9733; orange</span> &middot;
      <span class="c-red">0&#9733; red</span></li>
    </ul>

    <h3>Members tab</h3>
    <p>Tap any member for their full profile (heroes included). Sort buttons:
    tap once for highest-first (&darr;), tap again to flip (&uarr;). The
    Compare card at the bottom puts any two members head-to-head &mdash;
    green marks the better stat.</p>

    <h3>Install as app</h3>
    <p>On Android, use the &darr; Install button in the header. On iPhone:
    Share &rarr; Add to Home Screen. You'll get the clan badge as an app icon
    and a full-screen experience.</p>
  </div>
</div>
"""

FONT_LINKS = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;600&display=swap" rel="stylesheet">
"""

PAGE_JS = """
  const tabs = document.querySelectorAll('.tab');
  const panels = document.querySelectorAll('.panel');
  function activate(name) {
    tabs.forEach(t => t.classList.toggle('active', t.dataset.tab === name));
    panels.forEach(p => p.classList.toggle('active', p.id === 'panel-' + name));
    try { localStorage.setItem('coc-tab', name); } catch (e) {}
  }
  tabs.forEach(t => t.addEventListener('click', () => activate(t.dataset.tab)));
  let saved = null;
  try { saved = localStorage.getItem('coc-tab'); } catch (e) {}
  activate(saved && document.getElementById('panel-' + saved) ? saved : 'overview');

  document.querySelectorAll('[data-count]').forEach(el => {
    const target = parseFloat(el.dataset.count);
    if (!isFinite(target)) return;
    const dur = 850, t0 = performance.now();
    function tick(ts) {
      const p = Math.min(1, (ts - t0) / dur);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(target * eased).toLocaleString('en-US');
      if (p < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  });

  // ---------------- member sorting (click again to flip direction) ----------
  // Sorts BOTH views: the desktop table and the mobile card list.
  const sortContainers = [
    document.querySelector('#members-table tbody'),
    document.getElementById('mlist'),
  ].filter(Boolean);
  let sortKey = null, sortDir = 'desc';
  function applySort(key, dir) {
    sortKey = key; sortDir = dir;
    const sign = dir === 'desc' ? 1 : -1;
    sortContainers.forEach(c => {
      const items = Array.from(c.children).filter(el => el.dataset.tag);
      items.sort((a, b) =>
        (parseFloat(b.dataset[key] || 0) - parseFloat(a.dataset[key] || 0)) * sign);
      items.forEach((el, i) => {
        const rk = el.querySelector('.rk');
        if (rk) {
          rk.textContent = i + 1;
          rk.className = 'rk' + (i < 3 ? ' rk' + (i + 1) : '');
        }
        c.appendChild(el);
      });
    });
    document.querySelectorAll('.sortbtn').forEach(b => {
      const active = b.dataset.sort === key;
      b.classList.toggle('active', active);
      const arrow = b.querySelector('.dir');
      if (arrow) arrow.textContent = active ? (dir === 'desc' ? ' ↓' : ' ↑') : '';
    });
    try { localStorage.setItem('coc-sort', key + ':' + dir); } catch (e) {}
  }
  document.querySelectorAll('.sortbtn').forEach(b =>
    b.addEventListener('click', () => {
      const k = b.dataset.sort;
      applySort(k, k === sortKey ? (sortDir === 'desc' ? 'asc' : 'desc') : 'desc');
    }));
  let savedSort = null;
  try { savedSort = localStorage.getItem('coc-sort'); } catch (e) {}
  if (sortContainers.length) {
    const [sk, sd] = (savedSort || 'don:desc').split(':');
    applySort(sk || 'don', sd === 'asc' ? 'asc' : 'desc');
  }

  // ---------------- member detail ----------------
  const HERO_HUES = { 'Barbarian King':'--orange', 'Archer Queen':'--magenta',
    'Grand Warden':'--gold', 'Royal Champion':'--blue',
    'Minion Prince':'--violet', 'Dragon Duke':'--red' };
  const HERO_ICONS = { 'Barbarian King':'barbarian-king',
    'Archer Queen':'archer-queen', 'Grand Warden':'grand-warden',
    'Royal Champion':'royal-champion', 'Minion Prince':'minion-hero' };
  const heroIconUrl = s => `https://coc.guide/static/imgs/hero/${s}.png`;
  const escj = s => String(s).replace(/[&<>"]/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const fmt = n => Number(n).toLocaleString('en-US');
  const thImg = (th, size) =>
    `<span class="th-av" style="--s:${size}px">` +
    `<img src="https://www.clash.ninja/images/entities/1_${th}.png" alt="TH${th}" ` +
    `onerror="this.style.display='none';this.nextElementSibling.style.display='inline-grid'">` +
    `<span class="thchip" style="display:none">${th}</span></span>`;

  function tile(val, label, sub, tint) {
    return `<div class="tile" style="--tint:var(${tint})">` +
      `<div class="tile-label">${label}</div>` +
      `<div class="tile-value">${val}</div>` +
      `<div class="tile-sub">${sub}</div></div>`;
  }

  function showMember(tag) {
    const m = MEMBERS[tag];
    const box = document.getElementById('member-detail');
    if (!m || !box) return;
    const ratio = m.received ? (m.donations / m.received).toFixed(1) + '&times;' : '&mdash;';
    let heroesHtml = '';
    (m.heroes || []).forEach(h => {
      const hue = h.level == null ? '--muted' : (HERO_HUES[h.name] || '--blue');
      const slug = HERO_ICONS[h.name];
      const initials = h.name.split(' ').map(w => w[0]).join('').slice(0, 2);
      const icon = slug
        ? `<img class="hero-ico" src="${heroIconUrl(slug)}" alt="" loading="lazy"` +
          ` onerror="this.style.visibility='hidden'">`
        : `<span class="hero-fb">${escj(initials)}</span>`;
      let eq = '';
      (h.eq || []).forEach(e => {
        eq += `<span class="eqchip${e.on ? ' eq-on' : ''}${e.l >= e.m ? ' eq-max' : ''}"` +
              ` title="${e.on ? 'currently equipped' : 'owned'}">` +
              `${escj(e.n)} <b>${e.l}</b>/${e.m}</span>`;
      });
      const eqRow = eq ? `<div class="hero-eq">${eq}</div>` : '';
      if (h.level == null) {   // leftover equipment with no known hero
        heroesHtml += `<div class="hero-row" style="--hue:var(${hue})">${icon}` +
          `<span class="hero-name">${escj(h.name)}</span>` +
          `<div></div><div></div>${eqRow}</div>`;
        return;
      }
      const pct = h.max ? Math.round(h.level / h.max * 100) : 0;
      const maxed = h.level >= h.max ? ' meter-max' : '';
      heroesHtml += `<div class="hero-row" style="--hue:var(${hue})">${icon}` +
        `<span class="hero-name">${escj(h.name)}</span>` +
        `<div class="meter"><div class="meter-fill${maxed}" style="width:${pct}%"></div></div>` +
        `<span class="hero-lvl"><b>${h.level}</b> / ${h.max}</span>` +
        eqRow + `</div>`;
    });
    if (!heroesHtml) heroesHtml = '<p class="quiet">No hero data available.</p>';
    function unitGrid(units) {
      if (!units || !units.length) return '<p class="quiet">No data available.</p>';
      const norm = s => s.toLowerCase().replace(/[^a-z0-9]/g, '');
      return '<div class="unit-grid">' + units.map(u => {
        const maxed = u.l >= u.m ? ' maxed' : '';
        const src = UICONS[norm(u.n)];
        const icon = src ? `<img class="ui" src="${src}" alt="" loading="lazy" ` +
                           `onerror="this.style.display='none'">` : '';
        return `<div class="unit${maxed}">${icon}<span class="un">${escj(u.n)}</span>` +
               `<span class="ul">${u.l}/${u.m}</span></div>`;
      }).join('') + '</div>';
    }
    box.innerHTML =
      `<div class="detail-head">` + thImg(m.th, 46) +
      `<div><div class="n">${escj(m.name)}</div>` +
      `<div class="quiet">${escj(m.tag)} &middot; ${escj(m.roleName)} &middot; TH${m.th} &middot; XP ${m.xp}` +
      (m.league ? ` &middot; ${escj(m.league)}` : '') +
      (m.rush != null ? ` &middot; rush ${m.rush}%` : '') + `</div></div>` +
      `<button class="detail-close" onclick="hideMember()">&#10005; close</button></div>` +
      `<div class="detail-tiles">` +
      tile(fmt(m.trophies), 'Trophies', 'best ' + fmt(m.best), '--gold') +
      tile(fmt(m.warStars), 'War stars', 'all-time', '--violet') +
      tile(fmt(m.donations), 'Donated', 'season', '--aqua') +
      tile(fmt(m.received), 'Received', 'season', '--blue') +
      tile(ratio, 'Give / take', 'ratio', '--green') +
      tile(fmt(m.capital), 'Capital gold', 'all-time', '--orange') +
      `</div><h2>Heroes</h2>` + heroesHtml +
      `<h2 style="margin-top:20px">Troops</h2>` + unitGrid(m.troops) +
      `<h2 style="margin-top:20px">Spells</h2>` + unitGrid(m.spells);
    box.style.display = 'block';
    box.scrollIntoView({ behavior:'smooth', block:'start' });
  }
  function hideMember() {
    const box = document.getElementById('member-detail');
    if (box) box.style.display = 'none';
  }
  document.querySelectorAll('tr[data-tag], .mitem[data-tag]').forEach(row =>
    row.addEventListener('click', () => showMember(row.dataset.tag)));

  // ---------------- member comparison ----------------
  const selA = document.getElementById('cmpA');
  const selB = document.getElementById('cmpB');
  const cmpOut = document.getElementById('cmp-out');
  if (selA && selB && cmpOut) {
    const tags = Object.keys(MEMBERS);
    tags.forEach(t => {
      const name = MEMBERS[t].name;
      selA.add(new Option(name, t));
      selB.add(new Option(name, t));
    });
    if (tags.length > 1) selB.value = tags[1];

    function cmpRow(label, va, vb, disp, lowerWins) {
      let ca = '', cb = '';
      if (va != null && vb != null && va !== vb) {
        const aWins = lowerWins ? va < vb : va > vb;
        ca = aWins ? ' class="cv cmp-win"' : ' class="cv"';
        cb = aWins ? ' class="cv"' : ' class="cv cmp-win"';
      } else { ca = cb = ' class="cv"'; }
      return `<tr><td class="cl">${label}</td><td${ca}>${disp(va)}</td><td${cb}>${disp(vb)}</td></tr>`;
    }

    function renderCmp() {
      const a = MEMBERS[selA.value], b = MEMBERS[selB.value];
      if (!a || !b) return;
      const num = v => v == null ? '&mdash;' : fmt(v);
      const pct = v => v == null ? '&mdash;' : v + '%';
      const ratio = m => m.received ? +(m.donations / m.received).toFixed(1) : null;
      const rdisp = v => v == null ? '&mdash;' : v + '&times;';
      let rows = '';
      rows += cmpRow('Town Hall', a.th, b.th, num);
      rows += cmpRow('XP level', a.xp, b.xp, num);
      rows += cmpRow('Trophies', a.trophies, b.trophies, num);
      rows += cmpRow('Best trophies', a.best, b.best, num);
      rows += cmpRow('War stars', a.warStars, b.warStars, num);
      rows += cmpRow('Donated', a.donations, b.donations, num);
      rows += cmpRow('Received', a.received, b.received, num, true);
      rows += cmpRow('Give / take', ratio(a), ratio(b), rdisp);
      rows += cmpRow('Capital gold', a.capital, b.capital, num);
      rows += cmpRow('Rush index', a.rush, b.rush, pct, true);
      const heroOrder = ['Barbarian King','Archer Queen','Minion Prince',
                         'Grand Warden','Royal Champion','Dragon Duke'];
      const ha = {}, hb = {};
      (a.heroes || []).forEach(h => ha[h.name] = h.level);
      (b.heroes || []).forEach(h => hb[h.name] = h.level);
      heroOrder.forEach(n => {
        if (ha[n] != null || hb[n] != null)
          rows += cmpRow(n, ha[n] ?? null, hb[n] ?? null, num);
      });
      cmpOut.innerHTML =
        `<table class="cmp-table">` +
        `<thead><tr><th></th>` +
        `<th class="cmp-h">${thImg(a.th, 32)}<div class="cmp-hname">${escj(a.name)}</div></th>` +
        `<th class="cmp-h">${thImg(b.th, 32)}<div class="cmp-hname">${escj(b.name)}</div></th></tr></thead>` +
        `<tbody>${rows}</tbody></table>`;
    }
    selA.addEventListener('change', renderCmp);
    selB.addEventListener('change', renderCmp);
    renderCmp();
  }

  // ---------------- info modal ----------------
  const infoBack = document.getElementById('info-back');
  function openInfo() { if (infoBack) infoBack.hidden = false; }
  function closeInfo() { if (infoBack) infoBack.hidden = true; }
  const infoFab = document.getElementById('info-fab');
  if (infoFab) infoFab.addEventListener('click', openInfo);
  const infoClose = document.getElementById('info-close');
  if (infoClose) infoClose.addEventListener('click', closeInfo);
  if (infoBack) infoBack.addEventListener('click', e => {
    if (e.target === infoBack) closeInfo();
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeInfo(); });
  document.querySelectorAll('[data-info]').forEach(el =>
    el.addEventListener('click', e => { e.stopPropagation(); openInfo(); }));

  // ---------------- war roster: team / enemy switch ----------------
  // Defaults to our team; the choice survives the live auto-reload.
  const segUs = document.getElementById('rseg-us'),
        segThem = document.getElementById('rseg-them');
  if (segUs && segThem) {
    const showSide = mine => {
      document.getElementById('roster-us').hidden = !mine;
      document.getElementById('roster-them').hidden = mine;
      segUs.classList.toggle('active', mine);
      segThem.classList.toggle('active', !mine);
      try { sessionStorage.setItem('wr-roster-side', mine ? 'us' : 'them'); }
      catch (e) {}
    };
    segUs.addEventListener('click', () => showSide(true));
    segThem.addEventListener('click', () => showSide(false));
    try {
      if (sessionStorage.getItem('wr-roster-side') === 'them') showSide(false);
    } catch (e) {}
  }

  // ---------------- icon cache warm-up ----------------
  // Preload every unit/TH icon shortly after page load, so member profiles
  // open instantly. Combined with the service worker's cache-first strategy,
  // each icon is downloaded exactly once per device, ever.
  setTimeout(() => {
    const urls = new Set(Object.values(UICONS));
    Object.values(HERO_ICONS).forEach(s => urls.add(heroIconUrl(s)));
    Object.values(MEMBERS).forEach(m => {
      if (typeof m.th === 'number')
        urls.add(`https://www.clash.ninja/images/entities/1_${m.th}.png`);
    });
    urls.forEach(u => { const im = new Image(); im.src = u; });
  }, 1500);

  // ---------------- install app button ----------------
  if ('serviceWorker' in navigator && location.protocol.indexOf('http') === 0)
    navigator.serviceWorker.register('sw.js').catch(() => {});
  const installBtn = document.getElementById('install-btn');
  const standalone = window.matchMedia('(display-mode: standalone)').matches
                     || window.navigator.standalone === true;
  if (installBtn && !standalone && location.protocol.indexOf('http') === 0) {
    let deferred = null;
    window.addEventListener('beforeinstallprompt', e => {
      e.preventDefault(); deferred = e; installBtn.hidden = false;
    });
    if (/iphone|ipad|ipod/i.test(navigator.userAgent)) installBtn.hidden = false;
    installBtn.addEventListener('click', async () => {
      if (deferred) {
        deferred.prompt();
        await deferred.userChoice;
        deferred = null; installBtn.hidden = true;
      } else {
        const tip = document.getElementById('ios-tip');
        if (tip) tip.hidden = false;
      }
    });
    window.addEventListener('appinstalled', () => { installBtn.hidden = true; });
  }
"""


# Most recent manifest built by build_page (served by app.py at /manifest.json)
LAST_MANIFEST = "{}"


def build_manifest(clan):
    b = clan.get("badgeUrls", {})
    icons = []
    if b.get("medium"):
        icons.append({"src": b["medium"], "sizes": "200x200", "type": "image/png"})
    if b.get("large"):
        icons.append({"src": b["large"], "sizes": "512x512", "type": "image/png"})
    return json.dumps({
        "name": f'{clan.get("name", "Clan")} - War Room',
        "short_name": clan.get("name", "War Room"),
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0e1116",
        "theme_color": "#0e1116",
        "icons": icons,
    })


def build_error_page(msg):
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta http-equiv="refresh" content="30">'
            f'<title>Clan dashboard - error</title><style>{CSS}</style></head>'
            f'<body><div class="card"><h2>Dashboard error</h2>'
            f'<p>{esc(msg)}</p>'
            f'<p class="quiet">Retrying automatically in 30 seconds.</p>'
            f'</div></body></html>')


def _member_payload(m, profiles, eqmap=None):
    """Compact per-member data embedded in the page for the detail view.
    All 'm'/'max' values are the cap at the member's CURRENT Town Hall."""
    p = profiles.get(m["tag"])
    base = {
        "tag": m["tag"], "name": m["name"],
        "roleName": ROLE_NAMES.get(m["role"], m["role"]),
        "trophies": m["trophies"], "donations": m["donations"],
        "received": m["donationsReceived"],
        "xp": m.get("expLevel", 0), "th": m.get("townHallLevel", 0),
        "best": m["trophies"], "warStars": 0, "capital": 0,
        "league": (m.get("league") or {}).get("name", ""), "heroes": [],
        "troops": [], "spells": [],
    }
    if p:
        th = p.get("townHallLevel", base["th"])
        eqmap = eqmap or {}

        def unit_list(key):
            out = []
            for u in p.get(key, []):
                if (u.get("village") != "home"
                        or u["name"] in SUPER_TROOPS
                        or u["name"].startswith("Super ")):
                    continue
                cap = unit_cap(u["name"], th, u["maxLevel"])
                out.append({"n": u["name"], "l": u["level"],
                            "m": max(cap, u["level"])})
            return out

        owned_eq = [e for e in p.get("heroEquipment", [])
                    if e.get("village") == "home"]
        heroes, attached = [], set()
        for h in p.get("heroes", []):
            if h.get("village") != "home":
                continue
            equipped = {e["name"] for e in h.get("equipment", [])}
            eq = []
            for e in owned_eq:
                if (e["name"] not in equipped
                        and eqmap.get(_norm(e["name"])) != h["name"]):
                    continue
                cap = equip_cap(e["name"], th, e.get("maxLevel", 1))
                eq.append({"n": e["name"], "l": e["level"],
                           "m": max(cap, e["level"]),
                           "on": e["name"] in equipped})
                attached.add(e["name"])
            eq.sort(key=lambda x: (not x["on"], -x["l"]))
            cap = hero_cap(h["name"], th, h["maxLevel"])
            heroes.append({"name": h["name"], "level": h["level"],
                           "max": max(cap, h["level"]), "eq": eq})
        rest = [e for e in owned_eq if e["name"] not in attached]
        if rest:
            heroes.append({"name": "Other equipment", "level": None,
                           "max": None, "eq": [
                               {"n": e["name"], "l": e["level"],
                                "m": max(equip_cap(e["name"], th,
                                                   e.get("maxLevel", 1)),
                                         e["level"]),
                                "on": False} for e in rest]})

        base.update({
            "th": th,
            "xp": p.get("expLevel", base["xp"]),
            "best": p.get("bestTrophies", base["best"]),
            "warStars": p.get("warStars", 0),
            "capital": p.get("clanCapitalContributions", 0),
            "heroes": heroes,
            "troops": unit_list("troops"),
            "spells": unit_list("spells"),
        })
    base["rush"] = rush_score(base["th"], p)
    return base


def build_page(data, live_seconds=None):
    """Render the clan dashboard HTML. live_seconds enables auto-reload."""
    clan, war, profiles = data["clan"], data["war"], data["profiles"]
    w_err, cwl_round = data["w_err"], data.get("cwl_round")
    warlog, wl_err = data.get("warlog"), data.get("wl_err")
    raids, r_err = data.get("raids"), data.get("r_err")
    now = now_str()
    clan_name = clan["name"]
    members = sorted(clan["memberList"], key=lambda m: -m["donations"])

    # ------------------------------ clan tiles -------------------------------
    war_league = (clan.get("warLeague") or {}).get("name", "&mdash;")
    capital_league = (clan.get("capitalLeague") or {}).get("name", "")
    tiles_parts = []

    def tile(val, lbl, sub, tint, countable=True, txt=False, fx="", deco=""):
        cls = "tile-value txt" if txt else "tile-value"
        if countable and isinstance(val, int):
            v = f'<div class="{cls}" data-count="{val}">{val:,}</div>'
        else:
            v = f'<div class="{cls}">{val}</div>'
        tint_css = f"var({tint})" if tint.startswith("--") else tint
        fx_cls = f" {fx}" if fx else ""
        tiles_parts.append(
            f'<div class="tile{fx_cls}" style="--tint:{tint_css}">{deco}'
            f'<div class="tile-label">{lbl}</div>'
            f'{v}<div class="tile-sub">{sub}</div></div>')

    def league_color(name):
        """Tint the war-league tile by the league it is actually in."""
        n = (name or "").lower()
        for key, color in (("bronze", "#c98d5f"), ("silver", "#c9d2dd"),
                           ("gold", "#e8c25a"), ("crystal", "#8fd3f0"),
                           ("master", "#8f87d8"), ("champion", "#e06060"),
                           ("titan", "#e8a33d"), ("legend", "#f0c14b")):
            if key in n:
                return color
        return None

    embers = ('<span class="ember e1"></span><span class="ember e2"></span>'
              '<span class="ember e3"></span>')
    tile(clan["clanPoints"], "Clan points", "home village", "--gold",
         fx="tile-gold")
    tile(clan.get("warWins", 0), "War wins", "all-time", "--red",
         fx="tile-war", deco='<span class="tile-mark">&#9876;</span>')
    if clan.get("warWinStreak"):
        tile(clan["warWinStreak"], "Win streak", "current", "--red",
             fx="tile-streak", deco=embers)
    tile(clan["members"], "Members", "of 50", "--blue")
    tile(clan.get("clanCapitalPoints", 0), "Capital points",
         capital_league or "clan capital", "--violet")
    lg_color = league_color(war_league if isinstance(war_league, str) else "")
    tile(war_league, "War league", "CWL", lg_color or "--aqua",
         countable=False, txt=True, fx="tile-league" if lg_color else "")
    tiles_html = "\n".join(tiles_parts)

    # ------------------------------ MVP cards --------------------------------
    mvp_html = ""
    if members:
        top_d = members[0]
        top_t = max(clan["memberList"], key=lambda m: m["trophies"])
        def mvp(title, m, value, fx="", ico=""):
            th = (_member_payload(m, profiles))["th"]
            embers = ('<span class="ember e1"></span><span class="ember e2"></span>'
                      '<span class="ember e3"></span>') if fx == "mvp-fire" else ''
            icon = f'<span class="vic">{ico}</span>' if ico else ''
            cls = f' {fx}' if fx else ''
            return (f'<div class="mvp{cls}">{embers}{th_avatar(th, 38)}'
                    f'<div><div class="t">{title}</div><div class="n">{esc(m["name"])}</div></div>'
                    f'<div class="v">{icon}{value}</div></div>')
        mvp_html = ('<div class="mvps">'
                    + mvp("Top donator", top_d, f'{top_d["donations"]:,}',
                          fx="mvp-fire", ico="&#128293;")
                    + mvp("Trophy leader", top_t, f'{top_t["trophies"]:,}',
                          fx="mvp-bolt", ico="&#9889;")
                    + '</div>')

    # ------------------------------ war panel -------------------------------
    roster_html = ""
    war_cls = ""          # tints the war cards green/red once the war ends
    war_tab_cls = ""      # tints the "War" nav button
    glance_cls = ""       # overview card: red in battle, green in prep,
                          # gold on victory, slate on defeat
    glance_title = "War"
    if w_err:
        war_html = (f'<div class="empty"><div class="big">&#9876;</div>'
                    f'<p>War data unavailable ({esc(w_err)}).<br>'
                    'If it says accessDenied, the clan\'s war log is private in-game.</p></div>')
        war_mini = '<span class="quiet">war data unavailable</span>'
    elif war.get("state") == "notInWar":
        war_html = (f'<div class="empty"><div class="big">&#9876;</div>'
                    f'<p>{esc(clan_name)} is not in a war right now.</p></div>')
        war_mini = '<span class="quiet">not in war</span>'
    else:
        us, them = war["clan"], war["opponent"]
        state = war["state"]
        per_member = war.get("attacksPerMember", 2)
        total_attacks = war["teamSize"] * per_member

        if state == "preparation":
            when = parse_coc_time(war["startTime"])
            label, badge = "Preparation day", "prep"
            glance_cls = " glance-prep"
        elif state == "inWar":
            when = parse_coc_time(war["endTime"])
            label, badge = "Battle day", "live"
            glance_cls = " glance-live"
        else:
            when = None
            if us["stars"] != them["stars"]:
                won = us["stars"] > them["stars"]
            else:
                won = us["destructionPercentage"] > them["destructionPercentage"]
            label = "Victory" if won else "Defeat"
            badge = "won" if won else "lost"
            war_cls = " war-won" if won else " war-lost"
            war_tab_cls = " tab-won" if won else " tab-lost"
            glance_cls = " glance-won" if won else " glance-lost"
        if cwl_round:
            label = f"CWL Round {cwl_round} &middot; {label}"

        if when:
            left = when - datetime.now(timezone.utc)
            hrs, rem = divmod(max(0, int(left.total_seconds())), 3600)
            time_txt = f"{hrs}h {rem // 60}m {'until battle' if state == 'preparation' else 'remaining'}"
        else:
            time_txt = "war ended"

        slackers_html = ""
        if state == "inWar":
            pend = [(m["name"], per_member - len(m.get("attacks", [])))
                    for m in us.get("members", [])
                    if len(m.get("attacks", [])) < per_member]
            if pend:
                pend.sort(key=lambda x: -x[1])
                items = ", ".join(f"{esc(n)} ({k} left)" for n, k in pend)
                slackers_html = (f'<div class="war-pending"><strong>{len(pend)} member'
                                 f'{"s" if len(pend) != 1 else ""} with attacks left:</strong> {items}</div>')

        them_map = {m["tag"]: m for m in them.get("members", [])}
        us_map = {m["tag"]: m for m in us.get("members", [])}

        def stars_str(n):
            return ('<span class="st">' + "&#9733;" * n + "</span>"
                    + '<span class="st-off">' + "&#9734;" * (3 - n) + "</span>")

        def _target(d):
            return (f'#{d.get("mapPosition", "?")} '
                    f'{th_avatar(d.get("townhallLevel"), 15, "th-sm")}'
                    f'TH{d.get("townhallLevel", "?")}')

        def attack_cell(a, dmap):
            if a is None:
                return '<td class="att att-empty">&mdash;</td>'
            d = dmap.get(a["defenderTag"], {})
            target = _target(d)
            return (f'<td class="att st{a["stars"]}">{stars_str(a["stars"])} '
                    f'<span class="att-pct">{a["destructionPercentage"]}%</span>'
                    f'<div class="att-target">vs {target}</div></td>')

        def attack_span(a, idx, dmap):
            if a is None:
                return f'<span class="ratt att-empty">A{idx}: &mdash;</span>'
            d = dmap.get(a["defenderTag"], {})
            target = _target(d)
            return (f'<span class="ratt att st{a["stars"]}">{stars_str(a["stars"])} '
                    f'<span class="att-pct">{a["destructionPercentage"]}%</span> '
                    f'<span class="att-target">vs {target}</span></span>')

        def build_roster(side, dmap):
            """Table rows + mobile cards for one clan's attack roster.
            dmap maps defender tags to the OTHER side's members."""
            rows, cards = [], []
            for m in sorted(side.get("members", []), key=lambda x: x["mapPosition"]):
                atks = sorted(m.get("attacks", []), key=lambda a: a.get("order", 0))
                cells = "".join(attack_cell(atks[i] if i < len(atks) else None, dmap)
                                for i in range(per_member))
                used = len(atks)
                if state == "preparation":
                    status = '<span class="status-wait">prep</span>'
                    row_name = ''
                elif used == per_member:
                    status = '<span class="status-done">&#10003; done</span>'
                    row_name = 'rst-full'
                elif state == "warEnded":
                    status = f'<span class="status-missed">{per_member - used} unused</span>'
                    row_name = 'rst-part' if used else 'rst-none'
                else:
                    status = f'<span class="status-left">{per_member - used} left</span>'
                    row_name = 'rst-part' if used else 'rst-none'
                row_cls = f' class="{row_name}"' if row_name else ''
                rows.append(
                    f'<tr{row_cls}><td class="num">{m["mapPosition"]}</td>'
                    f'<td><div class="rmem">{th_avatar(m.get("townhallLevel"), 26)}'
                    f'<div>{esc(m["name"])}<div class="att-target">TH{m.get("townhallLevel", "?")}</div></div>'
                    f'</div></td>'
                    f'{cells}<td class="num">{status}</td></tr>')

                if state == "preparation":
                    atk_line = ''
                else:
                    spans = ''.join(attack_span(atks[i] if i < len(atks) else None, i + 1, dmap)
                                    for i in range(per_member))
                    atk_line = f'<div class="ri-atks">{spans}</div>'
                card_cls = f' {row_name}' if row_name else ''
                cards.append(
                    f'<div class="ritem{card_cls}">'
                    f'<span class="rk">{m["mapPosition"]}</span>'
                    f'{th_avatar(m.get("townhallLevel"), 24)}'
                    f'<div class="ri-main"><div class="mi-name">{esc(m["name"])} '
                    f'<span class="att-target">TH{m.get("townhallLevel", "?")}</span></div>'
                    f'{atk_line}</div>'
                    f'<div class="ri-status">{status}</div></div>')
            return rows, cards

        team_rows, team_cards = build_roster(us, them_map)
        foe_rows, foe_cards = build_roster(them, us_map)

        atk_heads = "".join(f'<th>Attack {i + 1}</th>' for i in range(per_member))
        legend = ""
        if state != "preparation":
            legend = ('<p class="quiet" style="margin-top:10px">'
                      'Row: <span class="c-green">&#9632;</span> all attacks used &middot; '
                      '<span style="color:var(--accent)">&#9632;</span> partial &middot; '
                      '<span class="c-red">&#9632;</span> none yet. '
                      'Stars: <span class="c-green">3&#9733;</span> &middot; '
                      '<span style="color:var(--gold)">2&#9733;</span> &middot; '
                      '<span style="color:var(--orange)">1&#9733;</span> &middot; '
                      '<span class="c-red">0&#9733;</span></p>')
        def roster_block(rows, cards):
            return (f'<div class="table-scroll roster-desktop"><table class="roster">'
                    f'<thead><tr><th class="num">#</th><th>Member</th>{atk_heads}'
                    f'<th class="num">Status</th></tr></thead>'
                    f'<tbody>{"".join(rows)}</tbody></table></div>'
                    f'<div class="rlist">{"".join(cards)}</div>')

        roster_html = f"""
        <div class="card"><div class="roster-head"><h2>Attack roster</h2>
        <div class="rseg">
          <button id="rseg-us" class="sortbtn active" style="--tint:var(--green)"><span class="si">&#9876;</span>{esc(us["name"])}</button>
          <button id="rseg-them" class="sortbtn" style="--tint:var(--red)"><span class="si">&#128737;</span>{esc(them["name"])}</button>
        </div></div>
        <div id="roster-us">{roster_block(team_rows, team_cards)}</div>
        <div id="roster-them" hidden>{roster_block(foe_rows, foe_cards)}</div>
        {legend}</div>"""

        def side(c, cls):
            badge_img = c.get("badgeUrls", {}).get("small", "")
            img = f'<img src="{esc(badge_img)}" alt="">' if badge_img else ''
            return (f'<div class="war-side {cls}">{img}'
                    f'<div class="war-clan">{esc(c["name"])}</div>'
                    f'<div class="war-stars"><span class="st-ico">&#9733;</span> '
                    f'<span data-count="{c["stars"]}">{c["stars"]}</span></div>'
                    f'<div class="destr"><div style="width:{c["destructionPercentage"]:.1f}%"></div></div>'
                    f'<div class="quiet">{c["destructionPercentage"]:.1f}% destruction &middot; '
                    f'{c.get("attacks", 0)}/{total_attacks} attacks</div></div>')

        war_html = f"""
        <div class="war-head"><span class="war-badge war-badge-{badge}">{label}</span>
        <span class="quiet">{war["teamSize"]}v{war["teamSize"]} &middot; {esc(time_txt)}</span></div>
        <div class="war-grid">{side(us, "us")}<div class="war-vs">VS</div>{side(them, "them")}</div>
        {slackers_html}"""
        glance_title = "Ongoing War" if state in ("preparation", "inWar") else "War Result"
        war_mini = (
            f'<div class="glance-top"><span class="war-badge war-badge-{badge}">{label}</span>'
            f'<span class="quiet">{war["teamSize"]}v{war["teamSize"]} &middot; {esc(time_txt)}</span></div>'
            f'<div class="glance-score"><span class="st">&#9733;</span> <b>{us["stars"]}</b>'
            f' &ndash; <b>{them["stars"]}</b> <span class="quiet">vs</span> '
            f'<span class="g-name">{esc(them["name"])}</span></div>')

    # --------------------------- members tab ---------------------------------
    eqmap = _equip_hero_map(profiles)
    member_data = {m["tag"]: _member_payload(m, profiles, eqmap) for m in members}
    members_json = json.dumps(member_data).replace("</", "<\\/")

    top_don = max((m["donations"] for m in members), default=0) or 1

    def fire_number(don):
        """One flame + color-graded number: light orange -> light red -> red."""
        pct = don / top_don
        if don <= 0 or pct < .08:
            return f'{don:,}'
        tier = 3 if pct >= .66 else (2 if pct >= .33 else 1)
        return (f'<span class="fire fire-{tier}"><span class="fl">&#128293;</span>'
                f'<span class="val">{don:,}</span></span>')

    rows = []
    mrows = []
    for i, m in enumerate(members, 1):
        md = member_data[m["tag"]]
        r = (m["donations"] / m["donationsReceived"]) if m["donationsReceived"] else None
        rk_cls = f' rk{i}' if i <= 3 else ''
        rush = md.get("rush")
        if rush is None:
            rush_html, rush_attr = '<span class="quiet">&mdash;</span>', -1
        else:
            rz = 'rz-ok' if rush <= 15 else ('rz-mid' if rush <= 40 else 'rz-bad')
            rush_html, rush_attr = f'<span class="{rz}">{rush}%</span>', rush
        mrows.append(
            f'<div class="mitem" data-tag="{esc(m["tag"])}" data-don="{m["donations"]}" '
            f'data-tro="{m["trophies"]}" data-th="{md["th"]}" data-xp="{md["xp"]}" '
            f'data-rush="{rush_attr}">'
            f'<span class="rk{rk_cls}">{i}</span>'
            f'{th_avatar(md["th"], 34)}'
            f'<div class="mi-main">'
            f'<div class="mi-name">{esc(m["name"])}</div>'
            f'<div class="mi-sub"><span class="role-chip role-{m["role"]}">'
            f'{ROLE_NAMES.get(m["role"], m["role"])}</span> '
            f'XP {md["xp"]} &middot; &#127942; {m["trophies"]:,} &middot; {rush_html}</div>'
            f'</div>'
            f'<div class="mi-right">'
            f'<div class="mi-don">{fire_number(m["donations"])}</div>'
            f'<div class="mi-sub">donations</div>'
            f'</div></div>')
        rows.append(
            f'<tr data-tag="{esc(m["tag"])}" data-don="{m["donations"]}" '
            f'data-tro="{m["trophies"]}" data-th="{md["th"]}" data-xp="{md["xp"]}" '
            f'data-rush="{rush_attr}">'
            f'<td class="num"><span class="rk{rk_cls}">{i}</span></td>'
            f'<td>{th_avatar(md["th"])}{esc(m["name"])}</td>'
            f'<td><span class="role-chip role-{m["role"]}">{ROLE_NAMES.get(m["role"], m["role"])}</span></td>'
            f'<td class="num">{md["xp"]}</td>'
            f'<td class="num">{m["trophies"]:,}</td>'
            f'<td class="num">{fire_number(m["donations"])}</td>'
            f'<td class="num">{m["donationsReceived"]:,}</td>'
            f'<td class="num">{f"{r:.1f}" if r is not None else "&mdash;"}</td>'
            f'<td class="num">{rush_html}</td></tr>')
    table_html = f"""
    <div class="table-scroll members-desktop">
    <table id="members-table">
      <thead><tr><th class="num">#</th><th>Member</th><th>Role</th>
      <th class="num">XP</th><th class="num">Trophies</th><th class="num">Donated</th>
      <th class="num">Received</th><th class="num">Ratio</th>
      <th class="num">Rush <span class="im" data-info title="How is this calculated?">&#9432;</span></th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    </div>
    <div class="mlist" id="mlist">{''.join(mrows)}</div>"""

    sortbar_html = """
    <div class="sortbar"><span class="lbl">Sort by</span>
      <button class="sortbtn" data-sort="don" style="--tint:var(--orange)"><span class="si">&#128293;</span>Donations<span class="dir"></span></button>
      <button class="sortbtn" data-sort="tro" style="--tint:var(--gold)"><span class="si">&#127942;</span>Trophies<span class="dir"></span></button>
      <button class="sortbtn" data-sort="th" style="--tint:var(--blue)"><span class="si">&#127984;</span>Town Hall<span class="dir"></span></button>
      <button class="sortbtn" data-sort="xp" style="--tint:var(--violet)"><span class="si">&#11088;</span>XP level<span class="dir"></span></button>
      <button class="sortbtn" data-sort="rush" style="--tint:var(--red)"><span class="si">&#127939;</span>Rushed<span class="dir"></span></button>
    </div>"""

    war_league_name = esc((clan.get("warLeague") or {}).get("name", ""))
    chip_defs = [
        ("&#128101;", f'{clan["members"]}/50 members', "--blue"),
        ("&#127942;", f'{clan["clanPoints"]:,} points', "--gold"),
        ("&#9876;&#65039;", f'{clan.get("warWins", "?")} war wins', "--red"),
        ("&#128737;&#65039;", war_league_name, "--aqua"),
    ]
    chips_html = "".join(
        f'<span class="chip chip-c" style="--tint:var({t})">'
        f'<span class="ci">{ico}</span>{txt}</span>'
        for ico, txt, t in chip_defs if txt)

    # --------------------------- history tab ---------------------------------
    if wl_err:
        history_html = ('<div class="empty"><div class="big">&#128274;</div>'
                        '<p>War log unavailable - it is set to private in-game.<br>'
                        'A leader can make it public: Clan settings &rarr; War log.</p></div>')
    else:
        entries = [e for e in (warlog or {}).get("items", [])
                   if (e.get("opponent") or {}).get("name")]
        if not entries:
            history_html = '<p class="quiet">No classic wars in the log yet.</p>'
        else:
            w_cnt = sum(1 for e in entries if e.get("result") == "win")
            l_cnt = sum(1 for e in entries if e.get("result") == "lose")
            t_cnt = len(entries) - w_cnt - l_cnt
            rate = round(w_cnt / len(entries) * 100)
            summary = (f'<div class="chips">'
                       f'<span class="chip">last {len(entries)} wars</span>'
                       f'<span class="chip"><b class="c-green">{w_cnt}W</b>'
                       f' &ndash; <b class="c-red">{l_cnt}L</b>'
                       + (f' &ndash; {t_cnt}T' if t_cnt else '') + '</span>'
                       f'<span class="chip">{rate}% win rate</span></div>')
            hrows = []
            hcards = []
            for e in entries:
                res = e.get("result") or "tie"
                res_cls = {"win": "res-w", "lose": "res-l"}.get(res, "res-t")
                res_txt = {"win": "WIN", "lose": "LOSS"}.get(res, "TIE")
                res_short = {"win": "W", "lose": "L"}.get(res, "T")
                opp = e["opponent"]
                badge_img = (opp.get("badgeUrls") or {}).get("small", "")
                img = f'<img class="wl-badge" src="{esc(badge_img)}" alt="">' if badge_img else ''
                try:
                    date_txt = parse_coc_time(e["endTime"]).strftime("%d %b")
                except Exception:
                    date_txt = ""
                uc, oc = e.get("clan", {}), opp
                hrows.append(
                    f'<tr class="hr-{res}"><td><span class="res {res_cls}">{res_txt}</span></td>'
                    f'<td class="wl-opp">{img}{esc(opp["name"])}</td>'
                    f'<td class="num">{e.get("teamSize", "?")}v{e.get("teamSize", "?")}</td>'
                    f'<td class="num"><b>{uc.get("stars", "?")}</b> &ndash; {oc.get("stars", "?")}</td>'
                    f'<td class="num">{uc.get("destructionPercentage", 0):.1f}% &ndash; '
                    f'{oc.get("destructionPercentage", 0):.1f}%</td>'
                    f'<td class="num quiet">{date_txt}</td></tr>')
                hcards.append(
                    f'<div class="hitem hr-{res}">{img}'
                    f'<div class="hi-main">'
                    f'<div class="hi-name">{esc(opp["name"])}</div>'
                    f'<div class="mi-sub">{e.get("teamSize", "?")}v{e.get("teamSize", "?")}'
                    f'{" &middot; " + date_txt if date_txt else ""}</div>'
                    f'</div>'
                    f'<div class="hi-right">'
                    f'<div class="hi-stars"><span class="st">&#9733;</span> '
                    f'<b>{uc.get("stars", "?")}</b> &ndash; {oc.get("stars", "?")} '
                    f'<span class="res {res_cls}">{res_short}</span></div>'
                    f'<div class="mi-sub">{uc.get("destructionPercentage", 0):.1f}% &ndash; '
                    f'{oc.get("destructionPercentage", 0):.1f}%</div>'
                    f'</div></div>')
            history_html = f"""{summary}
            <div class="table-scroll hist-desktop">
            <table>
              <thead><tr><th>Result</th><th>Opponent</th><th class="num">Size</th>
              <th class="num">Stars</th><th class="num">Destruction</th><th class="num">Date</th></tr></thead>
              <tbody>{''.join(hrows)}</tbody>
            </table>
            </div>
            <div class="hlist">{''.join(hcards)}</div>"""

    # --------------------------- capital tab ----------------------------------
    if r_err or not (raids or {}).get("items"):
        capital_html = '<p class="quiet">No Clan Capital raid data available.</p>'
        capital_past_html = ""
    else:
        latest = raids["items"][0]
        cap_tiles = []

        def cap_tile(val, lbl, sub, tint):
            v = f'<div class="tile-value" data-count="{val}">{val:,}</div>' \
                if isinstance(val, int) else f'<div class="tile-value">{val}</div>'
            cap_tiles.append(
                f'<div class="tile" style="--tint:var({tint})">'
                f'<div class="tile-label">{lbl}</div>{v}'
                f'<div class="tile-sub">{sub}</div></div>')

        try:
            season_range = (parse_coc_time(latest["startTime"]).strftime("%d %b")
                            + " &ndash; "
                            + parse_coc_time(latest["endTime"]).strftime("%d %b"))
        except Exception:
            season_range = "latest weekend"
        cap_tile(latest.get("capitalTotalLoot", 0), "Capital loot", season_range, "--gold")
        cap_tile(latest.get("raidsCompleted", 0), "Raids completed", "enemy capitals", "--red")
        cap_tile(latest.get("totalAttacks", 0), "Attacks used", "clan total", "--blue")
        cap_tile(latest.get("enemyDistrictsDestroyed", 0), "Districts destroyed",
                 "offense", "--orange")
        cap_tile(latest.get("offensiveReward", 0), "Offense medals",
                 "per attack", "--violet")
        cap_tile(latest.get("defensiveReward", 0), "Defense medals",
                 "flat bonus", "--aqua")
        cap_tiles_html = '<div class="tiles">' + "".join(cap_tiles) + '</div>'

        cap_members = sorted(latest.get("members", []),
                             key=lambda m: -m.get("capitalResourcesLooted", 0))
        cap_rows = []
        if cap_members:
            top_loot = max(m.get("capitalResourcesLooted", 0) for m in cap_members) or 1
            for i, m in enumerate(cap_members, 1):
                limit = m.get("attackLimit", 5) + m.get("bonusAttackLimit", 0)
                used = m.get("attacks", 0)
                loot = m.get("capitalResourcesLooted", 0)
                bar = round(loot / top_loot * 100)
                rk_cls = f' rk{i}' if i <= 3 else ''
                atk_cls = "status-done" if used >= limit else \
                          ("status-left" if used == 0 else "")
                cap_rows.append(
                    f'<tr><td class="num"><span class="rk{rk_cls}">{i}</span></td>'
                    f'<td style="white-space:nowrap">{esc(m["name"])}</td>'
                    f'<td class="num"><span class="{atk_cls}">{used}/{limit}</span></td>'
                    f'<td class="num">{loot:,}'
                    f'<div class="cellbar"><div style="width:{bar}%"></div></div></td></tr>')
            missing = len(clan["memberList"]) - len(cap_members)
            missing_note = (f'<p class="quiet" style="margin-top:10px">'
                            f'{missing} clan member{"s" if missing != 1 else ""} '
                            f'did not participate.</p>') if missing > 0 else ''
            capital_html = f"""{cap_tiles_html}
            <h2 style="margin-top:20px">Raid participants &mdash; {season_range}</h2>
            <div class="table-scroll">
            <table>
              <thead><tr><th class="num">#</th><th>Member</th>
              <th class="num">Attacks</th><th class="num">Capital loot</th></tr></thead>
              <tbody>{''.join(cap_rows)}</tbody>
            </table>
            </div>{missing_note}"""
        else:
            capital_html = cap_tiles_html + '<p class="quiet">No participants recorded.</p>'

        past_rows = []
        for s in raids["items"][1:]:
            try:
                d = parse_coc_time(s["startTime"]).strftime("%d %b %Y")
            except Exception:
                d = "?"
            past_rows.append(
                f'<tr><td>{d}</td>'
                f'<td class="num">{s.get("capitalTotalLoot", 0):,}</td>'
                f'<td class="num">{s.get("raidsCompleted", 0)}</td>'
                f'<td class="num">{s.get("totalAttacks", 0)}</td>'
                f'<td class="num">{s.get("enemyDistrictsDestroyed", 0)}</td></tr>')
        capital_past_html = ""
        if past_rows:
            capital_past_html = f"""
            <div class="card"><h2>Past raid weekends</h2>
            <div class="table-scroll">
            <table>
              <thead><tr><th>Weekend</th><th class="num">Loot</th><th class="num">Raids</th>
              <th class="num">Attacks</th><th class="num">Districts</th></tr></thead>
              <tbody>{''.join(past_rows)}</tbody>
            </table>
            </div></div>"""

    # ------------------------------- banner ----------------------------------
    crest = clan.get("badgeUrls", {}).get("medium", "")
    crest_large = clan.get("badgeUrls", {}).get("large", crest)
    crest_html = f'<img class="crest" src="{esc(crest)}" alt="clan badge">' if crest else ''
    desc = esc(clan.get("description", ""))

    global LAST_MANIFEST
    LAST_MANIFEST = build_manifest(clan)

    if live_seconds:
        updated_html = (f'<span class="live-dot"></span>LIVE &middot; {now}'
                        f'<br>refresh in <strong id="cd">{live_seconds}s</strong>')
        footer_html = (f'Data from the official Clash of Clans API &middot; '
                       f'auto-refreshes every {live_seconds}s (the API itself updates every 1&ndash;2 min)')
        live_js = f"""
  let s = {live_seconds};
  const el = document.getElementById('cd');
  setInterval(() => {{ s--; if (s <= 0) location.reload();
                       else if (el) el.textContent = s + 's'; }}, 1000);"""
    else:
        updated_html = f'updated {now}'
        footer_html = (f'Snapshot from {now} &middot; double-click '
                       f'<code>update-dashboard.bat</code> to refresh')
        live_js = ""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(clan_name)} &mdash; War Room</title>
<meta name="theme-color" content="#0e1116">
<link rel="icon" href="{esc(crest)}">
<link rel="manifest" href="manifest.json">
<link rel="apple-touch-icon" href="{esc(crest_large)}">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="{esc(clan_name)}">
{FONT_LINKS}<style>{CSS}</style></head><body>

<header class="banner">
  {crest_html}
  <div class="who">
    <div class="kicker">War Room</div>
    <h1>{esc(clan_name)}</h1>
    <div class="tag">{esc(clan.get("tag", ""))}</div>
  </div>
  <div class="banner-right">
    <span class="lvl-chip">LEVEL {clan["clanLevel"]}</span>
    <div class="updated">{updated_html}</div>
    <button id="install-btn" class="install-btn" hidden>&#8595; Install app</button>
  </div>
</header>

<div id="ios-tip" class="ios-tip" hidden>
  On iPhone: tap the <b>Share</b> button, then <b>Add to Home Screen</b>.
  <button class="detail-close" onclick="this.parentElement.hidden=true">OK</button>
</div>

<nav>
  <button class="tab" data-tab="overview">Overview</button>
  <button class="tab{war_tab_cls}" data-tab="war">War</button>
  <button class="tab" data-tab="members">Members</button>
  <button class="tab" data-tab="history">History</button>
  <button class="tab" data-tab="capital">Capital</button>
</nav>

<section class="panel" id="panel-overview">
  <div class="card"><div class="tiles">{tiles_html}</div></div>
  <div class="card"><h2>Clan MVPs</h2>{mvp_html}</div>
  <div class="card{glance_cls}"><h2>{glance_title}</h2>{war_mini}</div>
  {f'<div class="card"><h2>About</h2><p class="quiet">{desc}</p></div>' if desc else ''}
</section>

<section class="panel" id="panel-war">
  <div class="card{war_cls}"><h2>Current war</h2>{war_html}</div>
  {roster_html}
</section>

<section class="panel" id="panel-members">
  <div class="card" id="member-detail"></div>
  <div class="card"><h2>Members
  <span class="im h2-info" data-info title="How this works">&#9432;</span></h2>
  <div class="chips">{chips_html}</div>{sortbar_html}{table_html}</div>
  <div class="card"><h2>Compare members</h2>
    <div class="cmp-bar">
      <select id="cmpA" class="cmpsel"></select>
      <span class="cmp-vs">VS</span>
      <select id="cmpB" class="cmpsel"></select>
    </div>
    <div id="cmp-out"></div>
  </div>
</section>

<section class="panel" id="panel-history">
  <div class="card"><h2>War history</h2>{history_html}</div>
</section>

<section class="panel" id="panel-capital">
  <div class="card"><h2>Raid weekend</h2>{capital_html}</div>
  {capital_past_html}
</section>

<footer>{footer_html}</footer>
{INFO_MODAL}
<script>const MEMBERS = {members_json};
const UICONS = {json.dumps(UNIT_ICONS)};</script>
<script>{PAGE_JS}{live_js}</script>
</body></html>"""


def main():
    if not KEY_FILE.exists():
        sys.exit(f"Key file not found: {KEY_FILE}")
    key = KEY_FILE.read_text().strip()
    data = fetch_all(key)
    if data["c_err"]:
        sys.exit(f"Could not fetch clan: {data['c_err']}\n"
                 "(403 accessDenied usually means your IP changed - make a new key.)")
    doc = build_page(data)
    OUT_FILE.write_text(doc, encoding="utf-8")
    OUT_FILE.with_name("manifest.json").write_text(LAST_MANIFEST, encoding="utf-8")
    print(f"Wrote {OUT_FILE} ({len(data['profiles'])} member profiles embedded)")
    if "--open" in sys.argv:
        webbrowser.open(OUT_FILE.as_uri())


if __name__ == "__main__":
    main()
