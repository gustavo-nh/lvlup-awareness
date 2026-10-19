#!/usr/bin/env python3
"""Confere um briefing contra o formato aprovado (spec do agente, 17/10/2026).

Uso:  python3 scripts/validar_briefing.py <arquivo.json> [--data AAAA-MM-DD] [--janela manha|noite]
Sai com codigo 0 se estiver valido, 1 se houver erro. Avisos nao reprovam.
"""
import json
import re
import sys
import unicodedata

CATEGORIAS = {"bc", "macro", "mercados", "semis", "cripto", "geopolitica", "marco_preco", "agenda"}
JANELAS = {"manha", "noite"}
MODOS = {"util", "fim_de_semana", "domingo_noite"}
CAMPOS_TOPO = {"versao", "gerado_em", "data", "janela", "modo", "fatos_relevantes",
               "moveu_mercados", "pontos_sugeridos", "nota_sugerida"}
RE_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RE_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z$")
RE_CHAVE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def tem_emoji(s):
    for ch in s:
        cp = ord(ch)
        if cp >= 0x1F000 or 0x2600 <= cp <= 0x27BF or 0x2B00 <= cp <= 0x2BFF or cp in (0xFE0F, 0x200D):
            return True
        if unicodedata.category(ch) == "So" and cp > 0x2100:
            return True
    return False


def validar(b, data=None, janela=None):
    erros, avisos = [], []

    def texto(valor, onde, obrig=True, limite=None):
        if valor is None:
            if obrig:
                erros.append(f"{onde}: obrigatorio")
            return
        if not isinstance(valor, str) or not valor.strip():
            erros.append(f"{onde}: precisa ser texto nao vazio")
            return
        if tem_emoji(valor):
            erros.append(f"{onde}: nao pode ter emoji")
        if limite and len(valor) > limite:
            (erros if len(valor) > limite + 40 else avisos).append(f"{onde}: {len(valor)} caracteres (limite ~{limite})")

    if not isinstance(b, dict):
        return ["o arquivo nao e um objeto JSON"], avisos

    for extra in sorted(set(b) - CAMPOS_TOPO):
        avisos.append(f"campo desconhecido no topo: {extra}")

    if b.get("versao") != 1:
        erros.append("versao: precisa ser 1")
    if not isinstance(b.get("gerado_em"), str) or not RE_UTC.match(b["gerado_em"]):
        erros.append("gerado_em: precisa ser ISO 8601 em UTC (ex.: 2026-10-20T11:04:00Z)")
    if not isinstance(b.get("data"), str) or not RE_DATA.match(b["data"]):
        erros.append("data: precisa ser AAAA-MM-DD")
    elif data and b["data"] != data:
        erros.append(f"data: esperado {data}, veio {b['data']}")
    if b.get("janela") not in JANELAS:
        erros.append("janela: precisa ser 'manha' ou 'noite'")
    elif janela and b["janela"] != janela:
        erros.append(f"janela: esperado {janela}, veio {b['janela']}")
    if b.get("modo") not in MODOS:
        erros.append("modo: precisa ser 'util', 'fim_de_semana' ou 'domingo_noite'")
    elif b["modo"] == "domingo_noite" and b.get("janela") != "noite":
        erros.append("modo domingo_noite exige janela 'noite'")

    for lista in ("fatos_relevantes", "moveu_mercados"):
        itens = b.get(lista)
        if not isinstance(itens, list):
            erros.append(f"{lista}: precisa ser lista")
            continue
        if len(itens) > 8:
            erros.append(f"{lista}: {len(itens)} itens (maximo 8)")
        for i, f in enumerate(itens):
            onde = f"{lista}[{i}]"
            if not isinstance(f, dict):
                erros.append(f"{onde}: precisa ser objeto")
                continue
            texto(f.get("texto"), f"{onde}.texto", limite=200)
            if f.get("categoria") not in CATEGORIAS:
                erros.append(f"{onde}.categoria: invalida ({f.get('categoria')})")
            texto(f.get("fonte"), f"{onde}.fonte", obrig=False)
            if "fonte" not in f:
                avisos.append(f"{onde}: sem fonte")
            if "url" in f and not (isinstance(f["url"], str) and f["url"].startswith(("http://", "https://"))):
                erros.append(f"{onde}.url: precisa comecar com http(s)://")
            for extra in sorted(set(f) - {"texto", "categoria", "fonte", "url"}):
                avisos.append(f"{onde}: campo desconhecido {extra}")

    pontos = b.get("pontos_sugeridos")
    chaves = set()
    if not isinstance(pontos, list):
        erros.append("pontos_sugeridos: precisa ser lista")
        pontos = []
    if len(pontos) > 6:
        erros.append(f"pontos_sugeridos: {len(pontos)} itens (maximo 6)")
    placar = 0
    for i, p in enumerate(pontos):
        onde = f"pontos_sugeridos[{i}]"
        if not isinstance(p, dict):
            erros.append(f"{onde}: precisa ser objeto")
            continue
        ch = p.get("chave")
        if not isinstance(ch, str) or not RE_CHAVE.match(ch):
            erros.append(f"{onde}.chave: slug ASCII minusculo com hifens (ex.: fed-hawkish)")
        elif ch in chaves:
            erros.append(f"{onde}.chave: repetida ({ch})")
        else:
            chaves.add(ch)
        texto(p.get("tema"), f"{onde}.tema", limite=60)
        if p.get("vies") not in ("bull", "bear", "neutro"):
            erros.append(f"{onde}.vies: precisa ser 'bull', 'bear' ou 'neutro'")
        if p.get("peso") not in (1, 2, 3) or isinstance(p.get("peso"), bool):
            erros.append(f"{onde}.peso: precisa ser 1, 2 ou 3")
        texto(p.get("porque"), f"{onde}.porque", limite=160)
        if "revisao" in p and not (isinstance(p["revisao"], str) and RE_DATA.match(p["revisao"])):
            erros.append(f"{onde}.revisao: precisa ser AAAA-MM-DD")
        for extra in sorted(set(p) - {"chave", "tema", "vies", "peso", "porque", "revisao"}):
            avisos.append(f"{onde}: campo desconhecido {extra}")
        if p.get("vies") in ("bull", "bear") and p.get("peso") in (1, 2, 3):
            placar += p["peso"] * (1 if p["vies"] == "bull" else -1)

    nota = b.get("nota_sugerida")
    if not isinstance(nota, int) or isinstance(nota, bool) or not -2 <= nota <= 2:
        erros.append("nota_sugerida: inteiro de -2 a 2")
    else:
        esperado = -2 if placar <= -5 else -1 if placar <= -2 else 0 if placar <= 1 else 1 if placar <= 4 else 2
        if nota != esperado:
            avisos.append(f"nota_sugerida {nota} difere da faixa do placar dos pontos ({placar} -> {esperado})")
    return erros, avisos


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    caminho = argv[1]
    data = argv[argv.index("--data") + 1] if "--data" in argv else None
    janela = argv[argv.index("--janela") + 1] if "--janela" in argv else None
    try:
        with open(caminho, encoding="utf-8") as f:
            b = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"REPROVADO: JSON invalido ({e})")
        return 1
    erros, avisos = validar(b, data, janela)
    for a in avisos:
        print(f"aviso: {a}")
    for e in erros:
        print(f"ERRO: {e}")
    print("REPROVADO" if erros else "OK")
    return 1 if erros else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
