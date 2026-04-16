# BioScan Healthspan

Plataforma de análise de bioimpedância Tanita com foco em **healthspan** — qualidade de vida a longo prazo.

Desenvolvido para médicos e profissionais de saúde que usam balanças Tanita e querem acompanhar a evolução da composição corporal dos pacientes ao longo do tempo.

---

## O que o BioScan faz

- **Importa CSVs da Tanita** — todos os 28 campos mapeados, incluindo dados segmentais (braços, pernas, tronco)
- **Armazena o histórico** completo de cada paciente em banco de dados
- **API REST** pronta para conectar ao frontend (dashboard React) ou ao TriDash
- **Interpretação por IA** — envia os dados para o Groq (llama-3.3-70b) e retorna análise clínica em linguagem natural
- **Risk flags** automáticos para gordura visceral, IMC, idade metabólica e gordura corporal
- **Autenticação JWT** com perfis separados para médico e paciente

---

## Integração com o TriDash

O BioScan foi projetado para rodar junto com o TriDash ou de forma independente.

**Para integrar ao TriDash existente**, adicione ao `app.py` do TriDash:

```python
from bioscan import init_bioscan
init_bioscan(app)  # depois de configurar o app Flask
```

E no `requirements.txt` do TriDash:
```
bioscan @ git+https://github.com/SEU-USUARIO/bioscan.git
```

Pronto. Todas as rotas ficam disponíveis em `/bioscan/...` sem conflito com as rotas existentes.

---

## Instalação local (desenvolvimento)

### Pré-requisitos
- Python 3.11 ou superior
- Git

### Passo a passo

**1. Clone o repositório**
```bash
git clone https://github.com/SEU-USUARIO/bioscan.git
cd bioscan
```

**2. Crie um ambiente virtual**
```bash
python -m venv venv

# Mac / Linux:
source venv/bin/activate

# Windows:
venv\Scripts\activate
```

**3. Instale as dependências**
```bash
pip install -e ".[dev]"
```

**4. Configure as variáveis de ambiente**
```bash
cp .env.example .env
# Abra o .env e preencha os valores (veja as instruções dentro do arquivo)
```

**5. Inicie o servidor**
```bash
python -m bioscan.app
```

O servidor estará disponível em `http://localhost:5001`

---

## Deploy no Railway

### Pré-requisitos
- Conta no [Railway](https://railway.app) (o mesmo que você usa para o TriDash)

### Passo a passo

**1. Crie um novo projeto no Railway**
- Acesse [railway.app](https://railway.app)
- Clique em **New Project**
- Escolha **Deploy from GitHub repo**
- Selecione o repositório `bioscan`

**2. Adicione um banco de dados PostgreSQL**
- Dentro do projeto, clique em **+ New**
- Escolha **Database → PostgreSQL**
- O Railway vai criar automaticamente a variável `DATABASE_URL`

**3. Configure as variáveis de ambiente**

No painel do Railway, vá em **Variables** e adicione:

| Variável | Valor |
|----------|-------|
| `SECRET_KEY` | Gere com: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `JWT_SECRET` | Gere outro valor aleatório da mesma forma |
| `GROQ_API_KEY` | Sua chave do Groq (a mesma do TriDash) |

As variáveis `DATABASE_URL` e `PORT` são preenchidas automaticamente pelo Railway.

**4. Deploy**
- O Railway fará o deploy automaticamente a cada `git push`
- Acompanhe os logs em tempo real no painel

---

## Testando a API

Após o deploy (ou localmente), você pode testar com os comandos abaixo.

**Login**
```bash
curl -X POST https://SEU-APP.railway.app/bioscan/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@bioscan.fit","password":"demo"}'
```

**Importar CSV da Tanita**
```bash
# Salve o token do login em TOKEN
TOKEN="eyJ..."

curl -X POST https://SEU-APP.railway.app/bioscan/patients/1/import-csv \
  -H "Authorization: Bearer $TOKEN" \
  -F "csv=@csv_report_2026-04-14_14-20-18.csv"
```

**Pedir interpretação por IA**
```bash
curl -X POST https://SEU-APP.railway.app/bioscan/patients/1/interpret \
  -H "Authorization: Bearer $TOKEN"
```

---

## Rodando os testes

```bash
pytest
```

Os testes cobrem o parser CSV, autenticação, CRUD de pacientes, importação e risk flags. O arquivo CSV real da Tanita está embutido como fixture nos testes.

---

## Estrutura do projeto

```
bioscan/
├── bioscan/
│   ├── __init__.py        — exports públicos
│   ├── app.py             — factory Flask + integração TriDash
│   ├── models.py          — User, Patient, Measurement (SQLAlchemy)
│   ├── routes.py          — todos os endpoints REST
│   └── tanita_parser.py   — parser CSV Tanita (28 campos)
├── tests/
│   └── test_bioscan.py    — testes automatizados
├── .env.example           — template de variáveis de ambiente
├── .gitignore
├── Procfile               — comando de start para Railway
├── pyproject.toml         — empacotamento e dependências
└── railway.toml           — configuração de deploy
```

---

## Endpoints da API

### Auth
| Método | Rota | Descrição |
|--------|------|-----------|
| POST | `/bioscan/auth/login` | Login — retorna JWT |
| POST | `/bioscan/auth/register` | Criar usuário (somente médico) |

### Pacientes
| Método | Rota | Descrição |
|--------|------|-----------|
| GET | `/bioscan/patients` | Lista com última medição |
| POST | `/bioscan/patients` | Criar paciente |
| GET | `/bioscan/patients/:id` | Detalhes + histórico completo |
| PATCH | `/bioscan/patients/:id` | Atualizar dados |
| DELETE | `/bioscan/patients/:id` | Remover |

### Medições
| Método | Rota | Descrição |
|--------|------|-----------|
| GET | `/bioscan/patients/:id/measurements` | Histórico (suporta `?from=` e `?to=`) |
| POST | `/bioscan/patients/:id/measurements` | Inserção manual |
| DELETE | `/bioscan/patients/:id/measurements/:mid` | Remover medição |

### Import CSV
| Método | Rota | Descrição |
|--------|------|-----------|
| POST | `/bioscan/patients/:id/import-csv` | Upload CSV → paciente específico |
| POST | `/bioscan/import-csv-raw` | Upload CSV → cria paciente se necessário |

### Inteligência
| Método | Rota | Descrição |
|--------|------|-----------|
| GET | `/bioscan/patients/:id/summary` | Resumo healthspan + risk flags |
| POST | `/bioscan/patients/:id/interpret` | Interpretação Groq (llama-3.3-70b) |
| GET | `/bioscan/health` | Health check |

---

## Balanças Tanita suportadas

O parser foi desenvolvido e testado com o formato real exportado pela Tanita. Modelos compatíveis:

- BC-780 ✓
- BC-545N ✓  
- MC-780MA ✓ (possui campos adicionais de água intracelular/extracelular)
- Outros modelos que exportam o mesmo formato CSV

> **Nota:** Modelos da linha MC-780MA Professional exportam colunas adicionais de água intracelular e extracelular. O parser ignora colunas desconhecidas, então funciona normalmente — os campos extras simplesmente ficam como `null`.

---

## Licença

MIT — use, modifique e distribua livremente.
