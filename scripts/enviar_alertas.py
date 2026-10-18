#!/usr/bin/env python3
"""Envia ao Telegram os alertas de noticia do alerta.json que ainda nao foram enviados.

Uso:
  python3 scripts/enviar_alertas.py                 envia os pendentes (GitHub Actions)
  python3 scripts/enviar_alertas.py --validar ARQ   so confere o formato (usado pelo agente)

Formato do alerta.json:
{
  "versao": 1,
  "atualizado_em": "2026-10-20T13:12:00Z",
  "alertas": [            # mais novo primeiro, no maximo 20
    { "id": "2026-10-20-circuit-breaker-coreia", "gerado_em": "2026-10-20T13:12:00Z",
      "categoria": "mercados", "titulo": "...", "texto": "...",
      "fonte": "Reuters", "url": "https://...", "teste": false }
  ]
}
Segredos: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
"""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from precos import ARQ_CONFIG, ARQ_ESTADO, RAIZ, gravar_json, iso_utc, ler_json, telegram  # noqa: E402
from validar_briefing import tem_emoji  # noqa: E402

ARQ_ALERTA = os.path.join(RAIZ, "alerta.json")
CATEGORIAS = {"bc", "macro", "mercados", "semis", "cripto", "geopolitica", "financeiro"}
RE_ID = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(-[a-z0-9]+)*$")
RE_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z$")
MAX_ALERTAS = 20
HORAS_VALIDADE = 24     # alerta pendente mais velho que isso nao e enviado (so registrado)


def validar(dados):
    erros = []
    if not isinstance(dados, dict) or dados.get("versao") != 1:
        return ["precisa ser objeto com versao 1"]
    alertas = dados.get("alertas")
    if not isinstance(alertas, list):
        return ["'alertas' precisa ser lista"]
    if len(alertas) > MAX_ALERTAS:
        erros.append(f"{len(alertas)} alertas (maximo {MAX_ALERTAS}; apague os mais antigos)")
    ids = set()
    for i, a in enumerate(alertas):
        onde = f"alertas[{i}]"
        if not isinstance(a, dict):
            erros.append(f"{onde}: precisa ser objeto")
            continue
        aid = a.get("id")
        if not isinstance(aid, str) or not RE_ID.match(aid):
            erros.append(f"{onde}.id: formato AAAA-MM-DD-slug (ex.: 2026-10-20-circuit-breaker-coreia)")
        elif aid in ids:
            erros.append(f"{onde}.id repetido: {aid}")
        else:
            ids.add(aid)
        if not isinstance(a.get("gerado_em"), str) or not RE_UTC.match(a["gerado_em"]):
            erros.append(f"{onde}.gerado_em: ISO 8601 em UTC")
        if a.get("categoria") not in CATEGORIAS:
            erros.append(f"{onde}.categoria invalida: {a.get('categoria')}")
        for campo, limite in (("titulo", 80), ("texto", 280), ("fonte", 60)):
            v = a.get(campo)
            if not isinstance(v, str) or not v.strip():
                erros.append(f"{onde}.{campo}: obrigatorio")
            elif len(v) > limite:
                erros.append(f"{onde}.{campo}: {len(v)} caracteres (maximo {limite})")
            elif tem_emoji(v):
                erros.append(f"{onde}.{campo}: sem emoji")
        if "url" in a and not (isinstance(a["url"], str) and a["url"].startswith(("http://", "https://"))):
            erros.append(f"{onde}.url: precisa comecar com http(s)://")
        if "teste" in a and not isinstance(a["teste"], bool):
            erros.append(f"{onde}.teste: true ou false")
    return erros


def montar(a, prefixo):
    titulo = ("\U0001F9EA TESTE — " if a.get("teste") else "\U0001F6A8 ") + a["titulo"]
    linhas = [f"{prefixo} {titulo}", a["texto"]]
    rodape = a["fonte"] + (f" · {a['url']}" if a.get("url") else "")
    linhas.append(rodape)
    return "\n".join(linhas)


def main(argv):
    if "--validar" in argv:
        caminho = argv[argv.index("--validar") + 1]
        try:
            with open(caminho, encoding="utf-8") as f:
                erros = validar(json.load(f))
        except (OSError, json.JSONDecodeError) as e:
            erros = [f"JSON invalido: {e}"]
        for e in erros:
            print(f"ERRO: {e}")
        print("REPROVADO" if erros else "OK")
        return 1 if erros else 0

    dados = ler_json(ARQ_ALERTA)
    if dados is None:
        print("sem alerta.json")
        return 0
    erros = validar(dados)
    if erros:
        print("alerta.json invalido:\n" + "\n".join(erros))
        return 1

    cfg = ler_json(ARQ_CONFIG, {}) or {}
    estado = ler_json(ARQ_ESTADO, {}) or {}
    estado.setdefault("versao", 1)
    estado.setdefault("disparos", {})
    prefixo = cfg.get("telegram", {}).get("prefixo", "[Awareness]")
    agora = datetime.now(timezone.utc)

    pendentes = [a for a in dados["alertas"] if f"noticia|{a['id']}" not in estado["disparos"]]
    for a in reversed(pendentes):           # mais antigo primeiro
        chave = f"noticia|{a['id']}"
        gerado = datetime.strptime(a["gerado_em"][:16], "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
        if agora - gerado > timedelta(hours=HORAS_VALIDADE):
            status = "expirado"
        else:
            status = telegram(montar(a, prefixo), cfg)
        print(f"{a['id']}: {status}")
        if status == "falhou":
            continue                        # tenta de novo na proxima execucao
        estado["disparos"][chave] = {"em": iso_utc(agora), "status": status, "texto": a["titulo"]}

    gravar_json(ARQ_ESTADO, estado)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
