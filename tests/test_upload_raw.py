"""
Testes de upload_raw.py

Só a lógica pura e o fluxo do main() com S3 simulado em memória.
O cliente simulado aceita somente head_bucket, get_paginator e upload_file:
qualquer outra operação (em especial delete_*) derruba o teste, o que
protege a regra "a camada raw nunca é apagada".
"""

import logging
from pathlib import Path

import pytest

import upload_raw

PADRAO = r"^estatisticas_di_(\d{4})_(\d{2})\.csv$"   # mesmo valor do config.yaml


# ---------------------------------------------------------------------------
# Nome de arquivo e chave S3
# ---------------------------------------------------------------------------

class TestExtrairAnoMes:
    @pytest.mark.parametrize("nome, esperado", [
        ("estatisticas_di_2026_08.csv", "2026-08"),
        ("estatisticas_di_2021_01.csv", "2021-01"),
        ("estatisticas_di_2023_12.csv", "2023-12"),
    ])
    def test_nomes_validos(self, nome, esperado):
        assert upload_raw.extrair_ano_mes(nome, PADRAO) == esperado

    @pytest.mark.parametrize("nome", [
        "estatisticas_di_2026_8.csv",          # mês com um dígito
        "estatisticas_di_26_08.csv",           # ano com dois dígitos
        "estatisticas_di_2026_08.xlsx",        # extensão errada
        "estatisticas_di_2026_08.csv.bak",     # sufixo extra
        "estatisticas_di_2026_08 (1).csv",     # download duplicado
        "Estatisticas_DI_2026_08.csv",         # caixa diferente
        "outro_arquivo_2026_08.csv",
        "",
    ])
    def test_nomes_invalidos_retornam_none(self, nome):
        assert upload_raw.extrair_ano_mes(nome, PADRAO) is None


def test_montar_chave_s3_segue_particionamento_hive():
    chave = upload_raw.montar_chave_s3("raw/estatisticas_di/", "2026-08",
                                       "estatisticas_di_2026_08.csv")
    assert chave == "raw/estatisticas_di/ano_mes=2026-08/estatisticas_di_2026_08.csv"


# ---------------------------------------------------------------------------
# Arquivos locais
# ---------------------------------------------------------------------------

class TestListarArquivosLocais:
    def test_filtra_pelo_padrao_e_ordena_por_nome(self, tmp_path):
        for nome in ["estatisticas_di_2026_08.csv", "estatisticas_di_2021_01.csv",
                     "rascunho.csv", "estatisticas_di_2026_8.csv", "leia-me.txt",
                     "estatisticas_di_2022_05.xlsx"]:
            (tmp_path / nome).write_text("x")
        arquivos = upload_raw.listar_arquivos_locais(tmp_path, PADRAO)
        assert [a.name for a in arquivos] == ["estatisticas_di_2021_01.csv",
                                              "estatisticas_di_2026_08.csv"]

    def test_pasta_sem_csv_valido(self, tmp_path):
        (tmp_path / "x.csv").write_text("x")
        assert upload_raw.listar_arquivos_locais(tmp_path, PADRAO) == []

    def test_pasta_inexistente(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="download_local"):
            upload_raw.listar_arquivos_locais(tmp_path / "nao_existe", PADRAO)

    def test_avisa_sobre_arquivo_fora_do_padrao(self, tmp_path, caplog):
        (tmp_path / "rascunho.csv").write_text("x")
        with caplog.at_level(logging.WARNING, logger="upload_raw"):
            upload_raw.listar_arquivos_locais(tmp_path, PADRAO)
        assert "rascunho.csv" in caplog.text


# ---------------------------------------------------------------------------
# S3 simulado
# ---------------------------------------------------------------------------

class FakePaginador:
    def __init__(self, paginas):
        self.paginas = paginas

    def paginate(self, **_):
        return iter(self.paginas)


class FakeS3:
    """Aceita apenas as operações usadas pelo upload. O resto falha o teste."""

    def __init__(self, chaves=()):
        self.chaves = list(chaves)
        self.enviados = []

    def head_bucket(self, Bucket):
        pass

    def get_paginator(self, nome):
        assert nome == "list_objects_v2"
        return FakePaginador([{"Contents": [{"Key": k} for k in self.chaves]}])

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None):
        self.enviados.append(Key)

    def __getattr__(self, nome):
        raise AssertionError(f"Operação S3 inesperada: {nome}")


class TestListarArquivosNoS3:
    def test_retorna_basenames_dos_csv(self):
        s3 = FakeS3([
            "raw/estatisticas_di/ano_mes=2026-07/estatisticas_di_2026_07.csv",
            "raw/estatisticas_di/ano_mes=2026-08/estatisticas_di_2026_08.csv",
            "raw/estatisticas_di/ano_mes=2026-08/_SUCCESS",
            "raw/estatisticas_di/ano_mes=2026-08/leia-me.txt",
        ])
        assert upload_raw.listar_arquivos_no_s3(s3, "b", "raw/estatisticas_di/") == {
            "estatisticas_di_2026_07.csv", "estatisticas_di_2026_08.csv"}

    def test_varias_paginas(self):
        s3 = FakeS3()
        s3.get_paginator = lambda nome: FakePaginador([
            {"Contents": [{"Key": "raw/a/ano_mes=2026-01/estatisticas_di_2026_01.csv"}]},
            {"Contents": [{"Key": "raw/a/ano_mes=2026-02/estatisticas_di_2026_02.csv"}]},
        ])
        assert upload_raw.listar_arquivos_no_s3(s3, "b", "raw/a/") == {
            "estatisticas_di_2026_01.csv", "estatisticas_di_2026_02.csv"}

    def test_bucket_vazio(self):
        s3 = FakeS3()
        s3.get_paginator = lambda nome: FakePaginador([{}])   # página sem 'Contents'
        assert upload_raw.listar_arquivos_no_s3(s3, "b", "raw/") == set()


