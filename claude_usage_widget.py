"""
Claude Usage Widget — a small, auditable Windows 10/11 system-tray app
that shows your Claude Code usage (5-hour session % and weekly %).

SECURITY DESIGN (read this — it's short):
  * Reads your existing Claude Code login token from
    %USERPROFILE%\\.claude\\.credentials.json  (READ-ONLY, never modified)
  * The token is kept in memory only. It is NEVER logged, printed,
    written to disk, or sent anywhere except https://api.anthropic.com
  * Exactly ONE network destination: api.anthropic.com (HTTPS, certificate
    verified by Python's default SSL context)
  * No admin rights, no registry writes, no auto-update, no telemetry
  * Uses only the Python standard library + pystray + Pillow (both are
    widely-used, mainstream open-source packages)

REQUIREMENTS:
  * Windows 10 or 11
  * Claude Code installed and logged in (run `claude` once and /login)
  * Python 3.10+  ->  pip install pystray pillow

RUN:
  pythonw claude_usage_widget.py     (pythonw = no console window)

TRAY:
  * Icon shows your WEEKLY usage % (ring + number), colour-coded:
      green < 70%, orange 70-90%, red > 90%
  * Hover for a tooltip with session + weekly details and reset times
  * Right-click: Refresh now / Show session % instead / Exit
"""

import json
import os
import ssl
import sys
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("Missing dependencies. Run:  pip install pystray pillow")

# ---------------------------------------------------------------- constants

API_URL = "https://api.anthropic.com/api/oauth/usage"
POLL_SECONDS = 300          # 5 min — the endpoint rate-limits aggressive polling
ERROR_RETRY_SECONDS = 600   # back off harder after an error / 429
WARN_AT = 70                # orange threshold (%)
CRIT_AT = 90                # red threshold (%)
ALERT_AT = 85               # weekly % that triggers a Windows notification
ALERT_CLEAR_AT = 80         # re-arm the notification once weekly drops below this
MUTEX_NAME = "Local\\ClaudeUsageWidget.SingleInstance"

CRED_PATHS = [
    # Allow an explicit override, e.g. for a WSL-mounted path:
    os.environ.get("CLAUDE_CREDENTIALS_PATH", ""),
    os.path.join(os.path.expanduser("~"), ".claude", ".credentials.json"),
]

# ---------------------------------------------------------------- data layer


def read_token() -> str | None:
    """Read the OAuth access token from Claude Code's local credentials file.
    Read-only. Returns None if not found."""
    for path in CRED_PATHS:
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            oauth = data.get("claudeAiOauth") or data
            token = oauth.get("accessToken")
            if token:
                return token
        except (OSError, json.JSONDecodeError, AttributeError):
            continue
    return None


def fetch_usage(token: str) -> dict:
    """Call Anthropic's usage endpoint. Raises on error."""
    req = urllib.request.Request(
        API_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "Content-Type": "application/json",
            # Identifying as claude-code avoids the stricter anonymous
            # rate-limit bucket on this endpoint.
            "User-Agent": "claude-code/2.0 (usage-widget)",
        },
        method="GET",
    )
    ctx = ssl.create_default_context()  # full certificate verification
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_usage(data: dict) -> dict:
    """Normalise the API response into {label: (percent, resets_at_iso)}.

    Handles both the newer structured `limits` array and the older flat
    five_hour / seven_day keys.
    """
    out: dict[str, tuple[float, str | None]] = {}

    limits = data.get("limits")
    if isinstance(limits, list) and limits:
        for entry in limits:
            kind = entry.get("kind", "")
            pct = entry.get("percent")
            if pct is None:
                pct = entry.get("utilization")
            resets = entry.get("resets_at")
            if pct is None:
                continue
            if kind == "session":
                out["Session"] = (float(pct), resets)
            elif kind == "weekly_all":
                out["Weekly"] = (float(pct), resets)
            elif kind == "weekly_scoped":
                name = (
                    entry.get("scope", {}).get("model", {}).get("display_name")
                    or "Model"
                )
                out[f"Weekly ({name})"] = (float(pct), resets)

    def flat(key: str, label: str) -> None:
        block = data.get(key)
        if isinstance(block, dict) and block.get("utilization") is not None:
            out.setdefault(label, (float(block["utilization"]),
                                   block.get("resets_at")))

    flat("five_hour", "Session")
    flat("seven_day", "Weekly")
    flat("seven_day_sonnet", "Weekly (Sonnet)")
    flat("seven_day_opus", "Weekly (Opus)")
    return out


