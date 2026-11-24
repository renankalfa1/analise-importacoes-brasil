"""
Testes de bronze_to_silver.py

Cobrem as regras de tratamento da silver (transformar), as validações de
schema e de negócio, e o contrato com a gold: a silver produzida aqui precisa
passar em silver_to_gold.validar_schema_silver.

Nenhum teste acessa a AWS.
"""

import numpy as np
import pandas as pd
import pytest

import bronze_to_silver as silver
import silver_to_gold as gold

CHINA = "CHINA, REPUBLICA POPULAR"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg():
    return {
        "esquema": {
            "colunas_obrigatorias": ["ano_mes", "ncm", "nves", "pais_origem", "ume"],
            "chave_unica": ["ano_mes", "ncm", "nves", "pais_origem"],
            "ncm_tamanho": 8,
        },
        "qualidade": {"variacao_linhas_mes_pct": 30},
    }


@pytest.fixture
def criar_bronze():
    """
    Bronze sintética: tudo texto, como sai do raw_to_bronze
    (dtype=str, keep_default_na=False). Todas as métricas valem '10',
    então min == max, Q1 == Q3 e a mediana fica dentro de [Q1, Q3].
    """

    def _criar(registros=None, ano_mes="2026-08") -> pd.DataFrame:
        registros = registros or [{}]
        linhas = []
        for i, extra in enumerate(registros):
            linha = {
                "ano_mes": ano_mes,
                "ncm": f"8471301{i}",
                "nves": "S/NVE",
                "nves_nm": "SEM NVE",
                "pais_origem": CHINA,
                "ume": "QUILOGRAMA LIQUIDO",
                "arquivo_origem": f"estatisticas_di_{ano_mes.replace('-', '_')}.csv",
                "data_ingestao": "2026-11-21",
            }
            linha.update({c: "10" for c in silver.COLUNAS_NUMERICAS})
            linha.update(extra)
            linhas.append(linha)
        return pd.DataFrame(linhas)

    return _criar


def tratar(df, cfg, ano_mes="2026-08"):
    return silver.transformar(df.copy(), ano_mes, cfg)


# ---------------------------------------------------------------------------
# transformar: texto
# ---------------------------------------------------------------------------

class TestTransformarTexto:
    @pytest.mark.parametrize("entrada, esperado", [
        ("1012100", "01012100"),      # 7 dígitos: zero à esquerda perdido na leitura
        ("101210", "00101210"),
        ("21069030", "21069030"),     # já completo, não muda
        (" 21069030 ", "21069030"),   # espaços
    ])
    def test_ncm_zfill_8(self, criar_bronze, cfg, entrada, esperado):
        out = tratar(criar_bronze([{"ncm": entrada}]), cfg)
        assert out["ncm"].iloc[0] == esperado

    def test_pais_em_maiusculas_e_sem_espacos_nas_pontas(self, criar_bronze, cfg):
        out = tratar(criar_bronze([{"pais_origem": "  china, republica popular "}]), cfg)
        assert out["pais_origem"].iloc[0] == CHINA

    def test_pais_preserva_espaco_duplo_interno(self, criar_bronze, cfg):
        """A chave da dim_pais é o nome exato da RFB, com o espaço duplo."""
        out = tratar(criar_bronze([{"pais_origem": "ALBANIA, REPUBLICA  DA"}]), cfg)
        assert out["pais_origem"].iloc[0] == "ALBANIA, REPUBLICA  DA"

    def test_ume_vazia_vira_nao_informado(self, criar_bronze, cfg):
        out = tratar(criar_bronze([{"ume": ""}, {"ume": "   "}, {"ume": "UNIDADE"}]), cfg)
        assert out["ume"].tolist() == ["NÃO INFORMADO", "NÃO INFORMADO", "UNIDADE"]

    def test_nves_sem_espacos_nas_pontas(self, criar_bronze, cfg):
        out = tratar(criar_bronze([{"nves": " PO.7318.AA.9999 "}]), cfg)
        assert out["nves"].iloc[0] == "PO.7318.AA.9999"

    def test_nomes_de_colunas_em_minusculas(self, criar_bronze, cfg):
        bronze = criar_bronze().rename(columns={"ncm": " NCM ", "ume": "UME"})
        out = tratar(bronze, cfg)
        assert "ncm" in out.columns and "ume" in out.columns
        assert " NCM " not in out.columns


