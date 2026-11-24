"""
Fixtures compartilhadas pelos testes.

Princípios:
  - Nenhum teste acessa S3, rede ou credenciais AWS.
  - Os DataFrames são pequenos e sintéticos; cada teste altera só o que precisa.
  - A dim_ncm sintética reproduz o caso real que motivou o desenho da dimensão:
    o NCM 21069030 muda de versão em 01/11/2023.
"""

from datetime import date

import pandas as pd
import pytest

import silver_to_gold as gold

# ---------------------------------------------------------------------------
# Silver sintética
# ---------------------------------------------------------------------------

METRICAS_MIN_MAX = {
    "fob_kg_minimo_dolar_truncado": 1.0,
    "fob_kg_maximo_dolar_truncado": 9.0,
    "fob_ume_minimo_dolar_truncado": 1.0,
    "fob_ume_maximo_dolar_truncado": 9.0,
    "frete_minimo_dolar_truncado": 0.1,
    "frete_maximo_dolar_truncado": 0.9,
}
METRICAS_NAO_ADITIVAS = {
    "fob_kg_media_dolar_truncado": 5.0,
    "fob_kg_mediana_dolar_truncado": 4.0,
}


@pytest.fixture
def criar_silver():
    """
    Fábrica de silver válida.

    registros: lista de tuplas (ncm, pais_origem) ou (ncm, pais_origem, nves).
    O capítulo e a posição são derivados do NCM, como no bronze_to_silver.py.
    """

    def _criar(ano_mes: str, registros: list[tuple], vmle: float = 100.0) -> pd.DataFrame:
        linhas = []
        for i, reg in enumerate(registros):
            ncm, pais = reg[0], reg[1]
            nves = reg[2] if len(reg) > 2 else "S/NVE"
            linha = {
                "ano_mes": ano_mes,
                "ncm": ncm,
                "nves": nves,
                "pais_origem": pais,
                "ume": "QUILOGRAMA LIQUIDO",
                "capitulo_ncm": ncm[:2],
                "posicao_ncm": ncm[:4],
                "vmle_soma_dolar_truncado": vmle + i,
                "peso_liq_soma_truncado": 10.0 + i,
                "qtd_ume_soma_truncado": 10.0 + i,
            }
            linha.update(METRICAS_MIN_MAX)
            linha.update(METRICAS_NAO_ADITIVAS)
            linhas.append(linha)
        return pd.DataFrame(linhas)

    return _criar


# ---------------------------------------------------------------------------
# Dimensões sintéticas
# ---------------------------------------------------------------------------

@pytest.fixture
def dim_ncm_raw() -> pd.DataFrame:
    """
    dim_ncm no formato do CSV (tudo texto).

    - 21069030: duas versões, a segunda começa em 01/11/2023 (caso real).
      Mistura os formatos AAAA-MM-DD e DD/MM/AAAA de propósito.
    - 84713012: versão única, vigente.
    - 87032100: só passa a existir em 01/01/2024.
    """
    return pd.DataFrame([
        {"ncm": "21069030", "capitulo": "21", "posicao": "2106",
         "vigencia_inicio": "2022-04-01", "vigencia_fim": "2023-10-31"},
        {"ncm": "21069030", "capitulo": "21", "posicao": "2106",
         "vigencia_inicio": "01/11/2023", "vigencia_fim": "9999-12-31"},
        {"ncm": "84713012", "capitulo": "84", "posicao": "8471",
         "vigencia_inicio": "2022-04-01", "vigencia_fim": "9999-12-31"},
        {"ncm": "87032100", "capitulo": "87", "posicao": "8703",
         "vigencia_inicio": "2024-01-01", "vigencia_fim": "9999-12-31"},
    ])


@pytest.fixture
def dim_ncm(dim_ncm_raw) -> pd.DataFrame:
    """dim_ncm já preparada (com _ini, _fim e ncm_versao_id)."""
    df, erros, _ = gold.preparar_dim_ncm(dim_ncm_raw)
    assert erros == [], f"fixture dim_ncm inválida: {erros}"
    return df


@pytest.fixture
def dim_pais() -> pd.DataFrame:
    """
    dim_pais mínima. A chave é o nome exato da RFB, inclusive o espaço duplo
    de 'ALBANIA, REPUBLICA  DA'.
    """
    return pd.DataFrame([
        {"pais_origem_rfb": "CHINA, REPUBLICA POPULAR", "pais_nome": "China",
         "iso2": "CN", "iso3": "CHN", "continente": "Ásia", "regiao": "Ásia Oriental",
         "bloco_mercosul": False, "bloco_ocde": False, "bloco_brics": True, "bloco_g20": True},
        {"pais_origem_rfb": "ESTADOS UNIDOS", "pais_nome": "Estados Unidos",
         "iso2": "US", "iso3": "USA", "continente": "América do Norte",
         "regiao": "América do Norte",
         "bloco_mercosul": False, "bloco_ocde": True, "bloco_brics": False, "bloco_g20": True},
        {"pais_origem_rfb": "ALBANIA, REPUBLICA  DA", "pais_nome": "Albânia",
         "iso2": "AL", "iso3": "ALB", "continente": "Europa", "regiao": "Europa Meridional",
         "bloco_mercosul": False, "bloco_ocde": False, "bloco_brics": False, "bloco_g20": False},
    ])


@pytest.fixture
def ref():
    """Atalho para criar datas: ref(2023, 10, 31)."""
    return date
