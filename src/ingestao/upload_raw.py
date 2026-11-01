"""
upload_raw.py
=============
Verifica quais arquivos CSV já existem no S3 (camada raw) e faz o upload
apenas dos que estão na pasta local mas ainda não foram enviados.

Uso:
    python upload_raw.py                        # usa config.yaml do diretório atual
    python upload_raw.py --config outro.yaml    # usa outro arquivo de configuração
    python upload_raw.py --dry-run              # mostra o que seria feito, sem enviar

Dependências:
    pip install boto3 pyyaml
"""

import argparse
import logging
import re
import sys
from pathlib import Path

import boto3
import yaml
from botocore.exceptions import BotoCoreError, ClientError


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

def carregar_config(caminho_config: str) -> dict:
    """Lê o config.yaml e retorna o dicionário de configuração."""
    caminho = Path(caminho_config)
    if not caminho.exists():
        raise FileNotFoundError(f"Arquivo de configuração não encontrado: {caminho}")
    with caminho.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def configurar_log(cfg: dict) -> logging.Logger:
    """Configura o logger conforme definido no config.yaml."""
    nivel = getattr(logging, cfg["log"]["nivel"].upper(), logging.INFO)
    formato = cfg["log"]["formato"]
    logging.basicConfig(level=nivel, format=formato)
    return logging.getLogger("upload_raw")


# ---------------------------------------------------------------------------
# Helpers de nome de arquivo
# ---------------------------------------------------------------------------

def extrair_ano_mes(nome_arquivo: str, padrao: str) -> str | None:
    """
    Extrai 'AAAA-MM' do nome do arquivo usando o padrão regex do config.

    Retorna None se o nome não corresponder ao padrão esperado.

    Exemplo:
        'estatisticas_di_2026_08.csv'  →  '2026-08'
    """
    match = re.match(padrao, nome_arquivo)
    if not match:
        return None
    ano, mes = match.group(1), match.group(2)
    return f"{ano}-{mes}"


def montar_chave_s3(prefixo_raw: str, ano_mes: str, nome_arquivo: str) -> str:
    """
    Monta a chave S3 no formato particionado estilo Hive.

    Exemplo:
        raw/estatisticas_di/ano_mes=2026-08/estatisticas_di_2026_08.csv
    """
    return f"{prefixo_raw}ano_mes={ano_mes}/{nome_arquivo}"


# ---------------------------------------------------------------------------
# Operações no S3
# ---------------------------------------------------------------------------

def criar_cliente_s3(cfg: dict) -> boto3.client:
    """Cria e retorna um cliente S3 usando o perfil IAM do config."""
    session = boto3.Session(
        profile_name=cfg["aws"]["profile"],
        region_name=cfg["aws"]["regiao"],
    )
    return session.client("s3")


def listar_arquivos_no_s3(cliente, bucket: str, prefixo_raw: str) -> set[str]:
    """
    Lista todos os arquivos CSV presentes na camada raw do bucket.

    Retorna um conjunto de nomes de arquivo (apenas o basename),
    sem o prefixo nem a partição.

    Exemplo de retorno:
        {'estatisticas_di_2026_08.csv', 'estatisticas_di_2026_07.csv'}
    """
    log = logging.getLogger("upload_raw")
    arquivos_no_s3 = set()

    paginator = cliente.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket, Prefix=prefixo_raw)

    for page in pages:
        for obj in page.get("Contents", []):
            # Chave completa: raw/estatisticas_di/ano_mes=2026-08/estatisticas_di_2026_08.csv
            chave = obj["Key"]
            nome = Path(chave).name  # extrai apenas o basename
            if nome.endswith(".csv"):
                arquivos_no_s3.add(nome)

    log.info("Arquivos encontrados no S3 (raw): %d", len(arquivos_no_s3))
    return arquivos_no_s3


def listar_arquivos_locais(pasta_local: Path, padrao: str) -> list[Path]:
    """
    Lista os arquivos CSV na pasta local que correspondem ao padrão esperado.

    Ignora arquivos com nome fora do padrão e emite aviso para cada um.

    Retorna uma lista de objetos Path, ordenada por nome.
    """
    log = logging.getLogger("upload_raw")
    arquivos_validos = []

    if not pasta_local.exists():
        raise FileNotFoundError(
            f"Pasta local não encontrada: {pasta_local}\n"
            "Verifique o campo 'caminhos.download_local' no config.yaml."
        )

    for arquivo in sorted(pasta_local.glob("*.csv")):
        if re.match(padrao, arquivo.name):
            arquivos_validos.append(arquivo)
        else:
            log.warning(
                "Arquivo ignorado (nome fora do padrão): %s", arquivo.name
            )

    log.info("Arquivos válidos encontrados localmente: %d", len(arquivos_validos))
    return arquivos_validos