# ---------------------------------------------------------------------------
# transformar: números
# ---------------------------------------------------------------------------

class TestTransformarNumeros:
    def test_converte_as_27_metricas_para_float64(self, criar_bronze, cfg):
        out = tratar(criar_bronze(), cfg)
        assert len(silver.COLUNAS_NUMERICAS) == 27
        for col in silver.COLUNAS_NUMERICAS:
            assert out[col].dtype == np.float64, col

    def test_quartil_vazio_vira_nan_e_a_linha_permanece(self, criar_bronze, cfg):
        """RFB suprime Q1 e Q3 em distribuições heterogêneas."""
        bronze = criar_bronze([{"fob_kg_quartil1_truncado": "", "fob_kg_quartil3_truncado": ""},
                               {}])
        out = tratar(bronze, cfg)
        assert len(out) == 2
        assert out["fob_kg_quartil1_truncado"].isna().tolist() == [True, False]
        assert out["fob_kg_quartil3_truncado"].isna().tolist() == [True, False]
        assert out["vmle_soma_dolar_truncado"].notna().all()   # demais colunas intactas

    def test_valor_nao_numerico_vira_nan_sem_quebrar(self, criar_bronze, cfg):
        out = tratar(criar_bronze([{"frete_media_dolar_truncado": "abc"}]), cfg)
        assert pd.isna(out["frete_media_dolar_truncado"].iloc[0])

    def test_ponto_decimal(self, criar_bronze, cfg):
        out = tratar(criar_bronze([{"vmle_soma_dolar_truncado": "1234.56"}]), cfg)
        assert out["vmle_soma_dolar_truncado"].iloc[0] == pytest.approx(1234.56)

    def test_coluna_numerica_ausente_e_tolerada(self, criar_bronze, cfg):
        bronze = criar_bronze().drop(columns=["seguro_media_dolar_truncado"])
        out = tratar(bronze, cfg)
        assert "seguro_media_dolar_truncado" not in out.columns


# ---------------------------------------------------------------------------
# transformar: derivadas
# ---------------------------------------------------------------------------

class TestTransformarDerivadas:
    def test_capitulo_e_posicao(self, criar_bronze, cfg):
        out = tratar(criar_bronze([{"ncm": "21069030"}]), cfg)
        assert out["capitulo_ncm"].iloc[0] == "21"
        assert out["posicao_ncm"].iloc[0] == "2106"

    def test_capitulo_com_zero_a_esquerda_apos_zfill(self, criar_bronze, cfg):
        out = tratar(criar_bronze([{"ncm": "1012100"}]), cfg)
        assert out["capitulo_ncm"].iloc[0] == "01"
        assert out["posicao_ncm"].iloc[0] == "0101"

    @pytest.mark.parametrize("nves, tem_nve, multiplo", [
        ("S/NVE", False, False),
        ("PO.7318.AA.9999", True, False),
        ("PO.7318.AA.9999,PO.7318.AB.9999", True, True),
    ])
    def test_flags_de_nve(self, criar_bronze, cfg, nves, tem_nve, multiplo):
        out = tratar(criar_bronze([{"nves": nves}]), cfg)
        assert bool(out["tem_nve"].iloc[0]) is tem_nve
        assert bool(out["nves_multiplo"].iloc[0]) is multiplo


# ---------------------------------------------------------------------------
# validar_schema
# ---------------------------------------------------------------------------

