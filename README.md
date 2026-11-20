# Análise de Importações no Brasil

Projeto de análise das **Estatísticas de Declarações de Importação** publicadas mensalmente pela Receita Federal do Brasil (RFB). O pipeline ingere os dados brutos no Amazon S3, os processa em camadas (arquitetura medalhão), monta um modelo estrela na camada gold e disponibiliza os dados para exploração em notebooks Python. Um dashboard no Power BI está em desenvolvimento.

---

## Sumário

- [Status do projeto](#status-do-projeto)
- [Fonte de dados](#fonte-de-dados)
- [Limitações da fonte](#limitações-da-fonte)
- [Arquitetura](#arquitetura)
- [Modelo de dados (gold)](#modelo-de-dados-gold)
- [Estrutura do repositório](#estrutura-do-repositório)
- [Pré-requisitos](#pré-requisitos)
- [Configuração](#configuração)
- [Pipeline de dados](#pipeline-de-dados)
- [Análises previstas](#análises-previstas)
- [Qualidade de dados](#qualidade-de-dados)
- [Decisões de projeto](#decisões-de-projeto)
- [Aviso sobre cobertura dos dados](#aviso-sobre-cobertura-dos-dados)

---

## Status do projeto

| Etapa | Situação |
|---|---|
| Ingestão raw (`upload_raw.py`) | ✅ Concluída |
| Conversão para bronze (`raw_to_bronze.py`) | ✅ Concluída |
| Transformação para silver (`bronze_to_silver.py`) | ✅ Concluída, com validações de schema e de negócio |
| Dimensão de NCM com histórico de versões (`data_ref/dim_ncm.csv`) | ✅ Concluída |
| Dimensão de países (`data_ref/dim_pais.csv`) | ✅ Concluída, com 122 países |
| Camada gold, modelo estrela (`silver_to_gold.py`) | ✅ Concluída, em fase de validação com os dados completos |
| Notebooks de qualidade e EDA (`notebooks/eda/`) | ✅ Quatro notebooks |
| Notebooks de insights (`notebooks/insights/`) | 🚧 Em construção |
| Tabela de eventos regulatórios (`data_ref/eventos_regulatorios.csv`) | 🚧 Pendente |
| Dashboard no Power BI | 🚧 Em desenvolvimento |
| Testes automatizados (`tests/`) | 🚧 Em construção |

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
| **Período utilizado na gold** | A partir de abril de 2022, primeiro mês coberto pela tabela histórica de NCM |
| **Frequência** | Mensal |
| **Formato** | CSV, separador `@`, encoding UTF-8 |
| **Padrão de nome** | `estatisticas_di_AAAA_MM.csv` |

Cada arquivo contém estatísticas agregadas (mínimo, máximo, média, mediana, desvio padrão, quartis) dos valores de FOB/kg, FOB/UME, frete e seguro, além das somas de VMLE, peso líquido e quantidade, por combinação de NCM, NVE e país de origem.

O dicionário de dados completo está em [`docs/dicionario_dados.md`](docs/dicionario_dados.md). O documento original da RFB está em [`docs/estatisticas_di_metadados.pdf`](docs/estatisticas_di_metadados.pdf).

### Fonte da classificação NCM

As dimensões de NCM e de países não vêm do arquivo de importações. A descrição e a hierarquia dos códigos NCM vêm da tabela oficial publicada pela Receita Federal.

| Atributo | Valor |
|---|---|
| **Página de origem** | [Classificação Fiscal de Mercadorias — NCM (gov.br/receitafederal)](https://www.gov.br/receitafederal/pt-br/assuntos/aduana-e-comercio-exterior/classificacao-fiscal-de-mercadorias/ncm) |
| **Arquivo histórico** | `Tabela_NCM_Desc_Concatenada_Historica_20261119.xlsx`, com as alterações de abril de 2022 até a data de publicação |
| **Arquivos vigentes** | Dois arquivos Excel com os códigos vigentes e suas descrições, baixados da mesma página |
| **Data de extração** | 19/11/2026 |

Os três arquivos são combinados no notebook `notebooks/eda/02_dim_ncm.ipynb`, que gera `data_ref/dim_ncm.csv` com uma linha por versão de cada código. Os códigos vigentes que nunca mudaram aparecem com a vigência a partir de sua data de início, e os códigos encerrados mantêm a versão em que vigoraram.

A tabela histórica começa em abril de 2022. Por isso, a camada gold só cobre meses a partir desse período, como descrito em `gold.periodo_inicial` no `config.yaml`.

A `dim_pais` não tem fonte oficial única. Os nomes vêm dos valores publicados pela RFB, e os códigos ISO e os blocos econômicos foram preenchidos manualmente no notebook `notebooks/eda/03_dim_pais.ipynb`, com a referência de cada bloco documentada no próprio notebook.

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
Em linhas com distribuição de preços muito heterogênea, a Receita Federal pode suprimir os quartis (Q1 e Q3), deixando as células vazias. Esses valores chegam como `NaN` na silver e na gold — o dado permanece válido para as demais colunas.

**Métricas não aditivas**
Média, mediana, quartis, mínimo, máximo e desvio padrão não podem ser somados nem tirados média entre linhas. O preço médio ponderado é sempre recalculado como `SUM(vmle) / SUM(peso_liq)`.

**Sem dimensão interna**
Não há UF de destino, modal de transporte, porto de entrada, CNPJ do importador nem contagem de operações por linha.

O documento completo de limitações, pontos fortes e análises viáveis está em [`docs/documentacao_fontes_dados.docx`](docs/documentacao_fontes_dados.docx).

---

## Arquitetura

```
Download manual (CSV)
        │
        ▼
K:\Download\dados_importacoes_temp\     ← pasta local temporária, fora do repo
        │
        │  src/ingestao/upload_raw.py
        ▼
S3: raw/estatisticas_di/
    └── ano_mes=AAAA-MM/
        └── estatisticas_di_AAAA_MM.csv        ← arquivo original, intocado
        │
        │  src/ingestao/raw_to_bronze.py
        ▼
S3: bronze/estatisticas_di/
    └── ano_mes=AAAA-MM/
        └── estatisticas_di_AAAA_MM.parquet    ← colunas como texto + metadados
        │
        │  src/transformacao/bronze_to_silver.py
        ▼
S3: silver/estatisticas_di/
    └── ano_mes=AAAA-MM/
        └── estatisticas_di_AAAA_MM.parquet    ← tipada, normalizada, validada + derivadas
        │
        │  src/transformacao/silver_to_gold.py     + data_ref/dim_ncm.csv
        │                                          + data_ref/dim_pais.csv
        ▼
S3: gold/estatisticas_di/
    ├── fato_importacao_mensal/ano_mes=AAAA-MM/   ← fato no grão da silver
    ├── dim_ncm/                                  ← uma linha por versão do NCM
    ├── dim_pais/
    └── dim_tempo/
        │
        ├── Notebooks Python   ← qualidade (04) e exploração, insights
        └── Power BI           ← dashboard (em desenvolvimento)
```

**Bucket:** `importacoes-brasil-bucket` — região `sa-east-1`
**Particionamento:** estilo Hive (`ano_mes=AAAA-MM`) nas tabelas de fato de todas as camadas
**Formato:** Parquet com compressão Snappy

Um diagrama visual do pipeline está em [`diagrama_pipeline_v2.png`](diagrama_pipeline_v2.png).

---

## Modelo de dados (gold)

A gold é um modelo estrela. A fato guarda as métricas e as chaves; os atributos descritivos ficam nas dimensões.

**`fato_importacao_mensal`** — grão `ano_mes × ncm × nves × pais_origem`, igual ao da silver.

| Coluna | Descrição |
|---|---|
| `ano_mes` | Mês de referência (`AAAA-MM`) |
| `ncm` | Código NCM de 8 dígitos |
| `nves` | Código da NVE, mantido como chave degenerada para preservar a unicidade do grão |
| `pais_origem` | Nome do país de origem em uppercase (chave para `dim_pais`) |
| `ume` | Unidade de medida estatística |
| `ncm_versao_id` | Versão do NCM vigente no último dia do mês (chave para `dim_ncm`) |
| Métricas | 27 métricas da silver, sem agregação. Três são aditivas (`vmle_soma`, `peso_liq_soma`, `qtd_ume_soma`); as demais não são |

**`dim_ncm`** — uma linha por **versão** do NCM. Um mesmo código pode ter várias linhas, cada uma com sua vigência (`vigencia_inicio`, `vigencia_fim`). A chave é `ncm_versao_id` (código + data de início). Inclui capítulo, posição e descrições da hierarquia.

**`dim_pais`** — 122 países, com nome em português, códigos ISO 3166-1 (alpha-2 e alpha-3), continente, região e indicadores de pertencimento a Mercosul, OCDE, BRICS e G20. A chave é `pais_origem_rfb`, o nome exato como aparece na RFB.

**`dim_tempo`** — um registro por mês, do início da gold ao mês corrente, com ano, mês, trimestre e primeiro dia. Meses sem dados ainda aparecem na tabela.

---

## Estrutura do repositório

```
analise-importacoes-brasil/
├── data_ref/
│   ├── dim_ncm.csv                     # dimensão de NCM, uma linha por versão
│   └── dim_pais.csv                    # países com nome, código ISO e blocos econômicos
├── docs/
│   ├── dicionario_dados.md             # dicionário de dados em Markdown
│   ├── documentacao_fontes_dados.docx  # limitações, pontos fortes e análises viáveis
│   └── estatisticas_di_metadados.pdf   # documento original da RFB
├── notebooks/
│   ├── eda/
│   │   ├── images/                     # gráficos gerados pelos notebooks
│   │   ├── 01_eda_silver.ipynb         # conexão S3, checagens da silver, gráficos iniciais
│   │   ├── 02_dim_ncm.ipynb            # construção e validação da dim_ncm a partir da RFB
│   │   ├── 03_dim_pais.ipynb           # construção da dim_pais
│   │   └── 04_qualidade_eda_gold.ipynb # checagens da gold e exploração inicial
│   └── insights/                       # análises e conclusões (em construção)
├── src/
│   ├── ingestao/
│   │   ├── upload_raw.py               # envia CSVs locais para o S3 (camada raw)
│   │   └── raw_to_bronze.py            # converte raw CSV → bronze Parquet no S3
│   └── transformacao/
│       ├── bronze_to_silver.py         # tipagem, normalização, validações, derivadas
│       └── silver_to_gold.py           # modelo estrela com versões de NCM vigentes
├── tests/                              # testes automatizados (em construção)
├── .gitattributes
├── .gitignore
├── config.yaml                         # configurações do projeto (sem segredos)
├── diagrama_pipeline_v2.png            # diagrama do pipeline
├── LICENSE
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
pip install boto3 pyyaml pandas pyarrow matplotlib numpy
```

Os notebooks de exploração também usam `jupyter`.

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

O arquivo `config.yaml` na raiz do repositório centraliza todas as configurações do projeto. Os campos que costumam mudar são:

```yaml
caminhos:
  download_local: 'K:\Download\dados_importacoes_temp'   # pasta local dos CSVs
  data_ref:
    dim_ncm: data_ref/dim_ncm.csv
    dim_pais: data_ref/dim_pais.csv

gold:
  periodo_inicial: '2022-04'   # primeiro mês com versões de NCM na planilha histórica
```

> Credenciais AWS nunca entram no `config.yaml`. Ficam exclusivamente em `~/.aws/credentials`.

---

## Pipeline de dados

O pipeline é executado manualmente, mês a mês, na ordem abaixo. Todos os scripts aceitam `--config`, `--ano-mes AAAA-MM`, `--dry-run` e `--reprocessar`.

### Etapa 1 — Download manual

Acesse o portal [dados.gov.br](https://dados.gov.br/dados/conjuntos-dados/estatisticas-de-declaracoes-de-importacao) e baixe o(s) arquivo(s) CSV para a pasta local configurada.

### Etapa 2 — Upload para o S3 (raw)

```bash
python src/ingestao/upload_raw.py
```

Verifica quais arquivos já estão no S3 e envia apenas os novos. Os arquivos originais são preservados intactos na camada raw, sem nenhuma modificação.

### Etapa 3 — Conversão para bronze

```bash
python src/ingestao/raw_to_bronze.py
```

Converte os CSVs da camada raw para Parquet, mantendo todas as colunas como texto. Acrescenta três metadados de rastreabilidade: `arquivo_origem`, `ano_mes` e `data_ingestao`.

### Etapa 4 — Transformação para silver

```bash
python src/transformacao/bronze_to_silver.py
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

### Etapa 5 — Modelo estrela na gold

```bash
python src/transformacao/silver_to_gold.py
```

Monta o modelo estrela a partir da silver, a partir de `gold.periodo_inicial` (abril de 2022):

1. Valida o schema de cada mês da silver. Se faltar coluna ou se as métricas vierem como texto, o mês não é processado e o erro indica que a silver deve ser regerada.
2. Associa cada NCM à versão vigente no último dia do mês, usando `dim_ncm`.
3. Valida que a hierarquia da silver (capítulo e posição) coincide com a versão vigente.
4. Valida que todo país existe em `dim_pais`.
5. Grava a fato do mês e regrava as dimensões `dim_ncm`, `dim_pais` e `dim_tempo`.

As dimensões são regravadas a cada execução, porque são pequenas. Mudar uma dimensão não exige reprocessar a fato.

### Fluxo completo

```bash
# Executar em sequência após o download manual
python src/ingestao/upload_raw.py
python src/ingestao/raw_to_bronze.py
python src/transformacao/bronze_to_silver.py
python src/transformacao/silver_to_gold.py
```

**Opções disponíveis em todos os scripts:**

```
--config         caminho alternativo para o config.yaml  (padrão: config.yaml)
--ano-mes        processa apenas o mês informado         (ex.: 2026-08)
--dry-run        simula sem gravar nenhum arquivo
--reprocessar    regrava arquivos já existentes na camada de destino
```

---

## Análises previstas

As análises respeitam as limitações da fonte. Resultados devem ser lidos como evidências dentro do universo coberto pelo dado, não como totais nacionais.

Os notebooks de insights ficarão em `notebooks/insights/`:

- `01_padroes_importacao` (em desemvolvimento) — composição do valor importado, concentração geográfica por produto, comparação de preço por origem e custo logístico relativo.
- `02_analise_temporal` (em desemvolvimento) — evolução do preço médio ponderado por capítulo, participação de países ao longo do tempo, sazonalidade e persistência de origens.
- `03_impacto_regulatorio` (em desemvolvimento) — diferenças-em-diferenças e antecipação de compras. Requer `data_ref/eventos_regulatorios.csv`, ainda não criado.

> Análises inviáveis com esta fonte: total importado pelo Brasil, market share de importadores, impacto de antidumping em mercados concentrados, análise por UF ou modal de transporte.

---

## Qualidade de dados

As validações de qualidade estão **embutidas nos scripts de transformação**, não em módulos separados. Cada camada faz as próprias checagens antes de gravar.

**Silver (`bronze_to_silver.py`)**

Schema — interrompem o processamento do mês:
- Colunas obrigatórias presentes
- Formato `ano_mes` no padrão `AAAA-MM`
- Chave única `ano_mes + ncm + nves + pais_origem` sem duplicatas
- NCM com 8 dígitos após `zfill`

Negócio — derivadas do dicionário de dados da RFB:
- `fob_kg_minimo <= fob_kg_maximo`
- `fob_ume_minimo <= fob_ume_maximo`
- `frete_minimo <= frete_maximo`
- `fob_kg_quartil1 <= fob_kg_quartil3`
- `vmle_soma`, `peso_liq_soma` e `qtd_ume_soma` maiores que zero
- Mediana fora de `[Q1, Q3]` gera aviso (efeito do truncamento em 3 algarismos significativos)
- Seguro zero é válido e apenas informado no log

**Gold (`silver_to_gold.py`)**

Antes de montar a fato:
- Schema da silver completo, com métricas numéricas e chaves de texto
- NCM com 8 dígitos e chaves sem nulos
- Chave única do grão em cada mês
- Todo NCM com exatamente uma versão vigente na data de referência
- Todo país presente em `dim_pais`
- Hierarquia da silver coincidente com a versão vigente

Erros interrompem somente o mês afetado. Avisos são registrados no log.

**Notebooks**

- `01_eda_silver.ipynb` replica as checagens da silver de forma visual.
- `04_qualidade_eda_gold.ipynb` roda sobre a gold: integridade referencial, cobertura temporal, vigência das versões de NCM, valores, quartis suprimidos e reconciliação mês a mês com a soma de `vmle` da silver.

Relatórios usam ✅ para aprovado, ⚠️ para aviso e ❌ para erro.

---

## Decisões de projeto

**Por que Python local em vez de AWS Glue ou Lambda?**
O volume total da série cabe facilmente em memória local. A carga é manual e mensal, não justificando infraestrutura gerenciada. O S3 é usado para armazenamento e versionamento, não para processamento.

**Por que particionar por `ano_mes` e não por `pais_origem` ou `capitulo_ncm`?**
O dado já é gerado mensalmente pela RFB. A partição reflete a granularidade natural da fonte, e o filtro mais frequente nas análises é por período. Partições por país ou capítulo criariam tamanhos muito desiguais sem ganho real de performance.

**Por que as validações ficam nos scripts de transformação e não em módulos separados?**
Para simplicidade e manutenção. Com um único script por transformação, o desenvolvedor encontra as regras de qualidade no mesmo lugar onde a transformação acontece. Não há risco de desincronização entre módulo e script.

**Por que `keep_default_na=False` na leitura do CSV?**
Para preservar strings vazias como `''` em vez de convertê-las para `NaN`. Na camada bronze, o dado deve chegar intacto. Decisões sobre nulos ficam na silver.

**Por que uma dimensão de NCM com uma linha por versão?**
A classificação muda ao longo do tempo: códigos são criados, extintos e reclassificados. Uma importação só pode ser interpretada com a versão vigente no mês. Por isso a `dim_ncm` guarda o histórico e a fato carrega `ncm_versao_id`. A versão usada em cada mês é a vigente no último dia do mês; as mudanças da RFB caem no primeiro dia, então a escolha não altera o resultado, e o pipeline avisa se uma mudança cair fora dessa regra.

**Por que a gold tem o mesmo grão da silver?**
O volume de linhas por mês é pequeno. Manter o grão evita agregar métricas não aditivas e preserva a rastreabilidade para a silver. Agregações para análise são feitas nos notebooks, com as regras de cada métrica.

**Por que `nves` está na fato, se o projeto não usa dimensão de NVE?**
A chave do grão inclui `nves`. Removê-la faria linhas se somarem sem critério e invalidaria as métricas não aditivas. Por isso ela fica como chave degenerada, sem tabela própria.

**Por que `pais_origem` é normalizado para código ISO na gold, e não na silver?**
A silver mantém o nome original em uppercase, para que o join com `dim_pais` seja exato. A `dim_pais` é mantida manualmente e versionada no repositório, porque a busca automática por nomes em inglês não funciona bem com os nomes em português da RFB.

---

## Aviso sobre cobertura dos dados

> Os valores neste projeto **não representam o total nacional de importações do Brasil**. A fonte cobre apenas combinações com ao menos 4 importadores distintos por mês, o que corresponde a aproximadamente 10–15% do valor total importado. A ausência de um produto ou país nos dados não implica ausência de importação.