def fmt_reset(iso: str | None) -> str:
    """'resets in 2d 3h' style countdown from an ISO timestamp."""
    if not iso:
        return ""
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        delta = ts - datetime.now(timezone.utc)
        secs = int(delta.total_seconds())
        if secs <= 0:
            return "resets soon"
        d, rem = divmod(secs, 86400)
        h, rem = divmod(rem, 3600)
        m = rem // 60
        if d:
            return f"resets in {d}d {h}h"
        if h:
            return f"resets in {h}h {m}m"
        return f"resets in {m}m"
    except ValueError:
        return ""

# ---------------------------------------------------------------- icon layer


def make_icon(percent: float | None, error: bool = False) -> Image.Image:
    """Render a 64x64 tray icon: dark disc, colour-tinted face, progress
    ring with rounded caps, and the percentage in the centre."""
    import math

    size = 256  # draw at 4x then downscale for smooth curves
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if error or percent is None:
        ring = (150, 150, 158, 255)
        text = (210, 210, 215, 255)
        label = "?"
    else:
        if percent >= CRIT_AT:
            ring = (240, 82, 82, 255)      # red
        elif percent >= WARN_AT:
            ring = (250, 165, 50, 255)     # amber
        else:
            ring = (66, 200, 130, 255)     # green
        text = (255, 255, 255, 255)
        label = f"{round(percent)}"

    c = size / 2
    w = 26                          # ring thickness
    arc_box = [10, 10, size - 10, size - 10]   # outer edge of the ring
    rc = (size - 20) / 2 - w / 2    # ring centreline radius (arcs draw inward)

    # dark disc, faintly tinted toward the state colour so the icon reads
    # green/amber/red at a glance even before the number is legible
    face = tuple(int(30 + ch * 0.12) for ch in ring[:3]) + (255,)
    d.ellipse([6, 6, size - 6, size - 6], fill=face)

    # track ring
    d.arc(arc_box, start=0, end=360, fill=(255, 255, 255, 36), width=w)

    # progress arc with rounded caps
    if percent is not None and not error:
        sweep = max(0.0, min(100.0, percent)) * 3.6
        if sweep > 0:
            d.arc(arc_box, start=-90, end=-90 + sweep, fill=ring, width=w)
            for ang in (-90, -90 + sweep):     # rounded end caps
                rad = math.radians(ang)
                cx = c + rc * math.cos(rad)
                cy = c + rc * math.sin(rad)
                d.ellipse([cx - w / 2, cy - w / 2, cx + w / 2, cy + w / 2],
                          fill=ring)

    # centred number
    try:
        font = ImageFont.truetype(
            "segoeuib.ttf", 118 if len(label) < 3 else 92)
    except OSError:
        font = ImageFont.load_default()
    bbox = d.textbbox((0, 0), label, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]),
           label, font=font, fill=text)

    return img.resize((64, 64), Image.LANCZOS)

# ---------------------------------------------------------------- app


