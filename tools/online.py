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


def find_cloudflared() -> str | None:
    for cand in (shutil.which("cloudflared"), ROOT / "tools" / "cloudflared.exe", ROOT / "tools" / "cloudflared",
                 Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "cloudflared" / "cloudflared.exe",
                 Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "cloudflared" / "cloudflared.exe"):
        if cand and Path(cand).exists():
            return str(cand)
    return None


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
    exe = find_cloudflared()
    if exe is None:
        print("\n  Cloudflare's free tunnel program (cloudflared) is not installed.")
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
        print("\n" + "=" * 64)
        print(f"  Your ERP is online at:  {url}")
        print("  Share the catalogue and salesperson links from inside the ERP;")
        print("  they now use this address. Keep this window open.")
        print("  Close it (or press Ctrl+C) to go offline again.")
        print("=" * 64 + "\n")
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
