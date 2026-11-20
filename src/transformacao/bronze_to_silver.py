"""
bronze_to_silver.py
=====================
Lê os arquivos Parquet da camada bronze no S3, aplica as regras de
tratamento definidas para a camada silver e salva o resultado de volta
no S3 — particionado por ano_mes.

Regras aplicadas (conforme documentação da Receita Federal):
  Tipagem     : colunas numéricas convertidas para float64; textos normalizados.
  NCM         : zfill(8) — zeros à esquerda perdidos na leitura como número.
  UME         : vazio → "NÃO INFORMADO".
  Derivadas   : capitulo_ncm, posicao_ncm, tem_nve, nves_multiplo.
  Schema      : colunas obrigatórias, chave única, formato ano_mes.
  Negócio     : min <= max, quartil1 <= quartil3, somas > 0.

O que NÃO é feito aqui:
  - Agregações (ficam na gold).
  - Preço médio ponderado (exige soma de múltiplas linhas — gold).
  - Join com dim_pais ou dim_ncm (gold).
  - Filtro de linhas com S/NVE (decisão do notebook).

Uso:
    python bronze_to_silver.py                      # processa todos os pendentes
    python bronze_to_silver.py --ano-mes 2026-08    # processa um mês específico
    python bronze_to_silver.py --reprocessar        # regrava os já existentes
    python bronze_to_silver.py --dry-run            # simula sem gravar

Dependências:
    pip install boto3 pyyaml pandas pyarrow
"""

import argparse
import io
import logging
import re
import sys
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

# Colunas que devem ser convertidas para float64
COLUNAS_NUMERICAS = [
    "fob_kg_minimo_dolar_truncado",
    "fob_kg_maximo_dolar_truncado",
    "fob_kg_media_dolar_truncado",
    "fob_kg_mediana_dolar_truncado",
    "fob_kg_desvio_padrao_dolar_truncado",
    "fob_kg_quartil1_truncado",
    "fob_kg_quartil3_truncado",
    "fob_ume_minimo_dolar_truncado",
    "fob_ume_maximo_dolar_truncado",
    "fob_ume_media_dolar_truncado",
    "fob_ume_mediana_dolar_truncado",
    "fob_ume_desvio_padrao_dolar_truncado",
    "fob_ume_quartil1_truncado",
    "fob_ume_quartil3_truncado",
    "vmle_soma_dolar_truncado",
    "peso_liq_soma_truncado",
    "qtd_ume_soma_truncado",
    "frete_minimo_dolar_truncado",
    "frete_media_dolar_truncado",
    "frete_mediana_dolar_truncado",
    "frete_maximo_dolar_truncado",
    "frete_desvio_padrao_dolar_truncado",
    "seguro_minimo_dolar_truncado",
    "seguro_media_dolar_truncado",
    "seguro_mediana_dolar_truncado",
    "seguro_maximo_dolar_truncado",
    "seguro_desvio_padrao_dolar_truncado",
]

PADRAO_ANO_MES = re.compile(r"^\d{4}-\d{2}$")


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
    return logging.getLogger("bronze_to_silver")


# ---------------------------------------------------------------------------
# S3
# ---------------------------------------------------------------------------

def criar_cliente_s3(cfg: dict) -> boto3.client:
    session = boto3.Session(
        profile_name=cfg["aws"]["profile"],
        region_name=cfg["aws"]["regiao"],
    )
    return session.client("s3")


def testar_bucket(cliente, bucket: str, perfil: str) -> None:
    log = logging.getLogger("bronze_to_silver")
    try:
        cliente.head_bucket(Bucket=bucket)
    except ClientError as e:
        codigo = e.response["Error"]["Code"]
        if codigo == "404":
            log.error("Bucket não encontrado: %s", bucket)
        elif codigo in ("403", "AccessDenied"):
            log.error(
                "Acesso negado ao bucket '%s'. Verifique a política IAM do perfil '%s'.",
                bucket, perfil,
            )
        else:
            log.error("Erro ao acessar o bucket: %s", e)
        sys.exit(1)
    except BotoCoreError as e:
        log.error("Erro de conexão com a AWS: %s", e)
        sys.exit(1)


