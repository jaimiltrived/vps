import os
import sys
import json
import time
import urllib.request
from pathlib import Path
from kaggle.api.kaggle_api_extended import KaggleApi

def load_accounts():
    """
    Loads accounts safely from:
    1. GitHub Secret / Environment Variable: KAGGLE_ACCOUNTS_JSON
    2. Local file: kaggle_accounts.json (if running locally)
    """
    env_json = os.environ.get("KAGGLE_ACCOUNTS_JSON")
    if env_json:
        try:
            data = json.loads(env_json)
            accounts = data.get("accounts", []) if isinstance(data, dict) else data
            if accounts:
                return accounts
        except Exception as e:
            print(f"[!] Error parsing KAGGLE_ACCOUNTS_JSON: {e}")

    # Fallback to local file for local testing
    local_file = Path("kaggle_accounts.json")
    if local_file.exists():
        try:
            data = json.loads(local_file.read_text(encoding="utf-8"))
            return data.get("accounts", []) if isinstance(data, dict) else data
        except Exception:
            pass

    print("[!] No Kaggle accounts configured. Please add KAGGLE_ACCOUNTS_JSON in GitHub Secrets.")
    return []

def check_account(acc):
    token = acc.get("token") or acc.get("kaggle_token")
    username = acc.get("username") or acc.get("kaggle_username")
    slug = acc.get("slug") or acc.get("notebook_slug", f"vps-master-{username}")
    
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
    token = acc.get("token") or acc.get("kaggle_token")
    username = acc.get("username") or acc.get("kaggle_username")
    slug = acc.get("slug") or acc.get("notebook_slug", f"vps-master-{username}")
    
    os.environ["KAGGLE_API_TOKEN"] = token
    kaggle_dir = Path.home() / ".kaggle"
    kaggle_dir.mkdir(parents=True, exist_ok=True)
    (kaggle_dir / "access_token").write_text(token, encoding="utf-8")
    
    api = KaggleApi()
    api.authenticate()
    print(f"[*] Deploying to Kaggle Cloud under {username}/{slug}...")
    
    node_dir = Path(f"node_{username}")
    if not node_dir.exists():
        # Fallback to template if folder for this user doesn't exist
        for candidate in Path(".").glob("node_*"):
            if candidate.is_dir():
                node_dir = candidate
                break
                
    api.kernels_push(str(node_dir))
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
    accounts = load_accounts()
    if not accounts:
        print("[!] Exiting: No accounts configured.")
        sys.exit(0)

    # Check if any account is currently running
    active_acc = None
    for idx, acc in enumerate(accounts):
        uname = acc.get("username") or acc.get("kaggle_username")
        try:
            if check_account(acc):
                print(f"[+] Account {idx+1} ({uname}) is already running!")
                active_acc = acc
                break
        except Exception as e:
            print(f"[-] Check {uname} status: {e}")

    if not active_acc:
        target = accounts[0]
        deploy_account(target)
        time.sleep(15)

    tunnel_url = poll_latest_tunnel()
    if tunnel_url:
        update_index_html(tunnel_url)
    else:
        print("[!] No new tunnel detected in time.")
