"""
raw_to_bronze.py
==================
Lê os arquivos CSV da camada raw no S3, acrescenta os metadados
`arquivo_origem`, `ano_mes` e `data_ingestao`, e salva como Parquet
na camada bronze — com todas as colunas originais mantidas como texto.

Nenhum conteúdo é alterado: este script apenas padroniza o formato e
adiciona rastreabilidade. Transformações ficam no bronze_to_silver.py.

Uso:
    python raw_to_bronze.py                        # processa todos os meses pendentes
    python raw_to_bronze.py --ano-mes 2026-08      # processa apenas um mês específico
    python raw_to_bronze.py --reprocessar          # regrava mesmo os que já existem
    python raw_to_bronze.py --dry-run              # mostra o que seria feito, sem gravar
    python raw_to_bronze.py --config outro.yaml    # usa outro arquivo de configuração

Dependências:
    pip install boto3 pyyaml pandas pyarrow
"""

import argparse
import io
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml
from botocore.exceptions import BotoCoreError, ClientError


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

def carregar_config(caminho_config: str) -> dict:
    caminho = Path(caminho_config)
    if not caminho.exists():
        raise FileNotFoundError(
            f"Arquivo de configuração não encontrado: {caminho}"
        )
    with caminho.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def configurar_log(cfg: dict) -> logging.Logger:
    nivel = getattr(logging, cfg["log"]["nivel"].upper(), logging.INFO)
    logging.basicConfig(level=nivel, format=cfg["log"]["formato"])
    return logging.getLogger("raw_to_bronze")


# ---------------------------------------------------------------------------
# Helpers de chave S3
# ---------------------------------------------------------------------------

def extrair_ano_mes(nome_arquivo: str, padrao: str) -> str | None:
    """
    Extrai 'AAAA-MM' do nome do arquivo usando o padrão regex do config.

    Exemplo:
        'estatisticas_di_2026_08.csv'  →  '2026-08'
    """
    match = re.match(padrao, nome_arquivo)
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2)}"


def chave_raw(prefixo: str, ano_mes: str, nome_arquivo: str) -> str:
    return f"{prefixo}ano_mes={ano_mes}/{nome_arquivo}"


def chave_bronze(prefixo: str, ano_mes: str, nome_arquivo: str) -> str:
    """
    Monta a chave Parquet na camada bronze.

    Exemplo:
        bronze/estatisticas_di/ano_mes=2026-08/estatisticas_di_2026_08.parquet
    """
    nome_parquet = Path(nome_arquivo).stem + ".parquet"
    return f"{prefixo}ano_mes={ano_mes}/{nome_parquet}"


# ---------------------------------------------------------------------------
# Operações no S3
# ---------------------------------------------------------------------------

def criar_cliente_s3(cfg: dict) -> boto3.client:
    session = boto3.Session(
        profile_name=cfg["aws"]["profile"],
        region_name=cfg["aws"]["regiao"],
    )
    return session.client("s3")


def listar_csvs_no_raw(cliente, bucket: str, prefixo_raw: str, padrao: str) -> list[dict]:
    """
    Lista todos os CSVs presentes na camada raw.

    Retorna lista de dicts com 'nome', 'chave' e 'ano_mes'.
    """
    log = logging.getLogger("raw_to_bronze")
    arquivos = []

    paginator = cliente.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefixo_raw):
        for obj in page.get("Contents", []):
            chave = obj["Key"]
            nome = Path(chave).name
            if not nome.endswith(".csv"):
                continue
            ano_mes = extrair_ano_mes(nome, padrao)
            if ano_mes is None:
                log.warning("Arquivo ignorado (nome fora do padrão): %s", chave)
                continue
            arquivos.append({"nome": nome, "chave": chave, "ano_mes": ano_mes})

    log.info("CSVs encontrados no raw: %d", len(arquivos))
    return arquivos


def listar_parquets_no_bronze(cliente, bucket: str, prefixo_bronze: str) -> set[str]:
    """
    Lista os nomes de arquivo Parquet já presentes na camada bronze.

    Retorna um conjunto de basenames, ex.: {'estatisticas_di_2026_08.parquet'}
    """
    existentes = set()
    paginator = cliente.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefixo_bronze):
        for obj in page.get("Contents", []):
            nome = Path(obj["Key"]).name
            if nome.endswith(".parquet"):
                existentes.add(nome)
    return existentes