def listar_parquets(cliente, bucket: str, prefixo: str) -> list[dict]:
    """Lista Parquets em um prefixo. Retorna lista de {nome, chave, ano_mes}."""
    itens = []
    paginator = cliente.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefixo):
        for obj in page.get("Contents", []):
            chave = obj["Key"]
            nome = Path(chave).name
            if not nome.endswith(".parquet"):
                continue
            # Extrai ano_mes da partição: .../ano_mes=2026-08/arquivo.parquet
            match = re.search(r"ano_mes=(\d{4}-\d{2})", chave)
            ano_mes = match.group(1) if match else None
            itens.append({"nome": nome, "chave": chave, "ano_mes": ano_mes})
    return itens


def baixar_parquet(cliente, bucket: str, chave: str) -> pd.DataFrame:
    response = cliente.get_object(Bucket=bucket, Key=chave)
    buffer = io.BytesIO(response["Body"].read())
    return pd.read_parquet(buffer)


def fazer_upload_parquet(
    cliente, bucket: str, chave: str, df: pd.DataFrame, compressao: str, dry_run: bool
) -> bool:
    log = logging.getLogger("bronze_to_silver")
    if dry_run:
        log.info("[DRY-RUN] Gravaria: s3://%s/%s (%d linhas)", bucket, chave, len(df))
        return True
    try:
        tabela = pa.Table.from_pandas(df, preserve_index=False)
        buffer = io.BytesIO()
        pq.write_table(tabela, buffer, compression=compressao)
        buffer.seek(0)
        cliente.put_object(
            Bucket=bucket,
            Key=chave,
            Body=buffer.read(),
            ContentType="application/octet-stream",
        )
        log.info("Gravado: s3://%s/%s (%d linhas)", bucket, chave, len(df))
        return True
    except (BotoCoreError, ClientError) as e:
        log.error("Falha ao gravar %s: %s", chave, e)
        return False


def chave_silver(prefixo: str, ano_mes: str, nome_parquet: str) -> str:
    return f"{prefixo}ano_mes={ano_mes}/{nome_parquet}"


# ---------------------------------------------------------------------------
# Validações de schema
# ---------------------------------------------------------------------------

def validar_schema(df: pd.DataFrame, cfg: dict, ano_mes: str) -> list[str]:
    """
    Executa validações de schema. Retorna lista de erros encontrados.
    Erros impedem o pipeline; avisos são apenas registrados no log.
    """
    log = logging.getLogger("bronze_to_silver")
    erros = []

    # 1. Colunas obrigatórias
    colunas_obrig = cfg["esquema"]["colunas_obrigatorias"]
    faltando = [c for c in colunas_obrig if c not in df.columns]
    if faltando:
        erros.append(f"Colunas obrigatórias ausentes: {faltando}")

    # 2. Formato ano_mes
    if not PADRAO_ANO_MES.match(ano_mes):
        erros.append(f"Formato de ano_mes inválido: '{ano_mes}' (esperado AAAA-MM)")

    # 3. Chave única
    chave = cfg["esquema"]["chave_unica"]
    chave_presente = [c for c in chave if c in df.columns]
    if chave_presente:
        duplicatas = df.duplicated(subset=chave_presente).sum()
        if duplicatas > 0:
            erros.append(f"Chave única violada: {duplicatas} linha(s) duplicada(s) em {chave_presente}")

    # 4. NCM com tamanho != 8 (após zfill)
    if "ncm" in df.columns:
        ncm_invalido = (df["ncm"].str.len() != cfg["esquema"]["ncm_tamanho"]).sum()
        if ncm_invalido > 0:
            erros.append(f"NCMs com tamanho inválido após zfill(8): {ncm_invalido} linha(s)")

    # 5. Variação de linhas (aviso, não erro)
    limite_variacao = cfg.get("qualidade", {}).get("variacao_linhas_mes_pct", 30)
    log.debug(
        "Linhas no arquivo %s: %d (limite de variação: %d%%)",
        ano_mes, len(df), limite_variacao,
    )

    return erros


# ---------------------------------------------------------------------------
# Validações de negócio
# ---------------------------------------------------------------------------