# ---------------------------------------------------------------------------
# fazer_upload
# ---------------------------------------------------------------------------

class TestFazerUpload:
    def test_envia_com_content_type_csv(self, tmp_path):
        arq = tmp_path / "estatisticas_di_2026_08.csv"
        arq.write_text("x")
        s3 = FakeS3()
        assert upload_raw.fazer_upload(s3, "b", arq, "raw/k.csv", dry_run=False) is True
        assert s3.enviados == ["raw/k.csv"]

    def test_dry_run_nao_envia(self, tmp_path):
        arq = tmp_path / "estatisticas_di_2026_08.csv"
        arq.write_text("x")
        s3 = FakeS3()
        assert upload_raw.fazer_upload(s3, "b", arq, "raw/k.csv", dry_run=True) is True
        assert s3.enviados == []

    def test_erro_do_s3_retorna_false(self, tmp_path):
        from botocore.exceptions import ClientError
        arq = tmp_path / "x.csv"
        arq.write_text("x")
        s3 = FakeS3()

        def falha(**_):
            raise ClientError({"Error": {"Code": "500", "Message": "falha"}}, "PutObject")

        s3.upload_file = falha
        assert upload_raw.fazer_upload(s3, "b", arq, "raw/k.csv", dry_run=False) is False


# ---------------------------------------------------------------------------
# main(): o que falta enviar
# ---------------------------------------------------------------------------

@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    pasta = tmp_path / "downloads"
    pasta.mkdir()
    cfg = {
        "aws": {"bucket": "b", "profile": "p", "regiao": "sa-east-1"},
        "s3": {"raw": "raw/estatisticas_di/"},
        "origem": {"padrao_nome_arquivo": PADRAO},
        "caminhos": {"download_local": str(pasta)},
        "log": {"nivel": "INFO", "formato": "%(message)s"},
    }
    s3 = FakeS3()
    monkeypatch.setattr(upload_raw, "carregar_config", lambda c: cfg)
    monkeypatch.setattr(upload_raw, "configurar_log", lambda c: logging.getLogger("teste_upload"))
    monkeypatch.setattr(upload_raw, "criar_cliente_s3", lambda c: s3)

    class Amb:
        pass

    amb = Amb()
    amb.pasta, amb.s3, amb.cfg = pasta, s3, cfg
    amb.criar_local = lambda *nomes: [(pasta / n).write_text("x") for n in nomes]
    return amb


class TestMain:
    def test_envia_somente_o_que_falta(self, ambiente):
        ambiente.criar_local("estatisticas_di_2026_07.csv", "estatisticas_di_2026_08.csv")
        ambiente.s3.chaves = ["raw/estatisticas_di/ano_mes=2026-07/estatisticas_di_2026_07.csv"]
        upload_raw.main("config.yaml", dry_run=False)
        assert ambiente.s3.enviados == [
            "raw/estatisticas_di/ano_mes=2026-08/estatisticas_di_2026_08.csv"]

    def test_nada_a_enviar(self, ambiente):
        ambiente.criar_local("estatisticas_di_2026_08.csv")
        ambiente.s3.chaves = ["raw/estatisticas_di/ano_mes=2026-08/estatisticas_di_2026_08.csv"]
        upload_raw.main("config.yaml", dry_run=False)
        assert ambiente.s3.enviados == []

    def test_dry_run_nao_envia(self, ambiente):
        ambiente.criar_local("estatisticas_di_2026_08.csv")
        upload_raw.main("config.yaml", dry_run=True)
        assert ambiente.s3.enviados == []

    def test_ignora_arquivo_fora_do_padrao(self, ambiente):
        ambiente.criar_local("estatisticas_di_2026_08.csv", "rascunho.csv")
        upload_raw.main("config.yaml", dry_run=False)
        assert ambiente.s3.enviados == [
            "raw/estatisticas_di/ano_mes=2026-08/estatisticas_di_2026_08.csv"]

    def test_arquivo_ja_no_s3_nao_e_sobrescrito(self, ambiente):
        """O raw é imutável: um arquivo existente nunca é reenviado."""
        ambiente.criar_local("estatisticas_di_2026_08.csv")
        ambiente.s3.chaves = ["raw/estatisticas_di/ano_mes=2026-08/estatisticas_di_2026_08.csv"]
        upload_raw.main("config.yaml", dry_run=False)
        assert ambiente.s3.enviados == []

    def test_pasta_local_sem_arquivos_validos_encerra_com_zero(self, ambiente):
        with pytest.raises(SystemExit) as saida:
            upload_raw.main("config.yaml", dry_run=False)
        assert saida.value.code == 0

    def test_pasta_local_inexistente_encerra_com_erro(self, ambiente):
        ambiente.cfg["caminhos"]["download_local"] = str(ambiente.pasta / "nao_existe")
        with pytest.raises(SystemExit) as saida:
            upload_raw.main("config.yaml", dry_run=False)
        assert saida.value.code == 1

    def test_falha_de_upload_encerra_com_erro(self, ambiente):
        from botocore.exceptions import ClientError
        ambiente.criar_local("estatisticas_di_2026_08.csv")

        def falha(**_):
            raise ClientError({"Error": {"Code": "500", "Message": "x"}}, "PutObject")

        ambiente.s3.upload_file = falha
        with pytest.raises(SystemExit) as saida:
            upload_raw.main("config.yaml", dry_run=False)
        assert saida.value.code == 1
