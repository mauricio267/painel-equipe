"""Busca as tarefas no ClickUp e gera dist/index.html (ainda sem senha).

Uso:
  CLICKUP_TOKEN=pk_... python3 scripts/build.py   # dados reais
  python3 scripts/build.py --demo                  # dados de exemplo, para testar o visual
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

RAIZ = Path(__file__).resolve().parent.parent
API = "https://api.clickup.com/api/v2"

cfg = json.loads((RAIZ / "config.json").read_text(encoding="utf-8"))
TZ = ZoneInfo(cfg["fuso"])
FEITOS = {s.lower() for s in cfg["status_feitos"]}


def api_get(path, params, token):
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": token})
    for tentativa in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and tentativa < 3:
                time.sleep(30)
                continue
            raise SystemExit(f"ClickUp respondeu {e.code} em {path}: {e.read().decode()[:300]}")


def buscar(filtro, token, include_closed, atualizadas_desde_ms=None):
    """filtro: ("project_ids[]", folder_id) para uma pasta ou ("assignees[]", user_id) para um responsável."""
    tarefas, page = [], 0
    while page < 30:
        params = [
            filtro,
            ("include_closed", "true" if include_closed else "false"),
            ("subtasks", "true"),
            ("page", str(page)),
        ]
        if atualizadas_desde_ms:
            params.append(("date_updated_gt", str(atualizadas_desde_ms)))
        resp = api_get(f"/team/{cfg['team_id']}/task", params, token)
        lote = resp.get("tasks", [])
        tarefas += lote
        if resp.get("last_page", True) or not lote:
            break
        page += 1
    return tarefas


def dia(ms):
    if not ms:
        return None
    return datetime.fromtimestamp(int(ms) / 1000, TZ).date().isoformat()


def feita(t):
    st = t.get("status") or {}
    return st.get("type") in ("closed", "done") or str(st.get("status", "")).lower() in FEITOS


PASTAS_ENTREGA = {p["folder_id"]: p["nome"] for p in cfg["pastas_entrega"]}
PASTAS_TRABALHO = {p["folder_id"] for p in cfg["pessoas"]}  # pastas Duda e Raphael


def simplificar(t, pessoa):
    """Uma tarefa do ponto de vista de uma pessoa.

    Regra: tudo que estiver fora das pastas de trabalho (Duda e Raphael) é tarefa concluída.
    Dentro delas, conta como feita só quando o status é final. Atraso e fila só contam dentro delas.
    """
    pasta = str((t.get("folder") or {}).get("id", ""))
    entregue_fora = pasta not in PASTAS_TRABALHO
    fechada = dia(t.get("date_closed") or t.get("date_done"))
    ok = feita(t) or entregue_fora
    return {
        "id": t["id"],
        "nome": t.get("name", ""),
        "url": t.get("url", ""),
        "status": (t.get("status") or {}).get("status", ""),
        "prazo": dia(t.get("due_date")),
        "fechada": fechada if ok else None,  # sem data no ClickUp: completada pelo histórico
        "atualizada": dia(t.get("date_updated")),
        "feita": ok,
        "na_pasta": not entregue_fora,
        "destino": (PASTAS_ENTREGA.get(pasta) or "Outras pastas") if entregue_fora else None,
        "prioridade": PRIORIDADES.get(((t.get("priority") or {}).get("priority") or "").lower()),
    }


PRIORIDADES = {"urgent": "Urgente", "high": "Alta", "normal": "Normal", "low": "Baixa"}
ORDEM_PRI = {"Urgente": 0, "Alta": 1, "Normal": 2, "Baixa": 3, None: 4}
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]


def calcular(pessoas_tarefas, hoje):
    hoje_d = date_of(hoje)
    mes_ini = hoje_d.replace(day=1).isoformat()
    mes_fim = (hoje_d.replace(day=28) + timedelta(days=4)).replace(day=1).isoformat()  # 1º dia do mês seguinte
    sem_ini_d = hoje_d - timedelta(days=(hoje_d.weekday() + 1) % 7)  # domingo
    sem_ini, sem_fim = sem_ini_d.isoformat(), (sem_ini_d + timedelta(days=7)).isoformat()
    semanas = [(sem_ini_d - timedelta(weeks=i)) for i in range(4, -1, -1)]
    proxima = (hoje_d + timedelta(days=7)).isoformat()
    dias30 = [(hoje_d - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
    ordem = lambda t: (t["prazo"] or "9999", ORDEM_PRI[t["prioridade"]])

    def grupo(t):
        if not t["prazo"]:
            return "Sem prazo"
        if t["prazo"] < hoje:
            return "Atrasada"
        if t["prazo"] == hoje:
            return "Hoje"
        if t["prazo"] <= proxima:
            return "Próximos 7 dias"
        return "Mais adiante"

    pessoas, atrasos = [], []
    for p, tarefas in pessoas_tarefas:
        # Em aberto, atraso e fila: só o que está na pasta da pessoa
        abertas = [t for t in tarefas if not t["feita"] and t["na_pasta"]]
        # Feitas: em qualquer lugar, inclusive cards que foram para FERNANDA ou ANDERSON
        feitas = [t for t in tarefas if t["feita"] and t["fechada"]]
        contam = [t for t in tarefas if t["feita"] or t["na_pasta"]]
        no_mes = lambda d: d and mes_ini <= d < mes_fim
        na_semana = lambda d: d and sem_ini <= d < sem_fim

        previstas_mes = [t for t in contam if no_mes(t["prazo"])]
        entregues_mes = [t for t in feitas if no_mes(t["fechada"])]
        com_prazo_mes = [t for t in entregues_mes if t["prazo"]]

        semana = [t for t in contam if na_semana(t["prazo"]) or (t["feita"] and na_semana(t["fechada"]))]
        semana.sort(key=lambda t: (t["feita"], t["prazo"] or "9999"))

        atrasadas = sorted([t for t in abertas if t["prazo"] and t["prazo"] < hoje], key=ordem)
        for t in atrasadas:
            atrasos.append({**t, "pessoa": p["nome"], "dias": (hoje_d - date_of(t["prazo"])).days})

        fazer = [{**t, "grupo": grupo(t)} for t in sorted(abertas, key=ordem)]

        pessoas.append({
            "nome": p["nome"],
            "nome_completo": p["nome_completo"],
            "pasta": p["pasta"],
            "mes": {
                "previstas": len(previstas_mes),
                "previstas_feitas": sum(1 for t in previstas_mes if t["feita"]),
                "entregues": len(entregues_mes),
                "no_prazo": sum(1 for t in com_prazo_mes if t["fechada"] <= t["prazo"]),
                "com_prazo": len(com_prazo_mes),
            },
            "semana": {
                "total": len(semana),
                "feitas": sum(1 for t in semana if t["feita"]),
                "tarefas": semana[:15],
            },
            "abertas": len(abertas),
            "atrasadas": len(atrasadas),
            "hoje": sum(1 for t in abertas if t["prazo"] == hoje),
            "sem_prazo": sum(1 for t in abertas if not t["prazo"]),
            "ritmo": [
                sum(1 for t in feitas if s.isoformat() <= t["fechada"] < (s + timedelta(days=7)).isoformat())
                for s in semanas
            ],
            "fazer": fazer[:20],
            "fazer_total": len(fazer),
            # Entregas por dia nos últimos 30 dias (o último item é hoje)
            "diario": [sum(1 for t in feitas if t["fechada"] == d) for d in dias30],
            "media_dia": round(sum(1 for t in feitas if t["fechada"] >= dias30[0]) / 30, 1),
            "por_destino": {
                "Própria pasta": sum(1 for t in entregues_mes if not t["destino"]),
                **{n: sum(1 for t in entregues_mes if t["destino"] == n) for n in [*PASTAS_ENTREGA.values(), "Outras pastas"]},
            },
            "por_status": sorted(
                ({"status": s, "total": sum(1 for t in abertas if t["status"] == s)} for s in {t["status"] for t in abertas}),
                key=lambda r: -r["total"]),
        })
    atrasos.sort(key=lambda t: (t["prazo"], t["pessoa"]))
    return {
        "hoje": hoje,
        "dias30": dias30,
        "mes_nome": MESES[hoje_d.month - 1],
        "semana_ini": sem_ini,
        "semana_fim": (sem_ini_d + timedelta(days=6)).isoformat(),
        "gerado_em": datetime.now(TZ).strftime("%d/%m/%Y às %H:%M"),
        "gerado_iso": datetime.now(timezone.utc).isoformat(),
        "destinos": [*PASTAS_ENTREGA.values(), "Outras pastas"],
        "semanas": [s.isoformat() for s in semanas],
        "pessoas": pessoas,
        "atrasos": atrasos,
    }


def date_of(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def dados_reais():
    token = os.environ.get("CLICKUP_TOKEN")
    if not token:
        raise SystemExit("Falta a variável CLICKUP_TOKEN (o token pessoal da API do ClickUp).")
    hoje = datetime.now(TZ).date().isoformat()
    desde_ms = int((datetime.now(timezone.utc) - timedelta(days=cfg["dias"] + 2)).timestamp() * 1000)
    pessoas_tarefas = []
    for p in cfg["pessoas"]:
        pasta = ("project_ids[]", p["folder_id"])
        responsavel = ("assignees[]", p["user_id"])
        brutas = buscar(pasta, token, include_closed=False)
        brutas += buscar(pasta, token, include_closed=True, atualizadas_desde_ms=desde_ms)
        # Cards em que a pessoa é responsável em qualquer pasta: os abertos todos (inclusive na pasta
        # do colega) e os recentes de qualquer status (pega os que foram para outras pastas)
        brutas += buscar(responsavel, token, include_closed=False)
        brutas += buscar(responsavel, token, include_closed=True, atualizadas_desde_ms=desde_ms)
        unicas = {t["id"]: simplificar(t, p) for t in brutas}
        pessoas_tarefas.append((p, list(unicas.values())))
        fora = sum(1 for t in unicas.values() if t["destino"])
        print(f"{p['nome']}: {len(unicas)} tarefas lidas, {fora} fora das pastas Duda e Raphael")
    datar_entregas([t for _, lista in pessoas_tarefas for t in lista], hoje)
    return calcular(pessoas_tarefas, hoje) | {"exemplo": False}


HISTORICO = RAIZ / "estado" / "historico.json"


def datar_entregas(tarefas, hoje):
    """Dá uma data de entrega às tarefas feitas que não têm data de fechamento no ClickUp.

    O histórico guarda o dia em que cada card apareceu como feito pela primeira vez, para a data
    não mudar quando alguém mexer no card depois. Na primeira execução (sem histórico), usa o
    prazo do card se já passou, senão a data da última alteração.
    """
    primeira_vez = not HISTORICO.exists()
    hist = {} if primeira_vez else json.loads(HISTORICO.read_text(encoding="utf-8"))
    for t in tarefas:
        if not t["feita"]:
            continue
        if not t["fechada"]:
            if t["id"] in hist:
                t["fechada"] = hist[t["id"]]
            elif primeira_vez:
                t["fechada"] = t["prazo"] if t["prazo"] and t["prazo"] <= hoje else (t["atualizada"] or hoje)
            else:
                t["fechada"] = hoje
        hist.setdefault(t["id"], t["fechada"])
    HISTORICO.parent.mkdir(exist_ok=True)
    HISTORICO.write_text(json.dumps(hist, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    print(f"Histórico: {len(hist)} entregas registradas" + (" (primeira execução)" if primeira_vez else ""))


def dados_exemplo():
    hoje = datetime.now(TZ).date()
    h = hoje.isoformat()
    d = lambda n: (hoje - timedelta(days=n)).isoformat()
    def t(i, nome, prazo, fechada=None, status="edição", pri=None, destino=None):
        return {"id": f"ex{i}", "nome": nome, "url": "https://app.clickup.com/", "status": status,
                "prazo": prazo, "fechada": fechada, "feita": fechada is not None, "prioridade": pri,
                "na_pasta": destino is None, "destino": destino}
    duda = [t(1, "Exemplo: editar cortes do podcast", h, pri="Urgente"), t(2, "Exemplo: vinheta modelo", d(2), pri="Alta"),
            t(3, "Exemplo: roteiro de abertura", None), t(4, "Exemplo: teaser palestrante", d(-3), status="legenda"),
            t(5, "Exemplo: legendar posts", d(-9), status="to do", pri="Normal"),
            t(6, "Exemplo: corte Fernanda enviado", d(1), d(0), "legenda", destino="FERNANDA"),
            t(7, "Exemplo: criativo enviado ao Anderson", d(4), d(3), "legenda/ musica", destino="ANDERSON"),
            t(8, "Exemplo: teaser enviado ao Summit", d(6), d(6), "postado", destino="SUMMIT"),
            t(9, "Exemplo: corte enviado à Aliança", d(3), d(2), "legenda", destino="ALIANÇA")] + [
            t(10 + i, f"Exemplo: entrega {i}", d(i * 2 + 1), d(i * 2 + 2), "concluído") for i in range(12)]
    rapha = [t(40, "Exemplo: arrumar sede de eventos", d(1), pri="Alta"), t(41, "Exemplo: gravação de depoimentos", h),
             t(42, "Exemplo: montar sala de podcast", d(-2), status="to do"), t(43, "Exemplo: backup dos cartões", None)] + [
            t(50 + i, f"Exemplo: entrega {i}", d(i * 3), d(i * 3), "concluído") for i in range(8)]
    pessoas = [(p, lista) for p, lista in zip(cfg["pessoas"], [duda, rapha])]
    return calcular(pessoas, h) | {"exemplo": True}


def main():
    dados = dados_exemplo() if "--demo" in sys.argv else dados_reais()
    modelo = (RAIZ / "site" / "template.html").read_text(encoding="utf-8")
    js = json.dumps(dados, ensure_ascii=False).replace("</", "<\\/")
    saida = RAIZ / "dist"
    saida.mkdir(exist_ok=True)
    (saida / "index.html").write_text(modelo.replace("__DADOS__", js), encoding="utf-8")
    print(f"Página gerada em {saida / 'index.html'}")


if __name__ == "__main__":
    main()
