# Dicionário de dados — Estatísticas de Declarações de Importação

> **Fonte:** Secretaria Especial da Receita Federal do Brasil (RFB). Documento original: `estatisticas_di_metadados.pdf` (4 páginas). Esta versão em Markdown preserva o conteúdo informativo e a nomenclatura dos campos do documento de origem.

## 1. Metadados do conjunto de dados

| Propriedade | Descrição |
|---|---|
| Título | Estatísticas de declarações de importação |
| Descrição | Dados agregados de declarações de importação de pessoas físicas e jurídicas, agrupados por NCM, NVE e país de origem. |
| Catálogo origem | https://www.gov.br/receitafederal/dados |
| Órgão responsável | Secretaria Especial da Receita Federal do Brasil (RFB) |
| Categorias no VCGE | Comércio externo |
| Cobertura geográfica | Nacional |
| Granularidade temporal | Mês |
| Frequência de atualização | Mensal |

## 2. Descrição das colunas e dados

| Coluna | Descrição |
|---|---|
| `NCM` | Código da Nomenclatura Comum do Mercosul |
| `NVES` | Código da nomenclatura de valor aduaneiro e estatística |
| `NVES_NM` | Nome da nomenclatura de valor aduaneiro e estatística |
| `PAIS_ORIGEM` | País de origem |
| `FOB_KG_MINIMO_DOLAR_TRUNCADO` | Valor mínimo de FOB/kg, em US$, truncado |
| `FOB_KG_MAXIMO_DOLAR_TRUNCADO` | Valor máximo de FOB/kg, em US$, truncado |
| `FOB_KG_MEDIA_DOLAR_TRUNCADO` | Média do valor FOB/kg, em US$, truncado |
| `FOB_KG_MEDIANA_DOLAR_TRUNCADO` | Mediana do valor FOB/kg, em US$, truncado |
| `FOB_KG_DESVIO_PADRAO_DOLAR_TRUNCADO` | Desvio padrão do valor FOB/kg, em US$, truncado |
| `FOB_KG_QUARTIL1_TRUNCADO` | 1º quartil do valor FOB/kg, em US$, truncado |
| `FOB_KG_QUARTIL3_TRUNCADO` | 3º quartil do valor FOB/kg, em US$, truncado |
| `FOB_UME_MINIMO_DOLAR_TRUNCADO` | Valor mínimo de FOB/UME, em US$, truncado |
| `FOB_UME_MAXIMO_DOLAR_TRUNCADO` | Valor máximo de FOB/UME, em US$, truncado |
| `FOB_UME_MEDIA_DOLAR_TRUNCADO` | Média do valor FOB/UME, em US$, truncado |
| `FOB_UME_MEDIANA_DOLAR_TRUNCADO` | Mediana do valor FOB/UME, em US$, truncado |
| `FOB_UME_DESVIO_PADRAO_DOLAR_TRUNCADO` | Desvio padrão do valor FOB/UME, em US$, truncado |
| `FOB_UME_QUARTIL1_TRUNCADO` | 1º quartil do valor FOB/UME, em US$, truncado |
| `FOB_UME_QUARTIL3_TRUNCADO` | 3º quartil do valor FOB/UME, em US$, truncado |
| `VMLE_SOMA_DOLAR_TRUNCADO` | Soma do valor das mercadorias no local de embarque, em US$, truncado |
| `PESO_LIQ_SOMA_TRUNCADO` | Soma do peso líquido, em kg, truncado |
| `QTD_UME_SOMA_TRUNCADO` | Soma da quantidade na unidade de medida estatística, truncado |
| `FRETE_MINIMO_DOLAR_TRUNCADO` | Valor mínimo de frete, em US$, truncado |
| `FRETE_MEDIA_DOLAR_TRUNCADO` | Média do valor do frete, em US$, truncado |
| `FRETE_MEDIANA_DOLAR_TRUNCADO` | Mediana do valor do frete, em US$, truncado |
| `FRETE_MAXIMO_DOLAR_TRUNCADO` | Valor máximo de frete, em US$, truncado |
| `FRETE_DESVIO_PADRAO_DOLAR_TRUNCADO` | Desvio padrão do valor do frete, em US$, truncado |
| `SEGURO_MINIMO_DOLAR_TRUNCADO` | Valor mínimo do seguro, em US$, truncado |
| `SEGURO_MEDIA_DOLAR_TRUNCADO` | Média do valor do seguro, em US$, truncado |
| `SEGURO_MEDIANA_DOLAR_TRUNCADO` | Mediana do valor do seguro, em US$, truncado |
| `SEGURO_MAXIMO_DOLAR_TRUNCADO` | Valor máximo do seguro, em US$, truncado |
| `SEGURO_DESVIO_PADRAO_DOLAR_TRUNCADO` | Desvio padrão do valor do seguro, em US$, truncado |
| `UME` | Unidade de medida estatística da NCM |

