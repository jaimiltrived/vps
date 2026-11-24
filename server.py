#!/usr/bin/env python3
"""
Kaggle VPS Master Control Server (Ultimate Edition)
All-in-one Server & Comprehensive Control Center for Kaggle GPU instances.

Provides complete control from one web interface:
1. System & Dual-GPU Telemetry (NVIDIA T4 x2, CPU, RAM 30GB, Disk)
2. Multi-Tab Interactive Bash Web Terminals (Xterm.js + PTY)
3. 1-Click App Store (VS Code Server, JupyterLab, ComfyUI, Ollama LLM, Streamlit)
4. Advanced File Manager (Browse, Ace Editor, Upload, Download, Zip/Unzip)
5. Remote Downloader (Direct URL/Wget, Git Clone, HuggingFace Model Downloader)
6. Package Manager (Install APT & PIP packages with live progress)
7. Background Jobs Manager (Run long-running Python scripts with live log tail)
8. Multi-Port Public Tunneling (Cloudflare Tunnels)
9. Maintenance & System Tools (GPU Cache Purge, Disk Cache Clean, Full Backup Zip)
10. Active Keep-Alive Guard (Audio ping + heartbeat to prevent Kaggle idle disconnects)
"""

import os
import sys
import json
import time
import shutil
import asyncio
import subprocess
import threading
import urllib.request
from pathlib import Path
from typing import Optional, List, Dict, Any

try:
    import psutil
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Query, HTTPException, Request
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn
except ImportError:
    print("[*] Installing required packages: fastapi uvicorn psutil python-multipart websockets...")
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "-q",
        "fastapi", "uvicorn", "psutil", "python-multipart", "websockets"
    ])
    import psutil
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Query, HTTPException, Request
    from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn

# Linux PTY modules
IS_LINUX = sys.platform.startswith("linux")
if IS_LINUX:
    import pty
    import termios
    import struct
    import fcntl
    import select

app = FastAPI(title="Cloud VPS Master Control")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

START_TIME = time.time()
ACTIVE_TUNNELS: Dict[int, Dict[str, Any]] = {}
MANAGED_JOBS: Dict[str, Dict[str, Any]] = {}
MANAGED_APPS: Dict[str, Dict[str, Any]] = {}
CLOUDFLARED_PATH = Path("/tmp/cloudflared") if IS_LINUX else Path("cloudflared.exe")

# Define 1-Click Apps
CATALOG_APPS = {
    "vscode": {
        "name": "VS Code Server",
        "desc": "Full in-browser Visual Studio Code IDE with terminal, extensions, and file tree.",
        "port": 8080,
        "icon": "fa-solid fa-code text-cyan-400",
        "install_cmd": "curl -fsSL https://code-server.dev/install.sh | sh",
        "run_cmd": "code-server --bind-addr 0.0.0.0:8080 --auth none"
    },
    "jupyterlab": {
        "name": "JupyterLab",
        "desc": "Next-generation web-based interface for Project Jupyter notebooks.",
        "port": 8888,
        "icon": "fa-solid fa-book text-amber-400",
        "install_cmd": "pip install -q jupyterlab",
        "run_cmd": "jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --allow-root --NotebookApp.token=''"
    },
    "ollama": {
        "name": "Ollama LLM Server",
        "desc": "Run local LLMs (DeepSeek, Llama 3, Qwen) on Dual T4 GPUs.",
        "port": 11434,
        "icon": "fa-solid fa-brain text-indigo-400",
        "install_cmd": "curl -fsSL https://ollama.com/install.sh | sh",
        "run_cmd": "ollama serve"
    },
    "streamlit": {
        "name": "Streamlit Demo",
        "desc": "Interactive Python Web App Framework.",
        "port": 8501,
        "icon": "fa-solid fa-chart-pie text-rose-400",
        "install_cmd": "pip install -q streamlit",
        "run_cmd": "streamlit hello --server.port 8501 --server.address 0.0.0.0"
    },
    "filebrowser": {
        "name": "FileBrowser Pro",
        "desc": "Modern web file manager with file preview, media player, and multi-file upload.",
        "port": 8085,
        "icon": "fa-solid fa-folder-open text-emerald-400",
        "install_cmd": "curl -fsSL https://raw.githubusercontent.com/filebrowser/get/master/get.sh | bash",
        "run_cmd": "filebrowser -r /kaggle/working -a 0.0.0.0 -p 8085 --noauth"
    }
}

# --- Cloudflared Tunnel Utility ---
def ensure_cloudflared() -> str:
    if shutil.which("cloudflared"):
        return "cloudflared"
    if CLOUDFLARED_PATH.exists() and CLOUDFLARED_PATH.stat().st_size > 10 * 1024 * 1024:
        return str(CLOUDFLARED_PATH.resolve())
    
    print("[*] Downloading cloudflared binary (high-speed)...", flush=True)
    if IS_LINUX:
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
        cmd = f"curl -sSL -L '{url}' -o '{CLOUDFLARED_PATH}' && chmod +x '{CLOUDFLARED_PATH}'"
        res = subprocess.run(cmd, shell=True)
        if res.returncode == 0 and CLOUDFLARED_PATH.exists():
            print("[✓] cloudflared downloaded successfully.", flush=True)
            return str(CLOUDFLARED_PATH.resolve())
    elif sys.platform == "darwin":
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-amd64"
    else:
        url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    
    try:
        urllib.request.urlretrieve(url, str(CLOUDFLARED_PATH))
        if IS_LINUX:
            os.chmod(str(CLOUDFLARED_PATH), 0o755)
        print("[✓] cloudflared downloaded successfully.", flush=True)
        return str(CLOUDFLARED_PATH.resolve())
    except Exception as e:
        print(f"[!] Error downloading cloudflared: {e}", flush=True)
        return "cloudflared"

def start_cloudflare_tunnel(port: int, label: str = "app") -> Optional[str]:
    bin_path = ensure_cloudflared()
    log_file = f"/tmp/tunnel_{port}.log" if IS_LINUX else f"tunnel_{port}.log"
    cmd = [bin_path, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"]
    
    out_f = open(log_file, "w")
    proc = subprocess.Popen(cmd, stdout=out_f, stderr=subprocess.STDOUT, text=True)
    
    tunnel_url = None
    for _ in range(30):
        time.sleep(1)
        if os.path.exists(log_file):
            with open(log_file, "r") as f:
                content = f.read()
                for line in content.splitlines():
                    if "trycloudflare.com" in line and "api.trycloudflare.com" not in line:
                        for word in line.split():
                            clean = word.strip('"\' ,;:')
                            if clean.startswith("https://") and "trycloudflare.com" in clean and "api.trycloudflare.com" not in clean:
                                tunnel_url = clean
                                break
                    if tunnel_url:
                        break
        if tunnel_url:
            break
            
    ACTIVE_TUNNELS[port] = {
        "port": port,
        "label": label,
        "url": tunnel_url,
        "pid": proc.pid
    }
    return tunnel_url

# --- GPU Telemetry & Management ---
def get_gpu_metrics() -> List[Dict[str, Any]]:
    gpus = []
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return gpus

    try:
        query_cmd = [
            nvidia_smi,
            "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits"
        ]
        result = subprocess.run(query_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
        if result.returncode == 0:
            lines = [line.strip() for line in result.stdout.strip().split("\n") if line.strip()]
            for line in lines:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 6:
                    idx = parts[0]
                    name = parts[1]
                    util = float(parts[2]) if parts[2].replace('.', '', 1).isdigit() else 0.0
                    mem_used = float(parts[3]) if parts[3].replace('.', '', 1).isdigit() else 0.0
                    mem_total = float(parts[4]) if parts[4].replace('.', '', 1).isdigit() else 0.0
                    temp = float(parts[5]) if parts[5].replace('.', '', 1).isdigit() else 0.0
                    power = parts[6] if len(parts) > 6 else "N/A"
                    gpus.append({
                        "index": idx,
                        "name": name,
                        "utilization": util,
                        "memory_used_mb": mem_used,
                        "memory_total_mb": mem_total,
                        "memory_percent": round((mem_used / mem_total * 100), 1) if mem_total > 0 else 0,
                        "temperature": temp,
                        "power": power
                    })
    except Exception:
        pass
    return gpus

def get_system_stats() -> Dict[str, Any]:
    uptime_sec = int(time.time() - START_TIME)
    hours, remainder = divmod(uptime_sec, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"

    # Real Physical Host Hardware Telemetry
    cpu_percent = psutil.cpu_percent(interval=None)
    cpu_cores_logical = psutil.cpu_count(logical=True) or 16
    cpu_cores_physical = psutil.cpu_count(logical=False) or 10

    vm = psutil.virtual_memory()
    real_ram_total_gb = round(vm.total / (1024**3), 1)
    real_ram_used_gb = round(vm.used / (1024**3), 2)
    ram_pct = vm.percent

    drive_path = os.path.splitdrive(os.getcwd())[0] + "\\" if sys.platform == "win32" else "/"
    disk = psutil.disk_usage(drive_path)
    real_disk_total_gb = round(disk.total / (1024**3), 1)
    real_disk_used_gb = round(disk.used / (1024**3), 1)
    real_disk_free_gb = round(disk.free / (1024**3), 1)
    disk_pct = disk.percent

    gpus = get_gpu_metrics()

    import platform
    os_name = f"{platform.system()} {platform.release()}"

    return {
        "uptime": uptime_str,
        "uptime_sec": uptime_sec,
        "cpu_percent": cpu_percent,
        "cpu_cores": cpu_cores_logical,
        "cpu_cores_physical": cpu_cores_physical,
        "ram": {
            "used_gb": real_ram_used_gb,
            "total_gb": real_ram_total_gb,
            "percent": ram_pct
        },
        "disk": {
            "path": drive_path,
            "used_gb": real_disk_used_gb,
            "total_gb": real_disk_total_gb,
            "free_gb": real_disk_free_gb,
            "percent": disk_pct
        },
        "os_name": os_name,
        "gpus": gpus,
        "os": sys.platform
    }

# ==================== SECURITY FIREWALL & AUTH ====================
VPS_PASSWORD = os.environ.get("VPS_PASSWORD", "vpsadmin123")
AUTH_TOKENS = set()
FAILED_ATTEMPTS: Dict[str, Dict[str, Any]] = {}

def is_authenticated(req: Request) -> bool:
    auth_header = req.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "").strip() if auth_header.startswith("Bearer ") else req.cookies.get("vps_token", "")
    return token in AUTH_TOKENS

@app.post("/api/auth/login")
async def api_auth_login(req: Request):
    client_ip = req.client.host if req.client else "unknown"
    now = time.time()
    if client_ip in FAILED_ATTEMPTS:
        attempt = FAILED_ATTEMPTS[client_ip]
        if now < attempt.get("lockout_until", 0):
            wait_sec = int(attempt["lockout_until"] - now)
            raise HTTPException(status_code=429, detail=f"Firewall Active: Too many failed attempts. Locked for {wait_sec}s.")
    try:
        body = await req.json()
    except Exception:
        body = {}
    password = body.get("password", "")
    if password == VPS_PASSWORD:
        if client_ip in FAILED_ATTEMPTS:
            del FAILED_ATTEMPTS[client_ip]
        import secrets
        token = secrets.token_hex(24)
        AUTH_TOKENS.add(token)
        resp = JSONResponse({"status": "ok", "token": token})
        resp.set_cookie(key="vps_token", value=token, max_age=86400 * 30, httponly=False)
        return resp
    else:
        if client_ip not in FAILED_ATTEMPTS:
            FAILED_ATTEMPTS[client_ip] = {"count": 1, "lockout_until": 0}
        else:
            FAILED_ATTEMPTS[client_ip]["count"] += 1
            if FAILED_ATTEMPTS[client_ip]["count"] >= 5:
                FAILED_ATTEMPTS[client_ip]["lockout_until"] = now + 300
        raise HTTPException(status_code=401, detail="Invalid master password. Access denied.")

@app.get("/api/auth/status")
async def api_auth_status(req: Request):
    return {"authenticated": is_authenticated(req)}

@app.post("/api/auth/logout")
async def api_auth_logout(req: Request):
    token = req.cookies.get("vps_token", "")
    if token in AUTH_TOKENS:
        AUTH_TOKENS.remove(token)
    resp = JSONResponse({"status": "ok"})
    resp.delete_cookie("vps_token")
    return resp

# ==================== API ENDPOINTS ====================

@app.get("/api/stats")
async def api_stats():
    return get_system_stats()

# --- GPU Control ---
@app.post("/api/gpu/purge")
async def api_gpu_purge():
    purged_items = []
    # 1. PyTorch memory cache empty
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
            purged_items.append("PyTorch CUDA cache cleared")
    except Exception:
        pass
    
    # 2. Python garbage collect
    import gc
    gc.collect()
    purged_items.append("Python GC collected")
    return {"status": "ok", "message": ", ".join(purged_items)}

# --- File Manager Endpoints ---
@app.get("/api/files/list")
async def api_files_list(path: str = Query("/kaggle/working" if os.path.exists("/kaggle/working") else ".")):
    target = Path(path).resolve()
    if not target.exists():
        raise HTTPException(status_code=404, detail="Directory does not exist")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    items = []
    try:
        for entry in os.scandir(str(target)):
            try:
                st = entry.stat()
                items.append({
                    "name": entry.name,
                    "path": str(Path(entry.path).resolve()),
                    "is_dir": entry.is_dir(),
                    "size": st.st_size if not entry.is_dir() else 0,
                    "modified": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))
                })
            except Exception:
                continue
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return {
        "current_path": str(target),
        "parent_path": str(target.parent) if target.parent != target else None,
        "items": items
    }

