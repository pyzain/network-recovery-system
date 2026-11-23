"""
run_script.py — Company Intelligent Network Recovery System
Graphical entry point: runs diagnostics, classification and remediation on a
background thread and shows progress in a radar-style window.

Build (admin-elevated, UPX disabled to avoid AV false positives):
    pyinstaller --onefile --noconsole --uac-admin --name Company_Network_Brain ^
      --noupx --collect-data customtkinter --hidden-import=customtkinter ^
      run_script.py

    See README.md → "Building the .exe Yourself" for the full walkthrough.

Usage:
    Company_Network_Brain.exe            # Employee mode
    Company_Network_Brain.exe --debug    # IT / admin debug mode
"""

import sys
import threading
import math
import random
import datetime
import time
import queue
import tkinter as tk
import customtkinter as ctk

import helpers
from helpers import set_debug, is_debug, is_admin
from diagnostics import run_all_diagnostics
from wifi_analysis import get_wifi_info
from classifier import classify, Fault
from remediation import remediate
from config import TIMESTAMP_FORMAT

import os
import traceback
import faulthandler


def _parse_args() -> bool:
    return "--debug" in sys.argv or "-d" in sys.argv


# Resolved once at startup; used to gate all crash-logging and faulthandler I/O.
_DEBUG_MODE: bool = _parse_args()

_BASE_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else __file__)

LOG_FILE  = os.path.join(_BASE_DIR, "debug_crash_log.txt")
FAULT_LOG = os.path.join(_BASE_DIR, "faulthandler.log")


def debug_log(msg: str) -> None:
    """Write a timestamped line to the crash log — only when running with --debug."""
    if not _DEBUG_MODE:
        return
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            ts = datetime.datetime.now().strftime("%H:%M:%S.%f")
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


# Enable low-level fault handler only in debug mode (sys.stderr is None in
# --noconsole PyInstaller builds, so we write to a file instead).
if _DEBUG_MODE:
    try:
        _fault_handle = open(FAULT_LOG, "a", buffering=1, encoding="utf-8")
        faulthandler.enable(file=_fault_handle, all_threads=True)
    except Exception:
        pass


# ---- THEME PALETTE ----

BG        = "#070d19"       # near-black navy
BG_PANEL  = "#0b1428"       # slightly lighter header/panels
BG_CARD   = "#0f1e38"       # cards / result area
BG_LOG    = "#060c17"       # darkest — log box
CYAN      = "#00c8f0"       # primary accent
GREEN     = "#00e87a"       # success / healthy
AMBER     = "#ffa826"       # warning / degraded
RED       = "#ff3b4e"       # error / critical
MUTED     = "#192d4a"       # inactive borders
MUTED2    = "#243d5f"       # slightly visible borders
TEXT      = "#a8c2e0"       # body text
TEXT_DIM  = "#334d6a"       # de-emphasised text
MONO      = "Consolas"
SANS      = "Segoe UI"

SEV_COLOR  = {"healthy": GREEN, "advisory": CYAN, "degraded": AMBER, "critical": RED}
SEV_LABEL  = {
    "healthy":  "✓   NETWORK HEALTHY",
    "advisory": "◈   ADVISORY",
    "degraded": "⚠   DEGRADED",
    "critical": "✕   CRITICAL",
}

# Pre-compute radar sweep gradient (background → CYAN) for the fan trail
_SWEEP_STEPS = 28
_SWEEP_COLORS: list[str] = []
_bg_r, _bg_g, _bg_b = 0x07, 0x0d, 0x19
_cy_r, _cy_g, _cy_b = 0x00, 0xc8, 0xf0
for _i in range(_SWEEP_STEPS):
    _t = (_i / _SWEEP_STEPS) * 0.52
    _SWEEP_COLORS.append(
        "#{:02x}{:02x}{:02x}".format(
            int(_bg_r + (_cy_r - _bg_r) * _t),
            int(_bg_g + (_cy_g - _bg_g) * _t),
            int(_bg_b + (_cy_b - _bg_b) * _t),
        )
    )


