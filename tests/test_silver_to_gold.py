"""
Testes de silver_to_gold.py

Cobre as regras de negócio da gold:
  - datas e data de referência (último dia do mês)
  - preparação e validação da dim_ncm (versões de NCM, SCD tipo 2)
  - resolução da versão vigente no mês
  - dim_tempo e dim_pais
  - schema da silver
  - construção da fato
  - data de corte da gold (gold.periodo_inicial), exercitada pelo main()
    com S3 simulado em memória

Nenhum teste acessa a AWS.
"""

import logging
from datetime import date

import pandas as pd
import pytest

import silver_to_gold as gold

CHINA = "CHINA, REPUBLICA POPULAR"
EUA = "ESTADOS UNIDOS"
ALBANIA = "ALBANIA, REPUBLICA  DA"  # espaço duplo preservado, como na RFB


# ---------------------------------------------------------------------------
# Datas
# ---------------------------------------------------------------------------

class TestParseData:
    @pytest.mark.parametrize("texto, esperado", [
        ("2023-11-01", date(2023, 11, 1)),
        ("01/11/2023", date(2023, 11, 1)),
        ("2023-11-01 00:00:00", date(2023, 11, 1)),
        ("01/11/2023 00:00:00", date(2023, 11, 1)),
        ("  2023-11-01  ", date(2023, 11, 1)),
        ("9999-12-31", date(9999, 12, 31)),
    ])
    def test_formatos_aceitos(self, texto, esperado):
        assert gold.parse_data(texto) == esperado

    @pytest.mark.parametrize("texto", [None, "", "   ", "nan", "NaN", "lixo", "2023-13-45"])
    def test_vazio_ou_invalido_vira_none(self, texto):
        assert gold.parse_data(texto) is None


class TestDataReferencia:
    @pytest.mark.parametrize("ano_mes, esperado", [
        ("2023-10", date(2023, 10, 31)),
        ("2023-11", date(2023, 11, 30)),
        ("2023-02", date(2023, 2, 28)),
        ("2024-02", date(2024, 2, 29)),   # bissexto
        ("2022-04", date(2022, 4, 30)),   # primeiro mês da gold
        ("2023-12", date(2023, 12, 31)),
    ])
    def test_ultimo_dia_do_mes(self, ano_mes, esperado):
        assert gold.data_referencia(ano_mes) == esperado


# ---------------------------------------------------------------------------
# dim_ncm
# ---------------------------------------------------------------------------