@app.get("/api/files/read")
async def api_files_read(path: str):
    target = Path(path).resolve()
    if not target.exists() or target.is_dir():
        raise HTTPException(status_code=404, detail="File not found")
    if target.stat().st_size > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large to open in web editor (> 8MB)")
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        return {"path": str(target), "content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/files/write")
async def api_files_write(req: Request):
    body = await req.json()
    path_str = body.get("path")
    content = body.get("content", "")
    if not path_str:
        raise HTTPException(status_code=400, detail="Path missing")
    target = Path(path_str).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"status": "ok", "path": str(target)}

@app.post("/api/files/mkdir")
async def api_files_mkdir(req: Request):
    body = await req.json()
    path_str = body.get("path")
    if not path_str:
        raise HTTPException(status_code=400, detail="Path missing")
    target = Path(path_str).resolve()
    target.mkdir(parents=True, exist_ok=True)
    return {"status": "ok", "path": str(target)}

@app.post("/api/files/delete")
async def api_files_delete(req: Request):
    body = await req.json()
    path_str = body.get("path")
    if not path_str:
        raise HTTPException(status_code=400, detail="Path missing")
    target = Path(path_str).resolve()
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"status": "ok"}

@app.post("/api/files/upload")
async def api_files_upload(directory: str = Form(...), file: UploadFile = File(...)):
    target_dir = Path(directory).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / file.filename
    with dest.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"status": "ok", "saved_to": str(dest)}

@app.get("/api/files/download")
async def api_files_download(path: str):
    target = Path(path).resolve()
    if not target.exists() or target.is_dir():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path=str(target), filename=target.name)

# --- 1-Click App Store Endpoints ---
@app.get("/api/apps/list")
async def api_apps_list():
    result = []
    for app_id, app_info in CATALOG_APPS.items():
        state = MANAGED_APPS.get(app_id, {})
        running = False
        pid = state.get("pid")
        if pid and psutil.pid_exists(pid):
            running = True
        
        tunnel_url = ACTIVE_TUNNELS.get(app_info["port"], {}).get("url")
        result.append({
            "id": app_id,
            "name": app_info["name"],
            "desc": app_info["desc"],
            "port": app_info["port"],
            "icon": app_info["icon"],
            "running": running,
            "pid": pid if running else None,
            "tunnel_url": tunnel_url if running else None
        })
    return result

@app.post("/api/apps/launch")
async def api_apps_launch(req: Request):
    body = await req.json()
    app_id = body.get("app_id")
    if app_id not in CATALOG_APPS:
        raise HTTPException(status_code=404, detail="App not found in catalog")
    
    app_meta = CATALOG_APPS[app_id]
    port = app_meta["port"]

    def run_launcher():
        log_file = f"/tmp/app_{app_id}.log"
        print(f"[*] Installing & starting {app_meta['name']}...")
        full_script = f"({app_meta['install_cmd']}) && ({app_meta['run_cmd']})"
        f_out = open(log_file, "w")
        proc = subprocess.Popen(
            full_script,
            shell=True,
            stdout=f_out,
            stderr=subprocess.STDOUT,
            cwd="/kaggle/working" if os.path.exists("/kaggle/working") else "/root"
        )
        MANAGED_APPS[app_id] = {"pid": proc.pid, "log": log_file}
        # Start tunnel
        time.sleep(3)
        start_cloudflare_tunnel(port, app_meta["name"])

    threading.Thread(target=run_launcher, daemon=True).start()
    return {"status": "ok", "message": f"{app_meta['name']} launch initiated"}

@app.post("/api/apps/stop")
async def api_apps_stop(req: Request):
    body = await req.json()
    app_id = body.get("app_id")
    if app_id in MANAGED_APPS and MANAGED_APPS[app_id].get("pid"):
        pid = MANAGED_APPS[app_id]["pid"]
        try:
            p = psutil.Process(pid)
            for child in p.children(recursive=True):
                child.terminate()
            p.terminate()
        except Exception:
            pass
        MANAGED_APPS[app_id]["pid"] = None
    return {"status": "ok"}

# --- Downloader & Remote Tools ---
@app.post("/api/tools/download_url")
async def api_download_url(req: Request):
    body = await req.json()
    url = body.get("url")
    output_dir = body.get("dir", "/kaggle/working" if os.path.exists("/kaggle/working") else ".")
    if not url:
        raise HTTPException(status_code=400, detail="URL missing")
    
    job_id = f"dl_{int(time.time())}"
    log_file = f"/tmp/{job_id}.log"
    cmd = f"cd '{output_dir}' && wget -c '{url}'"
    f_out = open(log_file, "w")
    proc = subprocess.Popen(cmd, shell=True, stdout=f_out, stderr=subprocess.STDOUT)
    MANAGED_JOBS[job_id] = {
        "id": job_id,
        "name": f"Download: {url[:40]}...",
        "pid": proc.pid,
        "log": log_file,
        "start_time": time.time()
    }
    return {"status": "ok", "job_id": job_id}

@app.post("/api/tools/git_clone")
async def api_git_clone(req: Request):
    body = await req.json()
    repo_url = body.get("url")
    target_dir = body.get("dir", "/kaggle/working" if os.path.exists("/kaggle/working") else ".")
    if not repo_url:
        raise HTTPException(status_code=400, detail="Repo URL missing")
    
    job_id = f"git_{int(time.time())}"
    log_file = f"/tmp/{job_id}.log"
    cmd = f"cd '{target_dir}' && git clone {repo_url}"
    f_out = open(log_file, "w")
    proc = subprocess.Popen(cmd, shell=True, stdout=f_out, stderr=subprocess.STDOUT)
    MANAGED_JOBS[job_id] = {
        "id": job_id,
        "name": f"Git Clone: {repo_url[:40]}",
        "pid": proc.pid,
        "log": log_file,
        "start_time": time.time()
    }
    return {"status": "ok", "job_id": job_id}

@app.post("/api/tools/huggingface_download")
async def api_hf_download(req: Request):
    body = await req.json()
    repo_id = body.get("repo_id")
    target_dir = body.get("dir", "/kaggle/working" if os.path.exists("/kaggle/working") else ".")
    if not repo_id:
        raise HTTPException(status_code=400, detail="Model Repo ID missing")
    
    job_id = f"hf_{int(time.time())}"
    log_file = f"/tmp/{job_id}.log"
    py_code = f"from huggingface_hub import snapshot_download; snapshot_download(repo_id='{repo_id}', local_dir='{target_dir}/{repo_id.split('/')[-1]}')"
    cmd = f"pip install -q huggingface_hub && python3 -c \"{py_code}\""
    f_out = open(log_file, "w")
    proc = subprocess.Popen(cmd, shell=True, stdout=f_out, stderr=subprocess.STDOUT)
    MANAGED_JOBS[job_id] = {
        "id": job_id,
        "name": f"HuggingFace: {repo_id}",
        "pid": proc.pid,
        "log": log_file,
        "start_time": time.time()
    }
    return {"status": "ok", "job_id": job_id}

@app.post("/api/tools/zip_archive")
async def api_zip_archive():
    target = "/kaggle/working" if os.path.exists("/kaggle/working") else "."
    dest_zip = "/tmp/kaggle_vps_backup.zip"
    if os.path.exists(dest_zip):
        os.remove(dest_zip)
    cmd = f"zip -r -q {dest_zip} {target}"
    proc = subprocess.run(cmd, shell=True)
    if proc.returncode == 0 and os.path.exists(dest_zip):
        return FileResponse(dest_zip, filename="kaggle_vps_backup.zip")
    raise HTTPException(status_code=500, detail="Failed to create zip archive")

@app.post("/api/tools/clean_cache")
async def api_clean_cache():
    cmd = "rm -rf ~/.cache/pip /tmp/* /root/.cache/* 2>/dev/null || true"
    subprocess.run(cmd, shell=True)
    return {"status": "ok", "message": "Disk caches and /tmp cleaned successfully"}

# --- Package Manager Endpoints ---
@app.get("/api/packages/pip/list")
async def api_pip_list():
    try:
        proc = subprocess.run([sys.executable, "-m", "pip", "list", "--format=json"], stdout=subprocess.PIPE, text=True, timeout=5)
        if proc.returncode == 0:
            return json.loads(proc.stdout)
    except Exception:
        pass
    return []

@app.post("/api/packages/install")
async def api_packages_install(req: Request):
    body = await req.json()
    pkg_type = body.get("type", "pip") # 'pip' or 'apt'
    pkg_name = body.get("name", "").strip()
    if not pkg_name:
        raise HTTPException(status_code=400, detail="Package name missing")
    
    job_id = f"pkg_{int(time.time())}"
    log_file = f"/tmp/{job_id}.log"
    if pkg_type == "pip":
        cmd = f"{sys.executable} -m pip install {pkg_name}"
    else:
        cmd = f"DEBIAN_FRONTEND=noninteractive apt-get update && apt-get install -y {pkg_name}"
        
    f_out = open(log_file, "w")
    proc = subprocess.Popen(cmd, shell=True, stdout=f_out, stderr=subprocess.STDOUT)
    MANAGED_JOBS[job_id] = {
        "id": job_id,
        "name": f"Install {pkg_type.upper()}: {pkg_name}",
        "pid": proc.pid,
        "log": log_file,
        "start_time": time.time()
    }
    return {"status": "ok", "job_id": job_id}

# --- Background Jobs Manager ---
@app.get("/api/jobs/list")
async def api_jobs_list():
    results = []
    for jid, job in list(MANAGED_JOBS.items()):
        pid = job.get("pid")
        running = pid and psutil.pid_exists(pid)
        log_size = os.path.getsize(job["log"]) if os.path.exists(job["log"]) else 0
        results.append({
            "id": jid,
            "name": job["name"],
            "pid": pid,
            "running": running,
            "log_size": log_size,
            "elapsed": int(time.time() - job["start_time"])
        })
    return results

@app.get("/api/jobs/logs")
async def api_jobs_logs(id: str = Query(...)):
    if id not in MANAGED_JOBS:
        raise HTTPException(status_code=404, detail="Job not found")
    log_path = MANAGED_JOBS[id]["log"]
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
            return {"logs": "".join(lines[-150:])}
    return {"logs": "No logs yet."}

@app.post("/api/jobs/stop")
async def api_jobs_stop(req: Request):
    body = await req.json()
    jid = body.get("id")
    if jid in MANAGED_JOBS and MANAGED_JOBS[jid].get("pid"):
        try:
            psutil.Process(MANAGED_JOBS[jid]["pid"]).terminate()
        except Exception:
            pass
    return {"status": "ok"}

