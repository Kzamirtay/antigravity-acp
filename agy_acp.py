#!/usr/bin/env python3
"""
Antigravity ACP Helper CLI
General-purpose manager for Google Antigravity ACP (Agent Client Protocol) server:
automated downloading from official ACP Registry, headless/WSL2 OAuth authentication,
status health-checks, ACP proxying, and client integrations (Paseo, Zed, etc.).
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import zipfile

# Base paths
BASE_DIR = Path(__file__).resolve().parent
BIN_DIR = BASE_DIR / "bin"
AUTH_URL_FILE = BASE_DIR / "auth_url.txt"
CALLBACK_URL_FILE = BASE_DIR / "callback_url.txt"
STATUS_FILE = BASE_DIR / "auth_status.json"
PASEO_CONFIG = Path.home() / ".paseo" / "config.json"
REGISTRY_CACHE_FILE = BASE_DIR / ".registry_cache.json"
VERSION_FILE = BASE_DIR / "version.json"
TOKEN_FILE = Path.home() / ".gemini" / "antigravity-acp" / "acp_token.json"
SETTINGS_DIR = Path.home() / ".gemini" / "antigravity-acp"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"

# Official ACP Registry
REGISTRY_URL = "https://cdn.agentclientprotocol.com/registry/v1/latest/registry.json"
AGENT_ID = "antigravity-acp"

# Fallback release if registry is unreachable offline
FALLBACK_RELEASES = {
    "linux-x86_64": {
        "version": "1.1.1",
        "archive": "https://dl.google.com/agy-extensions/releases/linux/agy-acp-server-agy_acp_server_1.1.1-linux-x86_64.zip",
        "cmd": "./agy_acp_server.par",
        "args": ["--uid="],
    },
    "linux-aarch64": {
        "version": "1.1.1",
        "archive": "https://dl.google.com/agy-extensions/releases/linux/agy-acp-server-agy_acp_server_1.1.1-linux-arm64.zip",
        "cmd": "./agy_acp_server.par",
        "args": ["--uid="],
    },
    "darwin-aarch64": {
        "version": "1.1.1",
        "archive": "https://dl.google.com/agy-extensions/releases/macos/agy-acp-server-agy_acp_server_1.1.1-darwin-arm64.zip",
        "cmd": "./agy_acp_server.par",
        "args": [],
    },
    "windows-x86_64": {
        "version": "1.1.1",
        "archive": "https://dl.google.com/agy-extensions/releases/windows/agy-acp-server-agy_acp_server_1.1.1-windows-x86_64.zip",
        "cmd": "./agy_acp_server.exe",
        "args": [],
    },
    "windows-aarch64": {
        "version": "1.1.1",
        "archive": "https://dl.google.com/agy-extensions/releases/windows/agy-acp-server-agy_acp_server_1.1.1-windows-arm64.zip",
        "cmd": "./agy_acp_server.exe",
        "args": [],
    },
}


def is_wsl() -> bool:
    """Checks if running inside WSL2 environment."""
    if sys.platform != "linux":
        return False
    try:
        with open("/proc/version", "r", encoding="utf-8") as f:
            content = f.read().lower()
            return "microsoft" in content or "wsl" in content
    except Exception:
        return False


def get_platform_key() -> str:
    """
    Returns platform key matching ACP Registry:
    darwin-aarch64, linux-x86_64, linux-aarch64, windows-x86_64, windows-aarch64
    """
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "linux":
        sys_str = "linux"
    elif system == "darwin":
        sys_str = "darwin"
    elif system == "windows":
        sys_str = "windows"
    else:
        sys_str = system

    if machine in ("x86_64", "amd64", "x64"):
        arch_str = "x86_64"
    elif machine in ("aarch64", "arm64"):
        arch_str = "aarch64"
    else:
        arch_str = machine

    return f"{sys_str}-{arch_str}"


def kill_stale_server_processes():
    """Kills any previous orphaned agy_acp_server processes to prevent lock/port contention."""
    try:
        me = os.getpid()
        for line in subprocess.check_output(["ps", "-eo", "pid,comm,args"], text=True).splitlines():
            if "agy_acp_server" in line:
                parts = line.strip().split()
                pid = int(parts[0])
                if pid != me:
                    try:
                        os.kill(pid, 15)
                    except Exception:
                        pass
    except Exception:
        pass


def fetch_registry_info() -> Dict[str, Any]:
    """
    Fetches the latest release info for Antigravity ACP from the official registry.
    Falls back to local cache or built-in metadata if network is unavailable.
    """
    data = None
    platform_key = get_platform_key()

    try:
        req = urllib.request.Request(
            REGISTRY_URL,
            headers={"User-Agent": "antigravity-acp-helper/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)

        try:
            with open(REGISTRY_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass
    except Exception:
        if REGISTRY_CACHE_FILE.exists():
            try:
                with open(REGISTRY_CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass

    if not data:
        fallback = FALLBACK_RELEASES.get(platform_key, FALLBACK_RELEASES["linux-x86_64"])
        return {
            "id": AGENT_ID,
            "name": "Google Antigravity",
            "version": fallback["version"],
            "description": "Google’s AI coding agent",
            "platform_key": platform_key,
            "archive_url": fallback["archive"],
            "cmd": fallback["cmd"],
            "args": fallback.get("args", []),
            "source": "fallback",
        }

    target_agent = None
    for agent in data.get("agents", []):
        if agent.get("id") in (AGENT_ID, "antigravity"):
            target_agent = agent
            break

    if not target_agent:
        raise ValueError(f"Agent '{AGENT_ID}' not found in registry!")

    dist = target_agent.get("distribution", {}).get("binary", {}).get(platform_key)
    if not dist:
        raise ValueError(f"Platform '{platform_key}' not supported in registry for {AGENT_ID}")

    return {
        "id": target_agent.get("id", AGENT_ID),
        "name": target_agent.get("name", "Google Antigravity"),
        "version": target_agent.get("version", "1.0.0"),
        "description": target_agent.get("description", ""),
        "website": target_agent.get("website", ""),
        "platform_key": platform_key,
        "archive_url": dist["archive"],
        "cmd": dist.get("cmd", "./agy_acp_server.par"),
        "args": dist.get("args", []),
        "source": "registry",
    }


def get_server_binary_path(registry_info: Optional[Dict[str, Any]] = None) -> Path:
    """Returns absolute path to the local server executable."""
    cmd = "./agy_acp_server.par"
    if registry_info and "cmd" in registry_info:
        cmd = registry_info["cmd"]
    elif VERSION_FILE.exists():
        try:
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                info = json.load(f)
                cmd = info.get("cmd", cmd)
        except Exception:
            pass

    bin_name = Path(cmd).name
    return BASE_DIR / bin_name


def get_server_command(server_bin: Optional[Path] = None, registry_info: Optional[Dict[str, Any]] = None) -> List[str]:
    """Builds the execution command line including required arguments from registry (e.g. --uid=)."""
    if not server_bin:
        server_bin = get_server_binary_path(registry_info)

    cmd = [str(server_bin.resolve())]

    args_to_add = []
    if registry_info and "args" in registry_info:
        args_to_add = registry_info["args"]
    elif VERSION_FILE.exists():
        try:
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                info = json.load(f)
                args_to_add = info.get("args", [])
        except Exception:
            pass

    if not args_to_add:
        try:
            reg = fetch_registry_info()
            args_to_add = reg.get("args", [])
        except Exception:
            pass

    for a in args_to_add:
        if a not in cmd:
            cmd.append(a)

    return cmd


def get_installed_version_info() -> Optional[Dict[str, Any]]:
    """Reads installed version information if available."""
    if VERSION_FILE.exists():
        try:
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def setup_xdg_open_wrapper():
    """Sets up bin/xdg-open interceptor that non-blockingly captures any passed URL."""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    wrapper_path = BIN_DIR / "xdg-open"

    script_content = """#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for arg in "$@"; do
    case "$arg" in
        http*)
            echo "$arg" > "$SCRIPT_DIR/auth_url.txt"
            if command -v powershell.exe >/dev/null 2>&1; then
                powershell.exe -NoProfile -Command "Start-Process '$arg'" < /dev/null > /dev/null 2>&1 &
            fi
            ;;
    esac