class TestPrepararDimNcm:
    def test_dimensao_valida_nao_tem_erro_nem_aviso(self, dim_ncm_raw):
        df, erros, avisos = gold.preparar_dim_ncm(dim_ncm_raw)
        assert erros == []
        assert avisos == []

    def test_cria_id_de_versao_com_ncm_e_data_de_inicio(self, dim_ncm_raw):
        df, _, _ = gold.preparar_dim_ncm(dim_ncm_raw)
        assert set(df["ncm_versao_id"]) == {
            "21069030_20220401", "21069030_20231101",
            "84713012_20220401", "87032100_20240101",
        }

    def test_id_de_versao_e_unico(self, dim_ncm_raw):
        df, _, _ = gold.preparar_dim_ncm(dim_ncm_raw)
        assert df["ncm_versao_id"].is_unique

    def test_mistura_de_formatos_de_data_gera_o_mesmo_resultado(self, dim_ncm_raw):
        # A fixture usa '01/11/2023' em uma versão e ISO nas demais.
        df, _, _ = gold.preparar_dim_ncm(dim_ncm_raw)
        v2 = df[df["ncm_versao_id"] == "21069030_20231101"].iloc[0]
        assert v2["_ini"] == date(2023, 11, 1)

    def test_vigencia_fim_vazia_vira_sentinela(self, dim_ncm_raw):
        dim_ncm_raw.loc[2, "vigencia_fim"] = ""
        df, erros, _ = gold.preparar_dim_ncm(dim_ncm_raw)
        assert erros == []
        assert df.loc[2, "_fim"] == gold.DATA_VIGENTE == date(9999, 12, 31)

    def test_nao_altera_o_dataframe_de_entrada(self, dim_ncm_raw):
        colunas_antes = list(dim_ncm_raw.columns)
        gold.preparar_dim_ncm(dim_ncm_raw)
        assert list(dim_ncm_raw.columns) == colunas_antes

    def test_coluna_obrigatoria_ausente(self, dim_ncm_raw):
        df, erros, _ = gold.preparar_dim_ncm(dim_ncm_raw.drop(columns=["posicao"]))
        assert len(erros) == 1
        assert "posicao" in erros[0]

    def test_vigencia_inicio_invalida(self, dim_ncm_raw):
        dim_ncm_raw.loc[0, "vigencia_inicio"] = "sem data"
        _, erros, _ = gold.preparar_dim_ncm(dim_ncm_raw)
        assert any("vigencia_inicio" in e for e in erros)

    def test_inicio_posterior_ao_fim(self, dim_ncm_raw):
        dim_ncm_raw.loc[2, "vigencia_inicio"] = "2025-01-01"
        dim_ncm_raw.loc[2, "vigencia_fim"] = "2024-01-01"
        _, erros, _ = gold.preparar_dim_ncm(dim_ncm_raw)
        assert any("posterior ao fim" in e for e in erros)

    def test_versao_duplicada(self, dim_ncm_raw):
        duplicada = pd.concat([dim_ncm_raw, dim_ncm_raw.iloc[[2]]], ignore_index=True)
        _, erros, _ = gold.preparar_dim_ncm(duplicada)
        assert any("duplicado" in e for e in erros)

    def test_vigencias_sobrepostas(self, dim_ncm_raw):
        # a primeira versão passa a terminar depois do início da segunda
        dim_ncm_raw.loc[0, "vigencia_fim"] = "2023-11-15"
        _, erros, _ = gold.preparar_dim_ncm(dim_ncm_raw)
        assert any("sobrepostas" in e for e in erros)

    def test_versoes_consecutivas_nao_sao_sobreposicao(self, dim_ncm_raw):
        # fim em 31/10 e início em 01/11: contíguas, sem sobreposição
        _, erros, _ = gold.preparar_dim_ncm(dim_ncm_raw)
        assert not any("sobrepostas" in e for e in erros)

    def test_mudanca_fora_do_dia_1_gera_aviso_e_nao_erro(self, dim_ncm_raw):
        dim_ncm_raw.loc[0, "vigencia_fim"] = "2023-11-14"
        dim_ncm_raw.loc[1, "vigencia_inicio"] = "2023-11-15"
        _, erros, avisos = gold.preparar_dim_ncm(dim_ncm_raw)
        assert erros == []
        assert any("fora do dia 1" in a for a in avisos)


class TestVersoesVigentes:
    """Regressão do bug original: 19 NCMs duplicados por ignorar a vigência."""

    def _id(self, dim_ncm, ncm, ref):
        v = gold.versoes_vigentes(dim_ncm, ref)
        return v.loc[v["ncm"] == ncm, "ncm_versao_id"].tolist()

    def test_caso_real_mes_antes_da_mudanca(self, dim_ncm):
        assert self._id(dim_ncm, "21069030", date(2023, 10, 31)) == ["21069030_20220401"]

    def test_caso_real_mes_da_mudanca(self, dim_ncm):
        assert self._id(dim_ncm, "21069030", date(2023, 11, 30)) == ["21069030_20231101"]

    def test_fronteira_exata_entre_versoes(self, dim_ncm):
        # último dia da versão 1 e primeiro dia da versão 2
        assert self._id(dim_ncm, "21069030", date(2023, 10, 31)) == ["21069030_20220401"]
        assert self._id(dim_ncm, "21069030", date(2023, 11, 1)) == ["21069030_20231101"]

    @pytest.mark.parametrize("ref", [
        date(2022, 4, 30), date(2023, 6, 30), date(2023, 10, 31),
        date(2023, 11, 30), date(2026, 8, 31),
    ])
    def test_nunca_mais_de_uma_versao_por_ncm(self, dim_ncm, ref):
        vig = gold.versoes_vigentes(dim_ncm, ref)
        assert vig["ncm"].is_unique

    def test_ncm_ainda_nao_existente_nao_tem_versao(self, dim_ncm):
        assert self._id(dim_ncm, "87032100", date(2023, 12, 31)) == []

    def test_ncm_passa_a_existir_na_data_de_inicio(self, dim_ncm):
        assert self._id(dim_ncm, "87032100", date(2024, 1, 31)) == ["87032100_20240101"]

    def test_versao_sem_fim_continua_vigente(self, dim_ncm):
        assert self._id(dim_ncm, "84713012", date(2030, 1, 31)) == ["84713012_20220401"]


