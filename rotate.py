import os
import sys
import json
import time
import urllib.request
from pathlib import Path
from kaggle.api.kaggle_api_extended import KaggleApi

ACCOUNTS = [
    {
        "username": "jaimiltrivedi",
        "token": "KGAT_672d08c43ff869eb2c9c52c71b4042a9",
        "slug": "vps-master-jaimil"
    },
    {
        "username": "jaimiltrivedi123",
        "token": "KGAT_8bfdb60bc4e95268ee6b2abccd7aa6c9",
        "slug": "vps-node-2"
    },
    {
        "username": "kripal12121",
        "token": "KGAT_c23cd7af442d6760d433f1749c7c75ca",
        "slug": "vps-node-3"
    },
    {
        "username": "kripalmisan",
        "token": "KGAT_d680a4a91c6ac9ee6aab918d0e987267",
        "slug": "vps-node-4"
    }
]

def check_account(acc):
    token = acc["token"]
    username = acc["username"]
    slug = acc["slug"]
    os.environ["KAGGLE_API_TOKEN"] = token
    kaggle_dir = Path.home() / ".kaggle"
    kaggle_dir.mkdir(parents=True, exist_ok=True)
    (kaggle_dir / "access_token").write_text(token, encoding="utf-8")
    api = KaggleApi()
    api.authenticate()
    st = api.kernels_status(f"{username}/{slug}")
    status_str = str(getattr(st, "status", "")).upper()
    return "RUNNING" in status_str

def deploy_account(acc):
    token = acc["token"]
    username = acc["username"]
    slug = acc["slug"]
    os.environ["KAGGLE_API_TOKEN"] = token
    kaggle_dir = Path.home() / ".kaggle"
    kaggle_dir.mkdir(parents=True, exist_ok=True)
    (kaggle_dir / "access_token").write_text(token, encoding="utf-8")
    api = KaggleApi()
    api.authenticate()
    print(f"[*] Deploying to Kaggle Cloud under {username}/{slug}...")
    api.kernels_push(f"node_{username}")
    print(f"[✓] Deployed {username} successfully!")

def poll_latest_tunnel():
    print("[*] Waiting for new tunnel URL from ntfy channel...")
    for _ in range(25):
        try:
            req = urllib.request.Request("https://ntfy.sh/ourvps_jaimiltrivedi_channel/json?poll=1")
            with urllib.request.urlopen(req, timeout=5) as resp:
                lines = resp.read().decode("utf-8").strip().splitlines()
                for line in reversed(lines):
                    obj = json.loads(line)
                    msg = obj.get("message", "")
                    if "trycloudflare.com" in msg and "LLAMA_URL" not in msg:
                        return msg
        except Exception:
            pass
        time.sleep(4)
    return None

def update_index_html(new_url):
    index_file = Path("index.html")
    content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Connecting to Your 24/7 Kaggle VPS...</title>
    <meta http-equiv="refresh" content="0; url={new_url}">
    <script>window.location.href = "{new_url}";</script>
</head>
<body style="background:#0b0f19;color:white;font-family:sans-serif;text-align:center;padding-top:100px;">
    <h2>🚀 Connecting to Your 24/7 Active VPS...</h2>
    <p><a href="{new_url}" style="color:#6366f1;">Click here if not redirected automatically</a></p>
</body>
</html>"""
    index_file.write_text(content, encoding="utf-8")
    print(f"[✓] Updated index.html with new live URL: {new_url}")

if __name__ == "__main__":
    # Check if any is running
    active_acc = None
    for idx, acc in enumerate(ACCOUNTS):
        try:
            if check_account(acc):
                print(f"[+] Account {idx+1} ({acc['username']}) is already running!")
                active_acc = acc
                break
        except Exception as e:
            print(f"[-] Check {acc['username']} error: {e}")

    if not active_acc:
        # Deploy first account
        target = ACCOUNTS[0]
        deploy_account(target)
        time.sleep(15)

    tunnel_url = poll_latest_tunnel()
    if tunnel_url:
        update_index_html(tunnel_url)
    else:
        print("[!] No new tunnel detected in time.")