class TestValidarSchema:
    def test_valido(self, criar_bronze, cfg):
        assert silver.validar_schema(tratar(criar_bronze(), cfg), cfg, "2026-08") == []

    def test_coluna_obrigatoria_ausente(self, criar_bronze, cfg):
        df = tratar(criar_bronze(), cfg).drop(columns=["ume"])
        erros = silver.validar_schema(df, cfg, "2026-08")
        assert any("ausentes" in e and "ume" in e for e in erros)

    @pytest.mark.parametrize("ano_mes", ["2026-8", "202608", "2026/08", "agosto", ""])
    def test_ano_mes_invalido(self, criar_bronze, cfg, ano_mes):
        df = tratar(criar_bronze(), cfg)
        erros = silver.validar_schema(df, cfg, ano_mes)
        assert any("ano_mes inválido" in e for e in erros)

    def test_chave_unica_violada(self, criar_bronze, cfg):
        df = tratar(criar_bronze([{"ncm": "21069030"}, {"ncm": "21069030"}]), cfg)
        erros = silver.validar_schema(df, cfg, "2026-08")
        assert any("Chave única violada" in e for e in erros)

    def test_ncm_com_tamanho_invalido_apos_zfill(self, criar_bronze, cfg):
        df = tratar(criar_bronze([{"ncm": "123456789"}]), cfg)   # 9 dígitos: zfill não corrige
        erros = silver.validar_schema(df, cfg, "2026-08")
        assert any("tamanho inválido" in e for e in erros)


# ---------------------------------------------------------------------------
# validar_negocios
# ---------------------------------------------------------------------------

class TestValidarNegocios:
    def valida(self, criar_bronze, cfg, **colunas):
        return silver.validar_negocios(tratar(criar_bronze([colunas]), cfg))

    def test_linha_consistente(self, criar_bronze, cfg):
        erros, avisos = self.valida(criar_bronze, cfg)
        assert erros == [] and avisos == []

    @pytest.mark.parametrize("minimo, maximo", [
        ("fob_kg_minimo_dolar_truncado", "fob_kg_maximo_dolar_truncado"),
        ("fob_ume_minimo_dolar_truncado", "fob_ume_maximo_dolar_truncado"),
        ("frete_minimo_dolar_truncado", "frete_maximo_dolar_truncado"),
    ])
    def test_minimo_maior_que_maximo(self, criar_bronze, cfg, minimo, maximo):
        erros, _ = self.valida(criar_bronze, cfg, **{minimo: "20", maximo: "5"})
        assert any(minimo.replace("_dolar_truncado", "") in e for e in erros)

    def test_quartil1_maior_que_quartil3(self, criar_bronze, cfg):
        erros, _ = self.valida(criar_bronze, cfg,
                               fob_kg_quartil1_truncado="9", fob_kg_quartil3_truncado="3")
        assert any("quartil1 > fob_kg_quartil3" in e for e in erros)

    def test_mediana_fora_dos_quartis_e_aviso_nao_erro(self, criar_bronze, cfg):
        erros, avisos = self.valida(criar_bronze, cfg,
                                    fob_kg_quartil1_truncado="2", fob_kg_quartil3_truncado="3",
                                    fob_kg_mediana_dolar_truncado="9")
        assert erros == []
        assert any("mediana fora" in a for a in avisos)

    def test_quartis_suprimidos_nao_geram_erro_nem_aviso(self, criar_bronze, cfg):
        erros, avisos = self.valida(criar_bronze, cfg,
                                    fob_kg_quartil1_truncado="", fob_kg_quartil3_truncado="")
        assert erros == [] and avisos == []

    @pytest.mark.parametrize("coluna", ["vmle_soma_dolar_truncado", "peso_liq_soma_truncado",
                                        "qtd_ume_soma_truncado"])
    @pytest.mark.parametrize("valor", ["0", "-1"])
    def test_soma_nao_positiva(self, criar_bronze, cfg, coluna, valor):
        erros, _ = self.valida(criar_bronze, cfg, **{coluna: valor})
        assert any(coluna in e for e in erros)

    def test_seguro_zero_e_valido(self, criar_bronze, cfg):
        erros, avisos = self.valida(criar_bronze, cfg, seguro_minimo_dolar_truncado="0")
        assert erros == [] and avisos == []


# ---------------------------------------------------------------------------
# Contrato silver → gold
# ---------------------------------------------------------------------------