def baixar_csv(cliente, bucket: str, chave: str) -> bytes:
    """Baixa o CSV do S3 e retorna os bytes brutos."""
    response = cliente.get_object(Bucket=bucket, Key=chave)
    return response["Body"].read()


def fazer_upload_parquet(
    cliente,
    bucket: str,
    chave: str,
    buffer: io.BytesIO,
    dry_run: bool,
) -> bool:
    """Envia o buffer Parquet para o S3 bronze."""
    log = logging.getLogger("raw_to_bronze")

    if dry_run:
        log.info("[DRY-RUN] Gravaria: s3://%s/%s", bucket, chave)
        return True

    try:
        buffer.seek(0)
        cliente.put_object(
            Bucket=bucket,
            Key=chave,
            Body=buffer.read(),
            ContentType="application/octet-stream",
        )
        log.info("Gravado: s3://%s/%s", bucket, chave)
        return True
    except (BotoCoreError, ClientError) as erro:
        log.error("Falha ao gravar %s: %s", chave, erro)
        return False


# ---------------------------------------------------------------------------
# Transformação raw → bronze
# ---------------------------------------------------------------------------

def csv_para_parquet(
    conteudo_csv: bytes,
    nome_arquivo: str,
    ano_mes: str,
    separador: str,
    encoding: str,
    compressao: str,
) -> io.BytesIO:
    """
    Converte os bytes de um CSV para Parquet em memória.

    Regras da camada bronze:
    - Todas as colunas lidas como texto (dtype=str), sem inferência de tipo.
    - Nomes de colunas normalizados para minúsculas.
    - Três colunas de metadado acrescentadas:
        * arquivo_origem  : nome do arquivo CSV de origem
        * ano_mes         : partição no formato 'AAAA-MM'
        * data_ingestao   : timestamp UTC do momento do processamento
    - Nenhum conteúdo alterado além disso.
    """
    df = pd.read_csv(
        io.BytesIO(conteudo_csv),
        sep=separador,
        encoding=encoding,
        dtype=str,          # tudo como texto — sem inferência
        keep_default_na=False,  # preserva strings vazias, não converte para NaN
    )

    # Normaliza nomes de colunas para minúsculas
    df.columns = [c.strip().lower() for c in df.columns]

    # Metadados de rastreabilidade
    df["arquivo_origem"] = nome_arquivo
    df["ano_mes"]        = ano_mes
    df["data_ingestao"]  = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Converte para Parquet em memória
    tabela = pa.Table.from_pandas(df, preserve_index=False)
    buffer = io.BytesIO()
    pq.write_table(tabela, buffer, compression=compressao)
    return buffer


# ---------------------------------------------------------------------------
# Fluxo principal
# ---------------------------------------------------------------------------

