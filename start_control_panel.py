"""
start_control_panel.py — Launch the Delta-Neutral Bot Control Panel.

Usage:
  python start_control_panel.py

Environment variables (all optional):
  CONTROL_PANEL_PASSWORD   Panel login password       (default: admin)
  CONTROL_PANEL_PORT       Port to listen on          (default: 8080)
  CONTROL_PANEL_NAME       Instance label shown in UI (default: Local Bot)
  CONTROL_PANEL_SECRET     Flask session secret       (auto-generated if not set)

VPS access via SSH tunnel:
  ssh -L 8080:localhost:8080 user@your-vps-ip
  Then open http://localhost:8080 on your local machine.
"""

import sys
import os
from pathlib import Path

# Ensure the bot root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Set defaults if not already in environment
os.environ.setdefault("CONTROL_PANEL_PASSWORD", "admin")
os.environ.setdefault("CONTROL_PANEL_PORT", "8080")
os.environ.setdefault("CONTROL_PANEL_NAME", "Local Bot")

from control_panel.app import app, PANEL_PORT, PANEL_PASSWORD, INSTANCE_NAME

if __name__ == "__main__":
    print()
    print("  ╔══════════════════════════════════════════════╗")
    print(f"  ║  Delta-Neutral Bot — Control Panel           ║")
    print(f"  ║  Instance : {INSTANCE_NAME:<34}║")
    print(f"  ║  URL      : http://localhost:{PANEL_PORT:<16}║")
    print(f"  ║  Password : {PANEL_PASSWORD:<34}║")
    print(f"  ╚══════════════════════════════════════════════╝")
    print()
    print("  Tip: Set CONTROL_PANEL_PASSWORD env var to change the password.")
    print("  VPS:  ssh -L 8080:localhost:8080 user@your-vps-ip")
    print()
    app.run(host="0.0.0.0", port=PANEL_PORT, debug=False, threaded=True)