def test_dim_ncm_para_gold_formata_datas_e_coloca_id_na_primeira_coluna(dim_ncm):
    out = gold.dim_ncm_para_gold(dim_ncm)
    assert out.columns[0] == "ncm_versao_id"
    assert "_ini" not in out.columns and "_fim" not in out.columns
    assert set(out["vigencia_inicio"]) <= {"2022-04-01", "2023-11-01", "2024-01-01"}
    assert out.loc[out["ncm"] == "84713012", "vigencia_fim"].iloc[0] == "9999-12-31"
    assert out["ncm_versao_id"].is_unique


# ---------------------------------------------------------------------------
# dim_pais
# ---------------------------------------------------------------------------

class TestValidarDimPais:
    def test_valida(self, dim_pais):
        assert gold.validar_dim_pais(dim_pais) == []

    def test_chave_duplicada(self, dim_pais):
        dup = pd.concat([dim_pais, dim_pais.iloc[[0]]], ignore_index=True)
        erros = gold.validar_dim_pais(dup)
        assert any("duplicado" in e for e in erros)

    @pytest.mark.parametrize("coluna", ["pais_origem_rfb", "iso3", "bloco_g20"])
    def test_coluna_obrigatoria_ausente(self, dim_pais, coluna):
        erros = gold.validar_dim_pais(dim_pais.drop(columns=[coluna]))
        assert any(coluna in e for e in erros)


def test_carregar_dimensoes_le_csv_como_texto_e_converte_blocos(tmp_path):
    """
    Também fixa a chave do config: o script lê `caminhos.dados_ref`.
    Zeros à esquerda do NCM devem ser preservados.
    """
    (tmp_path / "data_ref").mkdir()
    (tmp_path / "data_ref" / "dim_ncm.csv").write_text(
        "ncm,capitulo,posicao,vigencia_inicio,vigencia_fim\n"
        "01012100,01,0101,2022-04-01,9999-12-31\n", encoding="utf-8")
    (tmp_path / "data_ref" / "dim_pais.csv").write_text(
        "pais_origem_rfb,pais_nome,iso2,iso3,bloco_mercosul,bloco_ocde,bloco_brics,bloco_g20\n"
        "BRASIL,Brasil,BR,BRA,True,False,True,True\n", encoding="utf-8-sig")
    (tmp_path / "config.yaml").write_text("x: 1", encoding="utf-8")
    cfg = {"caminhos": {"dados_ref": {"dim_ncm": "data_ref/dim_ncm.csv",
                                      "dim_pais": "data_ref/dim_pais.csv"}}}

    dim_ncm, dim_pais = gold.carregar_dimensoes(str(tmp_path / "config.yaml"), cfg)

    assert dim_ncm.loc[0, "ncm"] == "01012100"
    assert dim_ncm.loc[0, "capitulo"] == "01"
    assert dim_pais.loc[0, "bloco_mercosul"] is True or dim_pais.loc[0, "bloco_mercosul"] == True  # noqa: E712
    assert dim_pais.loc[0, "bloco_ocde"] == False  # noqa: E712
    assert dim_pais["bloco_g20"].dtype == bool


# ---------------------------------------------------------------------------
# dim_tempo
# ---------------------------------------------------------------------------

class TestDimTempo:
    def test_comeca_no_periodo_inicial(self):
        df = gold.construir_dim_tempo("2022-04")
        assert df["ano_mes"].iloc[0] == "2022-04"

    def test_termina_no_mes_corrente(self):
        hoje = date.today()
        df = gold.construir_dim_tempo("2022-04")
        assert df["ano_mes"].iloc[-1] == f"{hoje.year:04d}-{hoje.month:02d}"

    def test_um_registro_por_mes_sem_buracos(self):
        hoje = date.today()
        df = gold.construir_dim_tempo("2022-04")
        esperado = (hoje.year - 2022) * 12 + (hoje.month - 4) + 1
        assert len(df) == esperado
        assert df["ano_mes"].is_unique

    def test_virada_de_ano(self):
        meses = gold.construir_dim_tempo("2022-11")["ano_mes"].tolist()[:4]
        assert meses == ["2022-11", "2022-12", "2023-01", "2023-02"]

    def test_atributos_do_primeiro_mes(self):
        linha = gold.construir_dim_tempo("2022-04").iloc[0]
        assert linha["ano"] == 2022 and linha["mes"] == 4
        assert linha["nome_mes"] == "Abril"
        assert linha["trimestre"] == "T2"
        assert linha["ano_trimestre"] == "2022-T2"
        assert linha["primeiro_dia"] == "2022-04-01"

    def test_nao_inclui_meses_anteriores_ao_corte(self):
        df = gold.construir_dim_tempo("2022-04")
        assert (df["ano_mes"] < "2022-04").sum() == 0


