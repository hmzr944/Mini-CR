#!/usr/bin/env python3
"""Watchdog HÔTE (hors Docker) — survit à la mort de Docker Desktop.
Vérifie l'âge de live_state_v33.json ; si >75 min : tente de relancer Docker
Desktop + la stack, et alerte Telegram. Tâche planifiée toutes les 30 min.
(Le watchdog interne tourne DANS Docker : il meurt avec — incident du 09/07,
bot arrêté 3h sans alerte.)"""
import json, subprocess, sys, time
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STATE = ROOT / "live_state_v33.json"
MAX_MIN = 75

def alert(msg):
    try:
        import telegram_notif as tg
        tg._send(f"🚨 <b>HOST WATCHDOG</b>\n{msg}")
    except Exception:
        pass

try:
    last = json.load(open(STATE)).get("last_run_ts", "")
    age = (datetime.now() - datetime.fromisoformat(last)).total_seconds() / 60
except Exception as e:
    alert(f"State illisible ({e}) — vérifier le bot.")
    sys.exit(1)

if age <= MAX_MIN:
    print(f"OK — dernier tick il y a {age:.0f} min")
    sys.exit(0)

alert(f"Bot silencieux depuis {age:.0f} min — tentative de relance automatique...")
try:
    subprocess.run(["docker", "info"], capture_output=True, timeout=20, check=True)
except Exception:
    subprocess.Popen([r"C:\Program Files\Docker\Docker\Docker Desktop.exe"])
    time.sleep(90)
r = subprocess.run(["docker", "compose", "-f", str(ROOT / "docker-compose.v33.yml"),
                    "up", "-d"], capture_output=True, timeout=180, cwd=str(ROOT))
if r.returncode == 0:
    alert("Stack PRISM relancée automatiquement ✅ (vérifier le prochain tick HH:03)")
    print("Relance OK")
else:
    alert(f"ÉCHEC relance automatique — intervention manuelle requise.\n{r.stderr.decode(errors='ignore')[:200]}")
    print("Relance ÉCHOUÉE")
