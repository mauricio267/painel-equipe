"""Procura, só lendo, onde está o trabalho do Rapha no ClickUp (não altera nada).

Uso: CLICKUP_TOKEN=pk_... python3 scripts/explorar_rapha.py
Gera estado/rapha_rastros.json e imprime um resumo por origem e por pasta.
"""

import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # reaproveita api_get, buscar e a configuração

RAPHA = 99966795
DIAS = 90


def main():
    token = build.ler_token()
    team = build.cfg["team_id"]
    agora = datetime.now(timezone.utc)
    desde_ms = int((agora - timedelta(days=DIAS)).timestamp() * 1000)

    # 1. Tudo que foi mexido nos últimos 90 dias no workspace (abertos e fechados)
    print(f"Lendo tarefas alteradas nos últimos {DIAS} dias...")
    tudo, page = {}, 0
    while page < 60:
        resp = build.api_get(f"/team/{team}/task", [("include_closed", "true"), ("subtasks", "true"),
                                                    ("date_updated_gt", str(desde_ms)), ("page", str(page))], token)
        lote = resp.get("tasks", [])
        for t in lote:
            tudo[t["id"]] = t
        if resp.get("last_page", True) or not lote:
            break
        page += 1
        time.sleep(0.3)
    print(f"  {len(tudo)} tarefas")

    # 2. Tempo registrado por ele
    print("Lendo tempo registrado pelo Rapha...")
    tempo = build.api_get(f"/team/{team}/time_entries", [("assignee", str(RAPHA)),
                                                         ("start_date", str(desde_ms)), ("end_date", str(int(agora.timestamp() * 1000)))],
                          token, falhar=False) or {}
    com_tempo = Counter()
    for e in tempo.get("data", []) or []:
        tid = (e.get("task") or {}).get("id")
        if tid:
            com_tempo[tid] += int(e.get("duration") or 0) // 60000

    origens = defaultdict(set)
    for tid, t in tudo.items():
        if any(a.get("id") == RAPHA for a in t.get("assignees") or []):
            origens["responsável"].add(tid)
        if (t.get("creator") or {}).get("id") == RAPHA:
            origens["criou"].add(tid)
        if any(w.get("id") == RAPHA for w in t.get("watchers") or []):
            origens["segue (watcher)"].add(tid)
        for cf in t.get("custom_fields") or []:
            if cf.get("type") == "users" and any((u or {}).get("id") == RAPHA for u in cf.get("value") or []):
                origens[f"campo '{cf.get('name')}'"].add(tid)
    origens["registrou tempo"] = set(com_tempo)

    def pasta(t):
        return (t.get("folder") or {}).get("name") or "(sem pasta)"

    relatorio = {}
    print("\n=== Onde aparece o Rapha (últimos 90 dias) ===")
    for origem, ids in sorted(origens.items(), key=lambda x: -len(x[1])):
        por_pasta = Counter(pasta(tudo[i]) for i in ids if i in tudo)
        print(f"\n{origem}: {len(ids)} cards")
        for p, n in por_pasta.most_common(12):
            print(f"   {n:4d}  {p}")
        relatorio[origem] = {"total": len(ids), "por_pasta": dict(por_pasta)}

    so_rastro = set().union(*[v for k, v in origens.items() if k != "responsável"]) - origens["responsável"]
    print(f"\nCards com rastro dele mas SEM ele como responsável: {len(so_rastro)}")
    exemplos = [f"{pasta(tudo[i])} | {tudo[i].get('name')} | {(tudo[i].get('status') or {}).get('status')}"
                for i in list(so_rastro)[:25] if i in tudo]
    for e in exemplos:
        print("   ", e)
    relatorio["sem_responsavel_exemplos"] = exemplos
    relatorio["sem_responsavel_total"] = len(so_rastro)

    out = build.RAIZ / "estado" / "rapha_rastros.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(relatorio, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nRelatório salvo em {out}")


if __name__ == "__main__":
    main()