# ---------------------------------------------------------------------------
# Schema da silver
# ---------------------------------------------------------------------------

class TestValidarSchemaSilver:
    def test_silver_valida(self, criar_silver):
        silver = criar_silver("2023-10", [("21069030", CHINA)])
        assert gold.validar_schema_silver(silver, "2023-10") == []

    def test_reporta_todas_as_colunas_faltantes_de_uma_vez(self, criar_silver):
        """Silver gerada por versão antiga do bronze_to_silver (sem derivadas)."""
        silver = criar_silver("2026-04", [("21069030", CHINA)]).drop(
            columns=["capitulo_ncm", "posicao_ncm"])
        erros = gold.validar_schema_silver(silver, "2026-04")
        assert len(erros) == 1
        assert "capitulo_ncm" in erros[0] and "posicao_ncm" in erros[0]
        assert "2026-04" in erros[0]

    def test_metrica_como_texto(self, criar_silver):
        silver = criar_silver("2026-04", [("21069030", CHINA)])
        silver["vmle_soma_dolar_truncado"] = silver["vmle_soma_dolar_truncado"].astype(str)
        erros = gold.validar_schema_silver(silver, "2026-04")
        assert any("não numérica" in e for e in erros)

    def test_ncm_com_7_digitos(self, criar_silver):
        silver = criar_silver("2023-10", [("2106903", CHINA)])
        erros = gold.validar_schema_silver(silver, "2023-10")
        assert any("tamanho diferente de 8" in e for e in erros)

    def test_nulo_na_chave(self, criar_silver):
        silver = criar_silver("2023-10", [("21069030", CHINA)])
        silver["pais_origem"] = None
        erros = gold.validar_schema_silver(silver, "2023-10")
        assert any("nulo" in e and "pais_origem" in e for e in erros)

    def test_chave_de_texto_numerica(self, criar_silver):
        silver = criar_silver("2023-10", [("21069030", CHINA)])
        silver["ncm"] = silver["ncm"].astype("int64")
        erros = gold.validar_schema_silver(silver, "2023-10")
        assert any("deveria ser texto" in e for e in erros)


# ---------------------------------------------------------------------------
# Fato
# ---------------------------------------------------------------------------

