"""
silver_to_gold.py
===================
Lê os Parquets da camada silver no S3 e monta o modelo estrela da camada gold,
enriquecendo cada mês com a versão de NCM vigente naquele mês.

Modelo:
  fato_importacao_mensal  : grão ano_mes x ncm x nves x pais_origem (igual à silver).
                            Carrega ncm_versao_id, a versão do NCM vigente no mês.
  dim_ncm                 : uma linha por VERSÃO do NCM (ncm_versao_id), lida de
                            data_ref/dim_ncm.csv. Um mesmo código pode ter várias linhas.
  dim_pais                : lida de data_ref/dim_pais.csv.
  dim_tempo               : gerada pelo código, do periodo_inicial até o mês corrente.

Regra de vigência:
  Cada mês da silver é associado à data de referência = último dia do mês.
  Para cada NCM, usa-se a versão em que vigencia_inicio <= data_ref <= vigencia_fim.
  Nas datas da RFB/Gecex, as mudanças caem no dia 1 do mês, então a data de
  referência escolhida não altera a versão. Mudança fora do dia 1 gera aviso.

Decisões:
  - Grão mantido igual ao da silver. nves fica na fato como chave degenerada,
    pois é necessário para a unicidade do grão.
  - nves_nm, tem_nve, nves_multiplo, capitulo_ncm e posicao_ncm não entram na fato.
    A hierarquia vem de dim_ncm, via ncm_versao_id.
  - Métricas não aditivas não são agregadas aqui. Preço médio ponderado é cálculo
    de consulta: SUM(vmle) / SUM(peso_liq).
  - Dimensões são regravadas a cada execução. Mudar uma dimensão não exige
    reprocessar a fato.

Checagens (erro interrompe o mês; aviso é registrado no log):
  dim_ncm:
    - Vigência inicial preenchida e no formato de data.
    - Início não posterior ao fim.
    - ncm_versao_id único (ncm + data de início).
    - Sem sobreposição de vigências para o mesmo NCM.
    - Aviso se alguma mudança cai fora do dia 1.
  dim_pais:
    - pais_origem_rfb único.
  por mês:
    - Chave única na fato.
    - Todo NCM tem exatamente uma versão vigente na data de referência do mês.
    - Todo pais_origem existe em dim_pais.pais_origem_rfb.
    - capitulo_ncm e posicao_ncm da silver coincidem com a versão vigente.

Uso:
    python silver_to_gold.py                      # processa meses pendentes
    python silver_to_gold.py --ano-mes 2026-08    # processa um mês
    python silver_to_gold.py --reprocessar        # regrava fatos existentes
    python silver_to_gold.py --dry-run            # simula sem gravar

Dependências:
    pip install boto3 pyyaml pandas pyarrow
"""

import argparse
import calendar
import io
import logging
import re
import sys
from datetime import date, datetime
from pathlib import Path

import boto3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml
from botocore.exceptions import BotoCoreError, ClientError


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

CHAVES = ["ano_mes", "ncm", "nves", "pais_origem"]
COLUNAS_ATRIBUTO = ["ume"]
PREFIXOS_METRICAS = ("fob_", "frete_", "seguro_", "vmle_", "peso_", "qtd_")
ADITIVAS = ["vmle_soma_dolar_truncado", "peso_liq_soma_truncado", "qtd_ume_soma_truncado"]
BLOCOS = ["bloco_mercosul", "bloco_ocde", "bloco_brics", "bloco_g20"]
FORMATOS_DATA = ["%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"]
DATA_VIGENTE = date(9999, 12, 31)   # sentinela da RFB para "sem fim"
PADRAO_ANO_MES = re.compile(r"^\d{4}-\d{2}$")
NOMES_MES = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
             "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]


# ---------------------------------------------------------------------------
# Configuração e log
# ---------------------------------------------------------------------------

def carregar_config(caminho: str) -> dict:
    p = Path(caminho)
    if not p.exists():
        raise FileNotFoundError(f"Arquivo de configuração não encontrado: {p}")
    with p.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def configurar_log(cfg: dict) -> logging.Logger:
    nivel = getattr(logging, cfg["log"]["nivel"].upper(), logging.INFO)
    logging.basicConfig(level=nivel, format=cfg["log"]["formato"])
    return logging.getLogger("silver_to_gold")


# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------

def criar_cliente_s3(cfg: dict):
    session = boto3.Session(
        profile_name=cfg["aws"]["profile"],
        region_name=cfg["aws"]["regiao"],
    )
    return session.client("s3")


