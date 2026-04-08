# LLM Visibility Monitor — Guida Deploy & Utilizzo

## Panoramica

App Streamlit per il monitoraggio della visibilità brand su LLM e SERP AI, con login Google OAuth e gestione multi-utente/multi-progetto.

**Piattaforme monitorate:**
- LLM: ChatGPT, Claude, Gemini, Perplexity
- SERP AI: Google AI Overview, Google AI Mode
- *(Stand-by)*: Copilot (Bing)

**Flusso operativo:**
1. Login con Google → filtro su dominio email
2. Setup → crea progetto, inserisci keyword seed
3. Espansione → estrai PAA + genera fan-out, seleziona query
4. Configurazione → iterazioni, scheduling, piattaforme, gestione accessi
5. Esecuzione → lancia run con progress bar
6. Storico & Report → grafici Jaccard, trend, export Google Sheets / Excel

---

## Architettura Credenziali

| Cosa | Dove | Note |
|------|------|------|
| Supabase URL + Key | Secrets / `.env` | Infrastruttura DB |
| API keys (OpenAI, Anthropic, Google AI, Perplexity, SerpAPI) | Secrets / `.env` | Un unico set condiviso |
| Google OAuth (client_id, client_secret) | `google_client_secret.json` | File nella root del progetto |
| Cookie key + redirect URI + dominio | Secrets / `.env` | Configurazione login |
| Google Sheets service account | Secrets / `.env` | Per export su Sheets |

Le API keys vengono caricate una volta all'avvio dell'app e condivise tra tutti gli utenti e progetti.

---

## Passo 1 — Google Cloud: OAuth + Sheets

### 1a. OAuth per il Login