class TestConstruirFato:
    def test_associa_a_versao_vigente_no_mes_antes_da_mudanca(self, criar_silver, dim_ncm, dim_pais):
        silver = criar_silver("2023-10", [("21069030", CHINA)])
        fato, erros, _ = gold.construir_fato(silver, dim_ncm, dim_pais, "2023-10")
        assert erros == []
        assert fato["ncm_versao_id"].tolist() == ["21069030_20220401"]

    def test_associa_a_versao_vigente_no_mes_da_mudanca(self, criar_silver, dim_ncm, dim_pais):
        silver = criar_silver("2023-11", [("21069030", CHINA)])
        fato, erros, _ = gold.construir_fato(silver, dim_ncm, dim_pais, "2023-11")
        assert erros == []
        assert fato["ncm_versao_id"].tolist() == ["21069030_20231101"]

    def test_mantem_o_grao_da_silver(self, criar_silver, dim_ncm, dim_pais):
        silver = criar_silver("2023-10", [
            ("21069030", CHINA, "S/NVE"),
            ("21069030", CHINA, "PO.2106.AA.9999"),   # mesmo NCM e país, outra NVE
            ("21069030", EUA),
            ("84713012", CHINA),
            ("84713012", ALBANIA),
        ])
        fato, erros, _ = gold.construir_fato(silver, dim_ncm, dim_pais, "2023-10")
        assert erros == []
        assert len(fato) == len(silver)                  # o join m:1 não duplica nem perde linhas
        assert not fato.duplicated(gold.CHAVES).any()

    def test_conserva_a_soma_de_vmle(self, criar_silver, dim_ncm, dim_pais):
        silver = criar_silver("2023-10", [("21069030", CHINA), ("84713012", EUA)], vmle=250.0)
        fato, _, _ = gold.construir_fato(silver, dim_ncm, dim_pais, "2023-10")
        assert fato["vmle_soma_dolar_truncado"].sum() == silver["vmle_soma_dolar_truncado"].sum()

    def test_nao_agrega_nem_altera_metricas_nao_aditivas(self, criar_silver, dim_ncm, dim_pais):
        silver = criar_silver("2023-10", [("21069030", CHINA), ("84713012", EUA)])
        silver.loc[0, "fob_kg_mediana_dolar_truncado"] = 3.14
        fato, _, _ = gold.construir_fato(silver, dim_ncm, dim_pais, "2023-10")
        linha = fato[fato["ncm"] == "21069030"].iloc[0]
        assert linha["fob_kg_mediana_dolar_truncado"] == 3.14

    def test_colunas_da_fato(self, criar_silver, dim_ncm, dim_pais):
        silver = criar_silver("2023-10", [("21069030", CHINA)])
        fato, _, _ = gold.construir_fato(silver, dim_ncm, dim_pais, "2023-10")
        assert list(fato.columns[:6]) == ["ano_mes", "ncm", "nves", "pais_origem",
                                          "ncm_versao_id", "ume"]
        # atributos da hierarquia ficam só na dimensão
        assert "capitulo_ncm" not in fato.columns
        assert "posicao_ncm" not in fato.columns

    def test_pais_com_espaco_duplo_casa_exatamente(self, criar_silver, dim_ncm, dim_pais):
        silver = criar_silver("2023-10", [("84713012", ALBANIA)])
        _, erros, _ = gold.construir_fato(silver, dim_ncm, dim_pais, "2023-10")
        assert erros == []

    def test_pais_com_espaco_normalizado_nao_casa(self, criar_silver, dim_ncm, dim_pais):
        """Documenta a decisão: o join é exato, sem normalizar espaços."""
        silver = criar_silver("2023-10", [("84713012", "ALBANIA, REPUBLICA DA")])
        _, erros, _ = gold.construir_fato(silver, dim_ncm, dim_pais, "2023-10")
        assert any("sem correspondência em dim_pais" in e for e in erros)

    def test_pais_ausente_na_dimensao(self, criar_silver, dim_ncm, dim_pais):
        silver = criar_silver("2023-10", [("21069030", "ATLANTIDA")])
        fato, erros, _ = gold.construir_fato(silver, dim_ncm, dim_pais, "2023-10")
        assert fato.empty
        assert any("ATLANTIDA" in e for e in erros)

    def test_ncm_sem_versao_vigente_no_mes(self, criar_silver, dim_ncm, dim_pais):
        silver = criar_silver("2023-12", [("87032100", CHINA)])   # só existe desde 2024-01
        fato, erros, _ = gold.construir_fato(silver, dim_ncm, dim_pais, "2023-12")
        assert fato.empty
        assert any("sem versão vigente" in e and "87032100" in e for e in erros)

    def test_chave_duplicada_na_silver(self, criar_silver, dim_ncm, dim_pais):
        silver = criar_silver("2023-10", [("21069030", CHINA), ("21069030", CHINA)])
        fato, erros, _ = gold.construir_fato(silver, dim_ncm, dim_pais, "2023-10")
        assert fato.empty
        assert any("duplicada" in e for e in erros)

    def test_hierarquia_divergente_da_versao_vigente(self, criar_silver, dim_ncm, dim_pais):
        silver = criar_silver("2023-10", [("21069030", CHINA)])
        silver["capitulo_ncm"] = "99"
        fato, erros, _ = gold.construir_fato(silver, dim_ncm, dim_pais, "2023-10")
        assert fato.empty
        assert any("divergentes" in e for e in erros)

    def test_schema_invalido_interrompe_antes_de_qualquer_regra(self, criar_silver, dim_ncm, dim_pais):
        silver = criar_silver("2023-10", [("21069030", CHINA)]).drop(columns=["capitulo_ncm"])
        fato, erros, _ = gold.construir_fato(silver, dim_ncm, dim_pais, "2023-10")
        assert fato.empty
        assert len(erros) == 1 and "capitulo_ncm" in erros[0]

    def test_aviso_quando_versao_comeca_no_meio_do_mes(self, criar_silver, dim_pais, dim_ncm_raw):
        dim_ncm_raw.loc[0, "vigencia_fim"] = "2023-11-14"
        dim_ncm_raw.loc[1, "vigencia_inicio"] = "2023-11-15"
        dim, _, _ = gold.preparar_dim_ncm(dim_ncm_raw)
        silver = criar_silver("2023-11", [("21069030", CHINA)])
        fato, erros, avisos = gold.construir_fato(silver, dim, dim_pais, "2023-11")
        assert erros == []
        assert any("começam dentro do mês" in a for a in avisos)
        assert fato["ncm_versao_id"].tolist() == ["21069030_20231115"]  # versão do último dia

    def test_ordena_pelas_chaves(self, criar_silver, dim_ncm, dim_pais):
        silver = criar_silver("2023-10", [("84713012", EUA), ("21069030", CHINA)])
        fato, _, _ = gold.construir_fato(silver, dim_ncm, dim_pais, "2023-10")
        assert fato["ncm"].tolist() == ["21069030", "84713012"]


