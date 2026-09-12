#!/usr/bin/env python3
"""
Antigravity ACP Helper CLI
Automates downloading, headless JSON-RPC OAuth authentication,
and Paseo integration for Google Antigravity ACP Server.
"""

import argparse
import json
import os
import platform
import re
import select
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

# Default paths
BASE_DIR = Path(__file__).resolve().parent
BIN_DIR = BASE_DIR / "bin"
SERVER_BIN = BASE_DIR / "agy_acp_server.par"
AUTH_URL_FILE = BASE_DIR / "auth_url.txt"
CALLBACK_URL_FILE = BASE_DIR / "callback_url.txt"
STATUS_FILE = BASE_DIR / "auth_status.json"
PASEO_CONFIG = Path.home() / ".paseo" / "config.json"

# Official download URLs
RELEASES = {
    ("Linux", "x86_64"): "https://dl.google.com/agy-extensions/releases/linux/agy-acp-server-agy_acp_server_1.1.1-linux-x86_64.zip",
    ("Linux", "aarch64"): "https://dl.google.com/agy-extensions/releases/linux/agy-acp-server-agy_acp_server_1.1.1-linux-arm64.zip",
    ("Darwin", "arm64"): "https://dl.google.com/agy-extensions/releases/macos/agy-acp-server-agy_acp_server_1.1.1-darwin-arm64.zip",
}


def is_wsl() -> bool:
    if sys.platform != "linux":
        return False
    try:
        with open("/proc/version", "r", encoding="utf-8") as f:
            content = f.read().lower()
            return "microsoft" in content or "wsl" in content
    except Exception:
        return False


def setup_xdg_open_wrapper():
    """Sets up bin/xdg-open interceptor for capturing auth URLs."""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    wrapper_path = BIN_DIR / "xdg-open"

    wsl_mode = is_wsl()
    open_cmd = (
        'powershell.exe -NoProfile -Command "Start-Process \'$URL\'" < /dev/null > /dev/null 2>&1 &'
        if wsl_mode
        else 'python3 -m webbrowser "$URL" > /dev/null 2>&1 &'
    )

    script_content = f"""#!/bin/bash
URL="$1"
echo "$URL" > "{AUTH_URL_FILE}"
echo "[xdg-open] Intercepted URL: $URL" >&2
{open_cmd}
"""
    with open(wrapper_path, "w", encoding="utf-8") as f:
        f.write(script_content)
    wrapper_path.chmod(0o755)
    return wrapper_path


def open_in_browser(url: str):
    """Attempts to open URL across OS environments (WSL, Linux, macOS)."""
    if is_wsl():
        try:
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", f'Start-Process "{url}"'],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            return True
        except Exception:
            pass

    if sys.platform == "darwin":
        try:
            subprocess.run(["open", url], timeout=5)
            return True
        except Exception:
            pass

    try:
        import webbrowser
        return webbrowser.open(url)
    except Exception:
        return False


def cmd_install(args):
    """Downloads and extracts agy_acp_server if not present."""
    if SERVER_BIN.exists() and not args.force:
        print(f"✓ Binary already present: {SERVER_BIN}")
        return 0

    os_type = platform.system()
    arch = platform.machine()
    key = (os_type, arch)

    url = RELEASES.get(key)
    if not url:
        print(f"Error: Unsupported OS/architecture: {os_type} {arch}", file=sys.stderr)
        print("Please manually place 'agy_acp_server.par' in the repository directory.", file=sys.stderr)
        return 1

    zip_name = Path(url).name
    zip_path = BASE_DIR / zip_name

    print(f"Downloading {zip_name} from Google CDN...")
    urllib.request.urlretrieve(url, zip_path)
    print("Extracting archive...")
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(BASE_DIR)

    if SERVER_BIN.exists():
        SERVER_BIN.chmod(0o755)
        print(f"✓ agy_acp_server ready at {SERVER_BIN}")
        return 0
    else:
        print("Error: agy_acp_server.par was not found in archive!", file=sys.stderr)
        return 1


