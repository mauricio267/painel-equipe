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


def ler_token():
    token = (os.environ.get("CLICKUP_TOKEN") or "").strip()
    if not token:
        raise SystemExit("Falta o token do ClickUp.")
    if not token.isascii() or not token.startswith("pk_") or " " in token or len(token) > 80:
        raise SystemExit("O texto colado não parece um token do ClickUp. Ele começa com pk_ e não tem espaços. "
                         "Copie de novo em ClickUp → Settings → Apps → API Token.")
    return token


def api_get(path, params, token, falhar=True):
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
            if not falhar:
                return None
            raise SystemExit(f"ClickUp respondeu {e.code} em {path}: {e.read().decode()[:300]}")


def buscar(filtro, token, include_closed, atualizadas_desde_ms=None):
    """filtro: ("project_ids[]", folder_id) para uma pasta, ("assignees[]", user_id) para um
    responsável, ou None para o workspace inteiro."""
    tarefas, page = [], 0
    while page < 30:
        params = [
            *([filtro] if filtro else []),
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
        "criada": dia(t.get("date_created")),
        "criada_ms": int(t["date_created"]) if t.get("date_created") else None,
        "fechada_ms": int(t.get("date_closed") or t.get("date_done") or 0) or None,
        "status_cor": (t.get("status") or {}).get("color") or None,
        "minutos_registrados": round(int(t.get("time_spent") or 0) / 60000),
        "feita": ok,
        "na_pasta": not entregue_fora,
        "destino": (PASTAS_ENTREGA.get(pasta) or "Outras pastas") if entregue_fora else None,
        "prioridade": PRIORIDADES.get(((t.get("priority") or {}).get("priority") or "").lower()),
    }


EM_ANDAMENTO = {s.lower() for s in cfg.get("status_em_andamento", [])}


def buscar_cronometro(token, desde_ms, tarefas_com_tempo):
    """Registros do cronômetro do ClickUp ("Track time" dentro do card), com início e fim.

    Devolve uma lista de {tarefa, user, inicio, fim, minutos, rodando}. Tenta primeiro o
    relatório do workspace; se ele vier vazio (o ClickUp só mostra o tempo dos outros para
    administradores), lê card a card os que têm tempo registrado.
    """
    agora_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    ids = ",".join(str(p["user_id"]) for p in cfg["pessoas"])
    out = []
    resp = api_get(f"/team/{cfg['team_id']}/time_entries",
                   [("start_date", str(desde_ms)), ("end_date", str(agora_ms)), ("assignee", ids)], token, falhar=False)
    for e in (resp or {}).get("data") or []:
        tid = (e.get("task") or {}).get("id")
        ini, fim, dur = int(e.get("start") or 0), int(e.get("end") or 0), int(e.get("duration") or 0)
        rodando = dur < 0 or not fim
        if not tid or not ini:
            continue
        out.append({"tarefa": tid, "user": str((e.get("user") or {}).get("id")), "inicio": ini,
                    "fim": agora_ms if rodando else fim, "minutos": round(((agora_ms - ini) if rodando else dur) / 60000),
                    "rodando": rodando})
    origem = "relatório do workspace"
    if not out:
        origem = "card a card"
        for tid in tarefas_com_tempo:
            r = api_get(f"/task/{tid}/time", [], token, falhar=False)
            for por_user in (r or {}).get("data") or []:
                uid = str((por_user.get("user") or {}).get("id"))
                for it in por_user.get("intervals") or []:
                    ini, fim = int(it.get("start") or 0), int(it.get("end") or 0)
                    if not ini or ini < desde_ms:
                        continue
                    rodando = not fim
                    out.append({"tarefa": tid, "user": uid, "inicio": ini, "fim": agora_ms if rodando else fim,
                                "minutos": round(int(it.get("time") or ((agora_ms if rodando else fim) - ini)) / 60000),
                                "rodando": rodando})
            time.sleep(0.4)
    print(f"Cronômetro: {len(out)} registros ({origem})")
    return out