# ---------------------------------------------------------------------------
# Listagem da silver
# ---------------------------------------------------------------------------

class FakePaginador:
    def __init__(self, paginas):
        self.paginas = paginas

    def paginate(self, **_):
        return iter(self.paginas)


class FakeS3:
    """
    S3 em memória. Só aceita as operações que a gold usa. Qualquer outra
    (em especial delete_*) falha o teste.
    """
    PERMITIDAS = {"head_bucket", "get_paginator"}

    def __init__(self, chaves_existentes=()):
        self.chaves_existentes = list(chaves_existentes)
        self.chamadas = []

    def head_bucket(self, Bucket):
        self.chamadas.append("head_bucket")

    def get_paginator(self, nome):
        assert nome == "list_objects_v2"
        return FakePaginador([{"Contents": [{"Key": k} for k in self.chaves_existentes]}])

    def __getattr__(self, nome):
        raise AssertionError(f"Operação S3 inesperada: {nome}")


class TestListarSilver:
    def test_lista_apenas_parquets_com_particao(self):
        s3 = FakeS3([
            "silver/estatisticas_di/ano_mes=2023-10/estatisticas_di_2023_10.parquet",
            "silver/estatisticas_di/ano_mes=2023-11/estatisticas_di_2023_11.parquet",
            "silver/estatisticas_di/ano_mes=2023-11/_SUCCESS",
            "silver/estatisticas_di/sem_particao.parquet",
        ])
        itens = gold.listar_silver(s3, "bucket", "silver/estatisticas_di/")
        assert [i["ano_mes"] for i in itens] == ["2023-10", "2023-11"]
        assert itens[0]["nome"] == "estatisticas_di_2023_10.parquet"


# ---------------------------------------------------------------------------
# Data de corte da gold (gold.periodo_inicial) — main() com S3 simulado
# ---------------------------------------------------------------------------

@pytest.fixture
def ambiente_gold(monkeypatch, criar_silver, dim_ncm_raw, dim_pais):
    """
    Monta um main() isolado: sem S3, sem config.yaml, sem disco.

    Devolve um objeto com:
      .gravados  → chaves gravadas na gold
      .lidos     → chaves lidas da silver
      .rodar(...) → executa gold.main
    """

    class Ambiente:
        def __init__(self):
            self.gravados: dict[str, pd.DataFrame] = {}
            self.lidos: list[str] = []
            self.silver_por_chave: dict[str, pd.DataFrame] = {}
            self.itens_silver: list[dict] = []
            self.fatos_existentes: list[str] = []
            self.cfg = {
                "aws": {"bucket": "bucket-teste", "profile": "p", "regiao": "sa-east-1"},
                "s3": {"silver": "silver/estatisticas_di/", "gold": "gold/estatisticas_di/"},
                "log": {"nivel": "INFO", "formato": "%(message)s"},
                "gold": {"periodo_inicial": "2022-04"},
            }

        def adicionar_silver(self, ano_mes, registros=None):
            registros = registros or [("84713012", CHINA)]
            chave = f"silver/estatisticas_di/ano_mes={ano_mes}/estatisticas_di_{ano_mes.replace('-', '_')}.parquet"
            self.itens_silver.append({"nome": chave.split("/")[-1], "chave": chave, "ano_mes": ano_mes})
            self.silver_por_chave[chave] = criar_silver(ano_mes, registros)

        def rodar(self, ano_mes=None, reprocessar=False, dry_run=False):
            gold.main("config.yaml", ano_mes, reprocessar, dry_run)

        def fatos_gravados(self):
            return sorted(k.split("ano_mes=")[1].split("/")[0]
                          for k in self.gravados if "fato_importacao_mensal" in k)

    amb = Ambiente()
    s3 = FakeS3()

    monkeypatch.setattr(gold, "carregar_config", lambda caminho: amb.cfg)
    monkeypatch.setattr(gold, "configurar_log", lambda cfg: logging.getLogger("teste_gold"))
    monkeypatch.setattr(gold, "criar_cliente_s3", lambda cfg: s3)
    monkeypatch.setattr(gold, "carregar_dimensoes", lambda caminho, cfg: (dim_ncm_raw, dim_pais))
    monkeypatch.setattr(gold, "listar_silver", lambda c, b, p: list(amb.itens_silver))

    def _ler(cliente, bucket, chave):
        amb.lidos.append(chave)
        return amb.silver_por_chave[chave].copy()

    def _gravar(cliente, bucket, chave, df, dry_run):
        amb.gravados[chave] = df
        return True

    monkeypatch.setattr(gold, "ler_parquet_s3", _ler)
    monkeypatch.setattr(gold, "gravar_parquet_s3", _gravar)

    # fatos já existentes na gold, vistos pelo paginator do FakeS3
    def _paginator(nome):
        return FakePaginador([{"Contents": [{"Key": k} for k in amb.fatos_existentes]}])

    s3.get_paginator = _paginator
    amb.s3 = s3
    return amb