## 3. Observações

1. Dados agregados a nível de adição com valores por FOB/KG e FOB/UME.
2. Dados das DIs são relativos à versão inicial declarada.
3. Os dados são divulgados de forma agregada com **pelo menos 4 importadores por agregação** e os valores são **truncados em três algarismos significativos**.
4. Apenas importações efetivas são divulgadas, o que exclui DIs dos tipos: admissões em entrepostos, admissões temporárias, internações e transferências de regimes.
5. NVEs cujos atributos identificam marcas/modelos não são divulgadas.
6. Todos os arquivos estão em formato **CSV**, sendo o separador **`@`**.
7. Exemplos do truncamento de valores:
   - `1234` se torna `1230`.
   - `0,12345` se torna `0,123`.

## 4. Perguntas frequentes

### 4.1. O que significam os campos `FOB_KG_QUARTIL1_TRUNCADO` e `FOB_KG_QUARTIL3_TRUNCADO`?

Um quartil é qualquer um dos três valores que divide o conjunto ordenado de dados em quatro partes iguais, e assim cada parte representa 1/4 da amostra ou população. No caso em questão:

- **`FOB_KG_QUARTIL1_TRUNCADO`**: 1º quartil, em USD, da razão FOB/kg e truncado para os 3 algarismos mais significativos.
- **`FOB_KG_QUARTIL3_TRUNCADO`**: 3º quartil, em USD, da razão FOB/kg e truncado para os 3 algarismos mais significativos.
- O campo `fob_kg_mediana` é o 2º quartil.

### 4.2. Qual o critério de ordenamento das operações para delimitar os quartis?

**Pergunta do documento original:** Por exemplo, as operações são ordenadas de forma crescente em relação ao preço e o valor do primeiro quartil corresponde ao valor da operação localizada no primeiro ponto (posição que separa os 25% primeiros registros dos demais)?

**Resposta:** Para calcular os quartis da variável FOB/kg os dados são ordenados pelo seu valor (FOB/kg).

### 4.3. A divulgação por quartil tem relação com a relevância dos patamares no peso total importado?

É o quartil para a variável FOB/kg. A relação que esta medida guarda com o peso está ligada à razão entre valor FOB e peso em kg.

### 4.4. Definições e alterações de NVE

As definições de “Nomenclatura de Valor Estatístico (NVE)” podem ser encontradas no acesso público das Tabelas Aduaneiras, as quais são regidas pelo parágrafo único do inciso II do art. 30 da IN RFB nº 2090/2022 e pela Portaria Coana nº 81/2022.

Os interessados em apresentar propostas de criação ou alteração de atributos e especificações relativos às mercadorias, complementares à Nomenclatura Comum do Mercosul (NCM), a serem obrigatoriamente informados na declaração de importação no campo NVE, nos termos da Portaria Coana nº 81, de 28 de junho de 2022, poderão preencher os formulários indicados abaixo e enviá-los para o e-mail **nve.coana.df@rfb.gov.br**.

- [Formulário para criação de atributos e especificações — NVE](https://www.gov.br/receitafederal/pt-br/acesso-a-informacao/dados-abertos/resultados/aduana/dados-estatisticos/FormularioparacriacaodeAtributoseEspecificacoes_NVE.doc)
- [Formulário para alteração de atributos e especificações — NVE](https://www.gov.br/receitafederal/pt-br/acesso-a-informacao/dados-abertos/resultados/aduana/dados-estatisticos/FormularioparaalteracaodeAtributoseEspecificacoes_NVE.doc)

A Coordenação-Geral de Administração Aduaneira, nos termos da alínea “a”, do inciso II do art. 30 da IN RFB nº 2.090, de 22 de junho de 2022, analisará as propostas a fim de eventualmente publicar um ato administrativo sobre o tema.

## 5. Siglas

| Sigla | Significado |
|---|---|
| DI | Declaração de Importação |
| FOB | Free on Board |
| NVE | Nomenclatura de Valor Aduaneiro e Estatística |
| UME | Unidade de Medida Estatística |
| VMLE | Valor da Mercadoria no Local de Embarque |

---

**Referência:** Receita Federal do Brasil. *Conjunto de metadados: Estatísticas de Declarações de Importação*. Transcrição e formatação em Markdown a partir do PDF fornecido, páginas 1–4.