def check_auth_status() -> bool:
    """Checks if agy_acp_server is already authenticated."""
    if not SERVER_BIN.exists():
        return False

    proc = subprocess.Popen(
        [str(SERVER_BIN)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    try:
        # initialize
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}}) + "\n")
        proc.stdin.flush()
        proc.stdout.readline()

        # authenticate
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "authenticate", "params": {"methodId": "oauth-personal"}}) + "\n")
        proc.stdin.flush()

        r, _, _ = select.select([proc.stdout, proc.stderr], [], [], 3)
        if proc.stdout in r:
            line = proc.stdout.readline()
            data = json.loads(line)
            if "result" in data and not data.get("error"):
                return True
        return False
    except Exception:
        return False
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()


def cmd_auth(args):
    """Performs JSON-RPC stdio authentication."""
    if not SERVER_BIN.exists():
        print(f"Error: Server binary not found at {SERVER_BIN}. Run 'install' first.", file=sys.stderr)
        return 1

    # Check existing auth
    if not args.force and check_auth_status():
        print("✓ Already authenticated! Credentials are valid.")
        return 0

    setup_xdg_open_wrapper()

    env = os.environ.copy()
    env["PATH"] = f"{BIN_DIR}:{env.get('PATH', '')}"
    env["BROWSER"] = str(BIN_DIR / "xdg-open")
    env["PYTHONUNBUFFERED"] = "1"

    # Clean previous temp files
    for p in [AUTH_URL_FILE, CALLBACK_URL_FILE, STATUS_FILE]:
        if p.exists():
            p.unlink()

    print(f"Starting {SERVER_BIN.name}...")
    proc = subprocess.Popen(
        [str(SERVER_BIN)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )

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
    proc.stdin.write(json.dumps(init_req) + "\n")
    proc.stdin.flush()
    proc.stdout.readline()

    # 2. Authenticate
    print("2. Sending JSON-RPC 'authenticate' (method: oauth-personal)...")
    auth_req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "authenticate",
        "params": {"methodId": "oauth-personal"},
    }
    proc.stdin.write(json.dumps(auth_req) + "\n")
    proc.stdin.flush()

    # 3. Intercept auth URL
    auth_url = None
    listener_port = None
    start_wait = time.time()

    while time.time() - start_wait < 15:
        r, _, _ = select.select([proc.stderr], [], [], 0.5)
        if r:
            line = proc.stderr.readline()
            if line:
                m = re.search(r"https://accounts\.google\.com/\S+", line)
                if m:
                    auth_url = m.group(0)
                    port_m = re.search(r"redirect_uri=http%3A%2F%2F127\.0\.0\.1%3A(\d+)%2F", auth_url)
                    if port_m:
                        listener_port = int(port_m.group(1))
                    break

        if AUTH_URL_FILE.exists():
            content = AUTH_URL_FILE.read_text(encoding="utf-8").strip()
            if content:
                auth_url = content
                port_m = re.search(r"redirect_uri=http%3A%2F%2F127\.0\.0\.1%3A(\d+)%2F", auth_url)
                if port_m:
                    listener_port = int(port_m.group(1))
                break

    if not auth_url:
        print("Error: Could not retrieve authentication URL from server.", file=sys.stderr)
        proc.terminate()
        return 1

    print("\n" + "=" * 70)
    print("GOOGLE AUTHENTICATION REQUIRED")
    print("=" * 70)
    print(f"\nOpen this link in your browser if it did not open automatically:\n\n{auth_url}\n")
    if listener_port:
        print(f"Local callback listener is active on port: {listener_port}")
    print("=" * 70)
    print("Waiting for authentication callback...\n")
    print("(If the browser shows 'connection refused' after redirect, copy the full URL")
    print(f" from the browser's address bar and paste it into {CALLBACK_URL_FILE} or enter it here)\n")

    # Attempt to open browser
    open_in_browser(auth_url)

    authenticated = False
    stop_event = threading.Event()

    def input_thread():
        """Allows user to paste the callback URL directly into the CLI."""
        while not stop_event.is_set():
            if sys.stdin.isatty():
                try:
                    r, _, _ = select.select([sys.stdin], [], [], 1.0)
                    if r:
                        line = sys.stdin.readline().strip()
                        if line and ("code=" in line or "http" in line):
                            CALLBACK_URL_FILE.write_text(line, encoding="utf-8")
                except Exception:
                    pass
            else:
                time.sleep(1)

    t = threading.Thread(target=input_thread, daemon=True)
    t.start()

    delivered_callback = False
    start_time = time.time()
    timeout = 600

    try:
        while time.time() - start_time < timeout:
            if proc.poll() is not None:
                print(f"Server process terminated with code {proc.returncode}")
                break

            # Check stdout for success response
            r, _, _ = select.select([proc.stdout], [], [], 0.5)
            if r:
                line = proc.stdout.readline()
                if line:
                    try:
                        resp = json.loads(line)
                        if resp.get("id") == 2:
                            if "result" in resp:
                                print("\n🎉 Authentication successful!")
                                authenticated = True
                                break
                            elif "error" in resp:
                                print(f"\n❌ Authentication failed: {resp['error']}")
                                break
                    except json.JSONDecodeError:
                        pass

            # Check callback file
            if not delivered_callback and CALLBACK_URL_FILE.exists():
                cb_raw = CALLBACK_URL_FILE.read_text(encoding="utf-8").strip()
                if cb_raw and listener_port:
                    parsed = urlparse(cb_raw)
                    target = f"http://127.0.0.1:{listener_port}{parsed.path}"
                    if parsed.query:
                        target += f"?{parsed.query}"
                    print(f"Delivering redirect callback to loopback listener ({target})...")
                    try:
                        req = urllib.request.Request(target, headers={"User-Agent": "curl/7.81.0"})
                        with urllib.request.urlopen(req, timeout=5) as resp:
                            print(f"Callback delivered (HTTP {resp.status})")
                        delivered_callback = True
                    except Exception as e:
                        print(f"Callback delivery attempt error: {e}")

            time.sleep(0.5)
    finally:
        stop_event.set()
        time.sleep(1)
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()

    return 0 if authenticated else 1