def listar_silver(cliente, bucket: str, prefixo: str) -> list[dict]:
    """Lista os Parquets da silver: [{nome, chave, ano_mes}]."""
    itens = []
    paginator = cliente.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefixo):
        for obj in page.get("Contents", []):
            chave = obj["Key"]
            if not chave.endswith(".parquet"):
                continue
            match = re.search(r"ano_mes=(\d{4}-\d{2})", chave)
            if match:
                itens.append({"nome": Path(chave).name, "chave": chave,
                              "ano_mes": match.group(1)})
    return itens


def ler_parquet_s3(cliente, bucket: str, chave: str) -> pd.DataFrame:
    corpo = cliente.get_object(Bucket=bucket, Key=chave)["Body"].read()
    return pq.read_table(io.BytesIO(corpo)).to_pandas()


def gravar_parquet_s3(cliente, bucket: str, chave: str, df: pd.DataFrame,
                      dry_run: bool) -> bool:
    log = logging.getLogger("silver_to_gold")
    if dry_run:
        log.info("[DRY-RUN] Gravaria: s3://%s/%s (%d linhas)", bucket, chave, len(df))
        return True
    try:
        buffer = io.BytesIO()
        pq.write_table(pa.Table.from_pandas(df, preserve_index=False),
                       buffer, compression="snappy")
        buffer.seek(0)
        cliente.put_object(Bucket=bucket, Key=chave, Body=buffer.read(),
                           ContentType="application/octet-stream")
        log.info("Gravado: s3://%s/%s (%d linhas)", bucket, chave, len(df))
        return True
    except (BotoCoreError, ClientError) as erro:
        log.error("Falha ao gravar %s: %s", chave, erro)
        return False


# ---------------------------------------------------------------------------
# Datas
# ---------------------------------------------------------------------------

def parse_data(valor) -> date | None:
    """Converte texto de data (AAAA-MM-DD ou DD/MM/AAAA) em date. Vazio → None."""
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto or texto.lower() == "nan":
        return None
    for fmt in FORMATOS_DATA:
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            continue
    return None


def data_referencia(ano_mes: str) -> date:
    """Último dia do mês AAAA-MM."""
    ano, mes = map(int, ano_mes.split("-"))
    return date(ano, mes, calendar.monthrange(ano, mes)[1])


# ---------------------------------------------------------------------------
# Dimensões
# ---------------------------------------------------------------------------