class TestContratoComGold:
    def test_silver_gerada_passa_na_validacao_de_schema_da_gold(self, criar_bronze, cfg):
        out = tratar(criar_bronze([{"ncm": "21069030"}, {"ncm": "1012100"}]), cfg)
        assert gold.validar_schema_silver(out, "2026-08") == []

    def test_colunas_aditivas_da_gold_existem_na_silver(self, criar_bronze, cfg):
        out = tratar(criar_bronze(), cfg)
        for col in gold.ADITIVAS:
            assert col in out.columns

    def test_constantes_numericas_cobrem_as_metricas_exigidas_pela_gold(self):
        exigidas = set(gold.ADITIVAS) | set(gold.SILVER_METRICAS_MINIMAS)
        assert exigidas <= set(silver.COLUNAS_NUMERICAS)

    def test_silver_gerada_atravessa_a_construcao_da_fato(self, criar_bronze, cfg, dim_ncm, dim_pais):
        bronze = criar_bronze([{"ncm": "21069030"}, {"ncm": "84713012"}], ano_mes="2023-10")
        out = tratar(bronze, cfg, "2023-10")
        fato, erros, _ = gold.construir_fato(out, dim_ncm, dim_pais, "2023-10")
        assert erros == []
        assert len(fato) == 2


# ---------------------------------------------------------------------------
# Colunas opcionais ausentes na fonte (2022-05 a 2022-07 sem qtd_ume_soma_truncado)
# ---------------------------------------------------------------------------

class TestColunaOpcionalAusente:
    @pytest.fixture
    def bronze_sem_qtd(self, criar_bronze):
        return criar_bronze([{}, {"ncm": "21069030"}], ano_mes="2022-05").drop(
            columns=["qtd_ume_soma_truncado"])

    def test_coluna_e_criada_com_nan_float64(self, bronze_sem_qtd, cfg):
        out = tratar(bronze_sem_qtd, cfg, "2022-05")
        assert "qtd_ume_soma_truncado" in out.columns
        assert out["qtd_ume_soma_truncado"].dtype == np.float64
        assert out["qtd_ume_soma_truncado"].isna().all()

    def test_linhas_e_demais_metricas_ficam_intactas(self, bronze_sem_qtd, cfg):
        out = tratar(bronze_sem_qtd, cfg, "2022-05")
        assert len(out) == 2
        assert (out["vmle_soma_dolar_truncado"] == 10.0).all()

    def test_registra_aviso_no_log(self, bronze_sem_qtd, cfg, caplog):
        with caplog.at_level("WARNING"):
            tratar(bronze_sem_qtd, cfg, "2022-05")
        assert "qtd_ume_soma_truncado" in caplog.text and "2022-05" in caplog.text

    def test_mes_com_a_coluna_nao_gera_aviso(self, criar_bronze, cfg, caplog):
        with caplog.at_level("WARNING"):
            tratar(criar_bronze(ano_mes="2022-04"), cfg, "2022-04")
        assert "criada com NaN" not in caplog.text

    def test_nao_e_rejeitado_pela_validacao_de_schema_mesmo_se_obrigatoria_no_config(
            self, bronze_sem_qtd, cfg):
        cfg["esquema"]["colunas_obrigatorias"].append("qtd_ume_soma_truncado")
        out = tratar(bronze_sem_qtd, cfg, "2022-05")
        assert silver.validar_schema(out, cfg, "2022-05") == []

    def test_nan_na_soma_de_quantidade_nao_viola_regra_de_negocio(self, bronze_sem_qtd, cfg):
        erros, avisos = silver.validar_negocios(tratar(bronze_sem_qtd, cfg, "2022-05"))
        assert erros == [] and avisos == []

    def test_silver_resultante_passa_no_schema_da_gold(self, bronze_sem_qtd, cfg):
        out = tratar(bronze_sem_qtd, cfg, "2022-05")
        assert gold.validar_schema_silver(out, "2022-05") == []

    def test_schema_identico_ao_de_um_mes_completo(self, criar_bronze, bronze_sem_qtd, cfg):
        completo = tratar(criar_bronze(ano_mes="2022-04"), cfg, "2022-04")
        sem_qtd = tratar(bronze_sem_qtd, cfg, "2022-05")
        assert set(completo.columns) == set(sem_qtd.columns)
        assert completo.dtypes[silver.COLUNAS_NUMERICAS].equals(
            sem_qtd.dtypes[silver.COLUNAS_NUMERICAS])