def fazer_upload(
    cliente,
    bucket: str,
    arquivo_local: Path,
    chave_s3: str,
    dry_run: bool,
) -> bool:
    """
    Faz o upload de um arquivo para o S3.

    Em modo dry_run, apenas registra o que seria feito.
    Retorna True em caso de sucesso (ou simulação), False em caso de erro.
    """
    log = logging.getLogger("upload_raw")

    if dry_run:
        log.info("[DRY-RUN] Enviaria: %s  →  s3://%s/%s", arquivo_local.name, bucket, chave_s3)
        return True

    try:
        log.info("Enviando: %s  →  s3://%s/%s", arquivo_local.name, bucket, chave_s3)
        cliente.upload_file(
            Filename=str(arquivo_local),
            Bucket=bucket,
            Key=chave_s3,
            ExtraArgs={"ContentType": "text/csv"},
        )
        log.info("Upload concluído: %s", arquivo_local.name)
        return True

    except (BotoCoreError, ClientError) as erro:
        log.error("Falha no upload de %s: %s", arquivo_local.name, erro)
        return False


# ---------------------------------------------------------------------------
# Fluxo principal
# ---------------------------------------------------------------------------

def main(caminho_config: str, dry_run: bool) -> None:
    cfg = carregar_config(caminho_config)
    log = configurar_log(cfg)

    bucket       = cfg["aws"]["bucket"]
    prefixo_raw  = cfg["s3"]["raw"]
    padrao       = cfg["origem"]["padrao_nome_arquivo"]
    pasta_local  = Path(cfg["caminhos"]["download_local"])

    log.info("=== upload_raw | analise-importacoes-brasil ===")
    log.info("Bucket  : %s", bucket)
    log.info("Prefixo : %s", prefixo_raw)
    log.info("Local   : %s", pasta_local)
    if dry_run:
        log.info("Modo    : DRY-RUN (nenhum arquivo será enviado)")

    # 1. Conecta ao S3
    try:
        cliente = criar_cliente_s3(cfg)
        # Testa conectividade antes de listar locais
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

    # 2. Lista o que já está no S3
    arquivos_no_s3 = listar_arquivos_no_s3(cliente, bucket, prefixo_raw)

    # 3. Lista o que está disponível localmente
    try:
        arquivos_locais = listar_arquivos_locais(pasta_local, padrao)
    except FileNotFoundError as erro:
        log.error(str(erro))
        sys.exit(1)

    if not arquivos_locais:
        log.warning("Nenhum arquivo válido encontrado em: %s", pasta_local)
        sys.exit(0)

    # 4. Identifica o que falta enviar
    faltando = [a for a in arquivos_locais if a.name not in arquivos_no_s3]

    log.info("--- Resumo ---")
    log.info("Locais   : %d arquivo(s)", len(arquivos_locais))
    log.info("No S3    : %d arquivo(s)", len(arquivos_no_s3))
    log.info("A enviar : %d arquivo(s)", len(faltando))

    if not faltando:
        log.info("Nenhum arquivo novo para enviar. S3 está atualizado.")
        return

    # 5. Faz os uploads
    sucesso = 0
    falha   = 0

    for arquivo in faltando:
        ano_mes = extrair_ano_mes(arquivo.name, padrao)
        if ano_mes is None:
            log.error("Não foi possível extrair ano_mes de: %s", arquivo.name)
            falha += 1
            continue

        chave = montar_chave_s3(prefixo_raw, ano_mes, arquivo.name)

        if fazer_upload(cliente, bucket, arquivo, chave, dry_run):
            sucesso += 1
        else:
            falha += 1

    # 6. Relatório final
    log.info("--- Resultado ---")
    log.info("Enviados com sucesso : %d", sucesso)
    log.info("Falhas               : %d", falha)

    if falha > 0:
        log.warning(
            "%d arquivo(s) não foram enviados. Verifique os erros acima.", falha
        )
        sys.exit(1)

    log.info("Upload concluído.")


# ---------------------------------------------------------------------------
# Ponto de entrada
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Envia CSVs novos da pasta local para o S3 (camada raw).",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Caminho para o arquivo de configuração (padrão: config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula o upload sem enviar nenhum arquivo",
    )
    args = parser.parse_args()

    main(caminho_config=args.config, dry_run=args.dry_run)
