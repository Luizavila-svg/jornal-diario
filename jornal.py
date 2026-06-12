import os
import re
import json
import feedparser
from datetime import date, datetime
from anthropic import Anthropic

CACHE_FILE = "jornal_cache.json"

FEEDS = {
    "O Globo": {
        "url": "https://news.google.com/rss/search?q=site:oglobo.globo.com&hl=pt-BR&gl=BR&ceid=BR:pt-419",
        "traduzir": False,
    },
    "Folha de S.Paulo": {
        "url": "https://feeds.folha.uol.com.br/emcimadahora/rss091.xml",
        "traduzir": False,
    },
    "Estadão": {
        "url": "https://www.estadao.com.br/arc/outboundfeeds/rss/?outputType=xml",
        "traduzir": False,
    },
    "The New York Times": {
        "url": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
        "traduzir": True,
    },
}

MESES = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]
DIAS_SEMANA = [
    "Segunda-feira", "Terça-feira", "Quarta-feira",
    "Quinta-feira", "Sexta-feira", "Sábado", "Domingo",
]


def _limpar_html(texto):
    return re.sub(r"<[^>]+>", "", texto or "").strip()


def buscar_artigos(feed_url, max_items=20):
    feed = feedparser.parse(feed_url)
    artigos = []
    for entry in feed.entries[:max_items]:
        titulo = _limpar_html(entry.get("title", ""))
        resumo = _limpar_html(entry.get("summary", entry.get("description", "")))[:300]
        if titulo:
            linha = f"• {titulo}"
            if resumo and resumo != titulo:
                linha += f": {resumo}"
            artigos.append(linha)
    return artigos


def resumir_jornal(nome, artigos, traduzir=False):
    client = Anthropic()
    conteudo = "\n".join(artigos)
    instrucao = "Traduza para o português do Brasil e sintetize. " if traduzir else ""

    resposta = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1500,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Você é um editor experiente de jornal impresso. {instrucao}"
                    f"Com base nos títulos e resumos abaixo do jornal {nome} publicados hoje, "
                    "escreva um resumo editorial com no máximo 20 linhas. "
                    "Destaque os assuntos mais importantes e relevantes do dia. "
                    "Escreva em parágrafos fluidos, linguagem jornalística clara e objetiva, em português do Brasil. "
                    "Não use listas ou bullet points. Reescreva os títulos em forma de narrativa. "
                    "Separe os parágrafos com uma linha em branco.\n\n"
                    f"Notícias de hoje:\n{conteudo}\n\nResumo editorial:"
                ),
            }
        ],
    )
    return resposta.content[0].text.strip()


def data_formatada_pt(d: date) -> str:
    dia_semana = DIAS_SEMANA[d.weekday()]
    return f"{dia_semana}, {d.day} de {MESES[d.month - 1]} de {d.year}"


def get_resumos_hoje():
    hoje = date.today()
    hoje_iso = hoje.isoformat()

    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE) as f:
                cache = json.load(f)
            if cache.get("data") == hoje_iso:
                return cache["resumos"], cache["gerado_em"], data_formatada_pt(hoje)
        except Exception:
            pass

    resumos = {}
    for nome, config in FEEDS.items():
        try:
            artigos = buscar_artigos(config["url"])
            if not artigos:
                resumos[nome] = "Não foi possível carregar as notícias deste jornal hoje."
            else:
                resumos[nome] = resumir_jornal(nome, artigos, traduzir=config["traduzir"])
        except Exception:
            resumos[nome] = "Não foi possível carregar o conteúdo deste jornal hoje."

    gerado_em = datetime.now().strftime("%d/%m/%Y às %H:%M")

    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(
                {"data": hoje_iso, "resumos": resumos, "gerado_em": gerado_em},
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception:
        pass

    return resumos, gerado_em, data_formatada_pt(hoje)
