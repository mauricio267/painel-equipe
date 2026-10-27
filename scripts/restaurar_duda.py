"""Devolve a Duda como responsável nas tarefas de duda-restaurar.json (só acrescenta, não tira ninguém).

Uso: CLICKUP_TOKEN=pk_... python3 scripts/restaurar_duda.py
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVO = RAIZ / "duda-restaurar.json"
DUDA = 112042161


def main():
    token = (os.environ.get("CLICKUP_TOKEN") or "").strip()
    if not token.startswith("pk_") or not token.isascii():
        raise SystemExit("O texto colado não parece um token do ClickUp (começa com pk_).")
    if not ARQUIVO.exists():
        print("Nada a restaurar: duda-restaurar.json não existe (talvez já tenha sido feito).")
        return
    tarefas = list(json.loads(ARQUIVO.read_text(encoding="utf-8"))["tarefas"])
    corpo = json.dumps({"assignees": {"add": [DUDA], "rem": []}}).encode()
    ok, falhas = 0, []
    for i, tid in enumerate(tarefas, 1):
        req = urllib.request.Request(f"https://api.clickup.com/api/v2/task/{tid}", data=corpo, method="PUT",
                                     headers={"Authorization": token, "Content-Type": "application/json"})
        for tentativa in range(3):
            try:
                urllib.request.urlopen(req, timeout=60).read()
                ok += 1
                break
            except urllib.error.HTTPError as e:
                if e.code == 429 and tentativa < 2:
                    time.sleep(30)
                    continue
                falhas.append(f"{tid} ({e.code})")
                break
        if i % 20 == 0:
            print(f"  {i}/{len(tarefas)}")
        time.sleep(0.7)  # a API aceita 100 chamadas por minuto
    print(f"Duda devolvida em {ok} de {len(tarefas)} tarefas.")
    if falhas:
        print("Falharam:", ", ".join(falhas))
        sys.exit(1)
    ARQUIVO.rename(ARQUIVO.with_suffix(".feito.json"))


if __name__ == "__main__":
    main()