class TestDataDeCorteDaGold:
    def test_meses_anteriores_ao_corte_nao_sao_lidos_nem_gravados(self, ambiente_gold):
        for mes in ["2021-01", "2021-12", "2022-03", "2022-04", "2023-10"]:
            ambiente_gold.adicionar_silver(mes)

        ambiente_gold.rodar()

        assert ambiente_gold.fatos_gravados() == ["2022-04", "2023-10"]
        assert not any("ano_mes=2022-03" in k for k in ambiente_gold.lidos)
        assert not any("ano_mes=2021" in k for k in ambiente_gold.lidos)

    def test_o_proprio_mes_de_corte_e_processado(self, ambiente_gold):
        ambiente_gold.adicionar_silver("2022-04")
        ambiente_gold.rodar()
        assert ambiente_gold.fatos_gravados() == ["2022-04"]

    def test_mes_anterior_ao_corte_falharia_se_fosse_processado(self, ambiente_gold, dim_ncm,
                                                                dim_pais, criar_silver):
        """
        Por que o corte existe: em 2022-03 a dim_ncm não tem versão vigente
        para nenhum NCM, então a fato seria rejeitada.
        """
        silver = criar_silver("2022-03", [("84713012", CHINA)])
        fato, erros, _ = gold.construir_fato(silver, dim_ncm, dim_pais, "2022-03")
        assert fato.empty
        assert any("sem versão vigente" in e for e in erros)

    def test_filtro_de_mes_anterior_ao_corte_encerra_com_erro(self, ambiente_gold):
        ambiente_gold.adicionar_silver("2022-03")
        ambiente_gold.adicionar_silver("2022-04")
        with pytest.raises(SystemExit) as saida:
            ambiente_gold.rodar(ano_mes="2022-03")
        assert saida.value.code == 1
        assert ambiente_gold.fatos_gravados() == []

    def test_filtro_de_mes_valido_processa_somente_ele(self, ambiente_gold):
        for mes in ["2022-04", "2023-10", "2023-11"]:
            ambiente_gold.adicionar_silver(mes)
        ambiente_gold.rodar(ano_mes="2023-10")
        assert ambiente_gold.fatos_gravados() == ["2023-10"]

    def test_corte_vem_do_config(self, ambiente_gold):
        ambiente_gold.cfg["gold"]["periodo_inicial"] = "2023-11"
        for mes in ["2022-04", "2023-10", "2023-11", "2023-12"]:
            ambiente_gold.adicionar_silver(mes)
        ambiente_gold.rodar()
        assert ambiente_gold.fatos_gravados() == ["2023-11", "2023-12"]

    def test_sem_bloco_gold_no_config_usa_2022_04(self, ambiente_gold):
        del ambiente_gold.cfg["gold"]
        for mes in ["2022-03", "2022-04"]:
            ambiente_gold.adicionar_silver(mes)
        ambiente_gold.rodar()
        assert ambiente_gold.fatos_gravados() == ["2022-04"]

    def test_dim_tempo_comeca_no_corte(self, ambiente_gold):
        ambiente_gold.adicionar_silver("2022-04")
        ambiente_gold.rodar()
        dim_tempo = ambiente_gold.gravados["gold/estatisticas_di/dim_tempo/dim_tempo.parquet"]
        assert dim_tempo["ano_mes"].iloc[0] == "2022-04"

    def test_somente_meses_anteriores_ao_corte_grava_so_dimensoes(self, ambiente_gold):
        for mes in ["2021-01", "2021-02"]:
            ambiente_gold.adicionar_silver(mes)
        ambiente_gold.rodar()
        assert ambiente_gold.fatos_gravados() == []
        assert "gold/estatisticas_di/dim_ncm/dim_ncm.parquet" in ambiente_gold.gravados


