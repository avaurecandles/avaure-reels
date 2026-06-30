#!/usr/bin/env python3
"""
Avaure — Daily Instagram Trial Reel poster.

Two ways to supply the video, pick whichever you like in config.json:

  1) DROPBOX / any URL  ->  set "video_urls" (or "video_base_url" + "videos").
     Meta fetches the video from the link. Dropbox links are auto-converted to
     direct-download form, so you can paste the normal "Copy link" link as-is.

  2) LOCAL FILES (no hosting)  ->  leave the URL fields out.
     The script uploads the file's bytes straight to Meta (rupload.facebook.com).

Posts as a TRIAL REEL (shown to non-followers first) to grow outreach, rotating
through the pre-generated variations + a caption bank so each day is fresh.

Usage:
    python3 post_reel.py            # post today's reel
    python3 post_reel.py --dry-run  # show the plan, call no APIs
"""

import argparse
import datetime as dt
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")


def load_json(path, required=True):
    if not os.path.exists(path):
        if required:
            sys.exit(f"ERROR: missing file: {path}")
        return {}
    with open(path) as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def api_call(url, data=None, headers=None, raw_body=None, method=None):
    if raw_body is not None:
        body = raw_body
    elif data is not None:
        body = urllib.parse.urlencode(data).encode()
    else:
        body = None
    req = urllib.request.Request(url, data=body, headers=headers or {},
                                 method=method or ("POST" if body is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"ERROR {e.code} calling {url}\n{e.read().decode(errors='replace')}")


def normalize_dropbox(u):
    """Turn a normal Dropbox share link into a direct-download link Meta can fetch."""
    if "dropbox.com" not in u:
        return u
    u = u.replace("www.dropbox.com", "dl.dropboxusercontent.com")
    u = u.replace("://dropbox.com", "://dl.dropboxusercontent.com")
    if "dl=0" in u:
        u = u.replace("dl=0", "dl=1")
    elif "dl=1" not in u and "raw=1" not in u:
        u += ("&" if "?" in u else "?") + "dl=1"
    return u


def pick_index(n, key, state):
    idx = (state.get(key, -1) + 1) % n
    state[key] = idx
    return idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_json(os.path.join(HERE, "config.json"), required=False)
    # Credentials come from config.json OR environment variables (GitHub Secrets).
    token = cfg.get("access_token") or os.environ.get("IG_ACCESS_TOKEN")
    ig_user_id = cfg.get("ig_user_id") or os.environ.get("IG_USER_ID")
    if not token or not ig_user_id:
        sys.exit("ERROR: missing access_token / ig_user_id (set in config.json or "
                 "IG_ACCESS_TOKEN / IG_USER_ID env vars).")
    version = cfg.get("graph_version", "v21.0")
    # "graph.facebook.com" (Facebook-login flow) or "graph.instagram.com"
    # (Instagram-login flow). The Instagram-login flow is simpler to set up.
    graph_host = cfg.get("graph_host", "graph.facebook.com")
    share_to_feed = cfg.get("share_to_feed", True)
    trial_reel = cfg.get("trial_reel", True)
    graduation = cfg.get("graduation_strategy", "SS_PERFORMANCE")  # or "MANUAL"

    graph = f"https://{graph_host}/{version}"
    rupload = f"https://rupload.facebook.com/ig-api-upload/{version}"

    # ---- decide source mode -------------------------------------------------
    if cfg.get("video_urls"):
        mode = "url"
        items = [normalize_dropbox(u) for u in cfg["video_urls"]]
    elif cfg.get("video_base_url"):
        mode = "url"
        base = cfg["video_base_url"].rstrip("/")
        items = [normalize_dropbox(f"{base}/{v}") for v in cfg["videos"]]
    else:
        mode = "local"
        vdir = os.path.join(HERE, cfg.get("video_dir", "variations"))
        items = ([os.path.join(vdir, v) for v in cfg["videos"]]
                 if cfg.get("videos") else sorted(glob.glob(os.path.join(vdir, "*.mp4"))))
        items = [v for v in items if os.path.exists(v)]
    if not items:
        sys.exit("ERROR: no videos found (check config.json).")

    captions = [c.strip() for c in
                open(os.path.join(HERE, "captions.txt")).read().split("\n---\n")
                if c.strip()]
    if not captions:
        sys.exit("ERROR: captions.txt is empty")

    # Rotate through the videos forever, in order. The modulo wraps the counter
    # back to the start after the last video, so it loops 1->2->...->N->1->...
    # Preferred source of the counter is the POST_INDEX env var, which GitHub
    # Actions sets to the built-in run number (auto-increments every run). This
    # makes rotation reliable even if state.json can't be committed back to the
    # repo. Local runs (no POST_INDEX) fall back to the state.json counter.
    state = load_json(STATE_FILE, required=False)
    env_idx = os.environ.get("POST_INDEX")
    if env_idx is not None and env_idx.strip().lstrip("-").isdigit():
        seq = int(env_idx)
    else:
        seq = state.get("next_index", 0)     # ever-increasing post counter
    idx = seq % len(items)                    # wraps around -> rotates forever
    item = items[idx]
    caption = captions[idx % len(captions)]

    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{stamp}] Plan ({mode} mode):")
    print(f"  video    : {item if mode=='url' else os.path.basename(item)}")
    print(f"  caption  : {caption.splitlines()[0][:70]}...")
    print(f"  trial    : {trial_reel} (graduation={graduation})")
    print(f"  feed     : {share_to_feed}")

    if args.dry_run:
        print("DRY RUN — nothing posted, rotation not advanced.")
        return

    # ---- Step 1: create the container --------------------------------------
    params = {
        "media_type": "REELS",
        "caption": caption,
        "share_to_feed": "true" if share_to_feed else "false",
        "access_token": token,
    }
    if trial_reel:
        params["trial_params"] = json.dumps({"graduation_strategy": graduation})

    if mode == "url":
        params["video_url"] = item
    else:
        params["upload_type"] = "resumable"

    container = api_call(f"{graph}/{ig_user_id}/media", data=params)
    creation_id = container["id"]
    print(f"  container: {creation_id}")

    # ---- Step 2 (local only): upload the bytes -----------------------------
    if mode == "local":
        size = os.path.getsize(item)
        with open(item, "rb") as f:
            file_bytes = f.read()
        up = api_call(f"{rupload}/{creation_id}", raw_body=file_bytes,
                      headers={"Authorization": f"OAuth {token}", "offset": "0",
                               "file_size": str(size),
                               "Content-Type": "application/octet-stream"},
                      method="POST")
        print(f"  uploaded : {up}")

    # ---- Step 3: poll until processed --------------------------------------
    status_url = f"{graph}/{creation_id}?fields=status_code&access_token={token}"
    for _ in range(20):
        time.sleep(15)
        code = api_call(status_url).get("status_code")
        print(f"    status: {code}")
        if code == "FINISHED":
            break
        if code == "ERROR":
            sys.exit("ERROR: processing failed.")
    else:
        sys.exit("ERROR: timed out waiting for processing.")

    # ---- Step 4: publish ----------------------------------------------------
    result = api_call(f"{graph}/{ig_user_id}/media_publish",
                      data={"creation_id": creation_id, "access_token": token})
    print(f"  PUBLISHED ✓  media id: {result.get('id')}")

    state["next_index"] = idx + 1          # advance so each video posts only once
    state["last_posted"] = stamp
    state["last_media_id"] = result.get("id")
    save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()