def cmd_config_paseo(args):
    """Configures Paseo (~/.paseo/config.json) to use Antigravity ACP."""
    if not PASEO_CONFIG.parent.exists():
        PASEO_CONFIG.parent.mkdir(parents=True, exist_ok=True)

    config_data = {}
    if PASEO_CONFIG.exists():
        try:
            with open(PASEO_CONFIG, "r", encoding="utf-8") as f:
                config_data = json.load(f)
        except Exception as e:
            print(f"Warning: Failed to parse existing {PASEO_CONFIG}: {e}", file=sys.stderr)

    # Backup original
    if PASEO_CONFIG.exists():
        backup_path = PASEO_CONFIG.with_suffix(".json.bak")
        shutil.copy2(PASEO_CONFIG, backup_path)
        print(f"Backed up existing config to {backup_path}")

    # Ensure structure
    agents = config_data.setdefault("agents", {})
    providers = agents.setdefault("providers", {})

    cmd = [str(SERVER_BIN.resolve())]
    if args.use_uid:
        cmd.append("--uid=")

    providers["antigravity"] = {
        "extends": "acp",
        "label": "Antigravity",
        "command": cmd,
        "params": {
            "supportsMcpServers": False
        },
    }

    with open(PASEO_CONFIG, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2)

    print(f"✓ Updated {PASEO_CONFIG} with antigravity provider configuration.")

    # Reload Paseo if running
    if shutil.which("paseo"):
        print("Reloading Paseo daemon configuration...")
        res = subprocess.run(["paseo", "reload"], capture_output=True, text=True)
        if res.returncode == 0:
            print("✓ Paseo daemon reloaded.")
            # Give daemon a second to initialize ACP agent
            time.sleep(2)
            subprocess.run(["paseo", "provider", "ls"])
        else:
            print(f"Note: 'paseo reload' exited with code {res.returncode}: {res.stderr.strip()}")
    else:
        print("Note: 'paseo' CLI not found in PATH. Make sure to restart Paseo daemon.")

    return 0