def validar_negocios(df: pd.DataFrame) -> tuple[list[str], list[str]]:
    """
    Executa validações de negócio conforme dicionário de dados da Receita Federal.

    Retorna (erros, avisos).
    - Erros: violações que indicam dado corrompido.
    - Avisos: anomalias esperadas pela metodologia (truncamento, seguro zero).
    """
    log = logging.getLogger("bronze_to_silver")
    erros  = []
    avisos = []

    def cols(*nomes) -> bool:
        return all(c in df.columns for c in nomes)

    # FOB/kg: min <= max
    if cols("fob_kg_minimo_dolar_truncado", "fob_kg_maximo_dolar_truncado"):
        v = (df["fob_kg_minimo_dolar_truncado"] > df["fob_kg_maximo_dolar_truncado"]).sum()
        if v > 0:
            erros.append(f"fob_kg_minimo > fob_kg_maximo: {v} linha(s)")

    # FOB/UME: min <= max
    if cols("fob_ume_minimo_dolar_truncado", "fob_ume_maximo_dolar_truncado"):
        v = (df["fob_ume_minimo_dolar_truncado"] > df["fob_ume_maximo_dolar_truncado"]).sum()
        if v > 0:
            erros.append(f"fob_ume_minimo > fob_ume_maximo: {v} linha(s)")

    # FOB/kg: quartil1 <= quartil3
    if cols("fob_kg_quartil1_truncado", "fob_kg_quartil3_truncado"):
        v = (df["fob_kg_quartil1_truncado"] > df["fob_kg_quartil3_truncado"]).sum()
        if v > 0:
            erros.append(f"fob_kg_quartil1 > fob_kg_quartil3: {v} linha(s)")

    # FOB/kg: mediana entre Q1 e Q3 (aviso — truncamento pode causar pequenas violações)
    if cols("fob_kg_mediana_dolar_truncado", "fob_kg_quartil1_truncado", "fob_kg_quartil3_truncado"):
        med = df["fob_kg_mediana_dolar_truncado"]
        q1  = df["fob_kg_quartil1_truncado"]
        q3  = df["fob_kg_quartil3_truncado"]
        v = ((med < q1) | (med > q3)).sum()
        if v > 0:
            avisos.append(
                f"fob_kg_mediana fora de [Q1, Q3]: {v} linha(s) "
                "(pode ser efeito do truncamento em 3 algarismos significativos)"
            )

    # Frete: min <= max
    if cols("frete_minimo_dolar_truncado", "frete_maximo_dolar_truncado"):
        v = (df["frete_minimo_dolar_truncado"] > df["frete_maximo_dolar_truncado"]).sum()
        if v > 0:
            erros.append(f"frete_minimo > frete_maximo: {v} linha(s)")

    # Somas positivas
    for col in ["vmle_soma_dolar_truncado", "peso_liq_soma_truncado", "qtd_ume_soma_truncado"]:
        if col in df.columns:
            v = (df[col] <= 0).sum()
            if v > 0:
                erros.append(f"{col} <= 0: {v} linha(s)")

    # Seguro zero — válido, apenas informativo
    if "seguro_minimo_dolar_truncado" in df.columns:
        seg_zero = (df["seguro_minimo_dolar_truncado"] == 0).sum()
        if seg_zero > 0:
            pct = seg_zero / len(df) * 100
            log.info(
                "  Seguro mínimo = 0 em %d linha(s) (%.1f%%) — comportamento esperado.",
                seg_zero, pct,
            )

    return erros, avisos


# ---------------------------------------------------------------------------
# Transformação bronze → silver
# ---------------------------------------------------------------------------