def carregar_dimensoes(caminho_config: str, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Lê dim_ncm e dim_pais dos caminhos do config, relativos à raiz do projeto
    (pasta que contém o config.yaml). Tudo como texto, para preservar zeros
    à esquerda e códigos.
    """
    raiz = Path(caminho_config).resolve().parent
    refs = cfg["caminhos"]["dados_ref"]

    dim_ncm = pd.read_csv(raiz / refs["dim_ncm"], dtype=str, keep_default_na=False,
                          encoding="utf-8-sig")
    dim_pais = pd.read_csv(raiz / refs["dim_pais"], dtype=str, keep_default_na=False,
                           encoding="utf-8-sig")

    for col in BLOCOS:
        dim_pais[col] = dim_pais[col].str.strip().str.lower().eq("true")

    return dim_ncm, dim_pais


def preparar_dim_ncm(dim_ncm: pd.DataFrame) -> tuple[pd.DataFrame, list[str], list[str]]:
    """
    Converte datas, cria ncm_versao_id e valida as vigências.
    Retorna (dim_preparada, erros, avisos).
    """
    log = logging.getLogger("silver_to_gold")
    erros, avisos = [], []

    obrigatorias = ["ncm", "capitulo", "posicao", "vigencia_inicio", "vigencia_fim"]
    faltando = [c for c in obrigatorias if c not in dim_ncm.columns]
    if faltando:
        return dim_ncm, [f"dim_ncm sem colunas obrigatórias: {faltando}"], avisos

    df = dim_ncm.copy()
    df["_ini"] = df["vigencia_inicio"].map(parse_data)
    df["_fim"] = df["vigencia_fim"].map(lambda v: parse_data(v) or DATA_VIGENTE)

    sem_inicio = df["_ini"].isna()
    if sem_inicio.any():
        erros.append(f"dim_ncm com {sem_inicio.sum()} vigencia_inicio vazia ou fora do formato. "
                     f"Ex.: {df.loc[sem_inicio, 'ncm'].head(5).tolist()}")
        return df, erros, avisos

    invertidas = df[df["_ini"] > df["_fim"]]
    if len(invertidas):
        erros.append(f"dim_ncm com {len(invertidas)} vigência(s) com início posterior ao fim. "
                     f"Ex.: {invertidas['ncm'].head(5).tolist()}")

    df["ncm_versao_id"] = df["ncm"] + "_" + df["_ini"].map(lambda d: d.strftime("%Y%m%d"))
    dup = df["ncm_versao_id"].duplicated().sum()
    if dup:
        erros.append(f"dim_ncm com {dup} ncm_versao_id duplicado(s)")

    # Sobreposição de vigências para o mesmo NCM
    sobrepostos = []
    for ncm, g in df.sort_values(["ncm", "_ini"]).groupby("ncm"):
        ini, fim = g["_ini"].tolist(), g["_fim"].tolist()
        for k in range(1, len(g)):
            if ini[k] <= fim[k - 1]:
                sobrepostos.append(ncm)
                break
    if sobrepostos:
        erros.append(f"dim_ncm com vigências sobrepostas em {len(sobrepostos)} NCM(s). "
                     f"Ex.: {sobrepostos[:5]}")

    # Mudanças fora do dia 1 (a data de referência do mês não as separaria)
    fora_dia1 = df[(df["_ini"].map(lambda d: d.day) != 1) & (df["_ini"] > date(1900, 1, 1))]
    if len(fora_dia1):
        avisos.append(f"{len(fora_dia1)} versão(ões) de NCM começam fora do dia 1. "
                      f"O mês usa a versão vigente no último dia; revisar. "
                      f"Ex.: {fora_dia1['ncm'].head(5).tolist()}")

    # Cobertura temporal
    log.info("dim_ncm: vigências começam em %s", df["_ini"].min().isoformat())

    return df, erros, avisos


def validar_dim_pais(dim_pais: pd.DataFrame) -> list[str]:
    erros = []
    for col in ["pais_origem_rfb", "pais_nome", "iso2", "iso3"] + BLOCOS:
        if col not in dim_pais.columns:
            erros.append(f"dim_pais sem coluna obrigatória: '{col}'")
    if erros:
        return erros
    dup = dim_pais["pais_origem_rfb"].duplicated().sum()
    if dup:
        erros.append(f"dim_pais com {dup} pais_origem_rfb duplicado(s)")
    return erros


def construir_dim_tempo(periodo_inicial: str) -> pd.DataFrame:
    """Gera uma linha por mês, do periodo_inicial (AAAA-MM) até o mês corrente."""
    ano, mes = map(int, periodo_inicial.split("-"))
    hoje = date.today()

    linhas = []
    while (ano, mes) <= (hoje.year, hoje.month):
        linhas.append({
            "ano_mes":       f"{ano:04d}-{mes:02d}",
            "ano":           ano,
            "mes":           mes,
            "nome_mes":      NOMES_MES[mes - 1],
            "trimestre":     f"T{(mes - 1) // 3 + 1}",
            "ano_trimestre": f"{ano}-T{(mes - 1) // 3 + 1}",
            "primeiro_dia":  date(ano, mes, 1).isoformat(),
        })
        mes += 1
        if mes > 12:
            mes, ano = 1, ano + 1

    return pd.DataFrame(linhas)


# ---------------------------------------------------------------------------
# Fato
# ---------------------------------------------------------------------------

def versoes_vigentes(dim_ncm: pd.DataFrame, ref: date) -> pd.DataFrame:
    """Linhas de dim_ncm vigentes na data de referência."""
    return dim_ncm[(dim_ncm["_ini"] <= ref) & (dim_ncm["_fim"] >= ref)]


# Colunas que a silver precisa ter para a gold funcionar.
# Vêm do bronze_para_silver.py. Se faltar alguma, o arquivo é de versão antiga.
SILVER_OBRIGATORIAS = CHAVES + COLUNAS_ATRIBUTO + ["capitulo_ncm", "posicao_ncm"] + ADITIVAS
SILVER_METRICAS_MINIMAS = [
    "fob_kg_minimo_dolar_truncado", "fob_kg_maximo_dolar_truncado",
    "fob_ume_minimo_dolar_truncado", "fob_ume_maximo_dolar_truncado",
    "frete_minimo_dolar_truncado", "frete_maximo_dolar_truncado",
]


def validar_schema_silver(silver: pd.DataFrame, ano_mes: str) -> list[str]:
    """
    Verifica o schema de um mês da silver antes de montar a fato.
    Retorna lista de erros. Lista vazia significa que a silver está apta.
    """
    erros = []

    # 1. Colunas obrigatórias presentes (todas listadas de uma vez)
    faltando = [c for c in SILVER_OBRIGATORIAS + SILVER_METRICAS_MINIMAS
                if c not in silver.columns]
    if faltando:
        erros.append(
            f"[{ano_mes}] silver sem {len(faltando)} coluna(s) obrigatória(s): {faltando}. "
            "Arquivo provavelmente gerado por versão antiga do bronze_para_silver.py. "
            "Regere a silver deste mês antes de rodar a gold."
        )
        return erros  # sem as colunas, as checagens seguintes não podem rodar

    # 2. Métricas numéricas: texto indica que a conversão não foi feita
    metricas = [c for c in silver.columns if c.startswith(PREFIXOS_METRICAS)]
    nao_numericas = [c for c in metricas if not pd.api.types.is_numeric_dtype(silver[c])]
    if nao_numericas:
        erros.append(
            f"[{ano_mes}] {len(nao_numericas)} métrica(s) não numérica(s): {nao_numericas[:3]}. "
            "Na silver devem ser float64 (conversão feita no bronze_para_silver.py)."
        )

    # 3. Chaves de texto
    for c in ["ncm", "pais_origem", "nves"]:
        if not (pd.api.types.is_string_dtype(silver[c]) or pd.api.types.is_object_dtype(silver[c])):
            erros.append(f"[{ano_mes}] coluna '{c}' deveria ser texto, veio como {silver[c].dtype}")

    # 4. NCM com 8 caracteres (zfill feito na silver)
    fora = (silver["ncm"].astype(str).str.len() != 8).sum()
    if fora:
        erros.append(f"[{ano_mes}] {fora} NCM(s) com tamanho diferente de 8. "
                     "Verifique o zfill(8) no bronze_para_silver.py.")

    # 5. Nulos nas chaves
    for c in CHAVES:
        nulos = silver[c].isna().sum()
        if nulos:
            erros.append(f"[{ano_mes}] {nulos} nulo(s) na chave '{c}'")

    return erros


def construir_fato(silver: pd.DataFrame, dim_ncm: pd.DataFrame, dim_pais: pd.DataFrame,
                   ano_mes: str) -> tuple[pd.DataFrame, list[str], list[str]]:
    """
    Monta a fato de um mês, associando cada NCM à versão vigente no último dia
    do mês. Retorna (fato, erros, avisos).
    """
    log = logging.getLogger("silver_to_gold")
    erros, avisos = [], []

    # 0. Schema da silver: se falhar, nada abaixo é executado
    erros = validar_schema_silver(silver, ano_mes)
    if erros:
        return pd.DataFrame(), erros, avisos

    ref = data_referencia(ano_mes)

    # 1. Chave única
    dup = silver.duplicated(CHAVES).sum()
    if dup:
        erros.append(f"[{ano_mes}] {dup} linha(s) duplicada(s) na chave {CHAVES}")

    # 2. Cada NCM tem exatamente uma versão vigente na data de referência
    vig = versoes_vigentes(dim_ncm, ref)
    sem_versao = silver.loc[~silver["ncm"].isin(vig["ncm"]), "ncm"].unique()
    if len(sem_versao):
        erros.append(f"[{ano_mes}] {len(sem_versao)} NCM(s) sem versão vigente em {ref}. "
                     f"Ex.: {sorted(sem_versao)[:5]}")

    # 3. Países
    pais_sem = silver.loc[~silver["pais_origem"].isin(dim_pais["pais_origem_rfb"]),
                          "pais_origem"].unique()
    if len(pais_sem):
        erros.append(f"[{ano_mes}] {len(pais_sem)} país(es) sem correspondência em dim_pais. "
                     f"Ex.: {sorted(pais_sem)[:5]}")

    if erros:
        return pd.DataFrame(), erros, avisos

    # 4. Associa a versão vigente (validate garante uma versão por NCM no mês)
    vig_chave = vig[["ncm", "ncm_versao_id", "capitulo", "posicao"]]
    combinado = silver.merge(vig_chave, on="ncm", how="left", validate="m:1")

    # 5. Hierarquia da silver coincide com a versão vigente
    divergentes = combinado[(combinado["capitulo_ncm"] != combinado["capitulo"]) |
                            (combinado["posicao_ncm"] != combinado["posicao"])]
    if len(divergentes):
        erros.append(f"[{ano_mes}] {divergentes['ncm'].nunique()} NCM(s) com capítulo/posição "
                     f"divergentes da versão vigente. Ex.: "
                     f"{divergentes['ncm'].drop_duplicates().head(5).tolist()}")
        return pd.DataFrame(), erros, avisos

    # 6. Aviso: mudanças de versão que começam no meio do mês
    mes_ini = date(ref.year, ref.month, 1)
    no_meio = vig[(vig["_ini"] > mes_ini) & (vig["_ini"] <= ref)]
    if len(no_meio):
        avisos.append(f"[{ano_mes}] {len(no_meio)} versão(ões) começam dentro do mês. "
                      f"Linhas do mês usam a versão do último dia.")

    metricas = [c for c in silver.columns if c.startswith(PREFIXOS_METRICAS)]
    nao_aditivas = [c for c in metricas if c not in ADITIVAS]
    log.info("  [%s] ref=%s | métricas aditivas=%d | não aditivas=%d (nunca somar/média)",
             ano_mes, ref, len(ADITIVAS), len(nao_aditivas))

    fato = combinado[CHAVES + ["ncm_versao_id"] + COLUNAS_ATRIBUTO + metricas].copy()
    fato = fato.sort_values(CHAVES).reset_index(drop=True)
    return fato, erros, avisos


def dim_ncm_para_gold(dim: pd.DataFrame) -> pd.DataFrame:
    """Formata dim_ncm para a gold: datas em ISO, ncm_versao_id na primeira coluna."""
    out = dim.copy()
    out["vigencia_inicio"] = out["_ini"].map(lambda d: d.isoformat())
    out["vigencia_fim"] = out["_fim"].map(lambda d: d.isoformat())
    out = out.drop(columns=["_ini", "_fim"])
    cols = ["ncm_versao_id"] + [c for c in out.columns if c != "ncm_versao_id"]
    return out[cols].sort_values(["ncm", "vigencia_inicio"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Fluxo principal
# ---------------------------------------------------------------------------

def main(caminho_config: str, ano_mes_filtro: str | None, reprocessar: bool,
         dry_run: bool) -> None:
    cfg = carregar_config(caminho_config)
    log = configurar_log(cfg)

    bucket         = cfg["aws"]["bucket"]
    prefixo_silver = cfg["s3"]["silver"]
    prefixo_gold   = cfg["s3"]["gold"]
    # Primeiro mês coberto pela gold (config: gold.periodo_inicial).
    # Antes disso não há versões de NCM na planilha histórica.
    periodo_gold   = cfg.get("gold", {}).get("periodo_inicial", "2022-04")

    log.info("=== silver_to_gold | analise-importacoes-brasil ===")
    log.info("Bucket : %s", bucket)
    log.info("Silver : %s", prefixo_silver)
    log.info("Gold   : %s (a partir de %s)", prefixo_gold, periodo_gold)
    if ano_mes_filtro:
        log.info("Filtro : %s", ano_mes_filtro)
    if reprocessar:
        log.info("Modo   : REPROCESSAR")
    if dry_run:
        log.info("Modo   : DRY-RUN")

    # 1. Conexão
    try:
        cliente = criar_cliente_s3(cfg)
        cliente.head_bucket(Bucket=bucket)
    except ClientError as erro:
        codigo = erro.response["Error"]["Code"]
        if codigo in ("403", "AccessDenied"):
            log.error("Acesso negado. Verifique a política IAM do perfil '%s'.",
                      cfg["aws"]["profile"])
        else:
            log.error("Erro ao acessar o bucket: %s", erro)
        sys.exit(1)
    except BotoCoreError as erro:
        log.error("Erro de conexão com a AWS: %s", erro)
        sys.exit(1)

    # 2. Dimensões: carrega, prepara e valida antes de qualquer gravação
    log.info("--- Dimensões ---")
    dim_ncm_raw, dim_pais = carregar_dimensoes(caminho_config, cfg)
    dim_ncm, erros_ncm, avisos_ncm = preparar_dim_ncm(dim_ncm_raw)
    erros_dim = erros_ncm + validar_dim_pais(dim_pais)
    for a in avisos_ncm:
        log.warning(a)
    if erros_dim:
        for e in erros_dim:
            log.error(e)
        sys.exit(1)

    log.info("dim_ncm  : %d versões em %d NCMs", len(dim_ncm), dim_ncm["ncm"].nunique())
    log.info("dim_pais : %d países", len(dim_pais))

    dim_tempo = construir_dim_tempo(periodo_gold)
    log.info("dim_tempo: %d meses (%s até %s)", len(dim_tempo),
             dim_tempo["ano_mes"].iloc[0], dim_tempo["ano_mes"].iloc[-1])

    # 3. Silver: lista e filtra
    silver_itens = listar_silver(cliente, bucket, prefixo_silver)
    if not silver_itens:
        log.error("Nenhum Parquet encontrado na silver. Execute bronze_para_silver.py.")
        sys.exit(1)

    antes_gold = [i for i in silver_itens if i["ano_mes"] < periodo_gold]
    if antes_gold:
        log.info("Meses anteriores a %s ignorados (sem versões de NCM): %d",
                 periodo_gold, len(antes_gold))
    silver_itens = [i for i in silver_itens if i["ano_mes"] >= periodo_gold]

    if ano_mes_filtro:
        silver_itens = [i for i in silver_itens if i["ano_mes"] == ano_mes_filtro]
        if not silver_itens:
            log.error("Nenhum Parquet na silver para o mês %s (gold começa em %s)",
                      ano_mes_filtro, periodo_gold)
            sys.exit(1)

    # 4. Fatos já existentes na gold
    prefixo_fato = f"{prefixo_gold}fato_importacao_mensal/"
    existentes = set()
    if not reprocessar:
        paginator = cliente.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefixo_fato):
            for obj in page.get("Contents", []):
                m = re.search(r"ano_mes=(\d{4}-\d{2})", obj["Key"])
                if m:
                    existentes.add(m.group(1))

    pendentes = [i for i in silver_itens if reprocessar or i["ano_mes"] not in existentes]
    log.info("--- Resumo ---")
    log.info("Meses na silver : %d", len(silver_itens))
    log.info("A processar     : %d", len(pendentes))

    # 5. Fatos mês a mês, cada um com a versão de NCM vigente no seu período
    sucesso, falha = 0, 0
    for item in pendentes:
        ano_mes = item["ano_mes"]
        log.info("Processando fato: %s", ano_mes)

        silver = ler_parquet_s3(cliente, bucket, item["chave"])
        fato, erros, avisos = construir_fato(silver, dim_ncm, dim_pais, ano_mes)

        for a in avisos:
            log.warning(a)
        if erros:
            for e in erros:
                log.error(e)
            falha += 1
            continue

        chave_gold = (f"{prefixo_fato}ano_mes={ano_mes}/"
                      f"fato_importacao_{ano_mes.replace('-', '_')}.parquet")
        if gravar_parquet_s3(cliente, bucket, chave_gold, fato, dry_run):
            sucesso += 1
        else:
            falha += 1

    # 6. Dimensões: sempre regravadas (pequenas, sem partição)
    log.info("--- Dimensões na gold ---")
    dimensoes = {
        "dim_ncm":   dim_ncm_para_gold(dim_ncm),
        "dim_pais":  dim_pais,
        "dim_tempo": dim_tempo,
    }
    for nome, df in dimensoes.items():
        if not gravar_parquet_s3(cliente, bucket, f"{prefixo_gold}{nome}/{nome}.parquet",
                                 df, dry_run):
            falha += 1

    # 7. Relatório
    log.info("--- Resultado ---")
    log.info("Fatos gravados : %d", sucesso)
    log.info("Falhas         : %d", falha)
    if falha > 0:
        log.warning("%d item(ns) não processado(s). Verifique os erros acima.", falha)
        sys.exit(1)
    log.info("Camada gold atualizada.")


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Monta o modelo estrela da camada gold a partir da silver.",
    )
    parser.add_argument("--config", default="config.yaml",
                        help="Caminho para o config.yaml (padrão: config.yaml)")
    parser.add_argument("--ano-mes", dest="ano_mes", default=None, metavar="AAAA-MM",
                        help="Processa apenas o mês informado (ex.: 2026-08)")
    parser.add_argument("--reprocessar", action="store_true",
                        help="Regrava fatos já existentes na gold")
    parser.add_argument("--dry-run", action="store_true",
                        help="Simula sem gravar nenhum arquivo")
    args = parser.parse_args()

    if args.ano_mes and not PADRAO_ANO_MES.match(args.ano_mes):
        parser.error("--ano-mes deve estar no formato AAAA-MM")

    main(caminho_config=args.config, ano_mes_filtro=args.ano_mes,
         reprocessar=args.reprocessar, dry_run=args.dry_run)