# --- Process Manager ---
@app.get("/api/processes")
async def api_processes():
    procs = []
    for p in psutil.process_iter(['pid', 'name', 'username', 'cpu_percent', 'memory_percent', 'cmdline']):
        try:
            info = p.info
            cmdline = " ".join(info['cmdline']) if info.get('cmdline') else info['name']
            procs.append({
                "pid": info['pid'],
                "name": info['name'],
                "user": info.get('username') or 'root',
                "cpu": round(info.get('cpu_percent') or 0.0, 1),
                "mem": round(info.get('memory_percent') or 0.0, 1),
                "cmd": cmdline[:150]
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    procs.sort(key=lambda x: (x['cpu'] + x['mem']), reverse=True)
    return procs[:50]

@app.post("/api/processes/kill")
async def api_processes_kill(req: Request):
    body = await req.json()
    pid = body.get("pid")
    if not pid:
        raise HTTPException(status_code=400, detail="PID missing")
    try:
        p = psutil.Process(int(pid))
        p.terminate()
        return {"status": "ok", "message": f"PID {pid} terminated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Tunnels ---
@app.get("/api/tunnels")
async def api_tunnels_list():
    return list(ACTIVE_TUNNELS.values())

@app.post("/api/tunnels/start")
async def api_tunnels_start(req: Request):
    body = await req.json()
    port = int(body.get("port", 8080))
    label = body.get("label", f"port-{port}")
    if port in ACTIVE_TUNNELS and ACTIVE_TUNNELS[port].get("url"):
        return ACTIVE_TUNNELS[port]
    url = start_cloudflare_tunnel(port, label)
    return {"port": port, "label": label, "url": url}

# --- Multi-Session PTY Terminal WebSocket ---
@app.websocket("/ws/terminal")
async def websocket_terminal(websocket: WebSocket, session_id: str = "main"):
    await websocket.accept()

    if not IS_LINUX:
        await websocket.send_text("\r\n[!] Interactive Linux PTY bash requires Linux (Kaggle). Mock cmd started.\r\n")
        proc = subprocess.Popen(["cmd.exe"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=False)
        loop = asyncio.get_event_loop()

        def read_stdout():
            while True:
                data = proc.stdout.read(1024)
                if not data:
                    break
                asyncio.run_coroutine_threadsafe(websocket.send_bytes(data), loop)

        threading.Thread(target=read_stdout, daemon=True).start()
        try:
            while True:
                msg = await websocket.receive_text()
                proc.stdin.write(msg.encode())
                proc.stdin.flush()
        except WebSocketDisconnect:
            proc.kill()
        return

    # True Linux pseudo-terminal for Kaggle
    master_fd, slave_fd = pty.openpty()
    env = os.environ.copy()
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    env["SHELL"] = "/bin/bash"
    env["HOME"] = "/root"
    env["PS1"] = rf"\[\033[01;32m\]kaggle-vps[{session_id}]\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ "

    proc = subprocess.Popen(
        ["/bin/bash", "--login"],
        preexec_fn=os.setsid,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        universal_newlines=False,
        close_fds=True,
        env=env,
        cwd="/kaggle/working" if os.path.exists("/kaggle/working") else "/root"
    )
    os.close(slave_fd)

    fl = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

    loop = asyncio.get_event_loop()

    def set_winsize(fd, rows, cols):
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)

    set_winsize(master_fd, 24, 80)

    def terminal_reader():
        while True:
            try:
                r, _, _ = select.select([master_fd], [], [], 0.05)
                if r:
                    data = os.read(master_fd, 4096)
                    if data:
                        asyncio.run_coroutine_threadsafe(websocket.send_bytes(data), loop)
                    else:
                        break
            except Exception:
                break
        try:
            os.close(master_fd)
        except Exception:
            pass

    reader_thread = threading.Thread(target=terminal_reader, daemon=True)
    reader_thread.start()

    try:
        while True:
            message = await websocket.receive()
            if "text" in message:
                try:
                    payload = json.loads(message["text"])
                    if payload.get("type") == "resize":
                        cols = int(payload.get("cols", 80))
                        rows = int(payload.get("rows", 24))
                        set_winsize(master_fd, rows, cols)
                        continue
                except Exception:
                    pass
                os.write(master_fd, message["text"].encode())
            elif "bytes" in message:
                os.write(master_fd, message["bytes"])
    except WebSocketDisconnect:
        pass
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=1)
        except Exception:
            pass

# --- Embedded Web Cockpit UI ---
HTML_INDEX = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Cloud VPS Master Cockpit</title>
  <!-- Tailwind CSS CDN -->
  <script src="https://cdn.tailwindcss.com"></script>
  <!-- Xterm.js & addons -->
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.css">
  <script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/xterm-addon-web-links@0.9.0/lib/xterm-addon-web-links.min.js"></script>
  <!-- Ace Editor -->
  <script src="https://cdnjs.cloudflare.com/ajax/libs/ace/1.32.7/ace.js"></script>
  <!-- FontAwesome -->
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            surface: { 950: '#090d16', 900: '#0f172a', 850: '#141e33', 800: '#1e293b', 700: '#334155' }
          }
        }
      }
    }
  </script>
  <style>
    body { background-color: #090d16; color: #e2e8f0; font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; }
    .custom-scrollbar::-webkit-scrollbar { width: 6px; height: 6px; }
    .custom-scrollbar::-webkit-scrollbar-track { background: #0f172a; }
    .custom-scrollbar::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
  </style>
</head>
<body class="h-screen flex flex-col overflow-hidden">

  <!-- Security Firewall & Master Password Modal -->
  <div id="vps-auth-modal" class="fixed inset-0 z-50 bg-[#090d16]/95 backdrop-blur-md flex items-center justify-center p-4">
    <div class="bg-surface-900 border border-indigo-500/40 rounded-2xl p-8 max-w-sm w-full shadow-2xl shadow-indigo-500/10 text-center">
      <div class="w-14 h-14 mx-auto mb-4 rounded-xl bg-indigo-500/10 border border-indigo-500/30 flex items-center justify-center text-indigo-400 text-2xl">
        <i class="fa-solid fa-shield-halved"></i>
      </div>
      <h2 class="text-xl font-bold text-white mb-1">VPS Security Firewall</h2>
      <p class="text-xs text-slate-400 mb-6">Enter your master password to unlock your Cloud VPS Cockpit.</p>
      <div class="space-y-4">
        <input type="password" id="auth-pwd-input" placeholder="Enter master password..." class="w-full bg-surface-950 border border-surface-700 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-indigo-500 text-center tracking-widest font-mono" onkeydown="if(event.key==='Enter') submitAuth()">
        <button onclick="submitAuth()" class="w-full py-3 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-bold text-sm transition shadow-lg shadow-indigo-600/30">
          <i class="fa-solid fa-lock-open mr-1.5"></i> Unlock VPS Cockpit
        </button>
        <div id="auth-error-msg" class="text-xs text-rose-400 font-semibold hidden">Incorrect password. Access denied.</div>
        <p class="text-[11px] text-slate-500">Default master password: <span class="text-indigo-400 font-mono">vpsadmin123</span></p>
      </div>
    </div>
  </div>

  <!-- Top Master Bar -->
  <header class="h-14 bg-surface-900 border-b border-surface-700/60 px-4 flex items-center justify-between shrink-0 z-20">
    <div class="flex items-center space-x-3">
      <div class="w-8 h-8 rounded-lg bg-gradient-to-tr from-indigo-500 via-purple-500 to-pink-500 flex items-center justify-center text-white shadow-md shadow-indigo-500/25">
        <i class="fa-solid fa-cloud-bolt text-sm"></i>
      </div>
      <div>
        <div class="flex items-center space-x-2">
          <span class="font-extrabold text-base tracking-wide text-white">CLOUD<span class="text-indigo-400">VPS</span></span>
          <span class="px-2 py-0.5 text-xs font-semibold rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 flex items-center gap-1">
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span> HYBRID ONLINE
          </span>
        </div>
      </div>
    </div>

    <!-- Live Telemetry Badges -->
    <div class="hidden lg:flex items-center space-x-3 text-xs">
      <div class="flex items-center space-x-1.5 bg-surface-850 px-2.5 py-1 rounded-md border border-surface-700/50">
        <i class="fa-solid fa-clock text-slate-400"></i>
        <span class="text-slate-400">Uptime:</span>
        <span id="nav-uptime" class="font-mono text-slate-200">0h 0m</span>
      </div>
      <div class="flex items-center space-x-1.5 bg-surface-850 px-2.5 py-1 rounded-md border border-surface-700/50">
        <i class="fa-solid fa-microchip text-indigo-400"></i>
        <span class="text-slate-400">CPU:</span>
        <span id="nav-cpu" class="font-mono font-bold text-slate-200">0%</span>
      </div>
      <div class="flex items-center space-x-1.5 bg-surface-850 px-2.5 py-1 rounded-md border border-surface-700/50">
        <i class="fa-solid fa-memory text-cyan-400"></i>
        <span class="text-slate-400">RAM:</span>
        <span id="nav-ram" class="font-mono font-bold text-slate-200">0 GB</span>
      </div>
      <a href="https://colab.research.google.com/github/jaimiltrived/vps/blob/main/Colab_GPU_Worker.ipynb" target="_blank" title="Launch 16GB Tesla T4 GPU in Google Colab" class="flex items-center space-x-1.5 bg-surface-850 hover:bg-amber-500/20 px-2.5 py-1 rounded-md border border-surface-700/50 hover:border-amber-500/40 cursor-pointer transition">
        <i class="fa-solid fa-bolt text-amber-400"></i>
        <span class="text-slate-400">GPU:</span>
        <span id="nav-gpu" class="font-mono font-bold text-amber-300">Activate T4 ⚡</span>
      </a>
    </div>

    <!-- Quick Action Hub -->
    <div class="flex items-center space-x-2">
      <!-- Security Firewall Badge -->
      <div class="hidden sm:flex items-center space-x-1.5 px-2.5 py-1 rounded-md bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 text-xs font-semibold">
        <i class="fa-solid fa-shield-halved"></i> Firewall Active
      </div>
      <button onclick="logoutAuth()" title="Lock Cockpit" class="px-2 py-1 text-xs rounded-md bg-surface-800 hover:bg-rose-500/20 text-slate-400 hover:text-rose-400 border border-surface-700 hover:border-rose-500/40 transition">
        <i class="fa-solid fa-lock"></i>
      </button>
      <button onclick="purgeGpuMemory()" title="Purge GPU VRAM & Cache" class="px-2.5 py-1 rounded-md text-xs font-semibold bg-surface-800 hover:bg-surface-700 text-amber-300 border border-surface-700 flex items-center gap-1.5 transition">
        <i class="fa-solid fa-broom"></i> Purge VRAM
      </button>
      <button onclick="toggleAudioPing()" id="btn-keepalive" title="Prevent Kaggle session sleep" class="px-2.5 py-1 rounded-md text-xs font-semibold bg-surface-800 hover:bg-surface-700 text-slate-300 border border-surface-700 flex items-center gap-1.5 transition">
        <i class="fa-solid fa-shield-heart text-rose-400" id="icon-keepalive"></i> Keep-Alive
      </button>
    </div>
  </header>

  <!-- Container -->
  <div class="flex-1 flex overflow-hidden">
    <!-- Sidebar Navigation -->
    <aside class="w-16 md:w-56 bg-surface-900 border-r border-surface-700/60 flex flex-col justify-between shrink-0">
      <nav class="p-2 space-y-1">
        <button onclick="switchTab('dashboard')" id="tab-btn-dashboard" class="tab-button w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-sm font-semibold text-white bg-indigo-600/30 border border-indigo-500/40 transition">
          <i class="fa-solid fa-gauge-high text-lg w-5 text-center text-indigo-400"></i>
          <span class="hidden md:inline">Dashboard</span>
        </button>
        <button onclick="switchTab('terminal')" id="tab-btn-terminal" class="tab-button w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-sm font-semibold text-slate-400 hover:text-white hover:bg-surface-800 transition">
          <i class="fa-solid fa-terminal text-lg w-5 text-center text-emerald-400"></i>
          <span class="hidden md:inline">Web Terminal</span>
        </button>
        <button onclick="switchTab('apps')" id="tab-btn-apps" class="tab-button w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-sm font-semibold text-slate-400 hover:text-white hover:bg-surface-800 transition">
          <i class="fa-solid fa-shapes text-lg w-5 text-center text-pink-400"></i>
          <span class="hidden md:inline">1-Click Apps</span>
        </button>
        <button onclick="switchTab('files')" id="tab-btn-files" class="tab-button w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-sm font-semibold text-slate-400 hover:text-white hover:bg-surface-800 transition">
          <i class="fa-solid fa-folder-tree text-lg w-5 text-center text-amber-400"></i>
          <span class="hidden md:inline">File Manager</span>
        </button>
        <button onclick="switchTab('downloader')" id="tab-btn-downloader" class="tab-button w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-sm font-semibold text-slate-400 hover:text-white hover:bg-surface-800 transition">
          <i class="fa-solid fa-cloud-arrow-down text-lg w-5 text-center text-cyan-400"></i>
          <span class="hidden md:inline">Remote Tools</span>
        </button>
        <button onclick="switchTab('packages')" id="tab-btn-packages" class="tab-button w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-sm font-semibold text-slate-400 hover:text-white hover:bg-surface-800 transition">
          <i class="fa-solid fa-box-open text-lg w-5 text-center text-purple-400"></i>
          <span class="hidden md:inline">Packages</span>
        </button>
        <button onclick="switchTab('processes')" id="tab-btn-processes" class="tab-button w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-sm font-semibold text-slate-400 hover:text-white hover:bg-surface-800 transition">
          <i class="fa-solid fa-list-check text-lg w-5 text-center text-blue-400"></i>
          <span class="hidden md:inline">Processes & Jobs</span>
        </button>
        <button onclick="switchTab('tunnels')" id="tab-btn-tunnels" class="tab-button w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-sm font-semibold text-slate-400 hover:text-white hover:bg-surface-800 transition">
          <i class="fa-solid fa-network-wired text-lg w-5 text-center text-orange-400"></i>
          <span class="hidden md:inline">Port Tunnels</span>
        </button>
      </nav>

      <!-- Sidebar Session Countdown -->
      <div class="p-3 border-t border-surface-700/60 hidden md:block text-xs text-slate-400">
        <div class="flex items-center justify-between mb-1">
          <span>24/7 Permanent Session</span>
          <span id="sidebar-timer">Unlimited</span>
        </div>
        <div class="w-full bg-surface-800 rounded-full h-1.5 overflow-hidden">
          <div id="session-progress" class="bg-emerald-500 h-1.5 rounded-full" style="width: 100%"></div>
        </div>
      </div>
    </aside>

    <!-- Main View Area -->
    <main class="flex-1 bg-surface-950/60 overflow-y-auto custom-scrollbar relative flex flex-col">

      <!-- ================= 1. DASHBOARD ================= -->
      <section id="view-dashboard" class="tab-view p-6 space-y-6">
        <div>
          <h1 class="text-2xl font-black text-white tracking-tight flex items-center gap-2">
            <span>🚀 Real Host System Telemetry</span>
            <span class="px-2 py-0.5 rounded text-[11px] font-mono bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">16 Threads (10 Cores) | 16 GB RAM | 500 GB NVMe SSD</span>
          </h1>
          <p class="text-xs text-slate-400">Physical Host Dedicated Engine: Windows 11 with Real Hardware Telemetry & Cloudflare Protection.</p>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <!-- CPU -->
          <div class="bg-surface-900 border border-surface-700/60 rounded-xl p-4">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-slate-400 uppercase tracking-wider">Host CPU (16 Threads)</span>
              <i class="fa-solid fa-microchip text-indigo-400 text-lg"></i>
            </div>
            <div class="mt-3 flex items-baseline justify-between">
              <span id="metric-cpu-val" class="text-3xl font-extrabold text-white">0%</span>
              <span id="metric-cpu-cores" class="text-xs text-indigo-400 font-mono font-bold">16 Threads (10 Cores)</span>
            </div>
            <div class="mt-3 w-full bg-surface-800 rounded-full h-2 overflow-hidden">
              <div id="metric-cpu-bar" class="bg-indigo-500 h-2 rounded-full transition-all duration-300" style="width: 0%"></div>
            </div>
          </div>

          <!-- RAM -->
          <div class="bg-surface-900 border border-surface-700/60 rounded-xl p-4">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-slate-400 uppercase tracking-wider">Physical RAM (16 GB)</span>
              <i class="fa-solid fa-memory text-cyan-400 text-lg"></i>
            </div>
            <div class="mt-3 flex items-baseline justify-between">
              <span id="metric-ram-val" class="text-3xl font-extrabold text-white">0 GB</span>
              <span id="metric-ram-total" class="text-xs text-cyan-400 font-mono font-bold">/ 16.0 GB</span>
            </div>
            <div class="mt-3 w-full bg-surface-800 rounded-full h-2 overflow-hidden">
              <div id="metric-ram-bar" class="bg-cyan-500 h-2 rounded-full transition-all duration-300" style="width: 0%"></div>
            </div>
          </div>

          <!-- Disk -->
          <div class="bg-surface-900 border border-surface-700/60 rounded-xl p-4">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-slate-400 uppercase tracking-wider">NVMe Storage (500 GB)</span>
              <i class="fa-solid fa-hard-drive text-emerald-400 text-lg"></i>
            </div>
            <div class="mt-3 flex items-baseline justify-between">
              <span id="metric-disk-val" class="text-3xl font-extrabold text-white">0 GB</span>
              <span id="metric-disk-total" class="text-xs text-emerald-400 font-mono font-bold">/ 500.0 GB</span>
            </div>
            <div class="mt-3 w-full bg-surface-800 rounded-full h-2 overflow-hidden">
              <div id="metric-disk-bar" class="bg-emerald-500 h-2 rounded-full transition-all duration-300" style="width: 0%"></div>
            </div>
          </div>

          <!-- Session Runtime -->
          <div class="bg-surface-900 border border-surface-700/60 rounded-xl p-4">
            <div class="flex items-center justify-between">
              <span class="text-xs font-bold text-slate-400 uppercase tracking-wider">Host Engine Runtime</span>
              <i class="fa-solid fa-server text-emerald-400 text-lg"></i>
            </div>
            <div class="mt-3">
              <span id="metric-uptime-val" class="text-2xl font-extrabold text-white">0h 0m 0s</span>
            </div>
            <div class="mt-3 text-xs text-slate-400 flex items-center justify-between">
              <span>Platform: <b class="text-white">Windows 11</b></span>
              <span class="text-emerald-400 font-bold">Dedicated Online</span>
            </div>
          </div>
        </div>

        <!-- Real Host Architecture Grid -->
        <div class="bg-surface-900 border border-surface-700/60 rounded-xl p-4">
          <div class="flex flex-wrap items-center justify-between pb-3 mb-3 border-b border-surface-800 gap-2">
            <div class="flex items-center space-x-2">
              <span class="flex h-2.5 w-2.5 relative">
                <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                <span class="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-500"></span>
              </span>
              <h3 class="text-xs font-bold text-slate-200 uppercase tracking-wider">Host System & Cloud Compute Architecture</h3>
            </div>
            <div class="flex items-center space-x-2">
              <span class="px-2.5 py-0.5 rounded text-[11px] font-mono bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 font-bold">Host Engine: 🟢 VERIFIED ONLINE</span>
            </div>
          </div>

          <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <!-- Node 1 -->
            <div class="bg-surface-950/80 border border-emerald-500/40 rounded-lg p-3 hover:border-emerald-500 transition">
              <div class="flex items-center justify-between">
                <span class="font-bold text-white text-xs">Node 1: Physical Host Engine</span>
                <span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">ONLINE HOST</span>
              </div>
              <div class="mt-2.5 text-[11px] text-slate-400 space-y-1 font-mono">
                <div class="flex justify-between"><span>CPU Engine:</span> <span class="text-emerald-400 font-bold">16 Threads • 10 Cores</span></div>
                <div class="flex justify-between"><span>RAM & Disk:</span> <span class="text-slate-200">16 GB RAM • 500 GB NVMe</span></div>
              </div>
            </div>

            <!-- Node 2 -->
            <div class="bg-surface-950/80 border border-surface-700/60 rounded-lg p-3 hover:border-surface-600 transition">
              <div class="flex items-center justify-between">
                <span class="font-bold text-white text-xs">Node 2: Hugging Face Spaces</span>
                <span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-indigo-500/20 text-indigo-300 border border-indigo-500/30">READY 24/7</span>
              </div>
              <div class="mt-2.5 text-[11px] text-slate-400 space-y-1 font-mono">
                <div class="flex justify-between"><span>Compute:</span> <span class="text-indigo-300">Docker Cloud Container</span></div>
                <div class="flex justify-between"><span>Status:</span> <span class="text-slate-200">Permanent Public Port</span></div>
              </div>
            </div>

            <!-- Node 3 -->
            <div class="bg-surface-950/80 border border-surface-700/60 rounded-lg p-3 hover:border-surface-600 transition">
              <div class="flex items-center justify-between">
                <span class="font-bold text-white text-xs">Node 3: Google Colab GPU</span>
                <span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30">ON-DEMAND</span>
              </div>
              <div class="mt-2.5 text-[11px] text-slate-400 space-y-1 font-mono">
                <div class="flex justify-between"><span>Compute:</span> <span class="text-amber-300 font-bold">NVIDIA Tesla T4 (16GB)</span></div>
                <div class="flex justify-between"><span>Status:</span> <span class="text-slate-200">LLaMA 3.2 on Port 8008</span></div>
              </div>
            </div>

            <!-- Node 4 -->
            <div class="bg-surface-950/80 border border-surface-700/60 rounded-lg p-3 hover:border-surface-600 transition">
              <div class="flex items-center justify-between">
                <span class="font-bold text-white text-xs">Node 4: Modal Serverless</span>
                <span class="px-1.5 py-0.5 rounded text-[10px] font-bold bg-purple-500/20 text-purple-300 border border-purple-500/30">CONFIGURED</span>
              </div>
              <div class="mt-2.5 text-[11px] text-slate-400 space-y-1 font-mono">
                <div class="flex justify-between"><span>Compute:</span> <span class="text-purple-300">Cloud GPU Infrastructure</span></div>
                <div class="flex justify-between"><span>Status:</span> <span class="text-slate-200">$30 Free Credits / Month</span></div>
              </div>
            </div>
          </div>
        </div>

        <!-- GPU Acceleration Telemetry -->
        <div class="bg-surface-900 border border-surface-700/60 rounded-xl p-5">
          <div class="flex items-center justify-between mb-4">
            <div class="flex items-center space-x-2">
              <i class="fa-solid fa-bolt text-amber-400 text-lg"></i>
              <h2 class="text-base font-bold text-white">NVIDIA GPU Acceleration Unit</h2>
            </div>
            <div class="flex items-center space-x-2">
              <button onclick="purgeGpuMemory()" class="px-2.5 py-1 text-xs rounded bg-surface-800 hover:bg-surface-700 text-amber-400 border border-surface-700 font-semibold transition">
                <i class="fa-solid fa-broom"></i> Free VRAM Cache
              </button>
              <span id="gpu-count-badge" class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-amber-500/10 text-amber-400 border border-amber-500/30">
                Detecting...
              </span>
            </div>
          </div>
          <div id="gpu-cards-container" class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div class="p-4 rounded-lg bg-surface-850 text-slate-400 text-sm">Querying GPU hardware...</div>
          </div>
        </div>

        <!-- Maintenance & Quick Actions -->
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div class="bg-surface-900 border border-surface-700/60 rounded-xl p-4 flex flex-col justify-between">
            <div>
              <div class="flex items-center space-x-2 text-indigo-400 font-bold text-sm mb-1">
                <i class="fa-solid fa-box-archive"></i>
                <span>Download Full VPS Backup (.zip)</span>
              </div>
              <p class="text-xs text-slate-400">Archives all files in `/kaggle/working` into a single downloadable zip file.</p>
            </div>
            <form action="/api/tools/zip_archive" method="POST" class="mt-3">
              <button type="submit" class="w-full py-2 rounded-lg text-xs font-bold bg-indigo-600 hover:bg-indigo-500 text-white transition flex items-center justify-center gap-1.5">
                <i class="fa-solid fa-file-zipper"></i> Download Backup Archive
              </button>
            </form>
          </div>

          <div class="bg-surface-900 border border-surface-700/60 rounded-xl p-4 flex flex-col justify-between">
            <div>
              <div class="flex items-center space-x-2 text-rose-400 font-bold text-sm mb-1">
                <i class="fa-solid fa-trash-can"></i>
                <span>Clean Cache & Temp Files</span>
              </div>
              <p class="text-xs text-slate-400">Removes pip build cache and `/tmp` files to recover disk capacity.</p>
            </div>
            <button onclick="cleanCache()" class="mt-3 w-full py-2 rounded-lg text-xs font-bold bg-rose-600/20 hover:bg-rose-600/30 text-rose-300 border border-rose-500/30 transition flex items-center justify-center gap-1.5">
              <i class="fa-solid fa-broom"></i> Clean Temporary Disk Cache
            </button>
          </div>

          <div class="bg-surface-900 border border-surface-700/60 rounded-xl p-4 flex flex-col justify-between">
            <div>
              <div class="flex items-center space-x-2 text-emerald-400 font-bold text-sm mb-1">
                <i class="fa-solid fa-shield-halved"></i>
                <span>Active Keep-Alive Shield</span>
              </div>
              <p class="text-xs text-slate-400">Keeps background websockets and audio signals active so Kaggle doesn't sleep.</p>
            </div>
            <button onclick="toggleAudioPing()" class="mt-3 w-full py-2 rounded-lg text-xs font-bold bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-300 border border-emerald-500/30 transition flex items-center justify-center gap-1.5">
              <i class="fa-solid fa-toggle-on"></i> Toggle Session Shield
            </button>
          </div>
        </div>
      </section>

      <!-- ================= 2. MULTI-TAB TERMINAL ================= -->
      <section id="view-terminal" class="tab-view hidden flex-1 flex flex-col h-full bg-[#0d1117] p-2">
        <div class="h-10 px-3 flex items-center justify-between border-b border-surface-700/60 bg-surface-900 rounded-t-lg shrink-0">
          <div class="flex items-center space-x-2">
            <!-- Terminal Tabs -->
            <button onclick="switchTerminalTab('term-1')" id="tab-term-1" class="term-tab-btn px-2.5 py-1 text-xs font-bold rounded bg-indigo-600 text-white">
              Terminal 1
            </button>
            <button onclick="switchTerminalTab('term-2')" id="tab-term-2" class="term-tab-btn px-2.5 py-1 text-xs font-bold rounded bg-surface-800 text-slate-400 hover:text-white">
              Terminal 2
            </button>
            <button onclick="switchTerminalTab('term-3')" id="tab-term-3" class="term-tab-btn px-2.5 py-1 text-xs font-bold rounded bg-surface-800 text-slate-400 hover:text-white">
              Terminal 3
            </button>
          </div>

          <div class="flex items-center space-x-2">
            <button onclick="sendQuickSnippet('nvidia-smi')" class="px-2 py-0.5 text-xs text-amber-300 bg-surface-800 hover:bg-surface-700 rounded border border-surface-700">
              nvidia-smi
            </button>
            <button onclick="sendQuickSnippet('htop')" class="px-2 py-0.5 text-xs text-cyan-300 bg-surface-800 hover:bg-surface-700 rounded border border-surface-700">
              htop
            </button>
            <button onclick="reconnectTerminal()" title="Reconnect" class="px-2 py-0.5 text-xs text-slate-300 bg-surface-800 hover:bg-surface-700 rounded border border-surface-700">
              <i class="fa-solid fa-rotate-right"></i>
            </button>
            <button onclick="fitTerminal()" title="Fit Screen" class="px-2 py-0.5 text-xs text-slate-300 bg-surface-800 hover:bg-surface-700 rounded border border-surface-700">
              <i class="fa-solid fa-expand"></i>
            </button>
          </div>
        </div>
        <div id="terminal-container" class="flex-1 w-full h-[calc(100%-2.5rem)] p-2 overflow-hidden bg-[#0d1117] rounded-b-lg"></div>
      </section>

      <!-- ================= 3. 1-CLICK APP STORE ================= -->
      <section id="view-apps" class="tab-view hidden p-6 space-y-6">
        <div>
          <h1 class="text-2xl font-black text-white tracking-tight">1-Click VPS Application Store</h1>
          <p class="text-xs text-slate-400">Launch powerful development environments, AI servers, and tools directly with automated public HTTPS tunnels.</p>
        </div>

        <div id="apps-list-container" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <div class="p-6 text-center text-slate-500">Loading App Store catalog...</div>
        </div>
      </section>

      <!-- ================= 4. FILE MANAGER ================= -->
      <section id="view-files" class="tab-view hidden flex-1 flex flex-col p-6 space-y-4 h-full">
        <div class="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
          <div>
            <h1 class="text-2xl font-black text-white tracking-tight">VPS File Explorer</h1>
            <p class="text-xs text-slate-400">Manage files, upload code/models, download results, and edit code with Ace Editor.</p>
          </div>
          <div class="flex items-center space-x-2">
            <input type="file" id="file-upload-input" class="hidden" onchange="uploadSelectedFile(event)">
            <button onclick="document.getElementById('file-upload-input').click()" class="px-3 py-1.5 rounded-lg text-xs font-bold bg-indigo-600 hover:bg-indigo-500 text-white flex items-center gap-1.5 transition">
              <i class="fa-solid fa-upload"></i> Upload File
            </button>
            <button onclick="promptCreateFile()" class="px-3 py-1.5 rounded-lg text-xs font-bold bg-surface-850 hover:bg-surface-800 border border-surface-700 text-slate-200 flex items-center gap-1.5 transition">
              <i class="fa-solid fa-file-circle-plus"></i> New File
            </button>
            <button onclick="promptCreateFolder()" class="px-3 py-1.5 rounded-lg text-xs font-bold bg-surface-850 hover:bg-surface-800 border border-surface-700 text-slate-200 flex items-center gap-1.5 transition">
              <i class="fa-solid fa-folder-plus"></i> New Folder
            </button>
            <button onclick="loadFiles(currentDir)" class="px-3 py-1.5 rounded-lg text-xs font-bold bg-surface-850 hover:bg-surface-800 border border-surface-700 text-slate-200 transition">
              <i class="fa-solid fa-rotate"></i>
            </button>
          </div>
        </div>

        <div class="flex items-center space-x-2 bg-surface-900 px-3 py-2 rounded-lg border border-surface-700/60 text-xs">
          <i class="fa-solid fa-folder text-amber-400"></i>
          <span class="text-slate-400 font-semibold">Location:</span>
          <div id="files-breadcrumb" class="flex-1 font-mono text-slate-200 truncate">/kaggle/working</div>
          <button onclick="navigateToParent()" id="btn-file-parent" class="px-2 py-0.5 rounded bg-surface-800 hover:bg-surface-700 text-slate-300">
            <i class="fa-solid fa-arrow-up"></i> Up
          </button>
        </div>

        <div class="flex-1 bg-surface-900 border border-surface-700/60 rounded-xl overflow-hidden flex flex-col">
          <div class="grid grid-cols-12 gap-2 px-4 py-2.5 bg-surface-850 text-xs font-bold text-slate-400 border-b border-surface-700/60">
            <span class="col-span-6">Name</span>
            <span class="col-span-2 text-right">Size</span>
            <span class="col-span-2 text-center">Modified</span>
            <span class="col-span-2 text-right">Actions</span>
          </div>
          <div id="files-table-body" class="flex-1 overflow-y-auto custom-scrollbar divide-y divide-surface-700/40 text-sm">
            <div class="p-8 text-center text-slate-500">Loading files...</div>
          </div>
        </div>
      </section>

      <!-- ================= 5. REMOTE DOWNLOADER & TOOLS ================= -->
      <section id="view-downloader" class="tab-view hidden p-6 space-y-6">
        <div>
          <h1 class="text-2xl font-black text-white tracking-tight">Remote Tools & Downloader</h1>
          <p class="text-xs text-slate-400">Download files, clone git repositories, or pull HuggingFace models straight into your VPS at gigabit cloud speeds.</p>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
          <!-- Direct URL / Wget -->
          <div class="bg-surface-900 border border-surface-700/60 rounded-xl p-5 space-y-3">
            <div class="flex items-center space-x-2 text-indigo-400 font-bold text-sm">
              <i class="fa-solid fa-link"></i>
              <span>Direct URL Download (Wget)</span>
            </div>
            <p class="text-xs text-slate-400">Download any direct link (zip, tar, weights, datasets) directly to `/kaggle/working`.</p>
            <input type="text" id="input-dl-url" placeholder="https://example.com/file.zip" class="w-full bg-surface-950 border border-surface-700 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-indigo-500">
            <button onclick="startDownloadUrl()" class="w-full py-2 rounded-lg text-xs font-bold bg-indigo-600 hover:bg-indigo-500 text-white transition">
              Download to VPS
            </button>
          </div>

          <!-- Git Clone -->
          <div class="bg-surface-900 border border-surface-700/60 rounded-xl p-5 space-y-3">
            <div class="flex items-center space-x-2 text-emerald-400 font-bold text-sm">
              <i class="fa-brands fa-github"></i>
              <span>Git Repository Clone</span>
            </div>
            <p class="text-xs text-slate-400">Clone any GitHub or GitLab repository into `/kaggle/working`.</p>
            <input type="text" id="input-git-url" placeholder="https://github.com/user/repo.git" class="w-full bg-surface-950 border border-surface-700 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-emerald-500">
            <button onclick="startGitClone()" class="w-full py-2 rounded-lg text-xs font-bold bg-emerald-600 hover:bg-emerald-500 text-white transition">
              Clone Repository
            </button>
          </div>

          <!-- HuggingFace Model Downloader -->
          <div class="bg-surface-900 border border-surface-700/60 rounded-xl p-5 space-y-3">
            <div class="flex items-center space-x-2 text-amber-400 font-bold text-sm">
              <i class="fa-solid fa-face-smile"></i>
              <span>HuggingFace Model Puller</span>
            </div>
            <p class="text-xs text-slate-400">Download model weights or datasets by Repo ID directly using snapshot download.</p>
            <input type="text" id="input-hf-repo" placeholder="e.g. mistralai/Mistral-7B-v0.1" class="w-full bg-surface-950 border border-surface-700 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-amber-500">
            <button onclick="startHfDownload()" class="w-full py-2 rounded-lg text-xs font-bold bg-amber-600 hover:bg-amber-500 text-white transition">
              Pull HF Model
            </button>
          </div>
        </div>

        <!-- Download & Task Output Viewer -->
        <div class="bg-surface-900 border border-surface-700/60 rounded-xl p-5">
          <div class="flex items-center justify-between mb-3">
            <h2 class="text-base font-bold text-white">Live Remote Task Logs</h2>
            <button onclick="refreshTaskLogs()" class="text-xs text-slate-400 hover:text-white"><i class="fa-solid fa-rotate"></i> Refresh Log</button>
          </div>
          <pre id="remote-task-log-viewer" class="bg-surface-950 border border-surface-800 rounded-lg p-3 text-xs font-mono text-slate-300 h-44 overflow-y-auto custom-scrollbar">No active download or cloning task running.</pre>
        </div>
      </section>

      <!-- ================= 6. PACKAGES (PIP & APT) ================= -->
      <section id="view-packages" class="tab-view hidden p-6 space-y-6">
        <div>
          <h1 class="text-2xl font-black text-white tracking-tight">Package & Software Manager</h1>
          <p class="text-xs text-slate-400">Install Python pip modules and Linux apt system binaries directly from the interface.</p>
        </div>

        <div class="bg-surface-900 border border-surface-700/60 rounded-xl p-5">
          <h2 class="text-base font-bold text-white mb-3">Install New Software</h2>
          <div class="flex flex-col sm:flex-row gap-3">
            <select id="select-pkg-type" class="bg-surface-950 border border-surface-700 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-indigo-500 w-32">
              <option value="pip">Python (pip)</option>
              <option value="apt">System (apt)</option>
            </select>
            <input type="text" id="input-pkg-name" placeholder="Package name (e.g. transformers, ffmpeg, htop, zsh)" class="flex-1 bg-surface-950 border border-surface-700 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-indigo-500">
            <button onclick="installPackage()" class="px-5 py-2 rounded-lg text-xs font-bold bg-indigo-600 hover:bg-indigo-500 text-white transition flex items-center justify-center gap-2">
              <i class="fa-solid fa-download"></i> Install Package
            </button>
          </div>
        </div>

        <!-- Installed PIP packages list -->
        <div class="bg-surface-900 border border-surface-700/60 rounded-xl p-5">
          <div class="flex items-center justify-between mb-3">
            <h2 class="text-base font-bold text-white">Installed Python PIP Packages</h2>
            <button onclick="loadPipPackages()" class="text-xs text-slate-400 hover:text-white"><i class="fa-solid fa-rotate"></i> Refresh</button>
          </div>
          <div id="pip-packages-container" class="max-h-80 overflow-y-auto custom-scrollbar grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
            <div class="p-4 text-slate-500">Click Refresh to load installed packages...</div>
          </div>
        </div>
      </section>

      <!-- ================= 7. PROCESSES & JOBS ================= -->
      <section id="view-processes" class="tab-view hidden flex-1 flex flex-col p-6 space-y-4 h-full">
        <div class="flex items-center justify-between">
          <div>
            <h1 class="text-2xl font-black text-white tracking-tight">System Processes & Background Jobs</h1>
            <p class="text-xs text-slate-400">View running processes, monitor memory/CPU, and terminate jobs.</p>
          </div>
          <button onclick="loadProcesses()" class="px-3 py-1.5 rounded-lg text-xs font-bold bg-surface-850 hover:bg-surface-800 border border-surface-700 text-slate-200 flex items-center gap-1.5 transition">
            <i class="fa-solid fa-rotate"></i> Refresh
          </button>
        </div>

        <div class="flex-1 bg-surface-900 border border-surface-700/60 rounded-xl overflow-hidden flex flex-col">
          <div class="grid grid-cols-12 gap-2 px-4 py-2.5 bg-surface-850 text-xs font-bold text-slate-400 border-b border-surface-700/60">
            <span class="col-span-2 sm:col-span-1">PID</span>
            <span class="col-span-3 sm:col-span-2">Name</span>
            <span class="col-span-2 sm:col-span-1 text-right">CPU%</span>
            <span class="col-span-2 sm:col-span-1 text-right">MEM%</span>
            <span class="col-span-2 sm:col-span-6 hidden sm:block">Command Line</span>
            <span class="col-span-3 sm:col-span-1 text-right">Action</span>
          </div>
          <div id="processes-table-body" class="flex-1 overflow-y-auto custom-scrollbar divide-y divide-surface-700/40 text-xs">
            <div class="p-8 text-center text-slate-500">Loading processes...</div>
          </div>
        </div>
      </section>

      <!-- ================= 8. PORT TUNNELS ================= -->
      <section id="view-tunnels" class="tab-view hidden p-6 space-y-6">
        <div>
          <h1 class="text-2xl font-black text-white tracking-tight">Port Forwarding & Cloudflare Tunnels</h1>
          <p class="text-xs text-slate-400">Expose any internal port to a public HTTPS URL without router access or public IP.</p>
        </div>

        <div class="bg-surface-900 border border-surface-700/60 rounded-xl p-5">
          <h2 class="text-base font-bold text-white mb-3">Open New Public Port Tunnel</h2>
          <div class="flex flex-col sm:flex-row gap-3">
            <input type="number" id="input-tunnel-port" placeholder="Port (e.g. 7860, 8000, 8501, 8888)" class="bg-surface-950 border border-surface-700 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-indigo-500 w-full sm:w-60">
            <input type="text" id="input-tunnel-label" placeholder="Service Label (e.g. My API, Web App)" class="bg-surface-950 border border-surface-700 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-indigo-500 w-full sm:w-60">
            <button onclick="createTunnel()" class="px-5 py-2 rounded-lg text-xs font-bold bg-indigo-600 hover:bg-indigo-500 text-white transition flex items-center justify-center gap-2">
              <i class="fa-solid fa-bolt"></i> Generate Public HTTPS URL
            </button>
          </div>
        </div>

        <div class="bg-surface-900 border border-surface-700/60 rounded-xl p-5">
          <div class="flex items-center justify-between mb-3">
            <h2 class="text-base font-bold text-white">Active Cloudflare Tunnels</h2>
            <button onclick="loadTunnels()" class="text-xs text-slate-400 hover:text-white"><i class="fa-solid fa-rotate"></i> Refresh</button>
          </div>
          <div id="tunnels-list" class="space-y-3">
            <div class="p-4 rounded-lg bg-surface-950 border border-surface-700/60 text-slate-400 text-xs">
              Checking tunnels...
            </div>
          </div>
        </div>
      </section>

    </main>
  </div>

  <!-- In-Browser Code Editor Modal -->
  <div id="editor-modal" class="fixed inset-0 bg-black/80 backdrop-blur-sm hidden z-50 flex items-center justify-center p-4">
    <div class="bg-surface-900 border border-surface-700 rounded-xl w-full max-w-5xl h-[85vh] flex flex-col shadow-2xl overflow-hidden">
      <div class="h-12 px-4 bg-surface-850 border-b border-surface-700 flex items-center justify-between shrink-0">
        <div class="flex items-center space-x-2">
          <i class="fa-solid fa-code text-indigo-400"></i>
          <span id="editor-filename" class="font-mono text-xs text-white font-bold truncate">file.txt</span>
        </div>
        <div class="flex items-center space-x-2">
          <button onclick="saveFileContent()" class="px-3 py-1 rounded bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-bold flex items-center gap-1.5 transition">
            <i class="fa-solid fa-floppy-disk"></i> Save
          </button>
          <button onclick="closeEditor()" class="px-3 py-1 rounded bg-surface-700 hover:bg-surface-600 text-slate-200 text-xs font-bold transition">
            Close
          </button>
        </div>
      </div>
      <div id="ace-editor" class="flex-1 w-full"></div>
    </div>
  </div>

  <script>
    let currentDir = "/kaggle/working";
    let activeTab = "dashboard";
    let activeTermSession = "term-1";
    let term = null;
    let fitAddon = null;
    let termWs = null;
    let currentEditPath = "";
    let aceEditorInstance = null;
    let activeTaskId = null;
    let audioPingEnabled = false;
    let audioPingInterval = null;

    // --- Keep-Alive Shield ---
    function toggleAudioPing() {
      audioPingEnabled = !audioPingEnabled;
      const btn = document.getElementById("btn-keepalive");
      const icon = document.getElementById("icon-keepalive");
      if (audioPingEnabled) {
        btn.classList.add("bg-emerald-500/20", "text-emerald-400", "border-emerald-500/40");
        icon.classList.add("animate-pulse");
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        audioPingInterval = setInterval(() => {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          gain.gain.value = 0.0001;
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.start();
          osc.stop(ctx.currentTime + 0.1);
        }, 40000);
      } else {
        btn.classList.remove("bg-emerald-500/20", "text-emerald-400", "border-emerald-500/40");
        icon.classList.remove("animate-pulse");
        if (audioPingInterval) clearInterval(audioPingInterval);
      }
    }

    // --- Tabs Navigation ---
    function switchTab(tabId) {
      activeTab = tabId;
      document.querySelectorAll(".tab-view").forEach(el => el.classList.add("hidden"));
      document.querySelectorAll(".tab-button").forEach(el => {
        el.classList.remove("bg-indigo-600/30", "border-indigo-500/40", "text-white");
        el.classList.add("text-slate-400");
      });

      const activeView = document.getElementById(`view-${tabId}`);
      if (activeView) activeView.classList.remove("hidden");

      const activeBtn = document.getElementById(`tab-btn-${tabId}`);
      if (activeBtn) {
        activeBtn.classList.add("bg-indigo-600/30", "border-indigo-500/40", "text-white");
        activeBtn.classList.remove("text-slate-400");
      }

      if (tabId === "terminal") {
        setTimeout(() => {
          if (!term) initTerminal(activeTermSession);
          else fitTerminal();
        }, 50);
      } else if (tabId === "apps") {
        loadApps();
      } else if (tabId === "files") {
        loadFiles(currentDir);
      } else if (tabId === "processes") {
        loadProcesses();
      } else if (tabId === "tunnels") {
        loadTunnels();
      }
    }

    // --- Telemetry Polling ---
    async function updateMetrics() {
      try {
        const res = await fetch("/api/stats");
        if (!res.ok) return;
        const data = await res.json();

        document.getElementById("nav-uptime").textContent = data.uptime;
        document.getElementById("nav-cpu").textContent = `${data.cpu_percent}%`;
        document.getElementById("nav-ram").textContent = `${data.ram.used_gb} / ${data.ram.total_gb} GB`;

        document.getElementById("metric-cpu-val").textContent = `${data.cpu_percent}%`;
        document.getElementById("metric-cpu-cores").textContent = `${data.cpu_cores} Threads (${data.cpu_cores_physical || 10} Cores)`;
        document.getElementById("metric-cpu-bar").style.width = `${Math.min(data.cpu_percent, 100)}%`;

        document.getElementById("metric-ram-val").textContent = `${data.ram.used_gb} GB`;
        document.getElementById("metric-ram-total").textContent = `/ ${data.ram.total_gb} GB (${data.ram.percent}%)`;
        document.getElementById("metric-ram-bar").style.width = `${data.ram.percent}%`;

        document.getElementById("metric-disk-val").textContent = `${data.disk.used_gb} GB`;
        document.getElementById("metric-disk-total").textContent = `/ ${data.disk.total_gb} GB (${data.disk.percent}%)`;
        document.getElementById("metric-disk-bar").style.width = `${Math.min(100, Math.max(0, data.disk.percent))}%`;

        document.getElementById("metric-uptime-val").textContent = data.uptime;
        
        document.getElementById("session-progress").style.width = "100%";
        document.getElementById("sidebar-timer").textContent = "Unlimited 24/7";

        const gpuContainer = document.getElementById("gpu-cards-container");
        const navGpu = document.getElementById("nav-gpu");
        const gpuBadge = document.getElementById("gpu-count-badge");

        if (data.gpus && data.gpus.length > 0) {
          gpuBadge.textContent = `${data.gpus.length} GPU(s) Active`;
          navGpu.textContent = `${data.gpus[0].name.replace('Tesla ', '')} (${data.gpus[0].utilization}%)`;

          gpuContainer.innerHTML = data.gpus.map((gpu) => `
            <div class="bg-surface-850 border border-surface-700/70 rounded-lg p-4">
              <div class="flex items-center justify-between">
                <span class="font-bold text-white text-xs">GPU #${gpu.index}: ${gpu.name}</span>
                <span class="text-xs font-mono text-amber-400 font-bold">${gpu.temperature}°C</span>
              </div>
              <div class="mt-3 grid grid-cols-2 gap-2 text-xs">
                <div>
                  <span class="text-slate-400">GPU Core Load:</span>
                  <span class="text-white font-mono font-bold ml-1">${gpu.utilization}%</span>
                </div>
                <div>
                  <span class="text-slate-400">VRAM:</span>
                  <span class="text-white font-mono font-bold ml-1">${(gpu.memory_used_mb / 1024).toFixed(1)} / ${(gpu.memory_total_mb / 1024).toFixed(1)} GB</span>
                </div>
              </div>
              <div class="mt-2 w-full bg-surface-800 rounded-full h-1.5 overflow-hidden">
                <div class="bg-amber-400 h-1.5 rounded-full" style="width: ${gpu.memory_percent}%"></div>
              </div>
            </div>
          `).join('');
        } else {
          gpuBadge.textContent = "Standby (Click to Activate)";
          navGpu.textContent = "Activate T4 ⚡";
          gpuContainer.innerHTML = `
            <div class="col-span-full p-4 rounded-xl bg-surface-850 border border-amber-500/30 flex flex-col sm:flex-row items-center justify-between gap-4">
              <div>
                <div class="font-bold text-white text-sm flex items-center gap-2">
                  <i class="fa-solid fa-microchip text-amber-400"></i> Free NVIDIA Tesla T4 GPU Acceleration Ready
                </div>
                <div class="text-xs text-slate-400 mt-1">
                  Connect 16 GB VRAM on-demand for LLaMA 3.2 AI & Deep Learning via 1-click Google Colab.
                </div>
              </div>
              <a href="https://colab.research.google.com/github/jaimiltrived/vps/blob/main/Colab_GPU_Worker.ipynb" target="_blank" class="px-4 py-2 rounded-lg bg-amber-500 hover:bg-amber-400 text-black font-extrabold text-xs flex items-center gap-2 shadow-lg shadow-amber-500/20 transition whitespace-nowrap">
                <i class="fa-solid fa-bolt"></i> ⚡ ACTIVATE TESLA T4 GPU (1-CLICK)
              </a>
            </div>
          `;
        }
      } catch (err) {}
    }
    setInterval(updateMetrics, 2000);
    updateMetrics();

    // --- Security Firewall Functions ---
    async function checkAuthStatus() {
      const token = localStorage.getItem("vps_token") || getCookie("vps_token");
      try {
        const res = await fetch("/api/auth/status", {
          headers: token ? { "Authorization": `Bearer ${token}` } : {}
        });
        const d = await res.json();
        if (d.authenticated) {
          document.getElementById("vps-auth-modal").classList.add("hidden");
        } else {
          document.getElementById("vps-auth-modal").classList.remove("hidden");
        }
      } catch(e) {
        document.getElementById("vps-auth-modal").classList.remove("hidden");
      }
    }

    async function submitAuth() {
      const pwd = document.getElementById("auth-pwd-input").value;
      const errEl = document.getElementById("auth-error-msg");
      errEl.classList.add("hidden");
      try {
        const res = await fetch("/api/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ password: pwd })
        });
        const d = await res.json();
        if (res.ok && d.token) {
          localStorage.setItem("vps_token", d.token);
          document.cookie = `vps_token=${d.token}; path=/; max-age=2592000`;
          document.getElementById("vps-auth-modal").classList.add("hidden");
          updateMetrics();
        } else {
          errEl.textContent = d.detail || "Incorrect password.";
          errEl.classList.remove("hidden");
        }
      } catch(e) {
        errEl.textContent = "Firewall connection error.";
        errEl.classList.remove("hidden");
      }
    }

    async function logoutAuth() {
      await fetch("/api/auth/logout", { method: "POST" });
      localStorage.removeItem("vps_token");
      document.cookie = "vps_token=; path=/; max-age=0";
      document.getElementById("vps-auth-modal").classList.remove("hidden");
    }

    function getCookie(name) {
      const v = document.cookie.match('(^|;) ?' + name + '=([^;]*)(;|$)');
      return v ? v[2] : null;
    }

    checkAuthStatus();

    // --- GPU Purge ---
    async function purgeGpuMemory() {
      const res = await fetch("/api/gpu/purge", { method: "POST" });
      const data = await res.json();
      alert("GPU Purge: " + (data.message || "Completed"));
      updateMetrics();
    }

    async function cleanCache() {
      if (!confirm("Clean temporary files and pip cache?")) return;
      const res = await fetch("/api/tools/clean_cache", { method: "POST" });
      const data = await res.json();
      alert(data.message || "Cache cleaned");
      updateMetrics();
    }

    // --- Interactive Terminal ---
    function initTerminal(sessionId) {
      activeTermSession = sessionId;
      const container = document.getElementById("terminal-container");
      container.innerHTML = "";
      term = new Terminal({
        theme: {
          background: '#0d1117',
          foreground: '#c9d1d9',
          cursor: '#58a6ff',
          selectionBackground: '#1f6feb44'
        },
        fontFamily: 'Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
        fontSize: 14,
        cursorBlink: true,
        allowProposedApi: true
      });

      fitAddon = new FitAddon.FitAddon();
      term.loadAddon(fitAddon);
      term.loadAddon(new WebLinksAddon.WebLinksAddon());
      term.open(container);
      fitAddon.fit();

      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const wsUrl = `${protocol}//${window.location.host}/ws/terminal?session_id=${sessionId}`;
      termWs = new WebSocket(wsUrl);
      termWs.binaryType = "arraybuffer";

      termWs.onopen = () => {
        term.write(`\\r\\n\\x1b[1;32m[✓] Connected to Cloud VPS Terminal (${sessionId}).\\x1b[0m\\r\\n`);
        sendTerminalSize();
      };

      termWs.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) {
          term.write(new TextDecoder().decode(event.data));
        } else {
          term.write(event.data);
        }
      };

      term.onData(data => {
        if (termWs && termWs.readyState === WebSocket.OPEN) {
          termWs.send(data);
        }
      });

      window.addEventListener("resize", () => {
        if (fitAddon) {
          fitAddon.fit();
          sendTerminalSize();
        }
      });
    }

    function switchTerminalTab(tabId) {
      document.querySelectorAll(".term-tab-btn").forEach(btn => {
        btn.classList.remove("bg-indigo-600", "text-white");
        btn.classList.add("bg-surface-800", "text-slate-400");
      });
      const activeBtn = document.getElementById(`tab-${tabId}`);
      if (activeBtn) {
        activeBtn.classList.add("bg-indigo-600", "text-white");
        activeBtn.classList.remove("bg-surface-800", "text-slate-400");
      }
      if (termWs) {
        try { termWs.close(); } catch(e){}
      }
      initTerminal(tabId);
    }

    function sendTerminalSize() {
      if (term && termWs && termWs.readyState === WebSocket.OPEN) {
        termWs.send(JSON.stringify({
          type: "resize",
          cols: term.cols,
          rows: term.rows
        }));
      }
    }

    function fitTerminal() {
      if (fitAddon) {
        fitAddon.fit();
        sendTerminalSize();
      }
    }

    function reconnectTerminal() {
      if (termWs) {
        try { termWs.close(); } catch(e){}
      }
      initTerminal(activeTermSession);
    }

    function sendQuickSnippet(cmd) {
      if (termWs && termWs.readyState === WebSocket.OPEN) {
        termWs.send(cmd + "\\n");
      }
    }

    // --- 1-Click App Store ---
    async function loadApps() {
      const container = document.getElementById("apps-list-container");
      try {
        const res = await fetch("/api/apps/list");
        const apps = await res.json();
        container.innerHTML = apps.map(app => `
          <div class="bg-surface-900 border border-surface-700/60 rounded-xl p-5 flex flex-col justify-between">
            <div>
              <div class="flex items-center justify-between mb-2">
                <div class="flex items-center space-x-2.5">
                  <i class="${app.icon} text-xl"></i>
                  <h3 class="font-bold text-white text-sm">${app.name}</h3>
                </div>
                <span class="px-2 py-0.5 rounded text-[11px] font-mono font-bold ${app.running ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/40' : 'bg-surface-800 text-slate-400'}">
                  ${app.running ? 'RUNNING' : 'STOPPED'}
                </span>
              </div>
              <p class="text-xs text-slate-400 mb-4">${app.desc}</p>
              <div class="text-[11px] text-slate-400 font-mono mb-4">Port: <span class="text-indigo-400">${app.port}</span></div>
            </div>

            <div class="space-y-2">
              ${app.running ? `
                ${app.tunnel_url ? `
                  <a href="${app.tunnel_url}" target="_blank" class="w-full py-2 rounded-lg text-xs font-bold bg-indigo-600 hover:bg-indigo-500 text-white flex items-center justify-center gap-1.5 transition">
                    <i class="fa-solid fa-arrow-up-right-from-square"></i> Open Application
                  </a>
                ` : `
                  <div class="w-full py-2 text-center text-xs text-amber-300 font-semibold bg-surface-800 rounded-lg">Generating Tunnel...</div>
                `}
                <button onclick="stopApp('${app.id}')" class="w-full py-1.5 rounded-lg text-xs font-semibold bg-surface-800 hover:bg-rose-500/20 text-rose-400 transition">
                  Stop App
                </button>
              ` : `
                <button onclick="launchApp('${app.id}')" class="w-full py-2 rounded-lg text-xs font-bold bg-surface-800 hover:bg-surface-700 text-slate-200 border border-surface-700 flex items-center justify-center gap-1.5 transition">
                  <i class="fa-solid fa-play text-indigo-400"></i> Launch & Expose
                </button>
              `}
            </div>
          </div>
        `).join('');
      } catch (e) {
        container.innerHTML = `<div class="p-6 text-rose-400 text-xs">Error loading app catalog</div>`;
      }
    }

    async function launchApp(appId) {
      await fetch("/api/apps/launch", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ app_id: appId })
      });
      setTimeout(loadApps, 2000);
      loadApps();
    }

    async function stopApp(appId) {
      await fetch("/api/apps/stop", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ app_id: appId })
      });
      setTimeout(loadApps, 1000);
      loadApps();
    }

    // --- File Explorer ---
    async function loadFiles(path) {
      currentDir = path;
      document.getElementById("files-breadcrumb").textContent = path;
      const tbody = document.getElementById("files-table-body");
      tbody.innerHTML = `<div class="p-4 text-center text-slate-500 text-xs">Loading directory...</div>`;

      try {
        const res = await fetch(`/api/files/list?path=${encodeURIComponent(path)}`);
        const data = await res.json();
        if (!res.ok) {
          tbody.innerHTML = `<div class="p-4 text-center text-rose-400 text-xs">${data.detail || "Error loading path"}</div>`;
          return;
        }

        currentDir = data.current_path;
        document.getElementById("files-breadcrumb").textContent = currentDir;
        document.getElementById("btn-file-parent").style.display = data.parent_path ? "inline-block" : "none";

        if (data.items.length === 0) {
          tbody.innerHTML = `<div class="p-8 text-center text-slate-500 text-xs">Folder is empty</div>`;
          return;
        }

        tbody.innerHTML = data.items.map(item => {
          const icon = item.is_dir ? "fa-folder text-amber-400" : "fa-file-lines text-slate-400";
          const clickAction = item.is_dir 
            ? `onclick="loadFiles('${item.path.replace(/\\\\/g, '/')}')"`
            : `onclick="openFileInEditor('${item.path.replace(/\\\\/g, '/')}')"`;
          const sizeStr = item.is_dir ? "-" : formatBytes(item.size);

          return `
            <div class="grid grid-cols-12 gap-2 px-4 py-2.5 items-center hover:bg-surface-800/80 transition group">
              <div class="col-span-6 flex items-center space-x-2.5 cursor-pointer truncate" ${clickAction}>
                <i class="fa-solid ${icon} text-sm shrink-0"></i>
                <span class="truncate font-semibold text-slate-200 group-hover:text-indigo-400 text-xs">${item.name}</span>
              </div>
              <div class="col-span-2 text-right font-mono text-[11px] text-slate-400">${sizeStr}</div>
              <div class="col-span-2 text-center text-[11px] text-slate-400 hidden sm:block">${item.modified}</div>
              <div class="col-span-2 text-right flex items-center justify-end space-x-2">
                ${!item.is_dir ? `
                  <a href="/api/files/download?path=${encodeURIComponent(item.path)}" title="Download" class="p-1 text-slate-400 hover:text-indigo-400">
                    <i class="fa-solid fa-download"></i>
                  </a>
                ` : ''}
                <button onclick="deleteFile('${item.path.replace(/\\\\/g, '/')}')" title="Delete" class="p-1 text-slate-400 hover:text-rose-400">
                  <i class="fa-solid fa-trash"></i>
                </button>
              </div>
            </div>
          `;
        }).join('');
      } catch (e) {
        tbody.innerHTML = `<div class="p-4 text-center text-rose-400 text-xs">${e.message}</div>`;
      }
    }

    function navigateToParent() {
      const parts = currentDir.replace(/\\\\/g, '/').split('/').filter(Boolean);
      if (parts.length > 1) {
        parts.pop();
        loadFiles('/' + parts.join('/'));
      } else {
        loadFiles('/');
      }
    }

    async function openFileInEditor(path) {
      try {
        const res = await fetch(`/api/files/read?path=${encodeURIComponent(path)}`);
        const data = await res.json();
        if (!res.ok) {
          alert(data.detail || "Could not read file");
          return;
        }
        currentEditPath = data.path;
        document.getElementById("editor-filename").textContent = data.path;
        document.getElementById("editor-modal").classList.remove("hidden");

        if (!aceEditorInstance) {
          aceEditorInstance = ace.edit("ace-editor");
          aceEditorInstance.setTheme("ace/theme/tomorrow_night_eighties");
          aceEditorInstance.session.setMode("ace/mode/python");
          aceEditorInstance.setOptions({ fontSize: "14px" });
        }
        aceEditorInstance.setValue(data.content, -1);
      } catch (e) {
        alert("Error opening editor: " + e.message);
      }
    }

    async function saveFileContent() {
      if (!currentEditPath) return;
      const content = aceEditorInstance.getValue();
      const res = await fetch("/api/files/write", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ path: currentEditPath, content: content })
      });
      if (res.ok) alert("File saved successfully!");
      else alert("Failed to save file.");
    }

    function closeEditor() {
      document.getElementById("editor-modal").classList.add("hidden");
    }

    async function promptCreateFile() {
      const name = prompt("Enter new filename:");
      if (!name) return;
      const fullPath = (currentDir.endsWith('/') ? currentDir : currentDir + '/') + name;
      await fetch("/api/files/write", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ path: fullPath, content: "" })
      });
      loadFiles(currentDir);
    }

    async function promptCreateFolder() {
      const name = prompt("Enter new folder name:");
      if (!name) return;
      const fullPath = (currentDir.endsWith('/') ? currentDir : currentDir + '/') + name;
      await fetch("/api/files/mkdir", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ path: fullPath })
      });
      loadFiles(currentDir);
    }

    async function deleteFile(path) {
      if (!confirm(`Delete ${path}?`)) return;
      await fetch("/api/files/delete", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ path: path })
      });
      loadFiles(currentDir);
    }

    async function uploadSelectedFile(e) {
      const file = e.target.files[0];
      if (!file) return;
      const formData = new FormData();
      formData.append("directory", currentDir);
      formData.append("file", file);

      await fetch("/api/files/upload", { method: "POST", body: formData });
      loadFiles(currentDir);
      e.target.value = "";
    }

    // --- Remote Downloader ---
    async function startDownloadUrl() {
      const url = document.getElementById("input-dl-url").value.trim();
      if (!url) return;
      const res = await fetch("/api/tools/download_url", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ url: url, dir: currentDir })
      });
      const data = await res.json();
      activeTaskId = data.job_id;
      document.getElementById("input-dl-url").value = "";
      refreshTaskLogs();
    }

    async function startGitClone() {
      const url = document.getElementById("input-git-url").value.trim();
      if (!url) return;
      const res = await fetch("/api/tools/git_clone", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ url: url, dir: currentDir })
      });
      const data = await res.json();
      activeTaskId = data.job_id;
      document.getElementById("input-git-url").value = "";
      refreshTaskLogs();
    }

    async function startHfDownload() {
      const repo = document.getElementById("input-hf-repo").value.trim();
      if (!repo) return;
      const res = await fetch("/api/tools/huggingface_download", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ repo_id: repo, dir: currentDir })
      });
      const data = await res.json();
      activeTaskId = data.job_id;
      document.getElementById("input-hf-repo").value = "";
      refreshTaskLogs();
    }

    async function refreshTaskLogs() {
      if (!activeTaskId) return;
      const res = await fetch(`/api/jobs/logs?id=${activeTaskId}`);
      const data = await res.json();
      document.getElementById("remote-task-log-viewer").textContent = data.logs || "Processing...";
    }
    setInterval(() => {
      if (activeTaskId && activeTab === "downloader") refreshTaskLogs();
    }, 2500);

    // --- Package Manager ---
    async function installPackage() {
      const type = document.getElementById("select-pkg-type").value;
      const name = document.getElementById("input-pkg-name").value.trim();
      if (!name) return;
      const res = await fetch("/api/packages/install", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ type: type, name: name })
      });
      const data = await res.json();
      activeTaskId = data.job_id;
      switchTab("downloader");
      document.getElementById("input-pkg-name").value = "";
    }

    async function loadPipPackages() {
      const container = document.getElementById("pip-packages-container");
      container.innerHTML = `<div class="p-3 text-slate-500">Querying pip packages...</div>`;
      try {
        const res = await fetch("/api/packages/pip/list");
        const list = await res.json();
        container.innerHTML = list.map(pkg => `
          <div class="p-2 bg-surface-950 border border-surface-800 rounded font-mono truncate">
            <span class="text-slate-200 font-semibold">${pkg.name}</span>
            <span class="text-slate-500 text-[10px] ml-1">v${pkg.version}</span>
          </div>
        `).join('');
      } catch (e) {
        container.innerHTML = `<div class="p-3 text-rose-400">Failed to load packages</div>`;
      }
    }

    // --- Processes ---
    async function loadProcesses() {
      const tbody = document.getElementById("processes-table-body");
      try {
        const res = await fetch("/api/processes");
        const list = await res.json();
        tbody.innerHTML = list.map(p => `
          <div class="grid grid-cols-12 gap-2 px-4 py-2 items-center hover:bg-surface-800/80 transition">
            <span class="col-span-2 sm:col-span-1 font-mono text-slate-400 font-bold">${p.pid}</span>
            <span class="col-span-3 sm:col-span-2 font-semibold text-slate-200 truncate">${p.name}</span>
            <span class="col-span-2 sm:col-span-1 text-right font-mono ${p.cpu > 20 ? 'text-rose-400 font-bold' : 'text-slate-300'}">${p.cpu}%</span>
            <span class="col-span-2 sm:col-span-1 text-right font-mono text-slate-300">${p.mem}%</span>
            <span class="col-span-2 sm:col-span-6 hidden sm:block font-mono text-slate-400 truncate">${p.cmd}</span>
            <div class="col-span-3 sm:col-span-1 text-right">
              <button onclick="killProcess(${p.pid})" class="px-2 py-0.5 rounded bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 border border-rose-500/30 text-xs transition">
                Kill
              </button>
            </div>
          </div>
        `).join('');
      } catch (e) {
        tbody.innerHTML = `<div class="p-4 text-center text-rose-400">Failed to load processes</div>`;
      }
    }

    async function killProcess(pid) {
      if (!confirm(`Kill PID ${pid}?`)) return;
      await fetch("/api/processes/kill", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ pid: pid })
      });
      loadProcesses();
    }

    // --- Port Tunnels ---
    async function loadTunnels() {
      const listEl = document.getElementById("tunnels-list");
      try {
        const res = await fetch("/api/tunnels");
        const tunnels = await res.json();
        if (tunnels.length === 0) {
          listEl.innerHTML = `<div class="p-4 text-slate-500 text-xs">No custom port tunnels active. Open one above!</div>`;
          return;
        }
        listEl.innerHTML = tunnels.map(t => `
          <div class="flex flex-col sm:flex-row sm:items-center justify-between p-3.5 bg-surface-950 border border-surface-700/60 rounded-lg gap-2">
            <div>
              <div class="flex items-center space-x-2">
                <span class="font-bold text-white text-xs">${t.label}</span>
                <span class="px-2 py-0.5 rounded text-[11px] font-mono bg-indigo-500/10 text-indigo-400 border border-indigo-500/30">Port ${t.port}</span>
              </div>
              <a href="${t.url}" target="_blank" class="text-xs text-cyan-400 hover:underline font-mono mt-1 inline-block">
                ${t.url || 'Generating tunnel...'} <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
              </a>
            </div>
            <a href="${t.url}" target="_blank" class="px-3 py-1.5 rounded-lg text-xs font-bold bg-indigo-600 hover:bg-indigo-500 text-white text-center transition">
              Open App
            </a>
          </div>
        `).join('');
      } catch(e) {
        listEl.innerHTML = `<div class="p-4 text-rose-400 text-xs">Error loading tunnels</div>`;
      }
    }

    async function createTunnel() {
      const port = parseInt(document.getElementById("input-tunnel-port").value);
      if (!port) return alert("Please enter a valid port number");
      const label = document.getElementById("input-tunnel-label").value || `Service-${port}`;
      await fetch("/api/tunnels/start", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ port: port, label: label })
      });
      loadTunnels();
      document.getElementById("input-tunnel-port").value = "";
      document.getElementById("input-tunnel-label").value = "";
    }

    function formatBytes(bytes) {
      if (bytes === 0) return '0 B';
      const k = 1024;
      const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
      const i = Math.floor(Math.log(bytes) / Math.log(k));
      return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    }
  </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def get_index():
    return HTML_INDEX

