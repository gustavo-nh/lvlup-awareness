#!/usr/bin/env python3
"""Busca precos, grava precos.json e confere os gatilhos do config.json.

Roda no GitHub Actions (workflow precos.yml), de hora em hora.
So usa a biblioteca padrao do Python.

Arquivos:
  config.json          (le)       ativos, gatilhos, marcos, telegram
  precos.json          (grava)    precos e marcos de cada ativo (lido pelo agente)
  estado_alertas.json  (le/grava) disparos ja feitos + ultima config valida
Segredos (opcionais): TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
"""
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARQ_CONFIG = os.path.join(RAIZ, "config.json")
ARQ_PRECOS = os.path.join(RAIZ, "precos.json")
ARQ_ESTADO = os.path.join(RAIZ, "estado_alertas.json")

BRT = timezone(timedelta(hours=-3))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
DERIBIT = "https://www.deribit.com/api/v2/public"
TIPOS = ("variacao_dia_pct", "variacao_24h_pct", "acima", "salto_dia_pct")
DIAS_DESATUALIZADO = 4      # cotacao mais velha que isso nao dispara gatilho
DIAS_ESTADO = 10            # disparos mais velhos que isso saem do estado


# ---------------------------------------------------------------- utilidades

def agora_utc():
    return datetime.now(timezone.utc)


def iso_utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_json(url, tentativas=3):
    erro = None
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            erro = e
            time.sleep(4 * (i + 1))
    raise RuntimeError(f"{url.split('?')[0]} -> {erro}")


def ler_json(caminho, padrao=None):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return padrao


def gravar_json(caminho, dados):
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
        f.write("\n")


def num_br(x, casas=2):
    s = f"{x:,.{casas}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def pct_br(x):
    sinal = "+" if x >= 0 else "−"
    return f"{sinal}{num_br(abs(x), 1)}%"


# ---------------------------------------------------------------- config

def validar_config(c):
    if not isinstance(c, dict):
        raise ValueError("config nao e um objeto JSON")
    ativos = c.get("ativos")
    if not isinstance(ativos, dict) or not ativos:
        raise ValueError("'ativos' ausente ou vazio")
    for nome, a in ativos.items():
        if not isinstance(a, dict) or a.get("fonte") not in ("yahoo", "deribit") or not isinstance(a.get("simbolo"), str):
            raise ValueError(f"ativo {nome}: precisa de 'fonte' (yahoo|deribit) e 'simbolo'")
    for g in c.get("gatilhos_preco", []):
        if g.get("ativo") not in ativos:
            raise ValueError(f"gatilho com ativo desconhecido: {g.get('ativo')}")
        if g.get("tipo") not in TIPOS:
            raise ValueError(f"gatilho {g.get('ativo')}: tipo invalido {g.get('tipo')}")
        chaves = ("queda", "alta") if g["tipo"].startswith("variacao") else ("valor",)
        if not any(k in g for k in chaves):
            raise ValueError(f"gatilho {g['ativo']} {g['tipo']}: falta {' ou '.join(chaves)}")
        for k in chaves:
            if k in g and not isinstance(g[k], (int, float)):
                raise ValueError(f"gatilho {g['ativo']} {g['tipo']}: '{k}' precisa ser numero")
    meses = c.get("marcos", {}).get("meses", 3)
    if not isinstance(meses, (int, float)) or meses <= 0:
        raise ValueError("'marcos.meses' precisa ser numero positivo")
    return c


# ---------------------------------------------------------------- marcos