class TestMainGold:
    def test_sempre_regrava_as_tres_dimensoes(self, ambiente_gold):
        ambiente_gold.adicionar_silver("2023-10")
        ambiente_gold.rodar()
        for nome in ["dim_ncm", "dim_pais", "dim_tempo"]:
            assert f"gold/estatisticas_di/{nome}/{nome}.parquet" in ambiente_gold.gravados

    def test_chave_da_fato_segue_o_particionamento_hive(self, ambiente_gold):
        ambiente_gold.adicionar_silver("2023-10")
        ambiente_gold.rodar()
        assert ("gold/estatisticas_di/fato_importacao_mensal/ano_mes=2023-10/"
                "fato_importacao_2023_10.parquet") in ambiente_gold.gravados

    def test_cada_mes_usa_a_versao_de_ncm_do_proprio_periodo(self, ambiente_gold):
        ambiente_gold.adicionar_silver("2023-10", [("21069030", CHINA)])
        ambiente_gold.adicionar_silver("2023-11", [("21069030", CHINA)])
        ambiente_gold.rodar()
        base = "gold/estatisticas_di/fato_importacao_mensal/"
        f10 = ambiente_gold.gravados[f"{base}ano_mes=2023-10/fato_importacao_2023_10.parquet"]
        f11 = ambiente_gold.gravados[f"{base}ano_mes=2023-11/fato_importacao_2023_11.parquet"]
        assert f10["ncm_versao_id"].iloc[0] == "21069030_20220401"
        assert f11["ncm_versao_id"].iloc[0] == "21069030_20231101"

    def test_pula_meses_ja_existentes_na_gold(self, ambiente_gold):
        ambiente_gold.adicionar_silver("2023-10")
        ambiente_gold.adicionar_silver("2023-11")
        ambiente_gold.fatos_existentes = [
            "gold/estatisticas_di/fato_importacao_mensal/ano_mes=2023-10/fato_importacao_2023_10.parquet"]
        ambiente_gold.rodar()
        assert ambiente_gold.fatos_gravados() == ["2023-11"]

    def test_reprocessar_regrava_meses_existentes(self, ambiente_gold):
        ambiente_gold.adicionar_silver("2023-10")
        ambiente_gold.fatos_existentes = [
            "gold/estatisticas_di/fato_importacao_mensal/ano_mes=2023-10/fato_importacao_2023_10.parquet"]
        ambiente_gold.rodar(reprocessar=True)
        assert ambiente_gold.fatos_gravados() == ["2023-10"]

    def test_mes_com_erro_nao_grava_e_encerra_com_codigo_1_mas_processa_os_demais(self, ambiente_gold):
        ambiente_gold.adicionar_silver("2023-10", [("84713012", "ATLANTIDA")])   # país inválido
        ambiente_gold.adicionar_silver("2023-11", [("84713012", CHINA)])
        with pytest.raises(SystemExit) as saida:
            ambiente_gold.rodar()
        assert saida.value.code == 1
        assert ambiente_gold.fatos_gravados() == ["2023-11"]

    def test_dim_ncm_invalida_interrompe_antes_de_gravar_qualquer_coisa(self, ambiente_gold, monkeypatch,
                                                                         dim_ncm_raw, dim_pais):
        dim_ncm_raw.loc[0, "vigencia_fim"] = "2023-11-15"   # sobreposição
        monkeypatch.setattr(gold, "carregar_dimensoes", lambda c, cfg: (dim_ncm_raw, dim_pais))
        ambiente_gold.adicionar_silver("2023-10")
        with pytest.raises(SystemExit) as saida:
            ambiente_gold.rodar()
        assert saida.value.code == 1
        assert ambiente_gold.gravados == {}

    def test_nenhuma_silver_encerra_com_erro(self, ambiente_gold):
        with pytest.raises(SystemExit) as saida:
            ambiente_gold.rodar()
        assert saida.value.code == 1

    def test_nunca_apaga_nada_no_s3(self, ambiente_gold):
        """O FakeS3 falha qualquer operação fora de head_bucket/get_paginator."""
        ambiente_gold.adicionar_silver("2023-10")
        ambiente_gold.rodar()
        assert not any("delete" in c.lower() for c in ambiente_gold.s3.chamadas)