def run_server(port: int = 8000):
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print("=" * 65, flush=True)
    print("      🚀 KAGGLE VPS MASTER COCKPIT INITIALIZING...", flush=True)
    print("=" * 65, flush=True)
    
    def auto_provision_ai():
        if not IS_LINUX:
            return
        time.sleep(2)
        print("[*] [AUTO-PROVISION] Setting up Node.js, Ollama & LLaMA 3.2 on Port 8008...", flush=True)
        try:
            # 1. Install zstd & ollama if missing
            if not shutil.which("ollama"):
                os.system("apt-get update -qq && apt-get install -y -qq zstd curl >/dev/null 2>&1")
                os.system("curl -fsSL https://ollama.com/install.sh | sh >/dev/null 2>&1")
            
            # 2. Start Ollama on custom port 8008
            os.system("pkill -9 ollama 2>/dev/null; sleep 1")
            os.environ["OLLAMA_HOST"] = "0.0.0.0:8008"
            os.system("nohup ollama serve > /tmp/ollama_8008.log 2>&1 &")
            time.sleep(4)
            
            # 3. Pull LLaMA 3.2 in background
            os.system("nohup ollama pull llama3.2:1b >/dev/null 2>&1 &")
            
            # 4. Open Cloudflare tunnel for Port 8008
            print("[*] [AUTO-PROVISION] Launching Cloudflare tunnel for LLaMA 3.2 (Port 8008)...", flush=True)
            ai_url = start_cloudflare_tunnel(8008, "LLaMA-3.2-API-8008")
            if ai_url:
                print(f"  🤖 LLaMA 3.2 AI ENDPOINT: {ai_url}", flush=True)
                try:
                    import urllib.request
                    req = urllib.request.Request(
                        "https://ntfy.sh/ourvps_jaimiltrivedi_channel",
                        data=f"LLAMA_URL:{ai_url}".encode("utf-8"),
                        headers={"Title": "LLaMA 3.2 Live", "Priority": "high"}
                    )
                    urllib.request.urlopen(req, timeout=5)
                except Exception:
                    pass
        except Exception as e:
            print(f"[!] Auto-provision error: {e}", flush=True)

    def tunnel_starter():
        time.sleep(1.0)
        print("\n[*] Establishing secure Cloudflare public tunnel...", flush=True)
        url = start_cloudflare_tunnel(port, "VPS-Master-Cockpit")
        try:
            with open("LIVE_URL.txt", "w") as f:
                f.write(str(url))
        except Exception:
            pass
        try:
            with open("/tmp/kaggle_vps_url.txt", "w") as f:
                f.write(str(url))
        except Exception:
            pass
        # Broadcast URL instantly to free sync channel
        try:
            import urllib.request
            req = urllib.request.Request(
                "https://ntfy.sh/ourvps_jaimiltrivedi_channel",
                data=str(url).encode("utf-8"),
                headers={"Title": "Kaggle VPS Live", "Priority": "high"}
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass
        print("\n" + "=" * 65, flush=True)
        print("  🎉 YOUR KAGGLE VPS MASTER COCKPIT IS LIVE!", flush=True)
        print(f"  👉 PUBLIC URL: {url}", flush=True)
        print("=" * 65 + "\n", flush=True)

    threading.Thread(target=tunnel_starter, daemon=True).start()
    threading.Thread(target=auto_provision_ai, daemon=True).start()
    run_server(port)