def calcular_marcos(barras, meses, mensais=None, ultimas=5):
    """barras: lista diaria ordenada de dicts {data, max, min, fech}.
    mensais: barras mensais (Yahoo, historico completo) para o ATH; None = so diario.
    Devolve (resumo, marcos_recentes) olhando as `ultimas` barras."""
    if not barras:
        return {}, []
    dias = int(round(meses * 30.5))
    primeira = date.fromisoformat(barras[0]["data"])
    marcos = []
    for i in range(max(0, len(barras) - ultimas), len(barras)):
        b = barras[i]
        d = date.fromisoformat(b["data"])
        corte = d - timedelta(days=dias)
        janela = [x for x in barras[:i] if date.fromisoformat(x["data"]) >= corte]
        anteriores_max = [x["max"] for x in barras[:i]]
        if mensais is not None:
            anteriores_max += [x["max"] for x in mensais if x["data"][:7] < b["data"][:7]]
        if anteriores_max and b["max"] > max(anteriores_max):
            marcos.append({"data": b["data"], "tipo": "ath", "valor": b["max"]})
        elif primeira <= corte and janela and b["max"] > max(x["max"] for x in janela):
            marcos.append({"data": b["data"], "tipo": f"max_{meses}m", "valor": b["max"]})
        if primeira <= corte and janela and b["min"] < min(x["min"] for x in janela):
            marcos.append({"data": b["data"], "tipo": f"min_{meses}m", "valor": b["min"]})

    ultima = date.fromisoformat(barras[-1]["data"])
    janela = [x for x in barras if date.fromisoformat(x["data"]) >= ultima - timedelta(days=dias)]
    todos = barras + (mensais or [])
    topo = max(todos, key=lambda x: x["max"])
    resumo = {
        f"max_{meses}m": max(x["max"] for x in janela),
        f"min_{meses}m": min(x["min"] for x in janela),
        "ath": topo["max"],
        "ath_data": topo["data"] if mensais is None or topo in barras else topo["data"][:7],
    }
    return resumo, marcos


# ---------------------------------------------------------------- fontes

def _barras_yahoo(res, gmtoffset):
    q = res["indicators"]["quote"][0]
    out = []
    for ts, h, l, c in zip(res.get("timestamp", []), q.get("high", []), q.get("low", []), q.get("close", [])):
        if None in (ts, h, l, c):
            continue
        d = datetime.fromtimestamp(ts + gmtoffset, timezone.utc).date().isoformat()
        if out and out[-1]["data"] == d:
            out[-1] = {"data": d, "max": h, "min": l, "fech": c}
        else:
            out.append({"data": d, "max": h, "min": l, "fech": c})
    return out


def fonte_yahoo(simbolo, meses):
    base = "https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(simbolo)
    diario = get_json(base + "?range=1y&interval=1d")["chart"]["result"][0]
    mensal = get_json(base + "?range=max&interval=1mo")["chart"]["result"][0]
    meta = diario["meta"]
    gmt = meta.get("gmtoffset", 0) or 0
    barras = _barras_yahoo(diario, gmt)
    mensais = _barras_yahoo(mensal, gmt)
    ultimo = meta.get("regularMarketPrice")
    hora = meta.get("regularMarketTime")
    if ultimo is None or hora is None or not barras:
        raise RuntimeError("resposta do Yahoo sem preco")
    sessao = datetime.fromtimestamp(hora + gmt, timezone.utc).date().isoformat()

    # fechamento anterior: tenta o range=1d (campo oficial); senao, pelas barras diarias
    anterior = None
    try:
        m1 = get_json(base + "?range=1d&interval=1d")["chart"]["result"][0]["meta"]
        anterior = m1.get("previousClose") or m1.get("chartPreviousClose")
    except RuntimeError:
        pass
    if anterior is None:
        anteriores = [b for b in barras if b["data"] < sessao]
        anterior = anteriores[-1]["fech"] if anteriores else None
    # a barra da sessao atual reflete o ultimo preco
    if barras[-1]["data"] == sessao:
        barras[-1]["fech"] = ultimo
        barras[-1]["max"] = max(barras[-1]["max"], ultimo)
        barras[-1]["min"] = min(barras[-1]["min"], ultimo)

    resumo, marcos = calcular_marcos(barras, meses, mensais)
    hora_dt = datetime.fromtimestamp(hora, timezone.utc)
    return {
        "ultimo": ultimo,
        "hora_ultimo": iso_utc(hora_dt),
        "sessao": sessao,
        "fechamento_anterior": anterior,
        "variacao_dia_pct": round((ultimo / anterior - 1) * 100, 2) if anterior else None,
        **resumo,
        "marcos_recentes": marcos,
        "desatualizado": (agora_utc() - hora_dt).days >= DIAS_DESATUALIZADO,
    }