def tempo_em_status(ids, token):
    """Minutos que cada tarefa passou em cada status (precisa do ClickApp "Total time in Status").

    Devolve {id: {"atual": {"status", "desde", "minutos"}, "producao": minutos em status de andamento}}.
    Se o ClickUp não liberar essa informação, devolve {} e o painel mostra só o que tiver.
    """
    out = {}
    ids = list(ids)
    for i in range(0, len(ids), 100):
        lote = ids[i:i + 100]
        try:
            resp = api_get("/task/bulk_time_in_status/task_ids", [("task_ids", x) for x in lote], token, falhar=False)
        except Exception:
            resp = None
        if not isinstance(resp, dict):
            print("Aviso: o ClickUp não devolveu o tempo em cada status (ClickApp 'Total time in Status' desligado?).")
            return out
        for tid, info in resp.items():
            if not isinstance(info, dict):
                continue
            atual = info.get("current_status") or {}
            tt = atual.get("total_time") or {}
            producao = sum(
                int((h.get("total_time") or {}).get("by_minute") or 0)
                for h in (info.get("status_history") or [])
                if str(h.get("status", "")).lower() in EM_ANDAMENTO
            )
            out[tid] = {
                "atual": {
                    "status": atual.get("status"),
                    "desde": datetime.fromtimestamp(int(tt["since"]) / 1000, timezone.utc).isoformat() if tt.get("since") else None,
                    "minutos": int(tt.get("by_minute") or 0),
                },
                "producao": producao,
            }
    return out


PRIORIDADES = {"urgent": "Urgente", "high": "Alta", "normal": "Normal", "low": "Baixa"}
ORDEM_PRI = {"Urgente": 0, "Alta": 1, "Normal": 2, "Baixa": 3, None: 4}
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]


