"""Regenerate ../th_caps.py from coc.guide's game data JSONs.

Run after a Clash of Clans balance update:  python tools/gen_th_caps.py
Derivation: a unit level is reachable at a TH if its required Laboratory
(or Pet House / Blacksmith) level is available at that TH.
"""
import json
import urllib.request
from collections import Counter
from pathlib import Path

UA = {"User-Agent": "coc-clan-dashboard/1.0 (+https://github.com/gabrielborvs-sudo/coc-dashboard)"}
OUT = Path(__file__).resolve().parent.parent / "th_caps.py"
MAX_TH = 17  # data covers up to TH17; TH18+ falls back to the API global max

HEADER = '''"""Exact per-Town-Hall max levels, generated from coc.guide's game data files.

Regenerate with tools/gen_th_caps.py when the game gets a balance update.
- UNIT_TH_CAPS:  normalized unit name -> max level at TH1..TH17 (0 = locked).
  Covers troops, spells, siege machines, pets and super troops.
- EQUIP_BS_CAPS: normalized equipment name -> max level at Blacksmith 1..9.
- EQUIP_HERO:    normalized equipment name -> owning hero (display name).
- EQUIP_CURVE:   generic Blacksmith curve per rarity (fallback for items
  newer than the data files; rarity is inferred from the API global max).
TH18+ callers should fall back to the API's global maxLevel.
"""
'''


def get(path):
    req = urllib.request.Request("https://coc.guide" + path, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def norm(name):
    return "".join(ch for ch in name.lower() if ch.isalnum())


def main():
    texts = get("/static/json/lang/texts_EN.json")

    def tid_name(entry):
        tid = entry.get("TID")
        if isinstance(tid, list):
            tid = tid[0] if tid else None
        v = texts.get(tid) if tid else None
        if isinstance(v, list):
            v = v[0] if v else None
        return v

    blds = get("/static/json/buildings.json")
    lab_th = blds["Laboratory"]["TownHallLevel"]
    pet_th = blds["Pet Shop"]["TownHallLevel"]

    def bmax(th, arr):
        return sum(1 for v in arr if v <= th)

    unit_caps = {}

    def add_unit(entry, building_arr):
        name = tid_name(entry)
        reqs = entry.get("LaboratoryLevel")
        if not name or not reqs:
            return
        levels = (entry.get("VisualLevel") or entry.get("Level")
                  or list(range(1, len(reqs) + 1)))
        caps = []
        for th in range(1, MAX_TH + 1):
            b = bmax(th, building_arr)
            caps.append(max([lv for lv, rq in zip(levels, reqs) if rq <= b],
                            default=0))
        k = norm(name)
        if k not in unit_caps or len(reqs) > unit_caps[k][1]:
            unit_caps[k] = (caps, len(reqs))

    for e in get("/static/json/characters.json").values():
        add_unit(e, lab_th)
    for e in get("/static/json/spells.json").values():
        add_unit(e, lab_th)
    for e in get("/static/json/pets.json").values():
        add_unit(e, pet_th)

    items = get("/static/json/character_items.json")
    hero_display = {"Minion Hero": "Minion Prince",
                    "Warrior Princess": "Royal Champion"}
    eq_caps, eq_hero, eq_rarity = {}, {}, {}
    for key, e in items.items():
        if key.startswith("UNUSED") or key == "Football":
            continue
        name = tid_name(e)
        reqs = e.get("RequiredBlacksmithLevel")
        if not name or not reqs:
            continue
        levels = e.get("Level") or list(range(1, len(reqs) + 1))
        k = norm(name)
        eq_caps[k] = [max([lv for lv, rq in zip(levels, reqs) if rq <= b],
                          default=0) for b in range(1, 10)]
        allowed = e.get("AllowedCharacters", [""])[0].rstrip(";")
        eq_hero[k] = hero_display.get(allowed, allowed)
        eq_rarity[k] = (e.get("Rarity") or ["Common"])[0]

    curve = {}
    for rar in ("Common", "Epic"):
        curve[rar] = [Counter(eq_caps[k][b] for k in eq_caps
                              if eq_rarity[k] == rar).most_common(1)[0][0]
                      for b in range(9)]

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(HEADER)
        f.write("\n# Auto-generated from coc.guide game data"
                " (see tools/gen_th_caps.py). TH1..TH17.\n")
        f.write("UNIT_TH_CAPS = {\n")
        for k in sorted(unit_caps):
            f.write(f'    "{k}": {tuple(unit_caps[k][0])},\n')
        f.write("}\n\nEQUIP_BS_CAPS = {\n")
        for k in sorted(eq_caps):
            f.write(f'    "{k}": {tuple(eq_caps[k])},\n')
        f.write("}\n\nEQUIP_HERO = {\n")
        for k in sorted(eq_hero):
            f.write(f'    "{k}": "{eq_hero[k]}",\n')
        f.write("}\n\nEQUIP_CURVE = {\n")
        for r, c in curve.items():
            f.write(f'    "{r}": {tuple(c)},\n')
        f.write("}\n")
    print(f"wrote {OUT}: {len(unit_caps)} units, {len(eq_caps)} equipment")


if __name__ == "__main__":
    main()