def fonte_deribit_ticker(simbolo, meses):
    t = get_json(f"{DERIBIT}/ticker?instrument_name={urllib.parse.quote(simbolo)}")["result"]
    agora = int(time.time() * 1000)
    inicio = int(datetime(2019, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    c = get_json(f"{DERIBIT}/get_tradingview_chart_data?instrument_name={urllib.parse.quote(simbolo)}"
                 f"&start_timestamp={inicio}&end_timestamp={agora}&resolution=1D")["result"]
    barras = []
    for ts, h, l, f in zip(c.get("ticks", []), c.get("high", []), c.get("low", []), c.get("close", [])):
        d = datetime.fromtimestamp(ts / 1000, timezone.utc).date().isoformat()
        barras.append({"data": d, "max": h, "min": l, "fech": f})
    ultimo = t["last_price"]
    if barras:
        barras[-1]["fech"] = ultimo
        barras[-1]["max"] = max(barras[-1]["max"], ultimo)
        barras[-1]["min"] = min(barras[-1]["min"], ultimo)
    resumo, marcos = calcular_marcos(barras, meses)
    var24 = (t.get("stats") or {}).get("price_change")
    hora_dt = datetime.fromtimestamp(t["timestamp"] / 1000, timezone.utc)
    return {
        "ultimo": ultimo,
        "hora_ultimo": iso_utc(hora_dt),
        "sessao": hora_dt.astimezone(BRT).date().isoformat(),
        "variacao_24h_pct": round(var24, 2) if var24 is not None else None,
        **resumo,
        "marcos_recentes": marcos,
        "desatualizado": False,
    }


def fonte_deribit_dvol(simbolo):
    moeda = simbolo.split("-")[0].upper()
    agora = int(time.time() * 1000)
    inicio = agora - 3 * 86400 * 1000
    dados = get_json(f"{DERIBIT}/get_volatility_index_data?currency={moeda}"
                     f"&start_timestamp={inicio}&end_timestamp={agora}&resolution=3600")["result"]["data"]
    if not dados:
        raise RuntimeError("Deribit sem dados de DVOL")
    dados.sort(key=lambda x: x[0])
    ts, _, _, _, ultimo = dados[-1]
    alvo = ts - 86400 * 1000
    antes = [x for x in dados if x[0] <= alvo]
    ref = antes[-1][4] if antes else None
    hora_dt = datetime.fromtimestamp(ts / 1000, timezone.utc)
    return {
        "ultimo": ultimo,
        "hora_ultimo": iso_utc(hora_dt),
        "sessao": hora_dt.astimezone(BRT).date().isoformat(),
        "variacao_24h_pct": round((ultimo / ref - 1) * 100, 2) if ref else None,
        "desatualizado": False,
    }


def buscar(ativo, meses):
    if ativo["fonte"] == "yahoo":
        return fonte_yahoo(ativo["simbolo"], meses)
    if ativo["simbolo"].upper().endswith("-DVOL"):
        return fonte_deribit_dvol(ativo["simbolo"])
    return fonte_deribit_ticker(ativo["simbolo"], meses)


# ---------------------------------------------------------------- gatilhos

def conferir_gatilhos(cfg, ativos):
    disparos = []
    for g in cfg.get("gatilhos_preco", []):
        a = ativos.get(g["ativo"])
        if not a or a.get("erro") or a.get("desatualizado"):
            continue
        tipo = g["tipo"]
        if tipo in ("variacao_dia_pct", "variacao_24h_pct"):
            v = a.get(tipo)
            if v is None:
                continue
            onde = "no dia" if tipo == "variacao_dia_pct" else "em 24 h"
            if "queda" in g and v <= -abs(g["queda"]):
                disparos.append((g, "queda", f"\U0001F534 {g['ativo']} {pct_br(v)} {onde} (gatilho: queda > {num_br(abs(g['queda']), 1)}%) · {num_br(a['ultimo'])}"))
            if "alta" in g and v >= abs(g["alta"]):
                disparos.append((g, "alta", f"\U0001F7E2 {g['ativo']} {pct_br(v)} {onde} (gatilho: alta > {num_br(abs(g['alta']), 1)}%) · {num_br(a['ultimo'])}"))
        elif tipo == "acima":
            if a["ultimo"] > g["valor"]:
                disparos.append((g, "acima", f"⚠️ {g['ativo']} em {num_br(a['ultimo'])} (gatilho: acima de {num_br(g['valor'], 1)})"))
        elif tipo == "salto_dia_pct":
            v = a.get("variacao_dia_pct", a.get("variacao_24h_pct"))
            if v is not None and v >= g["valor"]:
                disparos.append((g, "salto", f"⚠️ {g['ativo']} {pct_br(v)} no dia (gatilho: salto > {num_br(g['valor'], 1)}%) · {num_br(a['ultimo'])}"))
    return disparos


def telegram(texto, cfg):
    tg = cfg.get("telegram", {})
    if not tg.get("ativo", True):
        return "telegram_desligado"
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return "sem_bot"
    corpo = urllib.parse.urlencode({"chat_id": chat, "text": texto, "disable_web_page_preview": "true"}).encode()
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=corpo)
        with urllib.request.urlopen(req, timeout=20):
            return "enviado"
    except Exception as e:  # noqa: BLE001
        print(f"Falha ao enviar ao Telegram: {e}")
        return "falhou"


# ---------------------------------------------------------------- principal

def main():
    agora = agora_utc()
    estado = ler_json(ARQ_ESTADO, {}) or {}
    estado.setdefault("versao", 1)
    estado.setdefault("disparos", {})

    # config: usa a ultima valida se a atual estiver quebrada
    bruto = open(ARQ_CONFIG, encoding="utf-8").read() if os.path.exists(ARQ_CONFIG) else ""
    try:
        cfg = validar_config(json.loads(bruto))
        estado["ultima_config_valida"] = cfg
    except (ValueError, json.JSONDecodeError) as e:
        print(f"config.json invalido: {e}")
        cfg = estado.get("ultima_config_valida")
        if not cfg:
            print("Nenhuma config valida anterior. Abortando.")
            return 1
        chave = "config_invalida|" + hashlib.sha1(bruto.encode()).hexdigest()[:12]
        if chave not in estado["disparos"]:
            prefixo = cfg.get("telegram", {}).get("prefixo", "[Awareness]")
            r = telegram(f"{prefixo} ⚠️ config.json com erro ({e}). Usando a ultima versao valida.", cfg)
            if r != "falhou":
                estado["disparos"][chave] = {"em": iso_utc(agora), "status": r}

    meses = cfg.get("marcos", {}).get("meses", 3)
    anterior = (ler_json(ARQ_PRECOS, {}) or {}).get("ativos", {})
    ativos, erros = {}, []
    for nome, a in cfg["ativos"].items():
        try:
            ativos[nome] = {"fonte": a["fonte"], "simbolo": a["simbolo"], **buscar(a, meses), "erro": None}
            print(f"{nome}: ok ({ativos[nome]['ultimo']})")
        except Exception as e:  # noqa: BLE001
            msg = str(e)[:300]
            print(f"{nome}: ERRO {msg}")
            erros.append(f"{nome}: {msg}")
            velho = dict(anterior.get(nome, {}))
            velho.update({"fonte": a["fonte"], "simbolo": a["simbolo"], "erro": msg, "desatualizado": True})
            ativos[nome] = velho

    # gatilhos
    marcos_cfg = set(cfg.get("marcos", {}).get("ativos", []))
    for nome, a in ativos.items():
        if nome not in marcos_cfg:
            a.pop("marcos_recentes", None)
    prefixo = cfg.get("telegram", {}).get("prefixo", "[Awareness]")
    novos = []
    for g, lado, texto in conferir_gatilhos(cfg, ativos):
        chave = f"{g['ativo']}|{g['tipo']}|{lado}|{ativos[g['ativo']]['sessao']}"
        if chave not in estado["disparos"]:
            novos.append((chave, texto))
    if novos:
        mensagem = prefixo + "\n" + "\n".join(t for _, t in novos)
        print("Disparos:\n" + mensagem)
        r = telegram(mensagem, cfg)
        if r != "falhou":
            for chave, texto in novos:
                estado["disparos"][chave] = {"em": iso_utc(agora), "status": r, "texto": texto}

    # limpa estado antigo
    limite = agora - timedelta(days=DIAS_ESTADO)
    estado["disparos"] = {k: v for k, v in estado["disparos"].items()
                          if datetime.strptime(v["em"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) >= limite}

    gravar_json(ARQ_PRECOS, {
        "versao": 1,
        "gerado_em": iso_utc(agora),
        "gerado_em_brt": agora.astimezone(BRT).strftime("%Y-%m-%d %H:%M"),
        "marcos_meses": meses,
        "ativos": ativos,
        "erros": erros,
    })
    gravar_json(ARQ_ESTADO, estado)

    if erros and len(erros) == len(cfg["ativos"]):
        print("Todas as fontes falharam.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