def calcular(pessoas_tarefas, hoje, tempos=None, hist=None):
    tempos = tempos or {}
    hist = hist or {}
    hoje_d = date_of(hoje)
    mes_ini = hoje_d.replace(day=1).isoformat()
    mes_fim = (hoje_d.replace(day=28) + timedelta(days=4)).replace(day=1).isoformat()  # 1º dia do mês seguinte
    sem_ini_d = hoje_d - timedelta(days=(hoje_d.weekday() + 1) % 7)  # domingo
    sem_ini, sem_fim = sem_ini_d.isoformat(), (sem_ini_d + timedelta(days=7)).isoformat()
    semanas = [(sem_ini_d - timedelta(weeks=i)) for i in range(4, -1, -1)]
    proxima = (hoje_d + timedelta(days=7)).isoformat()
    dias30 = [(hoje_d - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
    ini90 = (hoje_d - timedelta(days=cfg["dias"])).isoformat()
    # Os 3 últimos meses do calendário (o atual por último): pares [início, início do seguinte)
    inicios = [hoje_d.replace(day=1)]
    for _ in range(2):
        inicios.insert(0, (inicios[0] - timedelta(days=1)).replace(day=1))
    meses = [(a.isoformat(), ((a.replace(day=28) + timedelta(days=4)).replace(day=1)).isoformat()) for a in inicios]
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

        # O que está fazendo agora: cards em aberto num status de andamento, com o tempo nesse status
        fazendo = []
        for t in abertas:
            if t["status"].lower() not in EM_ANDAMENTO:
                continue
            tp = tempos.get(t["id"], {})
            fazendo.append({**t, "desde": (tp.get("atual") or {}).get("desde"),
                            "minutos_status": (tp.get("atual") or {}).get("minutos"),
                            "minutos_producao": tp.get("producao")})
        fazendo.sort(key=lambda t: -(t["minutos_status"] or 0))

        # Tempo de produção das entregas do mês (soma do tempo nos status de andamento)
        prod = [tempos[t["id"]]["producao"] for t in entregues_mes if t["id"] in tempos and tempos[t["id"]]["producao"]]
        registrado = [t["minutos_registrados"] for t in entregues_mes if t.get("minutos_registrados")]

        # Entregas em outras pastas: do mês (com os cards) e o total desde o início do histórico
        fora_mes = sorted([t for t in entregues_mes if t["destino"]], key=lambda t: t["fechada"], reverse=True)
        feitas90 = [t for t in feitas if t["fechada"] >= ini90]
        total_dest = {}
        for t in feitas90:
            d = t["destino"] or "Própria pasta"
            total_dest[d] = total_dest.get(d, 0) + 1
        mensal = [sum(1 for t in feitas if a <= t["fechada"] < b) for a, b in meses]

        # Tempo por tarefa: da criação do card até a entrega (exato quando o ClickUp tem a hora do
        # fechamento; senão, aproximado pelo dia da entrega)
        def duracao(t):
            if not t.get("criada_ms"):
                return None, True
            if t.get("fechada_ms"):
                return max(0, round((t["fechada_ms"] - t["criada_ms"]) / 60000)), False
            fim = datetime.strptime(t["fechada"], "%Y-%m-%d").replace(hour=18, tzinfo=TZ)
            return max(0, round((fim.timestamp() * 1000 - t["criada_ms"]) / 60000)), True
        tempos_tarefas = []
        for t in sorted([t for t in feitas if t["fechada"] >= dias30[0]], key=lambda t: t["fechada"], reverse=True):
            m, aprox = duracao(t)
            if m is not None:
                tempos_tarefas.append({"id": t["id"], "nome": t["nome"], "url": t["url"], "destino": t["destino"],
                                       "fechada": t["fechada"], "minutos": m, "aprox": aprox,
                                       "producao": (tempos.get(t["id"]) or {}).get("producao")})
        lead = sorted(x["minutos"] for x in tempos_tarefas)
        lead_mediano = lead[len(lead) // 2] if lead else None

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
            "fazendo": fazendo[:12],
            "tempo_medio_producao": round(sum(prod) / len(prod)) if prod else None,
            "tempo_medio_registrado": round(sum(registrado) / len(registrado)) if registrado else None,
            "fora_mes": fora_mes[:40],
            "por_destino_total": total_dest,
            "total_90": len(feitas90),
            "tempos_tarefas": tempos_tarefas[:60],
            "lead_mediano": lead_mediano,
            "lead_rapidas": sum(1 for m in lead if m <= 1440),
            "lead_total": len(lead),
            "mensal": mensal,
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
        "historico_desde": ini90,
        "meses": [MESES[int(a[5:7]) - 1] for a, _ in meses],
        "dias_janela": cfg["dias"],
        "tem_tempo_status": bool(tempos),
    }


def date_of(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def dados_reais():
    token = ler_token()
    hoje = datetime.now(TZ).date().isoformat()
    desde_ms = int((datetime.now(timezone.utc) - timedelta(days=cfg["dias"] + 2)).timestamp() * 1000)
    pessoas_tarefas = []
    # Quem tem "conta_criados" também recebe os cards que criou, mesmo sem ser responsável
    # (o Rapha cria os cards mas quase nunca se coloca como responsável). A API não filtra por
    # criador, então lemos tudo que foi alterado no período e filtramos aqui.
    recentes = []
    if any(p.get("conta_criados") for p in cfg["pessoas"]):
        recentes = buscar(None, token, include_closed=True, atualizadas_desde_ms=desde_ms)
        print(f"Workspace: {len(recentes)} tarefas alteradas no período")
    for p in cfg["pessoas"]:
        pasta = ("project_ids[]", p["folder_id"])
        responsavel = ("assignees[]", p["user_id"])
        brutas = buscar(pasta, token, include_closed=False)
        brutas += buscar(pasta, token, include_closed=True, atualizadas_desde_ms=desde_ms)
        # Cards em que a pessoa é responsável em qualquer pasta: os abertos todos (inclusive na pasta
        # do colega) e os recentes de qualquer status (pega os que foram para outras pastas)
        brutas += buscar(responsavel, token, include_closed=False)
        brutas += buscar(responsavel, token, include_closed=True, atualizadas_desde_ms=desde_ms)
        if p.get("conta_criados"):
            criados = [t for t in recentes if str((t.get("creator") or {}).get("id")) == str(p["user_id"])]
            print(f"{p['nome']}: {len(criados)} cards criados por ele no período")
            brutas += criados
        unicas = {t["id"]: simplificar(t, p) for t in brutas}
        pessoas_tarefas.append((p, list(unicas.values())))
        fora = sum(1 for t in unicas.values() if t["destino"])
        print(f"{p['nome']}: {len(unicas)} tarefas lidas, {fora} fora das pastas Duda e Raphael")
    hist = datar_entregas(pessoas_tarefas, hoje)
    # Tempo em cada status: dos cards em andamento e das entregas recentes
    limite = (date_of(hoje) - timedelta(days=31)).isoformat()
    ids = {t["id"] for _, lista in pessoas_tarefas for t in lista
           if (not t["feita"] and t["status"].lower() in EM_ANDAMENTO) or (t["feita"] and (t["fechada"] or "") >= limite)}
    tempos = tempo_em_status(ids, token)
    print(f"Tempo em status: {len(tempos)} de {len(ids)} cards")
    # Cronômetro (tempo disparado dentro do card) dos últimos 30 dias
    info = {t["id"]: {"nome": t.get("name", ""), "url": t.get("url", ""), "status": (t.get("status") or {}).get("status", "")}
            for t in recentes}
    for _, lista in pessoas_tarefas:
        for t in lista:
            info.setdefault(t["id"], {"nome": t["nome"], "url": t["url"], "status": t["status"]})
    com_tempo = {t["id"] for _, lista in pessoas_tarefas for t in lista if t.get("minutos_registrados")}
    com_tempo |= {t["id"] for t in recentes if int(t.get("time_spent") or 0) > 0}
    desde30 = int((datetime.now(timezone.utc) - timedelta(days=31)).timestamp() * 1000)
    registros = buscar_cronometro(token, desde30, com_tempo)
    dados = calcular(pessoas_tarefas, hoje, tempos, hist) | {"exemplo": False}
    return anexar_cronometro(dados, registros, info)


def anexar_cronometro(dados, registros, info):
    """Junta ao painel o tempo cronometrado de cada pessoa: por registro, por tarefa e por dia."""
    iso = lambda ms: datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()
    dia_sp = lambda ms: datetime.fromtimestamp(ms / 1000, TZ).date().isoformat()
    por_nome = {p["nome"]: p for p in cfg["pessoas"]}
    for pessoa in dados["pessoas"]:
        uid = str(por_nome[pessoa["nome"]]["user_id"])
        meus = sorted((r for r in registros if r["user"] == uid), key=lambda r: r["inicio"], reverse=True)
        sessoes = [{"tarefa": r["tarefa"], **info.get(r["tarefa"], {"nome": "(card sem acesso)", "url": "", "status": ""}),
                    "inicio": iso(r["inicio"]), "fim": iso(r["fim"]), "minutos": r["minutos"], "rodando": r["rodando"],
                    "dia": dia_sp(r["inicio"])} for r in meus]
        por_tarefa = {}
        for s in sessoes:
            t = por_tarefa.setdefault(s["tarefa"], {"tarefa": s["tarefa"], "nome": s["nome"], "url": s["url"], "status": s["status"],
                                                    "minutos": 0, "sessoes": [], "ultimo": s["inicio"], "rodando": False})
            t["minutos"] += s["minutos"]
            t["sessoes"].append({"inicio": s["inicio"], "fim": s["fim"], "minutos": s["minutos"], "rodando": s["rodando"]})
            t["rodando"] = t["rodando"] or s["rodando"]
        tarefas = sorted(por_tarefa.values(), key=lambda t: t["ultimo"], reverse=True)
        por_dia = {d: 0 for d in dados["dias30"]}
        for s in sessoes:
            if s["dia"] in por_dia:
                por_dia[s["dia"]] += s["minutos"]
        pessoa["crono"] = {
            "sessoes": sessoes[:400],
            "tarefas": tarefas[:80],
            "por_dia": [por_dia[d] for d in dados["dias30"]],
            "total_30": sum(s["minutos"] for s in sessoes),
            "rodando": [s for s in sessoes if s["rodando"]],
        }
    dados["tem_cronometro"] = bool(registros)
    return dados


HISTORICO = RAIZ / "estado" / "historico.json"


def datar_entregas(pessoas_tarefas, hoje):
    """Dá uma data de entrega às tarefas feitas que não têm data de fechamento no ClickUp.

    O histórico guarda, para cada card feito, o dia em que apareceu como feito pela primeira vez
    ("d"), a pasta para onde foi ("dest") e de quem é ("p"). Assim a data não muda quando alguém
    mexer no card depois. Guarda também os cards vistos em aberto ("__abertos__").

    Data de um card feito sem data de fechamento no ClickUp:
    - já estava no histórico: a data guardada;
    - foi visto em aberto antes e agora está feito: hoje (a entrega aconteceu agora);
    - nunca foi visto: o prazo, se já passou; senão o dia em que o card foi criado.
      (A data da última alteração não serve: muda sempre que alguém mexe no card.)
    """
    hist = json.loads(HISTORICO.read_text(encoding="utf-8")) if HISTORICO.exists() else {}
    abertos_antes = set(hist.pop("__abertos__", []))
    for tid, reg in list(hist.items()):  # formato antigo: só a data
        if not isinstance(reg, dict):
            hist[tid] = {"d": reg, "dest": None, "p": []}
    abertos_agora = set()
    for p, tarefas in pessoas_tarefas:
        for t in tarefas:
            if not t["feita"]:
                abertos_agora.add(t["id"])
                continue
            reg = hist.get(t["id"])
            if not t["fechada"]:
                if reg:
                    t["fechada"] = reg["d"]
                elif t["id"] in abertos_antes:
                    t["fechada"] = hoje
                elif t["prazo"] and t["prazo"] <= hoje:
                    t["fechada"] = t["prazo"]
                else:
                    t["fechada"] = min(t.get("criada") or hoje, hoje)
            reg = hist.setdefault(t["id"], {"d": t["fechada"], "dest": None, "p": []})
            reg["dest"] = t["destino"] or reg.get("dest")
            if p["nome"] not in reg["p"]:
                reg["p"].append(p["nome"])
    HISTORICO.parent.mkdir(exist_ok=True)
    salvar = {**hist, "__abertos__": sorted(abertos_agora - set(hist))}
    HISTORICO.write_text(json.dumps(salvar, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    print(f"Histórico: {len(hist)} entregas registradas, {len(abertos_agora)} cards em aberto acompanhados")
    return hist


def dados_exemplo():
    hoje = datetime.now(TZ).date()
    h = hoje.isoformat()
    d = lambda n: (hoje - timedelta(days=n)).isoformat()
    def t(i, nome, prazo, fechada=None, status="edição", pri=None, destino=None):
        return {"id": f"ex{i}", "nome": nome, "url": "https://app.clickup.com/", "status": status,
                "prazo": prazo, "fechada": fechada, "feita": fechada is not None, "prioridade": pri,
                "na_pasta": destino is None, "destino": destino, "status_cor": None, "minutos_registrados": 0,
                "criada_ms": int((datetime.now(timezone.utc) - timedelta(hours=6 + i * 7)).timestamp() * 1000), "fechada_ms": None}
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
    agora = datetime.now(timezone.utc)
    desde = lambda m: (agora - timedelta(minutes=m)).isoformat()
    tempos = {"ex1": {"atual": {"desde": desde(135), "minutos": 135}, "producao": 135},
              "ex2": {"atual": {"desde": desde(60 * 26), "minutos": 60 * 26}, "producao": 60 * 30},
              "ex4": {"atual": {"desde": desde(40), "minutos": 40}, "producao": 300},
              "ex40": {"atual": {"desde": desde(60 * 75), "minutos": 60 * 75}, "producao": 60 * 75},
              **{f"ex{10 + i}": {"atual": {}, "producao": 90 + i * 25} for i in range(12)},
              **{f"ex{50 + i}": {"atual": {}, "producao": 200 + i * 40} for i in range(8)}}
    hist = {f"h{i}": {"d": d(i), "dest": ["FERNANDA", "ALIANÇA", None, "SUMMIT", "ANDERSON"][i % 5], "p": ["Duda" if i % 3 else "Rapha"]}
            for i in range(60)}
    dados = calcular(pessoas, h, tempos, hist) | {"exemplo": True}
    # Sessões de cronômetro de exemplo, em horário comercial dos últimos dias
    registros, info = [], {}
    base = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    for (p, lista), uid in zip(pessoas, [cfg["pessoas"][0]["user_id"], cfg["pessoas"][1]["user_id"]]):
        for k, t in enumerate(lista[:10]):
            info[t["id"]] = {"nome": t["nome"], "url": t["url"], "status": t["status"]}
            for s in range(1 + k % 3):
                dia_ = base - timedelta(days=(k + s) % 6)
                ini = dia_.replace(hour=8 + (k * 2 + s * 3) % 10, minute=(k * 13) % 60)
                fim = ini + timedelta(minutes=25 + (k * 17 + s * 11) % 140)
                registros.append({"tarefa": t["id"], "user": str(uid), "inicio": int(ini.timestamp() * 1000),
                                  "fim": int(fim.timestamp() * 1000), "minutos": int((fim - ini).total_seconds() // 60), "rodando": False})
    return anexar_cronometro(dados, registros, info)


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