def main(caminho_config: str, ano_mes_filtro: str | None, reprocessar: bool, dry_run: bool) -> None:
    cfg    = carregar_config(caminho_config)
    log    = configurar_log(cfg)

    bucket         = cfg["aws"]["bucket"]
    prefixo_raw    = cfg["s3"]["raw"]
    prefixo_bronze = cfg["s3"]["bronze"]
    padrao         = cfg["origem"]["padrao_nome_arquivo"]
    separador      = cfg["origem"]["separador"]
    encoding       = cfg["origem"]["encoding"]
    compressao     = cfg["parquet"]["compressao"]

    log.info("=== raw_to_bronze | analise-importacoes-brasil ===")
    log.info("Bucket  : %s", bucket)
    log.info("Raw     : %s", prefixo_raw)
    log.info("Bronze  : %s", prefixo_bronze)
    if ano_mes_filtro:
        log.info("Filtro  : %s", ano_mes_filtro)
    if reprocessar:
        log.info("Modo    : REPROCESSAR (sobrescreve Parquets existentes)")
    if dry_run:
        log.info("Modo    : DRY-RUN (nenhum arquivo será gravado)")

    # 1. Conecta e testa o bucket
    try:
        cliente = criar_cliente_s3(cfg)
        cliente.head_bucket(Bucket=bucket)
    except ClientError as erro:
        codigo = erro.response["Error"]["Code"]
        if codigo == "404":
            log.error("Bucket não encontrado: %s", bucket)
        elif codigo in ("403", "AccessDenied"):
            log.error(
                "Acesso negado ao bucket '%s'. Verifique a política IAM do perfil '%s'.",
                bucket, cfg["aws"]["profile"],
            )
        else:
            log.error("Erro ao acessar o bucket: %s", erro)
        sys.exit(1)
    except BotoCoreError as erro:
        log.error("Erro de conexão com a AWS: %s", erro)
        sys.exit(1)

    # 2. Lista CSVs disponíveis no raw
    csvs = listar_csvs_no_raw(cliente, bucket, prefixo_raw, padrao)

    if not csvs:
        log.warning("Nenhum CSV encontrado no raw. Execute upload_raw.py primeiro.")
        sys.exit(0)

    # 3. Aplica filtro de mês, se informado
    if ano_mes_filtro:
        csvs = [c for c in csvs if c["ano_mes"] == ano_mes_filtro]
        if not csvs:
            log.error("Nenhum CSV encontrado no raw para o mês: %s", ano_mes_filtro)
            sys.exit(1)

    # 4. Lista Parquets já existentes no bronze
    if not reprocessar:
        existentes = listar_parquets_no_bronze(cliente, bucket, prefixo_bronze)
        log.info("Parquets já existentes no bronze: %d", len(existentes))
        pendentes = [
            c for c in csvs
            if (Path(c["nome"]).stem + ".parquet") not in existentes
        ]
    else:
        pendentes = csvs

    log.info("--- Resumo ---")
    log.info("CSVs no raw   : %d", len(csvs))
    log.info("A processar   : %d", len(pendentes))

    if not pendentes:
        log.info("Bronze está atualizado. Nenhum arquivo novo para processar.")
        return

    # 5. Processa cada CSV pendente
    sucesso = 0
    falha   = 0

    for item in pendentes:
        nome    = item["nome"]
        ano_mes = item["ano_mes"]
        chave_s = item["chave"]
        chave_b = chave_bronze(prefixo_bronze, ano_mes, nome)

        log.info("Processando: %s  (ano_mes=%s)", nome, ano_mes)

        if dry_run:
            log.info("[DRY-RUN] Leria s3://%s/%s → gravaria s3://%s/%s",
                     bucket, chave_s, bucket, chave_b)
            sucesso += 1
            continue

        # 5a. Download do CSV
        try:
            conteudo = baixar_csv(cliente, bucket, chave_s)
            log.info("  Download OK (%d bytes)", len(conteudo))
        except (BotoCoreError, ClientError) as erro:
            log.error("  Falha no download de %s: %s", chave_s, erro)
            falha += 1
            continue

        # 5b. Conversão CSV → Parquet
        try:
            buffer = csv_para_parquet(
                conteudo_csv=conteudo,
                nome_arquivo=nome,
                ano_mes=ano_mes,
                separador=separador,
                encoding=encoding,
                compressao=compressao,
            )
            log.info("  Conversão OK (%d bytes Parquet)", buffer.getbuffer().nbytes)
        except Exception as erro:
            log.error("  Falha na conversão de %s: %s", nome, erro)
            falha += 1
            continue

        # 5c. Upload do Parquet para o bronze
        if fazer_upload_parquet(cliente, bucket, chave_b, buffer, dry_run):
            sucesso += 1
        else:
            falha += 1

    # 6. Relatório final
    log.info("--- Resultado ---")
    log.info("Processados com sucesso : %d", sucesso)
    log.info("Falhas                  : %d", falha)

    if falha > 0:
        log.warning("%d arquivo(s) não foram processados. Verifique os erros acima.", falha)
        sys.exit(1)

    log.info("Camada bronze atualizada.")


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Converte CSVs do S3 raw para Parquet no S3 bronze.",
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
        help="Regrava Parquets já existentes no bronze",
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