def cmd_status(args):
    """Displays health and status of binary, auth, and Paseo."""
    print("=" * 60)
    print("ANTIGRAVITY ACP STATUS CHECK")
    print("=" * 60)

    # 1. Binary check
    if SERVER_BIN.exists():
        size_mb = SERVER_BIN.stat().st_size / (1024 * 1024)
        print(f"[Binary]    ✓ Present ({SERVER_BIN.resolve()}) - {size_mb:.1f} MB")
    else:
        print(f"[Binary]    ✗ Missing at {SERVER_BIN}")

    # 2. Auth check
    if SERVER_BIN.exists():
        is_auth = check_auth_status()
        if is_auth:
            print("[Auth]      ✓ Authenticated (OAuth token valid)")
        else:
            print("[Auth]      ✗ Not authenticated (Run 'auth' command)")
    else:
        print("[Auth]      ? Skipped (binary missing)")

    # 3. Paseo config check
    if PASEO_CONFIG.exists():
        try:
            with open(PASEO_CONFIG, "r", encoding="utf-8") as f:
                c = json.load(f)
            ag = c.get("agents", {}).get("providers", {}).get("antigravity")
            if ag:
                print(f"[Paseo Cfg] ✓ Configured (command: {ag.get('command')})")
            else:
                print("[Paseo Cfg] ✗ 'antigravity' provider not found in ~/.paseo/config.json")
        except Exception:
            print("[Paseo Cfg] ✗ Error reading ~/.paseo/config.json")
    else:
        print("[Paseo Cfg] ✗ ~/.paseo/config.json does not exist")

    # 4. Paseo CLI check
    if shutil.which("paseo"):
        res = subprocess.run(["paseo", "provider", "ls"], capture_output=True, text=True)
        if res.returncode == 0 and "antigravity" in res.stdout:
            for line in res.stdout.splitlines():
                if "antigravity" in line:
                    print(f"[Paseo CLI] ✓ {line.strip()}")
                    break
        else:
            print("[Paseo CLI] ✗ Antigravity provider not reported in 'paseo provider ls'")
    else:
        print("[Paseo CLI] - paseo command not installed in PATH")

    print("=" * 60)
    return 0


def cmd_setup(args):
    """End-to-end setup: install -> auth -> configure -> status."""
    print(">>> Step 1: Checking binary...")
    ret = cmd_install(args)
    if ret != 0:
        return ret

    print("\n>>> Step 2: Checking authentication...")
    ret = cmd_auth(args)
    if ret != 0:
        return ret

    print("\n>>> Step 3: Configuring Paseo...")
    ret = cmd_config_paseo(args)
    if ret != 0:
        return ret

    print("\n>>> Step 4: Verifying setup...")
    return cmd_status(args)


def main():
    parser = argparse.ArgumentParser(
        description="Google Antigravity ACP & Paseo Integration CLI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # setup
    p_setup = subparsers.add_parser("setup", help="Run full setup (install, auth, config)")
    p_setup.add_argument("--force", action="store_true", help="Force re-download and re-auth")
    p_setup.add_argument("--use-uid", action="store_true", help="Add --uid= argument to command in Paseo config")
    p_setup.set_defaults(func=cmd_setup)

    # install
    p_inst = subparsers.add_parser("install", help="Download agy_acp_server if missing")
    p_inst.add_argument("--force", action="store_true", help="Force re-download")
    p_inst.set_defaults(func=cmd_install)

    # auth
    p_auth = subparsers.add_parser("auth", help="Perform JSON-RPC OAuth authentication")
    p_auth.add_argument("--force", action="store_true", help="Force re-authentication")
    p_auth.set_defaults(func=cmd_auth)

    # config
    p_cfg = subparsers.add_parser("config", help="Update ~/.paseo/config.json with antigravity provider")
    p_cfg.add_argument("--use-uid", action="store_true", help="Add --uid= argument to command")
    p_cfg.set_defaults(func=cmd_config_paseo)

    # status
    p_stat = subparsers.add_parser("status", help="Check status of binary, auth, and Paseo")
    p_stat.set_defaults(func=cmd_status)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
