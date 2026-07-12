# Deploying the CoC dashboard to Render

## The IP problem (read this first!)

Your CoC API key only works from whitelisted IPs. Render's servers have
different IPs than your PC, so **your current key will NOT work there**.

**Recommended fix — the RoyaleAPI proxy** (free, made for exactly this):

1. Go to developer.clashofclans.com → create a **new key**
2. Set its allowed IP to: `45.79.218.79`  (the proxy's fixed IP)
3. On Render, set env var `COC_API_BASE` = `https://cocproxy.royaleapi.dev/v1`
4. The proxy forwards requests to Supercell unchanged — your key never
   breaks again, even if Render's IPs change.

(Alternative: after deploying, Render shows your service's static outbound
IPs under Settings → Outbound. Whitelist those 3 IPs in a new key instead
and skip the proxy. Either works.)

## Steps

1. **Push this folder to GitHub** (the .gitignore already excludes the key
   and generated files — NEVER commit the key):
   ```
   cd coc-dashboard
   git init
   git add .
   git commit -m "CoC dashboard"
   ```
   Create an empty repo on github.com, then:
   ```
   git remote add origin https://github.com/YOURNAME/coc-dashboard.git
   git push -u origin main
   ```

2. **On render.com**: New → Web Service → connect the GitHub repo
   - Runtime: Python
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app`
   - Instance type: Free

3. **Environment variables** (service → Environment):
   - `COC_API_KEY` = the new key you made for the proxy IP
   - `COC_API_BASE` = `https://cocproxy.royaleapi.dev/v1`
   - (optional) `CACHE_SECONDS` = `60`

4. Deploy. Your dashboard is live at `https://your-service.onrender.com`.

## How the "live" updating works

- The server calls the CoC API **at most once per 60 s** per page,
  no matter how many people are viewing (in-memory cache).
- The page auto-reloads every 75 s.
- Supercell itself caches data 1–2 min, so this is as real-time as anyone
  can get. During war day the roster updates itself — just leave the tab open.

## Free-tier note

Render's free web services **sleep after ~15 min without visitors**; the
next visitor waits ~30–60 s while it wakes. Fine for war day. If it
annoys you, the $7/mo instance never sleeps.

## Local use still works

- `python dashboard.py --open`  → one-shot local HTML (uses the key file)
- `python app.py`               → local live server at http://localhost:8000