class UsageApp:
    def __init__(self) -> None:
        self.metric = "Weekly"          # which % the icon shows
        self.usage: dict = {}
        self.error: str | None = None
        self.alerted = False            # True after the 85% toast fired
        self.stop_event = threading.Event()
        self.icon = pystray.Icon(
            "ClaudeUsage",
            make_icon(None),
            "Claude usage — loading…",
            menu=pystray.Menu(
                pystray.MenuItem("Refresh now", self.on_refresh),
                pystray.MenuItem(
                    "Icon shows",
                    pystray.Menu(
                        pystray.MenuItem(
                            "Weekly %", self.set_weekly, radio=True,
                            checked=lambda _: self.metric == "Weekly"),
                        pystray.MenuItem(
                            "Session %", self.set_session, radio=True,
                            checked=lambda _: self.metric == "Session"),
                    ),
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", self.on_exit),
            ),
        )

    # ---- menu handlers
    def on_refresh(self, *_):
        threading.Thread(target=self.refresh, daemon=True).start()

    def set_weekly(self, *_):
        self.metric = "Weekly"
        self.render()

    def set_session(self, *_):
        self.metric = "Session"
        self.render()

    def on_exit(self, *_):
        self.stop_event.set()
        self.icon.stop()

    # ---- core loop
    def refresh(self) -> None:
        token = read_token()
        if not token:
            self.error = ("Claude Code login not found.\n"
                          "Open a terminal, run `claude`, then /login.")
            self.render()
            return
        try:
            self.usage = parse_usage(fetch_usage(token))
            self.error = None
        except urllib.error.HTTPError as e:
            if e.code == 401:
                self.error = ("Token expired — open Claude Code once "
                              "to refresh your login.")
            elif e.code == 429:
                self.error = "Rate limited — will retry later."
            else:
                self.error = f"API error {e.code}"
        except (urllib.error.URLError, TimeoutError, OSError):
            self.error = "Network unreachable — showing last values."
        except (json.JSONDecodeError, ValueError):
            self.error = "Unexpected API response."
        self.check_alert()
        self.render()

    def check_alert(self) -> None:
        """Fire one Windows notification when weekly usage crosses ALERT_AT;
        re-arm after it falls back below ALERT_CLEAR_AT (hysteresis so a
        value hovering around the threshold doesn't spam toasts)."""
        weekly, resets = self.usage.get("Weekly", (None, None))
        if weekly is None:
            return
        if weekly >= ALERT_AT and not self.alerted:
            self.alerted = True
            extra = fmt_reset(resets)
            try:
                self.icon.notify(
                    f"Weekly usage at {weekly:.0f}%"
                    + (f" — {extra}" if extra else ""),
                    "Claude usage warning",
                )
            except Exception:
                pass  # notification is best-effort; never crash the widget
        elif weekly < ALERT_CLEAR_AT:
            self.alerted = False

    def render(self) -> None:
        pct = self.usage.get(self.metric, (None, None))[0]
        if pct is None and self.usage:
            # Selected metric missing from this response — show the first
            # available one rather than a blank icon.
            pct = next(iter(self.usage.values()))[0]
        hard_error = self.error is not None and not self.usage
        self.icon.icon = make_icon(pct, error=hard_error)

        lines = []
        for label, (p, resets) in self.usage.items():
            extra = fmt_reset(resets)
            lines.append(f"{label}: {p:.0f}%" + (f" · {extra}" if extra else ""))
        if self.error:
            lines.append(self.error.splitlines()[0])
        tip = "Claude usage\n" + "\n".join(lines) if lines else \
              (self.error or "Claude usage — loading…")
        self.icon.title = tip[:127]  # Windows tooltip length limit

    def poll_loop(self) -> None:
        while not self.stop_event.is_set():
            self.refresh()
            wait = ERROR_RETRY_SECONDS if self.error else POLL_SECONDS
            self.stop_event.wait(wait)

    def run(self) -> None:
        threading.Thread(target=self.poll_loop, daemon=True).start()
        self.icon.run()


def acquire_single_instance():
    """Hold a named Windows mutex for the life of the process so a second
    launch (e.g. startup shortcut + manual run) exits instead of adding a
    duplicate tray icon. Windows frees the mutex when the process ends,
    even on a crash. Returns the handle (keep it referenced), or None if
    another instance already holds it."""
    if sys.platform != "win32":
        return True
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return handle or True  # if the mutex can't be created, run anyway


if __name__ == "__main__":
    _instance_lock = acquire_single_instance()
    if _instance_lock is None:
        sys.exit(0)  # already running — the existing icon stays put
    UsageApp().run()