done
exit 0
"""
    with open(wrapper_path, "w", encoding="utf-8") as f:
        f.write(script_content)
    wrapper_path.chmod(0o755)
    return wrapper_path


def open_in_browser(url: str):
    """Attempts to open URL across OS environments (WSL, Linux, macOS)."""
    if is_wsl():
        try:
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-Command", f'Start-Process "{url}"'],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            pass

    if sys.platform == "darwin":
        try:
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            pass

    try:
        import webbrowser
        return webbrowser.open(url)
    except Exception:
        return False


def ensure_settings_json(auth_type: str = "oauth-personal") -> None:
    """Ensures ~/.gemini/antigravity-acp/settings.json exists with auth.type configured.

    Required by agy_acp_server when launched by ACP clients (like Paseo) without explicit
    JSON-RPC authenticate calls.
    """
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        settings = {}
        if SETTINGS_FILE.exists():
            try:
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    settings = json.load(f)
            except Exception:
                settings = {}
        auth_sec = settings.setdefault("auth", {})
        if auth_sec.get("type") != auth_type:
            auth_sec["type"] = auth_type
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2)
            print(f"✓ Configured auth.type='{auth_type}' in {SETTINGS_FILE}")
    except Exception as e:
        print(f"Warning: Could not update {SETTINGS_FILE}: {e}", file=sys.stderr)


def cmd_install(args):
    """Downloads and extracts agy_acp_server using the official ACP registry."""
    print(f"Fetching latest release info from ACP registry ({REGISTRY_URL})...")
    try:
        reg = fetch_registry_info()
    except Exception as e:
        print(f"Error fetching registry: {e}", file=sys.stderr)
        return 1

    server_bin = get_server_binary_path(reg)
    installed_info = get_installed_version_info()
    current_ver = installed_info.get("version") if installed_info else None
    latest_ver = reg["version"]

    print(f"Registry: {reg['name']} v{latest_ver} for {reg['platform_key']}")

    if server_bin.exists() and not getattr(args, "force", False):
        if current_ver == latest_ver:
            print(f"✓ Binary v{latest_ver} already present and up to date: {server_bin}")
            server_bin.chmod(0o755)
            return 0
        elif current_ver:
            print(f"Update available: v{current_ver} -> v{latest_ver}")
        else:
            print(f"✓ Binary already present at {server_bin} (use --force to reinstall)")
            server_bin.chmod(0o755)
            return 0

    url = reg["archive_url"]
    zip_name = Path(url).name
    zip_path = BASE_DIR / zip_name

    print(f"Downloading {zip_name}...")
    try:
        urllib.request.urlretrieve(url, zip_path)
    except Exception as e:
        print(f"Download failed: {e}", file=sys.stderr)
        return 1

    print("Extracting archive...")
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            for info in z.infolist():
                extracted_path = z.extract(info, BASE_DIR)
                perm = (info.external_attr >> 16) & 0o777
                if perm:
                    os.chmod(extracted_path, perm | 0o755 if (perm & 0o111) else perm)
                elif Path(extracted_path).suffix in (".par", ".sh") or "localharness" in info.filename:
                    os.chmod(extracted_path, 0o755)
    except Exception as e:
        print(f"Extraction failed: {e}", file=sys.stderr)
        return 1

    for item in BASE_DIR.iterdir():
        if item.is_file() and (item.suffix in (".par", ".sh", ".exe") or "localharness" in item.name):
            try:
                item.chmod(0o755)
            except Exception:
                pass

    if server_bin.exists():
        server_bin.chmod(0o755)
        record = {
            "name": reg["name"],
            "version": latest_ver,
            "platform": reg["platform_key"],
            "archive_url": url,
            "cmd": reg["cmd"],
            "args": reg.get("args", []),
            "installed_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(VERSION_FILE, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)

        print(f"✓ {reg['name']} v{latest_ver} successfully installed at {server_bin}")
        return 0
    else:
        print(f"Error: Executable {server_bin.name} not found after extraction!", file=sys.stderr)
        return 1


def check_auth_status(server_bin: Optional[Path] = None) -> bool:
    """Checks if agy_acp_server is already authenticated."""
    if not server_bin:
        server_bin = get_server_binary_path()
    if not server_bin.exists():
        return False

    # First check if token file exists on disk
    if TOKEN_FILE.exists():
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                td = json.load(f)
                if td.get("refresh_token") or td.get("access_token"):
                    ensure_settings_json("oauth-personal")
                    return True
        except Exception:
            pass

    server_cmd = get_server_command(server_bin)
    try:
        proc = subprocess.Popen(
            server_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except Exception:
        return False

    stdout_queue: List[str] = []
    stderr_queue: List[str] = []

    def stream_reader(stream, queue):
        for line in iter(stream.readline, ""):
            queue.append(line)

    t_out = threading.Thread(target=stream_reader, args=(proc.stdout, stdout_queue), daemon=True)
    t_err = threading.Thread(target=stream_reader, args=(proc.stderr, stderr_queue), daemon=True)
    t_out.start()
    t_err.start()

    try:
        # initialize
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}}) + "\n")
        proc.stdin.flush()

        start = time.time()
        while time.time() - start < 4:
            if stdout_queue:
                break
            time.sleep(0.05)

        if not stdout_queue:
            return False

        # authenticate
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "authenticate", "params": {"methodId": "oauth-personal"}}) + "\n")
        proc.stdin.flush()

        start = time.time()
        while time.time() - start < 4:
            for line in stdout_queue:
                if '"id":2' in line or '"id": 2' in line:
                    try:
                        d = json.loads(line)
                        if "result" in d and not d.get("error"):
                            return True
                    except json.JSONDecodeError:
                        pass
            if stderr_queue:
                for err_line in stderr_queue:
                    if "accounts.google.com" in err_line or "Open the following link" in err_line:
                        return False
            time.sleep(0.05)

        return False
    except Exception:
        return False
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=1)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def cmd_auth(args):
    """Performs JSON-RPC stdio authentication with robust threaded stream capture."""
    server_bin = get_server_binary_path()
    if not server_bin.exists():
        print(f"Error: Server binary not found at {server_bin}. Run 'install' first.", file=sys.stderr)
        return 1

    try:
        server_bin.chmod(0o755)
    except Exception:
        pass

    # Check existing auth
    if not getattr(args, "force", False) and check_auth_status(server_bin):
        print("✓ Already authenticated! Credentials are valid.")
        return 0

    kill_stale_server_processes()
    time.sleep(0.2)
    setup_xdg_open_wrapper()

    env = os.environ.copy()
    env["PATH"] = f"{BIN_DIR}:{env.get('PATH', '')}"
    env["BROWSER"] = str(BIN_DIR / "xdg-open")
    env["PYTHONUNBUFFERED"] = "1"

    # Clean previous temp files
    for p in [AUTH_URL_FILE, CALLBACK_URL_FILE, STATUS_FILE]:
        if p.exists():
            p.unlink()

    server_cmd = get_server_command(server_bin)
    cmd_str = " ".join(server_cmd)
    print(f"Starting {server_bin.name} ({cmd_str})...")

    try:
        proc = subprocess.Popen(
            server_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
    except Exception as e:
        print(f"Error launching server process: {e}", file=sys.stderr)
        return 1

    stdout_queue: List[str] = []
    stderr_queue: List[str] = []

    def stream_reader(stream, queue):
        for line in iter(stream.readline, ""):
            queue.append(line)

    t_out = threading.Thread(target=stream_reader, args=(proc.stdout, stdout_queue), daemon=True)
    t_err = threading.Thread(target=stream_reader, args=(proc.stderr, stderr_queue), daemon=True)
    t_out.start()
    t_err.start()

    # 1. Initialize
    print("1. Sending JSON-RPC 'initialize'...")
    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": 1,
            "clientInfo": {"name": "agy-acp-cli", "version": "1.0.0"},
        },
    }

    try:
        proc.stdin.write(json.dumps(init_req) + "\n")
        proc.stdin.flush()
    except (BrokenPipeError, OSError) as e:
        time.sleep(0.3)
        proc.poll()
        err_out = "".join(stderr_queue)
        print(f"\n❌ Failed to write to server (exit code: {proc.returncode}): {e}", file=sys.stderr)
        if err_out.strip():
            print(f"Server stderr:\n{err_out.strip()}", file=sys.stderr)
        return 1

    # Wait for initialize response
    start_init = time.time()
    init_data = None
    while time.time() - start_init < 8:
        if proc.poll() is not None:
            break
        if stdout_queue:
            try:
                init_data = json.loads(stdout_queue[0])
                break
            except json.JSONDecodeError:
                pass
        time.sleep(0.05)

    if not init_data:
        time.sleep(0.2)
        proc.poll()
        err_out = "".join(stderr_queue)
        print(f"\n❌ Server did not respond to 'initialize' (exit code: {proc.returncode})", file=sys.stderr)
        if err_out.strip():
            print(f"Server stderr:\n{err_out.strip()}", file=sys.stderr)
        try:
            proc.terminate()
        except Exception:
            pass
        return 1

    if "error" in init_data:
        print(f"\n❌ JSON-RPC initialize error: {init_data['error']}", file=sys.stderr)
        proc.terminate()
        return 1

    # 2. Authenticate
    print("2. Sending JSON-RPC 'authenticate' (method: oauth-personal)...")
    auth_req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "authenticate",
        "params": {"methodId": "oauth-personal"},
    }

    try:
        proc.stdin.write(json.dumps(auth_req) + "\n")
        proc.stdin.flush()
    except (BrokenPipeError, OSError) as e:
        time.sleep(0.3)
        proc.poll()
        err_out = "".join(stderr_queue)
        print(f"\n❌ Server pipe broken before authentication (exit code: {proc.returncode}): {e}", file=sys.stderr)
        if err_out.strip():
            print(f"Server stderr:\n{err_out.strip()}", file=sys.stderr)
        return 1

    # 3. Check for immediate success (already authenticated) OR intercept auth URL
    auth_url = None
    listener_port = None
    start_wait = time.time()

    while time.time() - start_wait < 15:
        if proc.poll() is not None:
            break

        # Check ALL lines in stdout and stderr
        all_lines = list(stdout_queue) + list(stderr_queue)
        for line in all_lines:
            # Check for Auth URL
            m = re.search(r"https://accounts\.google\.com/\S+", line)
            if m:
                auth_url = m.group(0)
                port_m = re.search(r"redirect_uri=http%3A%2F%2F(?:127\.0\.0\.1|localhost)%3A(\d+)", auth_url)
                if not port_m:
                    port_m = re.search(r"http://(?:127\.0\.0\.1|localhost):(\d+)", auth_url)
                if port_m:
                    listener_port = int(port_m.group(1))
                break

            # Check for JSON-RPC id: 2 response
            if '"id":2' in line or '"id": 2' in line:
                try:
                    resp = json.loads(line)
                    if "error" in resp:
                        print(f"\n❌ JSON-RPC authenticate error: {resp['error']}", file=sys.stderr)
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                        return 1
                    if "result" in resp:
                        ensure_settings_json("oauth-personal")
                        print("\n🎉 Already authenticated! Credentials are valid.")
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                        return 0
                except json.JSONDecodeError:
                    pass

        if auth_url:
            break

        # Check AUTH_URL_FILE written by xdg-open wrapper
        if AUTH_URL_FILE.exists():
            content = AUTH_URL_FILE.read_text(encoding="utf-8").strip()
            if content and "accounts.google.com" in content:
                auth_url = content
                port_m = re.search(r"redirect_uri=http%3A%2F%2F(?:127\.0\.0\.1|localhost)%3A(\d+)", auth_url)
                if not port_m:
                    port_m = re.search(r"http://(?:127\.0\.0\.1|localhost):(\d+)", auth_url)
                if port_m:
                    listener_port = int(port_m.group(1))
                break

        time.sleep(0.1)

    if not auth_url:
        time.sleep(0.2)
        proc.poll()
        err_out = "".join(stderr_queue).strip()
        stdout_out = "".join(stdout_queue).strip()
        print(f"\n❌ Error: Could not retrieve authentication URL from server (exit code: {proc.returncode}).", file=sys.stderr)
        if stdout_out:
            print(f"Server stdout:\n{stdout_out}", file=sys.stderr)
        if err_out:
            print(f"Server stderr:\n{err_out}", file=sys.stderr)
        try:
            proc.terminate()
        except Exception:
            pass
        return 1

    print("\n" + "=" * 70)
    print("GOOGLE AUTHENTICATION REQUIRED")
    print("=" * 70)
    print(f"\nOpen this link in your browser if it did not open automatically:\n\n{auth_url}\n")
    if listener_port:
        print(f"Local callback listener is active on port: {listener_port}")
    print("=" * 70)
    print("Ожидание завершения авторизации...")
    print("Внимание: в WSL2 браузер Windows после входа в Google покажет ошибку подключения")
    print("(ERR_CONNECTION_REFUSED) — это нормально!")
    print("\n👉 Скопируйте полный URL из адресной строки браузера")
    print(f"   (начинается с http://127.0.0.1:{listener_port}/... или http://localhost:{listener_port}/...)")
    print("   и вставьте его прямо сюда (или запишите в callback_url.txt):")
    print("=" * 70 + "\n")

    # Attempt to open browser
    open_in_browser(auth_url)

    stop_event = threading.Event()

    def input_thread():
        """Allows user to paste the callback URL directly into the CLI."""
        while not stop_event.is_set():
            if sys.stdin.isatty():
                try:
                    line = sys.stdin.readline()
                    if line:
                        cleaned = line.strip().strip("'\"")
                        if cleaned and ("code" in cleaned or "http" in cleaned or "127.0.0.1" in cleaned or "localhost" in cleaned):
                            CALLBACK_URL_FILE.write_text(cleaned, encoding="utf-8")
                except Exception:
                    pass
            else:
                time.sleep(0.5)

    t_in = threading.Thread(target=input_thread, daemon=True)
    t_in.start()

    delivered_callback = False
    start_time = time.time()
    timeout = 600
    authenticated = False

    try:
        while time.time() - start_time < timeout:
            if proc.poll() is not None:
                err_out = "".join(stderr_queue)
                print(f"Server process terminated unexpectedly with code {proc.returncode}")
                if err_out.strip():
                    print(f"Server output:\n{err_out.strip()}", file=sys.stderr)
                break

            # Check stdout for success response
            for line in stdout_queue:
                if '"id":2' in line or '"id": 2' in line:
                    try:
                        resp = json.loads(line)
                        if "result" in resp:
                            ensure_settings_json("oauth-personal")
                            print("\n🎉 Авторизация успешно завершена! (Authentication successful)")
                            authenticated = True
                            break
                        elif "error" in resp:
                            print(f"\n❌ Ошибка авторизации: {resp['error']}")
                            break
                    except json.JSONDecodeError:
                        pass

            if authenticated:
                break

            # Check callback file
            if not delivered_callback and CALLBACK_URL_FILE.exists():
                cb_raw = CALLBACK_URL_FILE.read_text(encoding="utf-8").strip().strip("'\"")
                if cb_raw and listener_port:
                    if cb_raw.startswith("http://") or cb_raw.startswith("https://"):
                        parsed = urlparse(cb_raw)
                        path = parsed.path if parsed.path else "/"
                        query = f"?{parsed.query}" if parsed.query else ""
                        target = f"http://127.0.0.1:{listener_port}{path}{query}"
                    elif cb_raw.startswith("/"):
                        target = f"http://127.0.0.1:{listener_port}{cb_raw}"
                    elif cb_raw.startswith("?"):
                        target = f"http://127.0.0.1:{listener_port}/{cb_raw}"
                    elif "code=" in cb_raw:
                        target = f"http://127.0.0.1:{listener_port}/?{cb_raw}"
                    else:
                        target = f"http://127.0.0.1:{listener_port}/?code={cb_raw}"

                    print(f"\nДоставка коллбека на локальный порт ({target})...")
                    try:
                        req = urllib.request.Request(target, headers={"User-Agent": "curl/7.81.0"})
                        with urllib.request.urlopen(req, timeout=5) as resp:
                            print(f"✓ Коллбек успешно доставлен (HTTP {resp.status})!")
                        delivered_callback = True
                    except Exception as e:
                        print(f"Попытка доставки коллбека: {e}")

            time.sleep(0.2)
    finally:
        stop_event.set()
        time.sleep(0.5)
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    return 0 if authenticated else 1


def cmd_run(args):
    """Executes the ACP server directly on stdio (for use by any ACP client/editor)."""
    ensure_settings_json("oauth-personal")
    server_bin = get_server_binary_path()
    if not server_bin.exists():
        print(f"Error: Binary not found at {server_bin}. Run 'install' first.", file=sys.stderr)
        return 1

    try:
        server_bin.chmod(0o755)
    except Exception:
        pass

    server_cmd = get_server_command(server_bin)
    extra_args = getattr(args, "extra_args", [])
    cmd = server_cmd + extra_args
    os.execv(str(server_bin.resolve()), cmd)


def cmd_paseo(args):
    """Configures Paseo (~/.paseo/config.json) to register the Antigravity ACP provider."""
    ensure_settings_json("oauth-personal")
    config_path = Path(getattr(args, "config_path", str(PASEO_CONFIG))).expanduser()
    if not config_path.parent.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)

    config_data = {}
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
        except Exception as e:
            print(f"Warning: Failed to parse existing {config_path}: {e}", file=sys.stderr)

    # Backup original
    if config_path.exists():
        backup_path = config_path.with_suffix(".json.bak")
        shutil.copy2(config_path, backup_path)
        print(f"Backed up existing config to {backup_path}")

    agents = config_data.setdefault("agents", {})
    providers = agents.setdefault("providers", {})

    server_bin = get_server_binary_path()
    cmd = get_server_command(server_bin)

    providers["antigravity"] = {
        "extends": "acp",
        "label": "Antigravity",
        "command": cmd,
        "params": {
            "supportsMcpServers": False
        },
    }

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2)

    print(f"✓ Updated {config_path} with antigravity provider configuration (command: {cmd}).")

    # Reload Paseo if running
    if shutil.which("paseo"):
        print("Reloading Paseo daemon configuration...")
        res = subprocess.run(["paseo", "reload"], capture_output=True, text=True)
        if res.returncode == 0:
            print("✓ Paseo daemon reloaded.")
            time.sleep(2)
            subprocess.run(["paseo", "provider", "ls"])
        elif "ECONNREFUSED" in res.stderr:
            print("ℹ️ Демон Paseo сейчас не запущен. Конфигурация успешно обновлена!")
            print("Запустите демон командой: paseo start")
        else:
            print(f"Note: 'paseo reload' exited with code {res.returncode}: {res.stderr.strip()}")
    else:
        print("Note: 'paseo' CLI not found in PATH. Make sure to restart Paseo daemon.")

    return 0


def cmd_status(args):
    """Displays health and status of binary, registry, auth, and Paseo."""
    print("=" * 65)
    print("ANTIGRAVITY ACP STATUS CHECK")
    print("=" * 65)

    # 1. Registry check
    try:
        reg = fetch_registry_info()
        print(f"[Registry]   ✓ Latest {reg['name']} v{reg['version']} ({reg['platform_key']})")
    except Exception as e:
        print(f"[Registry]   ! Unable to reach registry: {e}")
        reg = None

    # 2. Binary check
    server_bin = get_server_binary_path(reg)
    installed_info = get_installed_version_info()
    inst_ver = installed_info.get("version") if installed_info else "unknown"

    if server_bin.exists():
        size_mb = server_bin.stat().st_size / (1024 * 1024)
        print(f"[Binary]     ✓ Present ({server_bin.name}, version: {inst_ver}) - {size_mb:.1f} MB")
        if reg and inst_ver != "unknown" and inst_ver != reg["version"]:
            print(f"[Update]     ! Newer version v{reg['version']} available! Run './setup.sh install --force'")
    else:
        print(f"[Binary]     ✗ Missing at {server_bin}")

    # 3. Auth check
    if server_bin.exists():
        is_auth = check_auth_status(server_bin)
        if is_auth:
            print("[Auth]       ✓ Authenticated (OAuth token valid)")
        else:
            print("[Auth]       ✗ Not authenticated (Run './setup.sh auth')")
    else:
        print("[Auth]       ? Skipped (binary missing)")

    # 4. Paseo status (optional client check)
    print("-" * 65)
    print("Client Integrations:")
    if PASEO_CONFIG.exists():
        try:
            with open(PASEO_CONFIG, "r", encoding="utf-8") as f:
                c = json.load(f)
            ag = c.get("agents", {}).get("providers", {}).get("antigravity")
            if ag:
                print(f" [Paseo Config] ✓ Configured (command: {ag.get('command')})")
            else:
                print(" [Paseo Config] - Not configured in ~/.paseo/config.json (run './setup.sh paseo' to add)")
        except Exception:
            print(" [Paseo Config] ✗ Error reading ~/.paseo/config.json")
    else:
        print(" [Paseo Config] - ~/.paseo/config.json not found")

    if shutil.which("paseo"):
        res = subprocess.run(["paseo", "provider", "ls"], capture_output=True, text=True)
        if res.returncode == 0 and "antigravity" in res.stdout:
            for line in res.stdout.splitlines():
                if "antigravity" in line:
                    print(f" [Paseo Status] ✓ {line.strip()}")
                    break
        else:
            print(" [Paseo Status] - Antigravity provider not active in 'paseo provider ls'")

    print("=" * 65)
    return 0


def cmd_setup(args):
    """End-to-end ACP setup: install from registry -> auth -> status."""
    ensure_settings_json("oauth-personal")
    print(">>> Step 1: Checking and fetching binary from ACP Registry...")
    ret = cmd_install(args)
    if ret != 0:
        return ret

    print("\n>>> Step 2: Checking authentication...")
    ret = cmd_auth(args)
    if ret != 0:
        return ret

    print("\n>>> Step 3: Verifying ACP server status...")
    ret = cmd_status(args)

    print("\n💡 Antigravity ACP is ready for any ACP client (Zed, Cursor, OpenCode, Paseo, etc.)!")
    print("To integrate with Paseo specifically, run:")
    print("  ./setup.sh paseo\n")
    return ret


def main():
    parser = argparse.ArgumentParser(
        description="Google Antigravity ACP Server Manager & Auth Helper (ACP Registry)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # setup (general ACP setup: install + auth + status)
    p_setup = subparsers.add_parser("setup", help="Run full ACP setup (install from registry, auth, verify)")
    p_setup.add_argument("--force", action="store_true", help="Force re-download and re-auth")
    p_setup.set_defaults(func=cmd_setup)

    # install
    p_inst = subparsers.add_parser("install", help="Download/update agy_acp_server from ACP Registry")
    p_inst.add_argument("--force", action="store_true", help="Force re-download even if already present")
    p_inst.set_defaults(func=cmd_install)

    # auth
    p_auth = subparsers.add_parser("auth", help="Perform JSON-RPC OAuth authentication")
    p_auth.add_argument("--force", action="store_true", help="Force re-authentication")
    p_auth.set_defaults(func=cmd_auth)

    # status
    p_stat = subparsers.add_parser("status", help="Check status of registry, binary, and authentication")
    p_stat.set_defaults(func=cmd_status)

    # run (serve ACP over stdio)
    p_run = subparsers.add_parser("run", help="Run the Antigravity ACP server directly on stdio")
    p_run.add_argument("extra_args", nargs=argparse.REMAINDER, help="Extra arguments passed to the server binary")
    p_run.set_defaults(func=cmd_run)

    # paseo (configure Paseo integration)
    p_paseo = subparsers.add_parser("paseo", help="Configure Paseo (~/.paseo/config.json) to use Antigravity ACP")
    p_paseo.add_argument("--use-registry-args", action="store_true", help="Include default arguments from registry (e.g. --uid=)")
    p_paseo.add_argument("--use-uid", action="store_true", help="Alias for --use-registry-args")
    p_paseo.add_argument("--config-path", default=str(PASEO_CONFIG), help="Path to Paseo config.json")
    p_paseo.set_defaults(func=cmd_paseo)

    # config alias for paseo
    p_cfg = subparsers.add_parser("config", help="Alias for 'paseo' subcommand")
    p_cfg.add_argument("--use-registry-args", action="store_true", help="Include default arguments from registry (e.g. --uid=)")
    p_cfg.add_argument("--use-uid", action="store_true", help="Alias for --use-registry-args")
    p_cfg.add_argument("--config-path", default=str(PASEO_CONFIG), help="Path to Paseo config.json")
    p_cfg.set_defaults(func=cmd_paseo)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