def transformar(df: pd.DataFrame, ano_mes: str, cfg: dict) -> pd.DataFrame:
    """
    Aplica todas as regras de tratamento da camada silver.

    Ordem das operações:
      1. Normalizar nomes de colunas (lowercase + strip)
      2. Tratar colunas de texto
      3. Converter colunas numéricas
      4. Criar colunas derivadas
    """
    log = logging.getLogger("bronze_to_silver")

    # 1. Normalizar nomes de colunas
    df.columns = [c.strip().lower() for c in df.columns]

    # ------------------------------------------------------------------
    # 2. Colunas de texto
    # ------------------------------------------------------------------

    # NCM: zfill(8) — recupera zeros à esquerda perdidos na leitura como número
    if "ncm" in df.columns:
        antes = (df["ncm"].str.len() != 8).sum()
        df["ncm"] = df["ncm"].str.strip().str.zfill(8)
        depois = (df["ncm"].str.len() != 8).sum()
        if antes > 0:
            log.info("  NCM: %d código(s) corrigido(s) com zfill(8)", antes - depois)

    # NVES e NVES_NM: strip de espaços
    for col in ["nves", "nves_nm"]:
        if col in df.columns:
            df[col] = df[col].str.strip()

    # PAIS_ORIGEM: strip + uppercase
    if "pais_origem" in df.columns:
        df["pais_origem"] = df["pais_origem"].str.strip().str.upper()

    # UME: strip; vazio → "NÃO INFORMADO"
    if "ume" in df.columns:
        df["ume"] = df["ume"].str.strip()
        vazios = (df["ume"] == "").sum()
        if vazios > 0:
            df["ume"] = df["ume"].replace("", "NÃO INFORMADO")
            log.warning("  UME: %d linha(s) com valor vazio → 'NÃO INFORMADO'", vazios)

    # ------------------------------------------------------------------
    # 3. Colunas numéricas: texto → float64
    # ------------------------------------------------------------------
    colunas_num_presentes = [c for c in COLUNAS_NUMERICAS if c in df.columns]
    for col in colunas_num_presentes:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    nulos_pos_conversao = df[colunas_num_presentes].isna().sum()
    nulos_relevantes = nulos_pos_conversao[nulos_pos_conversao > 0]
    if not nulos_relevantes.empty:
        log.warning(
            "  Valores não convertíveis para número (→ NaN): %s",
            nulos_relevantes.to_dict(),
        )

    # ------------------------------------------------------------------
    # 4. Colunas derivadas
    # ------------------------------------------------------------------

    # Capítulo NCM (2 primeiros dígitos)
    if "ncm" in df.columns:
        df["capitulo_ncm"] = df["ncm"].str[:2]
        df["posicao_ncm"]  = df["ncm"].str[:4]

    # Flag: tem NVE declarado
    if "nves" in df.columns:
        df["tem_nve"] = df["nves"] != "S/NVE"

        # Flag: linha com múltiplos NVEs (ex.: "PO.7318.AA.9999,PO.7318.AB.9999")
        df["nves_multiplo"] = (
            df["nves"].str.contains(",", regex=False) & df["tem_nve"]
        )

    log.info("  Colunas derivadas criadas: capitulo_ncm, posicao_ncm, tem_nve, nves_multiplo")

    return df


# ---------------------------------------------------------------------------
# Fluxo principal
# ---------------------------------------------------------------------------

def processar_arquivo(
    cliente,
    bucket: str,
    item: dict,
    prefixo_silver: str,
    cfg: dict,
    dry_run: bool,
) -> bool:
    """
    Processa um único arquivo bronze → silver.
    Retorna True em caso de sucesso, False em caso de erro.
    """
    log     = logging.getLogger("bronze_to_silver")
    nome    = item["nome"]
    chave_b = item["chave"]
    ano_mes = item["ano_mes"]
    chave_s = chave_silver(prefixo_silver, ano_mes, nome)

    log.info("Processando: %s  (ano_mes=%s)", nome, ano_mes)

    # Download
    try:
        df = baixar_parquet(cliente, bucket, chave_b)
        log.info("  Download OK (%d linhas, %d colunas)", len(df), len(df.columns))
    except (BotoCoreError, ClientError) as e:
        log.error("  Falha no download de %s: %s", chave_b, e)
        return False

    # Transformação
    try:
        df = transformar(df, ano_mes, cfg)
    except Exception as e:
        log.error("  Falha na transformação de %s: %s", nome, e)
        return False

    # Validação de schema
    erros_schema = validar_schema(df, cfg, ano_mes)
    if erros_schema:
        for erro in erros_schema:
            log.error("  [SCHEMA] %s", erro)
        log.error("  Arquivo %s rejeitado por erros de schema.", nome)
        return False

    # Validação de negócio
    erros_neg, avisos_neg = validar_negocios(df)
    for aviso in avisos_neg:
        log.warning("  [NEGÓCIO] %s", aviso)
    if erros_neg:
        for erro in erros_neg:
            log.error("  [NEGÓCIO] %s", erro)
        log.error("  Arquivo %s rejeitado por erros de negócio.", nome)
        return False

    # Upload
    compressao = cfg["parquet"]["compressao"]
    return fazer_upload_parquet(cliente, bucket, chave_s, df, compressao, dry_run)