def _blend(a: str, b: str, t: float) -> str:
    """Linear blend between two hex colours."""
    ah, bh = a.lstrip("#"), b.lstrip("#")
    ar, ag, ab_ = int(ah[0:2], 16), int(ah[2:4], 16), int(ah[4:6], 16)
    br, bg, bb  = int(bh[0:2], 16), int(bh[2:4], 16), int(bh[4:6], 16)
    return "#{:02x}{:02x}{:02x}".format(
        int(ar + (br - ar) * t), int(ag + (bg - ag) * t), int(ab_ + (bb - ab_) * t)
    )


# ---- RADAR ANIMATION WIDGET ----

class RadarWidget(tk.Canvas):
    """
    Self-animating radar canvas.
    Animates only while a scan is running.
    """

    SZ = 224       # canvas side
    R  = 98        # outer ring radius

    def __init__(self, parent: tk.Widget):
        cx = cy = self.SZ // 2
        super().__init__(
            parent,
            width=self.SZ, height=self.SZ,
            bg=BG, highlightthickness=0, bd=0,
        )
        self._cx      = cx
        self._cy      = cy
        self._angle   = 0.0        # sweep arm angle in degrees
        self._pulse   = 0.0        # phase for the inner pulsing ring
        self._pts: list[list] = []  # [x, y, ttl, max_ttl]
        self._mode    = "idle"    # idle | scan | done
        self._success = True
        self._after_id = None

        # Pre-compute hex dot grid positions (drawn once, stored for speed)
        self._hex_dots: list[tuple[int, int]] = []
        step = 20
        for row in range(-self.R + step, self.R, step):
            for col in range(-self.R + step, self.R, step):
                if col * col + row * row < (self.R - 6) ** 2:
                    self._hex_dots.append((cx + col, cy + row))

        self._draw()   # Perform initial static render

    # Public API

    def start_scan(self) -> None:
        self.cancel()
        self._mode = "scan"
        self._pts.clear()
        self._frame()

    def finish_scan(self, success: bool = True) -> None:
        self.cancel()
        self._mode    = "done"
        self._success = success
        self._pts.clear()
        self._draw()

    def cancel(self) -> None:
        """Explicitly pulls down scheduled animation frames."""
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    # Internal drawing

    def _add_blip(self) -> None:
        a    = random.uniform(0, 2 * math.pi)
        dist = random.uniform(10, self.R - 10)
        x    = self._cx + dist * math.cos(a)
        y    = self._cy + dist * math.sin(a)
        ttl  = random.randint(14, 44)
        self._pts.append([x, y, ttl, ttl])

    def _draw(self) -> None:
        self.delete("all")
        cx, cy, r = self._cx, self._cy, self.R

        # Solid background
        self.create_rectangle(0, 0, self.SZ, self.SZ, fill=BG, outline="")

        # Hex-grid dot field
        for (dx, dy) in self._hex_dots:
            self.create_oval(dx - 1, dy - 1, dx + 1, dy + 1, fill=MUTED, outline="")

        # Concentric range rings
        for ratio in (0.33, 0.66, 1.0):
            ri = int(r * ratio)
            self.create_oval(cx - ri, cy - ri, cx + ri, cy + ri, outline=MUTED, width=1)

        # Cross-hair lines
        self.create_line(cx, cy - r - 6, cx, cy + r + 6, fill=MUTED, width=1)
        self.create_line(cx - r - 6, cy, cx + r + 6, cy, fill=MUTED, width=1)

        # Determine accent colour for this mode
        if self._mode == "done":
            accent = GREEN if self._success else RED
        else:
            accent = CYAN

        # Outer ring (brighter when done)
        ring_w = 3 if self._mode == "done" else 2
        self.create_oval(cx - r, cy - r, cx + r, cy + r, outline=accent, width=ring_w)

        if self._mode == "scan":
            # Sweep fan trail
            for idx, color in enumerate(_SWEEP_COLORS):
                a = math.radians(self._angle - idx * 1.4)
                xe = cx + r * math.cos(a)
                ye = cy + r * math.sin(a)
                self.create_line(cx, cy, xe, ye, fill=color, width=2)

            # Active sweep arm
            arm_a = math.radians(self._angle)
            self.create_line(
                cx, cy,
                cx + r * math.cos(arm_a),
                cy + r * math.sin(arm_a),
                fill=CYAN, width=2,
            )

            # Pulsing inner ring
            pr = r * (0.35 + 0.28 * abs(math.sin(self._pulse)))
            pc = _blend(BG, CYAN, 0.16)
            self.create_oval(cx - pr, cy - pr, cx + pr, cy + pr, outline=pc, width=1)

            # Blip particles
            for x, y, ttl, max_ttl in self._pts:
                t  = ttl / max_ttl
                c  = _blend(BG, CYAN, t * 0.88)
                s  = 2.8 * t
                self.create_oval(x - s, y - s, x + s, y + s, fill=c, outline="")

        elif self._mode == "done":
            # Large result symbol
            sym = "✓" if self._success else "✕"
            # Subtle glow behind the symbol
            self.create_text(cx + 1, cy + 1, text=sym, font=(MONO, 40, "bold"),
                             fill=_blend(BG, accent, 0.4))
            self.create_text(cx, cy, text=sym, font=(MONO, 40, "bold"), fill=accent)

        else:
            # Idle state: dim centre dot only
            self.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=MUTED2, outline="")

    def _frame(self) -> None:
        if not self.winfo_exists():
            return
        if self._mode == "scan":
            self._angle = (self._angle + 2.6) % 360
            self._pulse += 0.068
            if random.random() < 0.32:
                self._add_blip()
            self._pts = [
                [x, y, t - 1, m] for x, y, t, m in self._pts if t > 1
            ]
            self._draw()
            
            if self.winfo_exists():
                self._after_id = self.after(30, self._frame)