1. Vai su [Google Cloud Console](https://console.cloud.google.com)
2. Crea un progetto (o usa uno esistente)
3. **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**
4. Tipo: **Web Application**
5. Authorized redirect URIs: aggiungi `https://tua-app.streamlit.app` (e `http://localhost:8501` per sviluppo locale)
6. Scarica il JSON → rinominalo `google_client_secret.json` → mettilo nella root del progetto
7. **APIs & Services → OAuth consent screen**: configura nome app, dominio, email supporto

### 1b. Service Account per Google Sheets

1. **IAM & Admin → Service Accounts → Create**
2. Scarica il JSON delle credenziali
3. **APIs & Services → Library**: abilita Google Sheets API e Google Drive API
4. Su Streamlit Cloud, incolla il contenuto JSON come TOML dict in `[GOOGLE_SERVICE_ACCOUNT]`

---

## Passo 2 — Database Supabase

1. Crea un progetto su [supabase.com](https://supabase.com)
2. Vai su **SQL Editor**
3. Incolla ed esegui il contenuto di `supabase_schema.sql`
4. Annota URL e Service Role Key da **Project Settings → API**

### Tabelle

| Tabella | Scopo |
|---------|-------|
| `lvm_projects` | Progetti / clienti |
| `lvm_keywords` | Keyword seed |
| `lvm_expanded_queries` | PAA + fan-out con flag selezione |
| `lvm_run_configs` | Configurazione run |
| `lvm_runs` | Storico esecuzioni |
| `lvm_responses` | Risposte LLM (1 riga per chiamata) |
| `lvm_brand_mentions` | Brand estratti per risposta |
| `lvm_source_citations` | URL/fonti citate per risposta |
| `lvm_run_metrics` | Metriche aggregate (Jaccard, conteggi) |
| `lvm_users` | Utenti (auto-registrati al login Google) |
| `lvm_user_projects` | Assegnazione utente → progetto |

---

## Passo 3 — Setup Locale

```bash
git init llm-visibility-monitor
cd llm-visibility-monitor

# Copia tutti i file del progetto

pip install -r requirements.txt

# Configura
cp .env.example .env
# Modifica .env con le tue credenziali

# Metti google_client_secret.json nella root

streamlit run app.py
```

---

## Passo 4 — Deploy su Streamlit Cloud

1. Push del repo su GitHub (privato)
2. Vai su [share.streamlit.io](https://share.streamlit.io) → **New app**
3. Seleziona il repo, branch `main`, main file `app.py`
4. **Advanced Settings → Secrets**: incolla il contenuto qui sotto con le tue chiavi

### Secrets (TOML)

```toml
# Supabase
SUPABASE_URL = "https://xxxxx.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "eyJhbG..."

# API Keys
SERPAPI_KEY = "xxx"
OPENAI_API_KEY = "sk-..."
ANTHROPIC_API_KEY = "sk-ant-..."
GOOGLE_API_KEY = "AIza..."
PPLX_API_KEY = "pplx-..."

# Google OAuth
AUTH_COOKIE_KEY = "una_stringa_segreta_random_lunga_32chars"
AUTH_REDIRECT_URI = "https://tua-app.streamlit.app"
ALLOWED_DOMAIN = "tuaagenzia.com"

# Google Sheets
[GOOGLE_SERVICE_ACCOUNT]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "...@...iam.gserviceaccount.com"
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
```

**Nota:** il file `google_client_secret.json` deve essere committato nel repo (non contiene segreti sensibili — solo client_id e redirect URIs). Il `.gitignore` è configurato per escluderlo; rimuovi la riga `google_client_secret*.json` dal `.gitignore` oppure aggiungilo con `git add -f`.

---

## Struttura File

```
llm-visibility-monitor/
├── app.py                          # App Streamlit (login + 5 tab)
├── db.py                           # Supabase client, API keys loader, user helpers
├── llm_api.py                      # Chiamate API: LLM + SERP (SerpAPI)
├── analysis.py                     # NER brand, URL, Jaccard, metriche
├── fanout.py                       # Generazione query fan-out via Claude
├── engine.py                       # Motore di esecuzione run
├── sheets_export.py                # Export su Google Sheets (gspread)
├── supabase_schema.sql             # Schema DDL per Supabase
├── requirements.txt                # Dipendenze Python
├── google_client_secret.json       # OAuth credentials (da Google Cloud)
├── .env                            # Variabili d'ambiente (locale)
├── .env.example                    # Template .env
├── .gitignore
├── .streamlit/
│   ├── config.toml                 # Tema dark
│   └── secrets.toml.example        # Template Secrets
└── GUIDA_DEPLOY.md                 # Questa guida
```

---

## Sistema di Login

- L'utente accede con il suo account Google
- Solo le email `@ALLOWED_DOMAIN` passano il filtro
- Al primo login l'utente viene auto-registrato in `lvm_users`
- Nella sidebar vede solo i progetti a cui è stato assegnato
- Quando crea un nuovo progetto, viene auto-assegnato
- Nella tab Configurazione può assegnare altri utenti ai suoi progetti

### Primo Utente

Al primo deploy, nessun utente è registrato. Il primo utente che fa login con email del dominio corretto viene registrato automaticamente. Quando crea il primo progetto, viene assegnato. Da lì può assegnare altri utenti dalla tab Configurazione.

---

## Costi API Stimati

Per un run tipico (50 query × 6 piattaforme × 3 iterazioni = 900 chiamate):

| Servizio | Costo stimato per run |
|----------|-----------------------|
| OpenAI (GPT-4o) | ~$2-4 |
| Anthropic (Claude Sonnet) | ~$1-3 |
| Google (Gemini Flash) | ~$0.10 |
| Perplexity (Sonar) | ~$1-2 |
| SerpAPI (AIO + AI Mode + PAA) | ~$5-15 |
| **Totale** | **~$10-25 per run** |

---

## Note Tecniche

- **Thread-safety**: ogni operazione DB nei thread usa `make_supabase()` (client non-cached)
- **Paginazione**: `fetch_all()` pagina automaticamente oltre le 1000 righe Supabase
- **Secrets**: lo script legge prima da `st.secrets`, poi fallback su `os.getenv`
- **Scheduler**: APScheduler verifica ogni ora, carica API keys dai Secrets
- **Copilot**: predisposto — basta aggiungere `call_copilot()` in `llm_api.py`
