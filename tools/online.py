"""Put this computer's ERP on the internet for a while, through a free Cloudflare tunnel.

    python tools/online.py            (online.bat / ./online.sh start it for you)

Phones anywhere (customers opening the catalogue, salespeople on the road) can then reach the ERP
at an https://….trycloudflare.com address. Nothing is opened on your router; the address changes
each time this starts and stops working when you close this window. For a fixed address and
daily use, put the ERP on a server with your own domain (docs/deployment.md).

While running, it writes backend/data/public_url.json every 30 seconds; the ERP reads it to show
the public link on the catalogue and salesperson-portal pages.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "backend" / "data" / "public_url.json"
LOCAL = os.environ.get("ERP_LOCAL_URL", "http://localhost:3000")
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


VERSION = "2026.10.1"  # pinned release of Cloudflare's tunnel program (official GitHub releases)
RELEASES = f"https://github.com/cloudflare/cloudflared/releases/download/{VERSION}/"
BIN = ROOT / "tools" / ("cloudflared.exe" if platform.system() == "Windows" else "cloudflared")


def find_cloudflared() -> str | None:
    for cand in (BIN, shutil.which("cloudflared"),
                 Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "cloudflared" / "cloudflared.exe",
                 Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "cloudflared" / "cloudflared.exe"):
        if cand and Path(cand).exists():
            return str(cand)
    return None


def _asset() -> str | None:
    system, machine = platform.system(), platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if system == "Windows":
        return "cloudflared-windows-amd64.exe" if machine in ("amd64", "x86_64", "arm64") else "cloudflared-windows-386.exe"
    if system == "Darwin":
        return f"cloudflared-darwin-{'arm64' if arm else 'amd64'}.tgz"
    if system == "Linux":
        return f"cloudflared-linux-{'arm64' if arm else 'amd64'}"
    return None


def download() -> str | None:
    """One-time download of cloudflared into the ERP's tools folder (no admin rights needed)."""
    import tarfile
    import urllib.request

    asset = _asset()
    if asset is None:
        return None
    print(f"Downloading Cloudflare's tunnel program ({VERSION}, about 40-60 MB, only this once)...")
    tmp = BIN.with_suffix(".download")
    try:
        with urllib.request.urlopen(RELEASES + asset, timeout=60) as r, tmp.open("wb") as f:
            total, done, step = int(r.headers.get("Content-Length") or 0), 0, 0
            while chunk := r.read(1 << 16):
                f.write(chunk)
                done += len(chunk)
                if total and done * 20 // total > step:
                    step = done * 20 // total
                    print(f"\r  [{'#' * step}{'.' * (20 - step)}] {done * 100 // total}%", end="", flush=True)
        print()
        if asset.endswith(".tgz"):
            with tarfile.open(tmp) as t:
                member = next(m for m in t.getmembers() if m.name.endswith("cloudflared") and m.isfile())
                src = t.extractfile(member)
                assert src is not None
                BIN.write_bytes(src.read())
            tmp.unlink()
        else:
            tmp.replace(BIN)
        BIN.chmod(0o755)
        subprocess.run([str(BIN), "--version"], check=True, capture_output=True, timeout=30)
        return str(BIN)
    except Exception as exc:  # noqa: BLE001 - explain and fall back to manual steps
        print(f"\n  Download did not work: {exc}")
        tmp.unlink(missing_ok=True)
        BIN.unlink(missing_ok=True)
        return None


def erp_running() -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"{LOCAL}/login", timeout=4) as r:
            return r.status < 500
    except Exception:  # noqa: BLE001
        return False


def ensure_erp() -> bool:
    if erp_running():
        return True
    if platform.system() == "Windows" and (ROOT / "start.bat").exists():
        print("The ERP is not running on this computer - starting it first (start.bat)...")
        subprocess.run(["cmd", "/c", str(ROOT / "start.bat")], cwd=ROOT)
    else:
        print("The ERP is not running. Start it (./start.sh) in another window; waiting for it...")
    for _ in range(120):
        if erp_running():
            return True
        time.sleep(2)
    print("The ERP did not start. Run start.bat, check it opens in the browser, then run online.bat again.")
    return False


def copy_to_clipboard(text: str) -> bool:
    cmd = ["clip"] if platform.system() == "Windows" else ["pbcopy"] if platform.system() == "Darwin" else None
    if not cmd:
        return False
    try:
        subprocess.run(cmd, input=text, text=True, check=True, timeout=5)
        return True
    except Exception:  # noqa: BLE001
        return False


def write(url: str, started: float) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"url": url, "started_at": started, "seen_at": time.time()}), encoding="utf-8")
    tmp.replace(STATE)


def _stop(*_: object) -> None:
    raise KeyboardInterrupt


def main() -> int:
    import signal

    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGBREAK", None)):
        if sig is not None:
            signal.signal(sig, _stop)
    if not ensure_erp():
        return 1
    exe = find_cloudflared() or download()
    if exe is None:
        print("\n  Cloudflare's free tunnel program (cloudflared) could not be set up automatically.")
        if platform.system() == "Windows":
            print("  Install it once with:   winget install --id Cloudflare.cloudflared")
            print("  (or download cloudflared-windows-amd64.exe from github.com/cloudflare/cloudflared/releases,")
            print("   rename it to cloudflared.exe and put it in the ERP's tools folder), then run online.bat again.")
        elif platform.system() == "Darwin":
            print("  Install it once with:   brew install cloudflared")
        else:
            print("  Install it from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/")
        return 1
    print(f"Connecting {LOCAL} to the internet (this takes a few seconds)...")
    proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", LOCAL], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    found: dict[str, str] = {}
    started = time.time()

    def read() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            m = URL_RE.search(line)
            if m and "url" not in found:
                found["url"] = m.group(0)

    threading.Thread(target=read, daemon=True).start()
    try:
        while "url" not in found:
            if proc.poll() is not None:
                print("The tunnel stopped before it was ready. Check the internet connection and try again.")
                return 1
            if time.time() - started > 60:
                print("No address from Cloudflare after a minute. Check the internet connection and try again.")
                proc.terminate()
                return 1
            time.sleep(0.5)
        url = found["url"]
        write(url, started)
        portal = f"{url}/sp"
        copied = copy_to_clipboard(portal)
        print("\n" + "=" * 70)
        print(f"  Your ERP is online at:      {url}")
        print(f"  Salesperson / MR portal:    {portal}" + ("   (copied - paste it in WhatsApp)" if copied else ""))
        print("  Catalogue: open WhatsApp catalogue in the ERP; its link and QR now use this address.")
        print()
        print("  If the link does not open on a phone yet, wait 30 seconds and try again.")
        print("  Keep this window open. Close it (or press Ctrl+C) to go offline.")
        print("  The address changes each time online.bat starts: send the link again after a restart.")
        print("=" * 70 + "\n")
        while proc.poll() is None:
            write(url, started)
            time.sleep(30)
        print("The tunnel stopped.")
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        if proc.poll() is None:
            proc.terminate()
        STATE.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
