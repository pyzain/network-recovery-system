# Intelligent Network Recovery System

**A self-diagnosing, self-repairing network tool for Windows.**
Double-click it, and it automatically figures out *why* your internet or
Wi-Fi is misbehaving — then safely fixes what it can, all on its own.

Internally the compiled program is named `Company_Network_Brain.exe`.

---

## Table of Contents

1. [What This Does](#what-this-does)
2. [Why I Built This](#why-i-built-this)
3. [Features](#features)
4. [How It Works](#how-it-works)
5. [Project Structure](#project-structure)
6. [How to Download This Project](#how-to-download-this-project)
7. [Running It From Source](#running-it-from-source)
8. [Building the .exe Yourself](#building-the-exe-yourself)
9. [Safety Design](#safety-design)
10. [License](#license)
11. [Author](#author)

---

## What This Does

Most "fix my internet" tools just tell you to restart your router. This one
actually **investigates first, then acts.**

When you run it, it:

1. Runs a series of network health checks (is the local network stack
   working? Is the router reachable? Is the internet reachable? Is DNS
   working? How much data is being lost?).
2. Reads your Wi-Fi signal strength, band, and speed (if you're on Wi-Fi).
3. Uses that evidence to name the **exact problem** — for example
   *"DHCP failure"*, *"weak Wi-Fi signal"*, or *"DNS resolution failure"* —
   instead of just guessing.
4. Applies a small, carefully chosen set of safe fixes for that specific
   problem (flushing DNS, renewing your IP address, resetting the network
   adapter, reconnecting Wi-Fi, etc.).
5. Shows you a plain-English summary of what was wrong and what was done
   about it.

Nothing about your network is hardcoded — no fixed IP addresses, router
addresses, or Wi-Fi names. Every value is discovered live, on whichever
computer the program runs on.

## Why I Built This

Most people don't know the difference between "my Wi-Fi is weak," "my DNS is
broken," and "my ISP is down" — they just know the internet isn't working.
I wanted a tool that could tell them (and fix it, where it safely can)
without needing anyone to open Command Prompt.

## Features

- ✅ **One-click operation** — no setup, no configuration screen, nothing to
  type. Just run it.
- ✅ **Automatic elevation** — the program requests Administrator rights on
  its own via a Windows UAC prompt, so every repair step it's allowed to
  run, it can run.
- ✅ **Evidence-based diagnosis** — a rule-based classifier turns raw test
  results into one specific, named fault, ordered from most to least
  severe.
- ✅ **Safe, single-shot repairs** — every fix runs at most once per session.
  There are no retry loops, no repeated DHCP requests, and no network
  flooding.
- ✅ **Live results, not a spinner** — a real-time animated interface shows
  every test and every fix as it happens.
- ✅ **Fully dynamic** — the adapter name, IP address, gateway, DNS servers,
  and Wi-Fi SSID are all discovered at runtime. Nothing is hardcoded.
- ✅ **Never restarts your computer automatically** — a restart is only ever
  *recommended* to you, never forced.

## How It Works

The program is organized as a simple, linear pipeline:

```
 diagnostics.py          wifi_analysis.py
       │                        │
       └──────────┬─────────────┘
                   ▼
             classifier.py         (turns raw results into ONE named fault)
                   ▼
             remediation.py        (applies the safe fix plan for that fault)
                   ▼
             run_script.py         (shows it all in a live graphical window)
```

| Stage | File | Responsibility |
|---|---|---|
| 1. Diagnose | `diagnostics.py` | Runs loopback, gateway, internet, and DNS tests; measures packet loss and latency |
| 1b. Inspect Wi-Fi | `wifi_analysis.py` | Reads SSID, band, channel, signal strength, and link speed |
| 2. Classify | `classifier.py` | Reads the evidence and decides on the single most likely fault |
| 3. Remediate | `remediation.py` | Runs the correct, ordered set of safe fixes for that fault |
| — | `interface_manager.py` | Low-level actions: adapter reset, DHCP renewal, Wi-Fi reconnect, route cleanup |
| — | `tcp_optimizer.py` | Low-level actions: DNS flush, TCP tuning, Winsock reset |
| — | `helpers.py` | Shared logging, safe command runner, and admin-rights check |
| — | `config.py` | All the tunable thresholds (how much packet loss counts as "bad," etc.) |
| UI | `run_script.py` | The graphical window employees actually see and interact with |

## Project Structure

```
network-recovery-system/
├── run_script.py          ← Entry point — the graphical interface
├── diagnostics.py          ← Network tests (loopback, gateway, internet, DNS)
├── classifier.py           ← Turns test results into one named fault
├── remediation.py          ← Applies the safe fix plan for that fault
├── wifi_analysis.py        ← Parses Wi-Fi adapter state
├── tcp_optimizer.py        ← DNS flush, TCP tuning, Winsock reset
├── interface_manager.py    ← Adapter reset, DHCP renewal, Wi-Fi reconnect
├── helpers.py               ← Logging, safe subprocess runner, admin check
├── config.py                ← Tunable thresholds
├── requirements.txt
├── LICENSE
└── README.md
```

---

## How to Download This Project

If you've never used GitHub before, here's exactly what to click.

1. Go to this repository's page in your browser (the page you're probably
   reading this on right now).
2. Near the top of the file list, you'll see a **green button labeled
   "Code"**. Click it.
3. A small menu will drop down. Near the bottom of that menu, click
   **"Download ZIP"**.
4. Your browser will download a `.zip` file — by default this lands in your
   **Downloads** folder.
5. Open **File Explorer**, go to your Downloads folder, **right-click** the
   downloaded `.zip` file, and choose **"Extract All..."**.
6. Pick a folder to extract it to (or just accept the default) and click
   **"Extract"**.
7. You now have a normal folder with all the project's files in it — that's
   the project.

*(If you're comfortable with Git, the same "Code" button also gives you the
`git clone` URL directly.)*

## Running It From Source

You'll need **Python 3.11 or newer** installed. If you don't have it, get it
from [python.org/downloads](https://www.python.org/downloads/) — during
installation, make sure to tick **"Add python.exe to PATH"** before clicking
Install.

Once Python is installed, open the project folder in File Explorer, hold
**Shift** and **right-click** in an empty area of the folder, then choose
**"Open PowerShell window here"** (Windows 10) or **"Open in Terminal"**
(newer Windows). Then run:

```powershell
pip install -r requirements.txt
python run_script.py
```

The program window will open. Note that some repair actions (like TCP
tuning or adapter resets) only work when the window is running with
Administrator rights — if you're not prompted for elevation automatically
when running from source, right-click PowerShell itself and choose
**"Run as administrator"** before running the two commands above.

## Building the .exe Yourself

Want a single double-clickable `.exe` file (no Python required to run it)?

```powershell
pip install -r requirements.txt
pyinstaller --onefile --noconsole --uac-admin --name Company_Network_Brain --noupx --collect-data customtkinter --hidden-import=customtkinter run_script.py
```

**What each flag does:**

| Flag | Why it's there |
|---|---|
| `--onefile` | Packs everything into a single `.exe` file |
| `--noconsole` | Hides the black console window — only the graphical window shows |
| `--uac-admin` | Makes Windows automatically prompt for Administrator rights when the `.exe` is double-clicked, so every repair action can run |
| `--noupx` | Skips UPX compression — UPX is a common trigger for false-positive antivirus warnings on PyInstaller apps |
| `--collect-data customtkinter` | Bundles the theme files `customtkinter` needs at runtime (without this, the built `.exe` crashes on startup) |
| `--hidden-import=customtkinter` | Makes sure PyInstaller doesn't miss `customtkinter` during its automatic dependency scan |

The finished file will appear at:

```
dist\Company_Network_Brain.exe
```

You can copy that single file anywhere — a shared drive, a USB stick, an IT
portal — and it will run standalone, with no installation step.

## Safety Design

This tool is built to be safe to run on someone else's computer without
supervision:

- Every repair action runs **once per session** — never in a retry loop.
- **No infinite ping loops** and no network flooding.
- DHCP renewal only triggers when an APIPA (self-assigned) address or a
  broken lease is actually confirmed — never speculatively.
- Wi-Fi reconnects and route cleanups only run when instability or
  duplicate routes are actually detected.
- **The computer is never restarted automatically.** A restart is, at most,
  *recommended* to the person sitting at the keyboard.
- No IP addresses, gateway addresses, adapter names, or Wi-Fi network names
  are ever hardcoded — everything is discovered fresh, on whatever machine
  the program is running on, every time.

Some repair actions need Administrator rights to do anything (for example,
resetting a network adapter or tuning the TCP stack); the compiled `.exe`
requests elevation automatically so nothing silently fails.

| Action | Privilege Required |
|---|---|
| DNS flush | Standard user |
| Read Wi-Fi info | Standard user |
| Read adapter/IP info | Standard user |
| Ping tests | Standard user |
| DNS resolution check | Standard user |
| TCP optimization | **Administrator** |
| Adapter disable/enable | **Administrator** |
| DHCP release/renew | **Administrator** |
| Wi-Fi disconnect/reconnect | Standard user (own profile) |
| Route table cleanup | **Administrator** |
| Winsock reset | **Administrator** |

---

## License

This project is licensed under the [MIT License](LICENSE) — you're free to
use, copy, modify, and distribute it, including for commercial purposes, as
long as the original copyright notice is kept.

## Author

**Zain Raza**
GitHub: [@pyzain](https://github.com/pyzain)

Copyright © 2026 Zain Raza