def main(caminho_config: str, ano_mes_filtro: str | None, reprocessar: bool, dry_run: bool) -> None:
    cfg = carregar_config(caminho_config)
    log = configurar_log(cfg)

    bucket         = cfg["aws"]["bucket"]
    prefixo_bronze = cfg["s3"]["bronze"]
    prefixo_silver = cfg["s3"]["silver"]

    log.info("=== bronze_to_silver | analise-importacoes-brasil ===")
    log.info("Bucket : %s", bucket)
    log.info("Bronze : %s", prefixo_bronze)
    log.info("Silver : %s", prefixo_silver)
    if ano_mes_filtro:
        log.info("Filtro : %s", ano_mes_filtro)
    if reprocessar:
        log.info("Modo   : REPROCESSAR (sobrescreve arquivos existentes na silver)")
    if dry_run:
        log.info("Modo   : DRY-RUN (nenhum arquivo será gravado)")

    # Conecta ao bucket
    cliente = criar_cliente_s3(cfg)
    testar_bucket(cliente, bucket, cfg["aws"]["profile"])

    # Lista Parquets no bronze
    bronze_itens = listar_parquets(cliente, bucket, prefixo_bronze)
    if not bronze_itens:
        log.warning("Nenhum Parquet encontrado no bronze. Execute raw_para_bronze.py primeiro.")
        sys.exit(0)

    # Aplica filtro de mês
    if ano_mes_filtro:
        bronze_itens = [i for i in bronze_itens if i["ano_mes"] == ano_mes_filtro]
        if not bronze_itens:
            log.error("Nenhum arquivo encontrado no bronze para o mês: %s", ano_mes_filtro)
            sys.exit(1)

    # Identifica pendentes
    if not reprocessar:
        silver_existentes = {i["nome"] for i in listar_parquets(cliente, bucket, prefixo_silver)}
        log.info("Parquets já existentes na silver: %d", len(silver_existentes))
        pendentes = [i for i in bronze_itens if i["nome"] not in silver_existentes]
    else:
        pendentes = bronze_itens

    log.info("--- Resumo ---")
    log.info("No bronze   : %d arquivo(s)", len(bronze_itens))
    log.info("A processar : %d arquivo(s)", len(pendentes))

    if not pendentes:
        log.info("Silver está atualizada. Nenhum arquivo novo para processar.")
        return

    # Processa
    sucesso = 0
    falha   = 0

    for item in pendentes:
        if processar_arquivo(cliente, bucket, item, prefixo_silver, cfg, dry_run):
            sucesso += 1
        else:
            falha += 1

    # Relatório final
    log.info("--- Resultado ---")
    log.info("Processados com sucesso : %d", sucesso)
    log.info("Falhas                  : %d", falha)

    if falha > 0:
        log.warning("%d arquivo(s) não foram processados. Verifique os erros acima.", falha)
        sys.exit(1)

    log.info("Camada silver atualizada.")


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Converte Parquets do S3 bronze para a camada silver com tipagem e validações.",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Caminho para o arquivo de configuração (padrão: config.yaml)",
    )
    parser.add_argument(
        "--ano-mes",
        dest="ano_mes",
        default=None,
        metavar="AAAA-MM",
        help="Processa apenas o mês informado (ex.: 2026-08)",
    )
    parser.add_argument(
        "--reprocessar",
        action="store_true",
        help="Regrava arquivos já existentes na silver",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula o processamento sem gravar nenhum arquivo",
    )
    args = parser.parse_args()

    main(
        caminho_config=args.config,
        ano_mes_filtro=args.ano_mes,
        reprocessar=args.reprocessar,
        dry_run=args.dry_run,
    )
