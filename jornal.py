import re
import json
import feedparser
from datetime import date, datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed
from anthropic import Anthropic

TZ_BR = ZoneInfo("America/Sao_Paulo")

CACHE_VERSION = "v4"
_cache: dict = {}

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


def _parse_json_response(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
    return text.strip()


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
        f"Você é um editor sênior e analista do jornal {nome}. {instrucao}"
        "Com base nas notícias abaixo publicadas hoje, produza um resumo editorial "
        "dividido em até 5 seções temáticas.\n\n"
        "Para cada seção:\n"
        "• Texto jornalístico fluido de 8-12 linhas em parágrafos (sem listas ou bullets)\n"
        "• Campo 'analise' com: riscos identificados, sinais relevantes (econômicos/políticos/sociais) "
        "e perspectiva analítica (o que especialistas e analistas tipicamente argumentam sobre esse tipo de evento)\n\n"
        "Retorne APENAS JSON válido, sem texto antes ou depois, sem blocos de código:\n"
        '{"secoes":[{"titulo":"Nome do Tema","conteudo":"Parágrafo 1.\\n\\nParágrafo 2.",'
        '"analise":{"riscos":"Riscos identificados...","sinais":"Indicadores e tendências relevantes...",'
        '"perspectiva":"Visão analítica e contexto especializado..."},"grafico":null}]}\n\n'
        "GRÁFICO: Se e somente se uma seção mencionar dados numéricos REAIS "
        "(ex: inflação X%, PIB Y%, dólar R$Z, Ibovespa N pontos, juros X% a.a., desemprego X%), "
        "substitua null por:\n"
        '{"titulo":"Título do Gráfico","tipo":"bar|line|pie","labels":["Período A","Período B"],"valores":[1.2,3.4]}\n'
        "Use apenas dados numéricos presentes nas notícias. Não invente dados.\n\n"
        f"Notícias:\n{conteudo}"
    )

    resposta = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=3500,
        messages=[{"role": "user", "content": prompt}],
    )

    text = _parse_json_response(resposta.content[0].text)

    try:
        data = json.loads(text)
        secoes = data.get("secoes", [])
        if isinstance(secoes, list) and secoes:
            return secoes
    except (json.JSONDecodeError, AttributeError, ValueError):
        pass

    return [{"titulo": "Resumo", "conteudo": text, "analise": None, "grafico": None}]


def gerar_destaque_dia(artigos_por_fonte):
    """Analisa manchetes de todos os jornais e identifica os 3 grandes destaques do dia."""
    todas = []
    for fonte, artigos in artigos_por_fonte.items():
        for a in artigos[:12]:
            todas.append(f"[{fonte}] {a}")

    if not todas:
        return []

    conteudo = "\n".join(todas)

    prompt = (
        "Você é um editor-chefe e analista sênior examinando as manchetes dos 4 principais jornais do dia.\n"
        "Identifique os 3 assuntos de maior destaque — priorizando histórias que aparecem em múltiplas "
        "publicações ou têm impacto significativo para o Brasil e o mundo.\n\n"
        "Para cada assunto forneça:\n"
        "• 'titulo': manchete forte e impactante (até 12 palavras)\n"
        "• 'resumo': análise jornalística abrangente em 2-3 parágrafos fluidos, citando as diferentes perspectivas\n"
        "• 'analise_especializada': síntese do que economistas, cientistas políticos ou especialistas do setor "
        "argumentam sobre esse tipo de evento — baseado em frameworks analíticos e precedentes históricos\n"
        "• 'riscos': principais riscos e preocupações identificados a curto e médio prazo\n"
        "• 'sinais': indicadores-chave e tendências a monitorar nos próximos dias\n"
        "• 'grafico': null, ou dados reais mencionados nas notícias\n\n"
        "Retorne APENAS JSON válido, sem texto antes ou depois:\n"
        '{"destaques":[{"titulo":"...","resumo":"Parágrafo 1...\\n\\nParágrafo 2...",'
        '"analise_especializada":"...","riscos":"...","sinais":"...","grafico":null}]}\n\n'
        "Para gráfico com dados reais:\n"
        '{"titulo":"...","tipo":"bar|line|pie","labels":[...],"valores":[...]}\n\n'
        f"Notícias:\n{conteudo}"
    )

    try:
        resposta = Anthropic().messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = _parse_json_response(resposta.content[0].text)
        data = json.loads(text)
        return data.get("destaques", [])
    except Exception:
        return []


def data_formatada_pt(d: date) -> str:
    dia_semana = DIAS_SEMANA[d.weekday()]
    return f"{dia_semana}, {d.day} de {MESES[d.month - 1]} de {d.year}"


def get_resumos_hoje():
    hoje = datetime.now(TZ_BR).date()
    hoje_iso = hoje.isoformat()

    if (_cache.get("data") == hoje_iso and _cache.get("versao") == CACHE_VERSION):
        return _cache["resumos"], _cache["destaques"], _cache["gerado_em"], data_formatada_pt(hoje), _cache.get("manchetes", {})

    # Busca todos os feeds primeiro (em paralelo)
    artigos_por_fonte = {}
    manchetes_por_fonte = {}

    def fetch_feed(nome, config):
        try:
            feed = feedparser.parse(config["url"])
            artigos = []
            manchetes = []
            for entry in feed.entries[:20]:
                titulo = _limpar_html(entry.get("title", ""))
                resumo = _limpar_html(entry.get("summary", entry.get("description", "")))[:220]
                if titulo:
                    linha = f"• {titulo}"
                    if resumo and resumo != titulo:
                        linha += f": {resumo}"
                    artigos.append(linha)
                    if len(manchetes) < 6:
                        manchetes.append({
                            "titulo": titulo,
                            "resumo": resumo if resumo != titulo else "",
                        })
            return nome, artigos, manchetes
        except Exception:
            return nome, [], []

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fetch_feed, nome, config): nome for nome, config in FEEDS.items()}
        for future in as_completed(futures):
            nome, artigos, manchetes = future.result()
            artigos_por_fonte[nome] = artigos
            manchetes_por_fonte[nome] = manchetes

    # Gera resumos individuais + análise cruzada em paralelo
    def processar_feed(nome, config):
        artigos = artigos_por_fonte.get(nome, [])
        if not artigos:
            return nome, [{"titulo": "Indisponível", "conteudo": "Não foi possível carregar as notícias deste jornal hoje.", "analise": None, "grafico": None}]
        try:
            return nome, resumir_jornal(nome, artigos, traduzir=config["traduzir"])
        except Exception:
            return nome, [{"titulo": "Indisponível", "conteudo": "Não foi possível carregar o conteúdo deste jornal hoje.", "analise": None, "grafico": None}]

    resultados = {}
    destaques = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures_resumos = {executor.submit(processar_feed, nome, config): nome for nome, config in FEEDS.items()}
        future_destaque = executor.submit(gerar_destaque_dia, artigos_por_fonte)

        for future in as_completed(futures_resumos):
            nome, secoes = future.result()
            resultados[nome] = secoes

        try:
            destaques = future_destaque.result()
        except Exception:
            destaques = []

    resumos = {nome: resultados.get(nome, []) for nome in FEEDS}
    gerado_em = datetime.now(TZ_BR).strftime("%d/%m/%Y às %H:%M")

    _cache.update({
        "data": hoje_iso,
        "versao": CACHE_VERSION,
        "resumos": resumos,
        "destaques": destaques,
        "gerado_em": gerado_em,
        "manchetes": manchetes_por_fonte,
    })

    return resumos, destaques, gerado_em, data_formatada_pt(hoje), manchetes_por_fonte
