# Análise de Importações no Brasil

Projeto de análise de dados das **Estatísticas de Declarações de Importação** publicadas mensalmente pela Receita Federal do Brasil (RFB). O pipeline ingere os dados brutos no Amazon S3, os processa em camadas (medallion architecture) e os disponibiliza para exploração em notebooks Python e dashboards no Power BI.

---

## Sumário

- [Fonte de dados](#fonte-de-dados)
- [Limitações da fonte](#limitações-da-fonte)
- [Arquitetura](#arquitetura)
- [Estrutura do repositório](#estrutura-do-repositório)
- [Pré-requisitos](#pré-requisitos)
- [Configuração](#configuração)
- [Pipeline de dados](#pipeline-de-dados)
- [Análises previstas](#análises-previstas)
- [Qualidade de dados](#qualidade-de-dados)
- [Decisões de projeto](#decisões-de-projeto)

---

## Fonte de dados

| Atributo | Valor |
|---|---|
| **Nome** | Estatísticas de Declarações de Importação |
| **Órgão** | Secretaria Especial da Receita Federal do Brasil (RFB) |
| **Portal** | [dados.gov.br](https://dados.gov.br/dados/conjuntos-dados/estatisticas-de-declaracoes-de-importacao) |
| **Cobertura** | Nacional |
| **Granularidade** | Mês × NCM × NVE × País de origem |
| **Período disponível** | Janeiro de 2021 até o mês corrente |
| **Frequência** | Mensal |
| **Formato** | CSV, separador `@`, encoding UTF-8 |
| **Padrão de nome** | `estatisticas_di_AAAA_MM.csv` |

Cada arquivo contém estatísticas agregadas (mínimo, máximo, média, mediana, desvio padrão, quartis) dos valores de FOB/kg, FOB/UME, frete e seguro, além das somas de VMLE, peso líquido e quantidade, por combinação de NCM, NVE e país de origem.

O dicionário de dados completo está em [`docs/dicionario_dados.md`](docs/dicionario_dados.md). O documento original da RFB está em [`docs/estatisticas_di_metadados.pdf`](docs/estatisticas_di_metadados.pdf).

---

## Limitações da fonte

> Conhecer as limitações é essencial para evitar conclusões incorretas. Leia esta seção antes de interpretar qualquer resultado.

**Regra dos 4 importadores (LGPD)**
Uma linha só é publicada quando a combinação NCM + NVE + país de origem tiver ao menos 4 importadores distintos no mês. A ausência de uma linha não significa ausência de importação — significa dado censurado. Mercados concentrados (1–3 importadores) ficam estruturalmente de fora.

**Cobertura parcial do total nacional**
O dado cobre aproximadamente 10–15% do valor total de importações do Brasil. Não use o arquivo para calcular totais nacionais nem market share absoluto.

**Quebras artificiais na série temporal**
Uma linha pode desaparecer de um mês para outro porque o número de importadores caiu de 4 para 3, não porque a importação parou. Para análises temporais, prefira agregar por capítulo NCM (2 dígitos), que é bem mais estável.

**Quartis suprimidos pela RFB**
Em linhas com distribuição de preços muito heterogênea, a Receita Federal pode suprimir os quartis (Q1 e Q3), deixando as células vazias. Esses valores chegam como `NaN` na silver — o dado permanece válido para as demais colunas.

**Sem dimensão interna**
Não há UF de destino, modal de transporte, porto de entrada, CNPJ do importador nem contagem de operações por linha.

O documento completo de limitações, pontos fortes e análises viáveis está em [`docs/documentacao_fontes_dados.docx`](docs/).

---

## Arquitetura

```
Download manual (CSV)
        │
        ▼
K:\Download\dados_importacoes_temp\     ← pasta local temporária, fora do repo
        │
        │  upload_raw.py
        ▼
S3: raw/estatisticas_di/
    └── ano_mes=AAAA-MM/
        └── estatisticas_di_AAAA_MM.csv    ← arquivo original, intocado
        │
        │  raw_para_bronze.py
        ▼
S3: bronze/estatisticas_di/
    └── ano_mes=AAAA-MM/
        └── estatisticas_di_AAAA_MM.parquet  ← Parquet, colunas como texto + metadados
        │
        │  bronze_para_silver.py
        ▼
S3: silver/estatisticas_di/
    └── ano_mes=AAAA-MM/
        └── estatisticas_di_AAAA_MM.parquet  ← tipado, normalizado, validado + derivadas
        │
        │  silver_para_gold.py  (em desenvolvimento)
        ▼
S3: gold/estatisticas_di/
    ├── fato_importacao_mensal/
    ├── dim_ncm/
    ├── dim_pais/
    └── dim_tempo/
        │
        ├── Notebooks Python  ← EDA e levantamento de insights
        └── Power BI          ← dashboard para análise por outras pessoas
```

**Bucket:** `importacoes-brasil-bucket` — região `sa-east-1`  
**Particionamento:** estilo Hive (`ano_mes=AAAA-MM`) em todas as camadas  
**Formato:** Parquet com compressão Snappy

---

## Estrutura do repositório

```
analise-importacoes-brasil/
├── src/
│   ├── ingestao/
│   │   ├── upload_raw.py           # envia CSVs locais para o S3 (camada raw)
│   │   └── raw_para_bronze.py      # converte raw CSV → bronze Parquet no S3
│   └── transformacao/
│       ├── bronze_para_silver.py   # tipagem, normalização, validações, derivadas
│       └── silver_para_gold.py     # modelo estrela, métricas agregadas (em desenvolvimento)
├── notebooks/
│   ├── eda/
│   │   ├── 01_eda_silver.ipynb         # conexão S3, checagens de qualidade, 4 gráficos iniciais
│   └── insights/
├── docs/
│   ├── estatisticas_di_metadados.pdf                                    # documento original da RFB
│   └── Fontes de Dados — Limitações, Pontos Fortes e Análises Viáveis.docx
├── data_ref/                       # tabelas de referência versionadas no repo
├── config.yaml                      # configurações do projeto (sem segredos)
├── .gitignore
└── README.md
```

> **Dados brutos fora do repositório.** Os arquivos CSV baixados da RFB ficam em `K:\Download\dados_importacoes_temp` e são enviados ao S3 pelo `upload_raw.py`. Nunca devem ser commitados.

---

## Pré-requisitos

- Python 3.11+
- AWS CLI v2
- Conta AWS com bucket `importacoes-brasil-bucket` criado em `sa-east-1`
- Usuário IAM `estatisticas_di` com política de acesso ao bucket

**Instalar dependências Python:**

```bash
pip install boto3 pyyaml pandas pyarrow matplotlib seaborn awswrangler
```

---

## Configuração

### 1. Clonar o repositório

```bash
git clone https://github.com/renankalfa1/analise-importacoes-brasil.git
cd analise-importacoes-brasil
```

### 2. Configurar o perfil IAM

No Console AWS, acesse **IAM → Usuários → estatisticas_di → Credenciais de segurança → Criar chave de acesso** e gere as chaves de acesso para uso em CLI.

```bash
aws configure --profile estatisticas_di
# AWS Access Key ID:     AKIA...
# AWS Secret Access Key: ...
# Default region name:   sa-east-1
# Default output format: json
```

### 3. Verificar o acesso ao bucket

```bash
aws s3 ls s3://importacoes-brasil-bucket --profile estatisticas_di
```

### 4. Revisar o `config.yaml`

O arquivo `config.yaml` na raiz do repositório centraliza todas as configurações do projeto. Ajuste apenas o campo `caminhos.download_local` se necessário:

```yaml
caminhos:
  download_local: 'K:\Download\dados_importacoes_temp'  # pasta local dos CSVs
```

> Credenciais AWS nunca entram no `config.yaml`. Ficam exclusivamente em `~/.aws/credentials`.

---

## Pipeline de dados

O pipeline é executado manualmente, mês a mês, seguindo a sequência abaixo. Todos os scripts aceitam `--dry-run` para simular sem gravar e `--ano-mes AAAA-MM` para processar um mês específico.

### Etapa 1 — Download manual

Acesse o portal [dados.gov.br](https://dados.gov.br/dados/conjuntos-dados/estatisticas-de-declaracoes-de-importacao) e baixe o(s) arquivo(s) CSV para a pasta local configurada.

### Etapa 2 — Upload para o S3 (raw)

```bash
python src/ingestao/upload_raw.py
```

Verifica quais arquivos já estão no S3 e envia apenas os novos. Os arquivos originais são preservados intactos na camada raw, sem nenhuma modificação.

### Etapa 3 — Conversão para bronze

```bash
python src/ingestao/raw_para_bronze.py
```

Converte os CSVs da camada raw para Parquet, mantendo todas as colunas como texto. Acrescenta três metadados de rastreabilidade: `arquivo_origem`, `ano_mes` e `data_ingestao`.

### Etapa 4 — Transformação para silver

```bash
python src/transformacao/bronze_para_silver.py
```

Aplica as regras de tratamento da camada silver:

| Regra | Detalhe |
|---|---|
| Tipagem numérica | 27 colunas de valor convertidas para `float64` |
| `zfill(8)` no NCM | Recupera zeros à esquerda perdidos na leitura como número |
| Normalização de texto | `pais_origem` em uppercase; strip em todas as colunas de texto |
| UME vazio | Substituído por `"NÃO INFORMADO"` |
| Colunas derivadas | `capitulo_ncm`, `posicao_ncm`, `tem_nve`, `nves_multiplo` |
| Validações de schema | Colunas obrigatórias, chave única, formato `ano_mes`, tamanho do NCM |
| Validações de negócio | `min <= max`, `Q1 <= Q3`, somas positivas |

> **Quartis vazios:** a RFB pode suprimir Q1 e Q3 em linhas com distribuição de preços muito heterogênea. Esses valores chegam como `NaN` na silver — a linha permanece válida para as demais colunas.

### Fluxo completo

```bash
# Executar em sequência após o download manual
python src/ingestao/upload_raw.py
python src/ingestao/raw_para_bronze.py
python src/transformacao/bronze_para_silver.py
```

**Opções disponíveis em todos os scripts:**

```
--config   caminho alternativo para o config.yaml  (padrão: config.yaml)
--ano-mes  processa apenas o mês informado          (ex.: 2026-08)
--dry-run  simula sem gravar nenhum arquivo
--reprocessar  regrava arquivos já existentes na camada de destino
```

---

## Análises previstas

As análises respeitam as limitações da fonte. Resultados devem ser lidos como evidências dentro do universo coberto pelo dado, não como totais nacionais.

### Padrões de importação
- Composição do valor importado por país e capítulo NCM
- Concentração geográfica por produto (índice HHI)
- Comparação de preço FOB/kg entre países de origem para o mesmo NCM
- Custo logístico relativo (razão frete/VMLE) por par país-produto
- Dispersão de preços por produto (amplitude interquartil)

### Análise temporal
- Evolução do preço médio ponderado (`SUM(vmle) / SUM(peso)`) por capítulo e mês
- Participação relativa de países ao longo do tempo
- Sazonalidade por capítulo NCM
- Detecção de quebras de série
- Persistência de origens ao longo da série

### Impacto regulatório *(requer tabela `dados_ref/eventos_regulatorios.csv`)*
- Diferenças-em-diferenças para eventos em mercados pulverizados
- Detecção de antecipação de compras antes da vigência de medidas restritivas
- Análise válida apenas para linhas NCM × país que **não somem** após o evento

> Análises inviáveis com esta fonte: total importado pelo Brasil, market share de importadores, impacto de antidumping em mercados concentrados, análise por UF ou modal de transporte.

---

## Qualidade de dados

As validações de qualidade estão **embutidas nos scripts de transformação**, não em módulos separados. O `bronze_para_silver.py` executa dois níveis de checagem a cada arquivo processado:

**Schema** — interrompem o pipeline em caso de falha:
- Colunas obrigatórias presentes
- Formato `ano_mes` no padrão `AAAA-MM`
- Chave única `ano_mes + ncm + nves + pais_origem` sem duplicatas
- NCM com 8 dígitos após `zfill`

**Negócio** — derivadas do dicionário de dados da RFB:
- `fob_kg_minimo <= fob_kg_maximo`
- `fob_ume_minimo <= fob_ume_maximo`
- `frete_minimo <= frete_maximo`
- `fob_kg_quartil1 <= fob_kg_quartil3`
- `vmle_soma`, `peso_liq_soma` e `qtd_ume_soma` maiores que zero
- Mediana fora de `[Q1, Q3]` gera aviso (efeito do truncamento em 3 algarismos significativos)
- Seguro zero é válido e apenas informado no log

O notebook `notebooks/eda/01_eda_silver.ipynb` replica essas checagens de forma visual, com relatório de ✅ erros e ⚠️ avisos antes de qualquer análise.

---

## Decisões de projeto

**Por que Python local em vez de AWS Glue ou Lambda?**
O volume total da série (~68 meses × ~1 MB por arquivo) cabe facilmente em memória local. A carga é manual e mensal, não justificando infraestrutura gerenciada. O S3 é usado para armazenamento e versionamento, não para processamento.

**Por que particionar por `ano_mes` e não por `pais_origem` ou `capitulo_ncm`?**
O dado já é gerado mensalmente pela RFB. A partição reflete a granularidade natural da fonte, e o filtro mais frequente nas análises é por período. Partições por país ou capítulo criariam tamanhos muito desiguais sem ganho real de performance.

**Por que as validações ficam nos scripts de transformação e não em módulos separados?**
Para simplicidade e manutenção. Com um único script de transformação por camada, o desenvolvedor encontra as regras de qualidade no mesmo lugar onde a transformação acontece. Não há risco de desincronização entre módulo e script.

**Por que `keep_default_na=False` na leitura do CSV?**
Para preservar strings vazias como `''` em vez de convertê-las para `NaN`. Na camada bronze, o dado deve chegar intacto. Decisões sobre nulos — incluindo a conversão de `''` para `NaN` nas colunas numéricas — ficam na silver.

**Por que não normalizar `pais_origem` para código ISO na silver?**
A normalização depende da tabela `dados_ref/dim_pais.csv`, construída manualmente. Esse join é responsabilidade da camada gold, que monta o modelo estrela com `dim_pais`. A silver mantém o nome original em uppercase para facilitar o join posterior.

---

## Aviso sobre cobertura dos dados

> Os valores neste projeto **não representam o total nacional de importações do Brasil**. A fonte cobre apenas combinações com ao menos 4 importadores distintos por mês, o que corresponde a aproximadamente 10–15% do valor total importado. A ausência de um produto ou país nos dados não implica ausência de importação.
