"""
Testes dos arquivos de referência versionados em data_ref/.

Esses CSVs são gerados por notebooks e podem ser editados à mão. Os testes
rodam sobre os arquivos reais do repositório e protegem contra regressões
silenciosas (chave duplicada, vigência sobreposta, país sem código ISO...).

Se um arquivo ainda não existir, o teste correspondente é pulado.
"""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import silver_to_gold as gold

RAIZ = Path(__file__).resolve().parents[1]
DATA_REF = RAIZ / "data_ref"


def _ler(nome: str, encoding: str) -> pd.DataFrame:
    caminho = DATA_REF / nome
    if not caminho.exists():
        pytest.skip(f"{caminho} não encontrado")
    return pd.read_csv(caminho, dtype=str, keep_default_na=False, encoding=encoding)


@pytest.fixture(scope="module")
def dim_pais_csv() -> pd.DataFrame:
    return _ler("dim_pais.csv", "utf-8-sig")


@pytest.fixture(scope="module")
def dim_ncm_csv() -> pd.DataFrame:
    return _ler("dim_ncm.csv", "utf-8-sig")   # utf-8-sig lê arquivos com ou sem BOM


# ---------------------------------------------------------------------------
# dim_pais.csv
# ---------------------------------------------------------------------------

class TestDimPaisCsv:
    COLUNAS = ["pais_origem_rfb", "pais_nome", "iso2", "iso3", "continente", "regiao",
               "bloco_mercosul", "bloco_ocde", "bloco_brics", "bloco_g20"]

    def test_colunas_esperadas(self, dim_pais_csv):
        assert set(self.COLUNAS) <= set(dim_pais_csv.columns)

    def test_chave_da_rfb_unica(self, dim_pais_csv):
        dup = dim_pais_csv.loc[dim_pais_csv["pais_origem_rfb"].duplicated(), "pais_origem_rfb"]
        assert dup.empty, f"pais_origem_rfb duplicado: {dup.tolist()}"

    def test_codigos_iso_unicos(self, dim_pais_csv):
        for col in ["iso2", "iso3"]:
            dup = dim_pais_csv.loc[dim_pais_csv[col].duplicated(), col]
            assert dup.empty, f"{col} duplicado: {dup.tolist()}"

    def test_formato_dos_codigos_iso(self, dim_pais_csv):
        assert dim_pais_csv["iso2"].str.fullmatch(r"[A-Z]{2}").all()
        assert dim_pais_csv["iso3"].str.fullmatch(r"[A-Z]{3}").all()

    @pytest.mark.parametrize("coluna", ["pais_origem_rfb", "pais_nome", "iso2", "iso3",
                                        "continente", "regiao"])
    def test_sem_valores_vazios(self, dim_pais_csv, coluna):
        vazios = dim_pais_csv.loc[dim_pais_csv[coluna].str.strip() == "", "pais_origem_rfb"]
        assert vazios.empty, f"{coluna} vazio para: {vazios.tolist()}"

    @pytest.mark.parametrize("coluna", ["bloco_mercosul", "bloco_ocde", "bloco_brics", "bloco_g20"])
    def test_blocos_sao_booleanos(self, dim_pais_csv, coluna):
        valores = set(dim_pais_csv[coluna].str.strip().str.lower())
        assert valores <= {"true", "false"}

    def test_chave_da_rfb_esta_em_maiusculas(self, dim_pais_csv):
        """A silver grava pais_origem em maiúsculas; a chave precisa casar."""
        chaves = dim_pais_csv["pais_origem_rfb"]
        assert (chaves == chaves.str.upper()).all()

    def test_chave_da_rfb_sem_espacos_nas_pontas(self, dim_pais_csv):
        """Espaço interno duplo é legítimo (nome da RFB); nas pontas não."""
        chaves = dim_pais_csv["pais_origem_rfb"]
        assert (chaves == chaves.str.strip()).all()

    def test_mercosul_tem_os_membros_plenos(self, dim_pais_csv):
        mercosul = set(dim_pais_csv.loc[dim_pais_csv["bloco_mercosul"].str.lower() == "true", "iso2"])
        assert {"AR", "PY", "UY"} <= mercosul

    def test_passa_na_validacao_da_gold(self, dim_pais_csv):
        df = dim_pais_csv.copy()
        for col in gold.BLOCOS:
            df[col] = df[col].str.strip().str.lower().eq("true")
        assert gold.validar_dim_pais(df) == []


# ---------------------------------------------------------------------------
# dim_ncm.csv
# ---------------------------------------------------------------------------

class TestDimNcmCsv:
    def test_passa_na_preparacao_da_gold(self, dim_ncm_csv):
        """Reúne duplicidade de versão, sobreposição e datas inválidas."""
        _, erros, _ = gold.preparar_dim_ncm(dim_ncm_csv)
        assert erros == []

    def test_ncm_com_8_digitos(self, dim_ncm_csv):
        assert dim_ncm_csv["ncm"].str.fullmatch(r"\d{8}").all()

    def test_capitulo_e_posicao_coerentes_com_o_ncm(self, dim_ncm_csv):
        assert (dim_ncm_csv["capitulo"] == dim_ncm_csv["ncm"].str[:2]).all()
        assert (dim_ncm_csv["posicao"] == dim_ncm_csv["ncm"].str[:4]).all()

    def test_versao_e_unica(self, dim_ncm_csv):
        df, _, _ = gold.preparar_dim_ncm(dim_ncm_csv)
        assert df["ncm_versao_id"].is_unique

    def test_historico_comeca_no_corte_da_gold(self, dim_ncm_csv):
        """
        A tabela histórica da RFB começa em abril/2022, e a gold.periodo_inicial
        existe por causa disso. Se a dimensão começar depois do corte, a gold
        perderia meses.
        """
        df, _, _ = gold.preparar_dim_ncm(dim_ncm_csv)
        assert df["_ini"].min() <= date(2022, 4, 1)

    @pytest.mark.parametrize("ano_mes", ["2022-04", "2023-10", "2023-11", "2024-12"])
    def test_todo_ncm_tem_no_maximo_uma_versao_por_mes(self, dim_ncm_csv, ano_mes):
        df, _, _ = gold.preparar_dim_ncm(dim_ncm_csv)
        vig = gold.versoes_vigentes(df, gold.data_referencia(ano_mes))
        assert vig["ncm"].is_unique

    def test_mudancas_de_versao_comecam_no_dia_1(self, dim_ncm_csv):
        """A resolução pela data de fim de mês só é inequívoca se for assim."""
        _, _, avisos = gold.preparar_dim_ncm(dim_ncm_csv)
        assert not any("fora do dia 1" in a for a in avisos)
