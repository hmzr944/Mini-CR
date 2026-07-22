#!/usr/bin/env python3
"""Digest hebdomadaire Telegram — envoie /perf automatiquement chaque semaine.
Objectif : casser la boucle "vérifier chaque jour, silence = inquiétude" en
donnant un point de repère régulier même quand le bot n'a rien à signaler
(comportement normal vu le rythme ~1-2 trades/semaine de la stratégie).
Usage : tâche planifiée hebdomadaire (ex. lundi 09:00)."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import telegram_bot_v33 as tgb

if __name__ == "__main__":
    chat_id = tgb._chat_id()
    if chat_id:
        tgb.send("🗓 <b>Point hebdomadaire</b>", chat_id)
        tgb.cmd_perf(chat_id)
        print("Digest hebdomadaire envoyé")
    else:
        print("Pas de chat_id configuré — digest annulé")
