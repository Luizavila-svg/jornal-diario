import os
import re
import json
import feedparser
from datetime import date, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from anthropic import Anthropic

CACHE_FILE = "jornal_cache.json"
CACHE_VERSION = "v2"

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
    instrucao = "Traduza para o português do Brasil. " if traduzir else ""

    prompt = (
        f"Você é um editor experiente de jornal impresso. {instrucao}"
        f"Com base nas notícias abaixo do jornal {nome} publicadas hoje, "
        "escreva um resumo editorial dividido em até 5 seções temáticas. "
        "Cada seção deve ter de 8 a 12 linhas de texto em parágrafos fluidos, "
        "linguagem jornalística clara e objetiva, sem listas ou bullet points. "
        "Reescreva os títulos como narrativa jornalística.\n\n"
        "Retorne APENAS JSON válido, sem texto antes ou depois, sem blocos de código:\n"
        '{"secoes":[{"titulo":"Nome do Tema","conteudo":"Parágrafo 1.\\n\\nParágrafo 2.","grafico":null}]}\n\n'
        "IMPORTANTE: Se e somente se uma seção mencionar dados numéricos econômicos reais "
        "(ex: inflação X%, PIB cresceu Y%, dólar a R$Z, Ibovespa em N pontos, juros X% a.a., desemprego X%), "
        "substitua null por:\n"
        '{"titulo":"Título do Gráfico","tipo":"bar","labels":["Período A","Período B"],"valores":[1.2,3.4]}\n'
        "Use apenas dados numéricos presentes nas notícias. Não invente dados.\n\n"
        f"Notícias:\n{conteudo}"
    )

    resposta = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=3000,
        messages=[{"role": "user", "content": prompt}],
    )

    text = resposta.content[0].text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    text = text.strip()

    try:
        data = json.loads(text)
        secoes = data.get("secoes", [])
        if isinstance(secoes, list) and secoes:
            return secoes
    except (json.JSONDecodeError, AttributeError, ValueError):
        pass

    return [{"titulo": "Resumo", "conteudo": text, "grafico": None}]


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
            if cache.get("data") == hoje_iso and cache.get("versao") == CACHE_VERSION:
                return cache["resumos"], cache["gerado_em"], data_formatada_pt(hoje)
        except Exception:
            pass

    def processar_feed(nome, config):
        try:
            artigos = buscar_artigos(config["url"])
            if not artigos:
                return nome, [{"titulo": "Indisponível", "conteudo": "Não foi possível carregar as notícias deste jornal hoje.", "grafico": None}]
            return nome, resumir_jornal(nome, artigos, traduzir=config["traduzir"])
        except Exception:
            return nome, [{"titulo": "Indisponível", "conteudo": "Não foi possível carregar o conteúdo deste jornal hoje.", "grafico": None}]

    resultados = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(processar_feed, nome, config): nome for nome, config in FEEDS.items()}
        for future in as_completed(futures):
            nome, secoes = future.result()
            resultados[nome] = secoes
    resumos = {nome: resultados[nome] for nome in FEEDS}

    gerado_em = datetime.now().strftime("%d/%m/%Y às %H:%M")

    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(
                {"data": hoje_iso, "versao": CACHE_VERSION, "resumos": resumos, "gerado_em": gerado_em},
                f,
                ensure_ascii=False,
                indent=2,
            )
    except Exception:
        pass

    return resumos, gerado_em, data_formatada_pt(hoje)