# ---- SEGMENTED PROGRESS BAR  (pure tkinter canvas) ----

class ProgressBar(tk.Canvas):
    def __init__(self, parent: tk.Widget, width: int = 440, height: int = 20):
        super().__init__(parent, width=width, height=height,
                         bg=BG_CARD, highlightthickness=0, bd=0)
        self._width = width
        self._height = height
        self._pct = 0.0
        self._redraw()

    def set(self, pct: float) -> None:
        self._pct = max(0.0, min(1.0, pct))
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        w, h = self._width, self._height
        # Background track
        self.create_rectangle(0, 0, w, h, fill=BG_CARD, outline="")
        fill_w = int(w * self._pct)
        if fill_w > 2:
            self.create_rectangle(0, 0, fill_w, h, fill=CYAN, outline="")
            # Segment gutters
            seg = 10
            for x in range(seg, fill_w, seg):
                self.create_line(x, 0, x, h, fill=BG_CARD, width=2)
            # Leading-edge highlight
            self.create_line(fill_w - 1, 0, fill_w - 1, h, fill="white", width=1)
        # Border
        self.create_rectangle(0, 0, w - 1, h - 1, outline=MUTED2, width=1)


# ---- MAIN APPLICATION WINDOW ----

class App(ctk.CTk):

    # Window sizes
    _W       = 760
    _H_NORM  = 622
    _H_DEBUG = 960

    def _tk_callback_error(self, exc, val, tb):
        debug_log("TKINTER CALLBACK CRASH")
        debug_log(str(val))
        debug_log("".join(traceback.format_exception(exc, val, tb)))

    def __init__(self, debug: bool):
        super().__init__()
        self.report_callback_exception = self._tk_callback_error
        self.debug = debug
        set_debug(debug)

        # Timer safe-handling registry
        self._after_ids = {}
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Thread-safe UI update queue
        self._ui_queue = queue.Queue()
        self._closing = False

        # Window chrome
        H = self._H_DEBUG if debug else self._H_NORM
        self.title("Network Recovery System")
        self.configure(fg_color=BG)
        self.resizable(False, False)
        self.update_idletasks()
        sx = self.winfo_screenwidth()
        sy = self.winfo_screenheight()
        self.geometry(f"{self._W}x{H}+{(sx - self._W)//2}+{(sy - H)//2}")

        # Internal state
        self._blink_on = True

        # Build and safely execute
        self._build_ui()
        self.after_idle(self._start_timers)

    def _start_timers(self) -> None:
        """Kicks off the event scheduling loop safely once layout engine settles."""
        self._blink()
        self._poll_ui_queue()
        self._after_ids["analysis"] = self.after(480, self._run_analysis)

    def _poll_ui_queue(self) -> None:
        """Safely processes background UI updates in the main thread event loop."""
        if not self.winfo_exists():
            return
        try:
            for _ in range(20):
                fn = self._ui_queue.get_nowait()

                try:
                    try:
                        fn()
                    except Exception as e:
                        debug_log("QUEUE CALLBACK ERROR:")
                        debug_log(str(e))
                        debug_log(traceback.format_exc())
                except tk.TclError:
                    pass
                except Exception:
                    pass

        except queue.Empty:
            pass
        finally:
            if self.winfo_exists():
                self._after_ids["poll"] = self.after(30, self._poll_ui_queue)

    def _on_close(self) -> None:
        """Intercepts window termination to clean up outstanding Tcl callbacks."""
        self._closing = True
        if hasattr(self, "radar"):
            self.radar.cancel()
        for aid in list(self._after_ids.values()):
            if aid:
                try:
                    self.after_cancel(aid)
                except tk.TclError:
                    pass
        self._after_ids.clear()
        try:
            self.quit()
        except Exception:
            pass
        self.destroy()


    # ---- UI CONSTRUCTION ----

    def _build_ui(self) -> None:
        # Header bar
        hdr = tk.Frame(self, bg=BG_PANEL, height=68)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        lft = tk.Frame(hdr, bg=BG_PANEL)
        lft.pack(side="left", padx=22)

        # Hexagon glyph as icon
        tk.Label(lft, text="⬡", font=(SANS, 28), fg=CYAN, bg=BG_PANEL).pack(
            side="left", padx=(0, 10)
        )
        meta = tk.Frame(lft, bg=BG_PANEL)
        meta.pack(side="left", pady=14)
        tk.Label(meta, text="NETWORK RECOVERY SYSTEM",
                 font=(SANS, 14, "bold"), fg=CYAN, bg=BG_PANEL).pack(anchor="w")
        tk.Label(meta, text="Intelligent Diagnostics & Remediation  •  v1.0",
                 font=(SANS, 8), fg=TEXT_DIM, bg=BG_PANEL).pack(anchor="w")

        rgt = tk.Frame(hdr, bg=BG_PANEL)
        rgt.pack(side="right", padx=22)
        now = datetime.datetime.now().strftime("%Y-%m-%d   %H:%M")
        tk.Label(rgt, text=now, font=(MONO, 8), fg=TEXT_DIM, bg=BG_PANEL).pack(anchor="e")
        priv_txt = "● ADMINISTRATOR" if is_admin() else "● STANDARD USER"
        priv_col = GREEN if is_admin() else AMBER
        tk.Label(rgt, text=priv_txt, font=(MONO, 8, "bold"), fg=priv_col, bg=BG_PANEL).pack(
            anchor="e"
        )

        # Single-pixel divider under header
        tk.Frame(self, bg=MUTED, height=1).pack(fill="x")

        # Body
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=26, pady=20)

        # Top row: radar  |  status panel
        top_row = tk.Frame(body, bg=BG)
        top_row.pack(fill="x")

        self.radar = RadarWidget(top_row)
        self.radar.pack(side="left", anchor="n")

        # Status panel (right of radar)
        sp = tk.Frame(top_row, bg=BG)
        sp.pack(side="left", fill="both", expand=True, padx=(22, 0), anchor="n")

        # Phase pill
        pill = tk.Frame(sp, bg=BG_CARD, padx=14, pady=5)
        pill.pack(anchor="w", pady=(2, 0))
        self._phase_var = tk.StringVar(value="INITIALIZING")
        self._phase_lbl = tk.Label(
            pill, textvariable=self._phase_var,
            font=(MONO, 9, "bold"), fg=CYAN, bg=BG_CARD,
        )
        self._phase_lbl.pack()

        # Status line
        self._status_var = tk.StringVar(value="Starting up…")
        self._status_lbl = tk.Label(
            sp, textvariable=self._status_var,
            font=(MONO, 10), fg=TEXT, bg=BG,
            wraplength=450, justify="left", anchor="w",
        )
        self._status_lbl.pack(anchor="w", pady=(10, 0), fill="x")

        # Blinking cursor
        cur_row = tk.Frame(sp, bg=BG)
        cur_row.pack(anchor="w", pady=(2, 0))
        self._cursor = tk.Label(cur_row, text="█", font=(MONO, 10), fg=CYAN, bg=BG)
        self._cursor.pack(side="left")

        tk.Frame(sp, bg=BG, height=14).pack()

        # Progress bar + label
        tk.Label(sp, text="PROGRESS", font=(MONO, 7, "bold"), fg=TEXT_DIM, bg=BG).pack(
            anchor="w"
        )
        self._prog_bar = ProgressBar(sp, width=450, height=20)
        self._prog_bar.pack(anchor="w", pady=(4, 2))
        self._prog_pct = tk.Label(sp, text="0 %", font=(MONO, 8, "bold"), fg=CYAN, bg=BG)
        self._prog_pct.pack(anchor="e")

        # Divider + action log
        tk.Frame(body, bg=MUTED, height=1).pack(fill="x", pady=(18, 0))

        log_hdr = tk.Frame(body, bg=BG)
        log_hdr.pack(fill="x", pady=(7, 4))
        tk.Label(log_hdr, text="ACTION LOG", font=(MONO, 7, "bold"), fg=TEXT_DIM, bg=BG).pack(
            side="left"
        )

        log_box = tk.Frame(body, bg=BG_LOG)
        log_box.pack(fill="x")

        self._log = tk.Text(
            log_box, height=6, font=(MONO, 8),
            bg=BG_LOG, fg=TEXT_DIM, bd=0, relief="flat",
            state="disabled", wrap="word",
            padx=10, pady=8,
            insertbackground=CYAN,
            selectbackground=MUTED2,
        )
        self._log.pack(fill="x")

        for tag, col in [
            ("ok",   GREEN), ("fail",  RED),  ("info", CYAN),
            ("warn", AMBER), ("dim",   TEXT_DIM), ("txt",  TEXT),
        ]:
            self._log.tag_config(tag, foreground=col)

        # Result card (packed dynamically when analysis completes)
        self._res_card = tk.Frame(body, bg=BG_CARD)

        top_stripe = tk.Frame(self._res_card, bg=MUTED, height=1)
        top_stripe.pack(fill="x")

        self._res_sev = tk.Label(
            self._res_card, text="",
            font=(MONO, 13, "bold"), fg=GREEN, bg=BG_CARD,
            padx=18, pady=9,
        )
        self._res_sev.pack(anchor="w")

        self._res_msg = tk.Label(
            self._res_card,
            text="",
            font=(SANS, 9),
            fg=TEXT,
            bg=BG_CARD,
            wraplength=700,
            justify="left",
            padx=18,
            pady=12,
        )

        self._res_msg.pack(anchor="w")

        # Debug panel (only constructed in debug mode)
        if self.debug:
            tk.Frame(body, bg=MUTED, height=1).pack(fill="x", pady=(16, 0))
            tk.Label(
                body,
                text="DIAGNOSTIC DUMP   [ --debug mode ]",
                font=(MONO, 7, "bold"), fg=TEXT_DIM, bg=BG,
            ).pack(anchor="w", pady=(7, 4))

            db_outer = tk.Frame(body, bg=BG_LOG)
            db_outer.pack(fill="both", expand=True)

            db_scroll = tk.Scrollbar(db_outer, bg=BG_PANEL, troughcolor=BG_LOG,
                                     activebackground=MUTED2)
            db_scroll.pack(side="right", fill="y")

            self._dbtext = tk.Text(
                db_outer, height=14, font=(MONO, 8),
                bg=BG_LOG, fg=TEXT, bd=0, relief="flat",
                state="disabled", wrap="none",
                padx=10, pady=8,
                yscrollcommand=db_scroll.set,
            )
            self._dbtext.pack(side="left", fill="both", expand=True)
            db_scroll.config(command=self._dbtext.yview)

            for tag, col in [
                ("cy", CYAN), ("gr", GREEN), ("rd", RED),
                ("am", AMBER), ("dm", TEXT_DIM),
            ]:
                self._dbtext.tag_config(tag, foreground=col)

    # ---- UI HELPERS (all must be called from the main thread) ----

    def _upd_phase(self, phase: str, status: str = "") -> None:
        self._phase_var.set(phase)
        if status:
            self._status_var.set(status)

    def _upd_prog(self, pct: float) -> None:
        self._prog_bar.set(pct)
        self._prog_pct.configure(text=f"{int(pct * 100)} %")

    def _log_line(self, msg: str, tag: str = "dim") -> None:
        self._log.config(state="normal")
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._log.insert("end", f"[{ts}]  ", "dim")
        self._log.insert("end", msg + "\n", tag)
        self._log.see("end")
        self._log.config(state="disabled")

    def _db_line(self, msg: str = "", tag: str = "") -> None:
        if not self.debug:
            return
        self._dbtext.config(state="normal")
        if tag:
            self._dbtext.insert("end", msg + "\n", tag)
        else:
            self._dbtext.insert("end", msg + "\n")
        self._dbtext.see("end")
        self._dbtext.config(state="disabled")

    def _blink(self) -> None:
        if not self.winfo_exists():
            return

        self._blink_on = not self._blink_on
        try:
            self._cursor.configure(fg=CYAN if self._blink_on else BG)
        except tk.TclError:
            return

        if self.winfo_exists():
            self._after_ids["blink"] = self.after(520, self._blink)

    # ---- BACKGROUND ANALYSIS ----

    def _run_analysis(self) -> None:
        self.radar.start_scan()
        self._upd_phase("SCANNING", "Initializing diagnostic suite…")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        """
        Heavy lifting in a background thread.
        All GUI updates are dispatched to the main thread via self._ui_queue.
        """
        def ui(fn):
            # queue.put is thread-safe; the main thread drains it in
            # _poll_ui_queue. No Tk call may happen on this thread.
            if not self._closing:
                self._ui_queue.put(fn)

        try:
            # Phase 1: Diagnostics
            ui(lambda: self._upd_phase("SCANNING", "Running network diagnostic suite…"))
            ui(lambda: self._log_line("Starting full network diagnostic suite", "info"))
            ui(lambda: self._upd_prog(0.08))

            report = run_all_diagnostics()

            ui(lambda: self._upd_prog(0.44))
            ui(lambda: self._log_line(
                "Loopback (TCP/IP stack) ......... " +
                ("OK" if report.loopback_ok else "FAIL"),
                "ok" if report.loopback_ok else "fail",
            ))

            if report.adapter:
                a = report.adapter
                ui(lambda: self._log_line(
                    f"Adapter: {a.name}  [{a.media_type}]  {a.ip_address}", "txt"
                ))
                gw = a.gateway or "NOT FOUND"
                dns = ", ".join(a.dns_servers[:2]) or "N/A"
                ui(lambda: self._log_line(f"Gateway: {gw}   DNS: {dns}", "dim"))

            if report.gateway_ping:
                gp = report.gateway_ping
                ui(lambda: self._log_line(
                    f"Gateway ping ................... " +
                    ("OK" if gp.reachable else "FAIL") +
                    f"  loss={gp.loss_pct:.0f}%  avg={gp.avg_ms:.0f}ms",
                    "ok" if gp.reachable else "fail",
                ))

            if report.internet_ping:
                ip = report.internet_ping
                itag = (
                    "ok"   if ip.reachable and ip.loss_pct < 20 else
                    "warn" if ip.reachable else
                    "fail"
                )
                ui(lambda: self._log_line(
                    f"Internet ping (1.1.1.1) ........ " +
                    ("OK" if ip.reachable else "FAIL") +
                    f"  loss={ip.loss_pct:.0f}%  avg={ip.avg_ms:.0f}ms",
                    itag,
                ))

            ui(lambda: self._log_line(
                f"DNS resolution ................. " +
                ("OK" if report.dns_ok else "FAIL") +
                f"  ({report.dns_latency_ms:.0f}ms)",
                "ok" if report.dns_ok else "fail",
            ))

            # Phase 2: Wi-Fi
            ui(lambda: self._upd_phase("SCANNING", "Analyzing wireless interface…"))
            ui(lambda: self._upd_prog(0.56))

            wifi = get_wifi_info()

            if wifi and wifi.is_connected:
                ui(lambda: self._log_line(
                    f"Wi-Fi: {wifi.ssid}  {wifi.band}  Ch.{wifi.channel}  "
                    f"Signal: {wifi.signal_pct}%  Tx: {wifi.transmit_rate_mbps} Mbps",
                    "ok" if not wifi.is_weak_signal else "warn",
                ))
            elif wifi:
                ui(lambda: self._log_line("Wi-Fi adapter detected but not connected", "warn"))
            else:
                ui(lambda: self._log_line("No Wi-Fi interface (Ethernet or N/A)", "dim"))

            # Phase 3: Classification
            ui(lambda: self._upd_phase("CLASSIFYING", "Identifying fault pattern…"))
            ui(lambda: self._upd_prog(0.70))

            cls = classify(report, wifi)

            sev_to_tag = {
                "healthy": "ok", "advisory": "info",
                "degraded": "warn", "critical": "fail",
            }
            ui(lambda: self._log_line(
                f"Classification: {cls.fault}  [{cls.severity.upper()}]",
                sev_to_tag.get(cls.severity, "dim"),
            ))

            # Phase 4: Remediation
            ui(lambda: self._upd_phase("REMEDIATING", "Applying optimizations and fixes…"))
            ui(lambda: self._upd_prog(0.82))

            result = remediate(cls)

            for action in result.actions_attempted:
                ok  = action in result.actions_succeeded
                a_  = action    # capture for lambda closure
                ok_ = ok
                ui(lambda a_=a_, ok_=ok_: self._log_line(
                    f"{'✓' if ok_ else '✗'}  {a_}",
                    "ok" if ok_ else "warn",
                ))

            ui(lambda: self._upd_prog(1.0))

            if self._closing:
                return

            time.sleep(0.35)

            if self._closing:
                return
            ui(lambda: self._show_results(report, wifi, cls, result))

        except Exception as exc:
            err = str(exc)
            debug_log("WORKER ERROR:\n" + traceback.format_exc())
            ui(lambda: self._upd_phase("ERROR", f"Unexpected error: {err}"))
            ui(lambda: self._log_line(f"Fatal error: {err}", "fail"))
            ui(lambda: self.radar.finish_scan(success=False))

    # ---- RESULTS DISPLAY ----

    def _show_results(self, report, wifi, cls, result) -> None:
        # Finalise animated elements
        self._upd_phase("COMPLETE", "Analysis and remediation complete.")
        ok = cls.severity in ("healthy", "advisory")
        self.radar.finish_scan(success=ok)

        # Populate and reveal result card
        color = SEV_COLOR.get(cls.severity, CYAN)
        label = SEV_LABEL.get(cls.severity, cls.severity.upper())

        self._res_sev.configure(text=label, fg=color)
        self._res_msg.configure(text=result.user_message.replace("\n", "   "))
        self._res_card.pack(fill="x", pady=(14, 0))

        if result.reboot_recommended:
            reboot_note = tk.Label(
                self._res_card,
                text="⚠  Please save your work and restart your computer.",
                font=(MONO, 8, "bold"), fg=AMBER, bg=BG_CARD,
                padx=18,
            )
            reboot_note.pack(anchor="w", pady=(0, 10))

        # Populate debug dump if applicable
        if self.debug:
            self._populate_debug(report, wifi, cls, result)

    # ---- DEBUG DUMP ----

    def _populate_debug(self, report, wifi, cls, result) -> None:
        L = self._db_line
        HR = "─" * 62

        # Adapter
        L(HR, "dm");  L("  ADAPTER INFORMATION", "cy");  L(HR, "dm")
        if report.adapter:
            a = report.adapter
            L(f"  Name        : {a.name}")
            L(f"  Type        : {a.media_type}")
            L(f"  IP Address  : {a.ip_address}" + ("  ← APIPA" if a.is_apipa else ""),
              "am" if a.is_apipa else "")
            L(f"  Gateway     : {a.gateway or 'NOT FOUND'}")
            L(f"  DHCP Server : {a.dhcp_server or 'N/A'}")
            L(f"  DNS Servers : {', '.join(a.dns_servers) if a.dns_servers else 'None'}")
            L(f"  MAC Address : {a.mac_address}")
        else:
            L("  No active adapter found.", "rd")

        # Wi-Fi
        if wifi:
            L()
            L(HR, "dm");  L("  WI-FI DETAILS", "cy");  L(HR, "dm")
            L(f"  Adapter     : {wifi.adapter_name}")
            L(f"  SSID        : {wifi.ssid}")
            L(f"  Band/Channel: {wifi.band}  Ch.{wifi.channel}")
            L(f"  Radio Type  : {wifi.radio_type}")
            sig_tag = "am" if wifi.is_weak_signal else "gr"
            L(f"  Signal      : {wifi.signal_pct}%  (≈{wifi.rssi_dbm} dBm)", sig_tag)
            L(f"  Tx / Rx     : {wifi.transmit_rate_mbps} / {wifi.receive_rate_mbps} Mbps",
              "am" if wifi.is_slow_link else "")

        # Diagnostics
        L()
        L(HR, "dm");  L("  DIAGNOSTIC RESULTS", "cy");  L(HR, "dm")
        L(f"  Loopback         : {'✓ PASS' if report.loopback_ok else '✗ FAIL'}",
          "gr" if report.loopback_ok else "rd")
        if report.gateway_ping:
            gp = report.gateway_ping
            L(f"  Gateway ping     : {'✓' if gp.reachable else '✗'}  "
              f"loss={gp.loss_pct:.0f}%  avg={gp.avg_ms:.0f}ms",
              "gr" if gp.reachable else "rd")
        if report.internet_ping:
            ip = report.internet_ping
            itag = ("gr" if ip.reachable and ip.loss_pct < 20
                    else "am" if ip.reachable else "rd")
            L(f"  Internet ping    : {'✓' if ip.reachable else '✗'}  "
              f"loss={ip.loss_pct:.0f}%  avg={ip.avg_ms:.0f}ms  "
              f"min={ip.min_ms:.0f}ms  max={ip.max_ms:.0f}ms", itag)
        L(f"  DNS resolution   : {'✓ PASS' if report.dns_ok else '✗ FAIL'}  "
          f"({report.dns_latency_ms:.0f}ms)",
          "gr" if report.dns_ok else "rd")
        L()
        L(f"  Local connect    : {'Yes' if report.has_local_connectivity else 'No'}")
        L(f"  Internet         : {'Yes' if report.has_internet else 'No'}")
        L(f"  DNS working      : {'Yes' if report.has_dns else 'No'}")
        L(f"  APIPA detected   : {'Yes ⚠' if report.apipa_detected else 'No'}",
          "am" if report.apipa_detected else "")
        L(f"  Packet loss sev  : {'Yes ⚠' if report.packet_loss_severe else 'No'}",
          "am" if report.packet_loss_severe else "")
        L(f"  Latency high     : {'Yes ⚠' if report.latency_high else 'No'}",
          "am" if report.latency_high else "")

        # Classification
        L()
        L(HR, "dm");  L("  CLASSIFICATION", "cy");  L(HR, "dm")
        s_map = {"healthy": "gr", "advisory": "cy", "degraded": "am", "critical": "rd"}
        L(f"  Fault       : {cls.fault}")
        L(f"  Severity    : {cls.severity.upper()}", s_map.get(cls.severity, ""))
        L(f"  Description : {cls.description}")

        # Remediation
        L()
        L(HR, "dm");  L("  REMEDIATION ACTIONS", "cy");  L(HR, "dm")
        for action in result.actions_attempted:
            ok = action in result.actions_succeeded
            L(f"  {'✓' if ok else '✗'}  {action}", "gr" if ok else "am")
        if not result.actions_attempted:
            L("  (no actions were needed)", "dm")
        L()
        L(f"  Admin privileges : {'Yes' if is_admin() else 'No — some actions were skipped'}",
          "gr" if is_admin() else "am")
        L(f"  Reboot needed    : {'Yes ⚠' if result.reboot_recommended else 'No'}",
          "am" if result.reboot_recommended else "gr")


# ---- ENTRY POINT ----

def main() -> int:
    debug_log("===== APP START =====")

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    app = App(debug=_DEBUG_MODE)
    app.mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())

    except KeyboardInterrupt:
        sys.exit(0)

    except Exception as exc:
        debug_log("FATAL MAIN EXCEPTION")
        debug_log(str(exc))
        debug_log(traceback.format_exc())
        try:
            import tkinter.messagebox as mb
            mb.showerror(
                "Network Recovery System",
                f"Fatal startup error:\n{exc}\n\nPlease contact IT support.",
            )
        except Exception:
            print(f"Fatal error: {exc}")
        sys.exit(1)