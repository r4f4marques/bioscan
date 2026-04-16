# Como subir o BioScan no GitHub
## Guia passo a passo — sem conhecimento de programação necessário

---

## O que você vai precisar

- Conta no GitHub (https://github.com) — crie se não tiver, é gratuito
- Git instalado no seu computador
  - Mac: já vem instalado. Confirme abrindo o Terminal e digitando `git --version`
  - Windows: baixe em https://git-scm.com/download/win

---

## PARTE 1 — Criar o repositório no GitHub

1. Acesse https://github.com e faça login

2. Clique no botão verde **"New"** (canto superior esquerdo)

3. Preencha:
   - **Repository name:** `bioscan`
   - **Description:** `Plataforma de análise de bioimpedância Tanita com foco em healthspan`
   - Marque **Private** (por enquanto — você pode tornar público depois)
   - **NÃO marque** "Add a README file" (já temos um)
   - **NÃO marque** "Add .gitignore" (já temos um)

4. Clique em **"Create repository"**

5. O GitHub vai mostrar uma página com instruções. **Deixe essa página aberta** — vamos usar o endereço do repositório.

---

## PARTE 2 — Enviar os arquivos para o GitHub

### No Mac

1. Abra o **Terminal** (pesquise "Terminal" no Spotlight)

2. Navegue até a pasta do projeto. Se você salvou na pasta Downloads:
   ```
   cd ~/Downloads/bioscan
   ```

3. Execute os comandos abaixo, **um por vez**:

   ```bash
   git init
   ```
   ```bash
   git add .
   ```
   ```bash
   git commit -m "primeiro commit — BioScan Healthspan"
   ```
   ```bash
   git branch -M main
   ```
   ```bash
   git remote add origin https://github.com/SEU-USUARIO/bioscan.git
   ```
   ⚠️ Substitua `SEU-USUARIO` pelo seu nome de usuário do GitHub

   ```bash
   git push -u origin main
   ```

4. O GitHub vai pedir seu usuário e senha. Para a senha, **não use a senha do site** — use um "Personal Access Token":
   - Acesse: https://github.com/settings/tokens/new
   - Em "Note", escreva: `bioscan`
   - Em "Expiration", escolha `90 days`
   - Marque a opção `repo`
   - Clique **Generate token**
   - Copie o token gerado (começa com `ghp_`) e use como senha

### No Windows

1. Abra o **Git Bash** (instalado junto com o Git)

2. Os comandos são idênticos aos do Mac acima

---

## PARTE 3 — Conectar ao Railway

O Railway vai detectar automaticamente que é um projeto Python Flask.

1. Acesse https://railway.app e faça login

2. Clique em **"New Project"**

3. Escolha **"Deploy from GitHub repo"**

4. Autorize o Railway a acessar seu GitHub (primeira vez apenas)

5. Selecione o repositório **`bioscan`**

6. O Railway vai detectar o `Procfile` e iniciar o deploy

7. **Adicione o banco de dados:**
   - No painel do projeto, clique em **"+ New"**
   - Escolha **"Database"** → **"Add PostgreSQL"**
   - Aguarde criar (1-2 minutos)

8. **Configure as variáveis de ambiente:**
   - Clique no serviço `bioscan` (não no banco)
   - Vá em **"Variables"**
   - Clique em **"+ New Variable"** para cada uma:

   | Nome | Como obter o valor |
   |------|--------------------|
   | `SECRET_KEY` | Abra o Terminal e execute: `python3 -c "import secrets; print(secrets.token_hex(32))"` — copie o resultado |
   | `JWT_SECRET` | Execute o mesmo comando acima de novo para gerar um valor diferente |
   | `GROQ_API_KEY` | Copie do painel do Railway do TriDash — já está lá |

9. O Railway vai fazer um novo deploy com as variáveis. Aguarde ~1 minuto.

10. Clique em **"Settings"** → **"Domains"** → **"Generate Domain"** para ter uma URL pública.

---

## PARTE 4 — Testar se funcionou

Substitua `SEU-APP` pela URL gerada no Railway:

Abra no navegador:
```
https://SEU-APP.railway.app/bioscan/health
```

Deve aparecer:
```json
{"service": "bioscan-healthspan", "status": "ok"}
```

Se aparecer isso, o BioScan está no ar! 🎉

---

## PARTE 5 — Conectar ao TriDash (opcional por agora)

Quando quiser integrar ao TriDash, edite o arquivo `app.py` do TriDash e adicione:

```python
# No topo do arquivo, junto com os outros imports:
from bioscan import init_bioscan

# No final, depois de configurar o app:
init_bioscan(app)
```

E no `requirements.txt` do TriDash:
```
bioscan @ git+https://github.com/SEU-USUARIO/bioscan.git
```

Depois é só fazer `git push` no TriDash — o Railway vai reinstalar as dependências automaticamente.

---

## Problemas comuns

**"Permission denied" ou "Authentication failed"**
→ Use o Personal Access Token como senha (veja Parte 2, passo 4)

**"python3: command not found"**
→ No Windows, use `python` em vez de `python3`

**Railway diz "Build failed"**
→ Verifique se as 3 variáveis de ambiente foram configuradas corretamente (Parte 3, passo 8)

**Quero fazer uma alteração e enviar de novo**
→ Após editar qualquer arquivo:
```bash
git add .
git commit -m "descrição da alteração"
git push
```
O Railway faz o deploy automaticamente.
