# LLM Visibility Monitor — Guida Deploy & Utilizzo

## Panoramica

App Streamlit multi-pagina per il monitoraggio della visibilità brand su:
- **LLM**: ChatGPT, Claude, Gemini, Perplexity
- **SERP AI**: Google AI Overview, Google AI Mode
- *(Stand-by)*: Copilot (Bing)

### Flusso operativo

1. **Setup** — Crea progetto, inserisci 10-20 keyword seed
2. **Espansione** — Estrai PAA (SerpAPI) + genera fan-out (Claude), seleziona query
3. **Configurazione** — Iterazioni, scheduling, lingua, piattaforme attive
4. **Esecuzione** — Lancia run con progress bar in tempo reale
5. **Storico & Report** — Grafici, metriche Jaccard, export Google Sheets / Excel

---

## Prerequisiti

### API Keys necessarie

| Servizio | Variabile | Dove ottenerla |
|----------|-----------|----------------|
| Supabase | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | [supabase.com](https://supabase.com) → Project Settings → API |
| SerpAPI | `SERPAPI_KEY` | [serpapi.com](https://serpapi.com) |
| OpenAI | `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com) |
| Anthropic | `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| Google AI | `GOOGLE_API_KEY` | [aistudio.google.dev](https://aistudio.google.dev) |
| Perplexity | `PPLX_API_KEY` | [perplexity.ai](https://docs.perplexity.ai) |
| Google Sheets | `GOOGLE_SERVICE_ACCOUNT` | Google Cloud Console → Service Account |

### Google Sheets Setup

1. Vai su Google Cloud Console → IAM → Service Accounts
2. Crea un service account
3. Scarica il JSON delle credenziali
4. Abilita Google Sheets API e Google Drive API nel progetto
5. Su Streamlit Cloud, incolla il contenuto JSON come TOML dict in `[GOOGLE_SERVICE_ACCOUNT]`

---

## Passo 1 — Database Supabase

1. Crea un progetto su [supabase.com](https://supabase.com)
2. Vai su **SQL Editor**
3. Incolla ed esegui il contenuto di `supabase_schema.sql`
4. Annota URL e Service Role Key da **Project Settings → API**

Le tabelle create:

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

---

## Passo 2 — Setup Locale

```bash
# Clona o crea il repo
git init llm-visibility-monitor
cd llm-visibility-monitor

# Copia tutti i file del progetto nella root

# Installa dipendenze
pip install -r requirements.txt

# Configura .env
cp .env.example .env
# Modifica .env con le tue API key

# Avvia
streamlit run app.py
```

---

## Passo 3 — Deploy su Streamlit Cloud

1. Push del repo su GitHub (privato)
2. Vai su [share.streamlit.io](https://share.streamlit.io) → **New app**
3. Seleziona il repo, branch `main`, main file `app.py`
4. **Advanced Settings → Secrets**: incolla il contenuto da `secrets.toml.example` con le tue chiavi reali
5. Deploy

### Struttura Secrets (TOML)

```toml
SUPABASE_URL = "https://xxxxx.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "eyJhbG..."
SERPAPI_KEY = "xxx"
OPENAI_API_KEY = "sk-..."
ANTHROPIC_API_KEY = "sk-ant-..."
GOOGLE_API_KEY = "AIza..."
PPLX_API_KEY = "pplx-..."

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

---

## Struttura File

```
llm-visibility-monitor/
├── app.py                          # App principale Streamlit (5 tab)
├── db.py                           # Client Supabase, env helper, paginazione
├── llm_api.py                      # Chiamate API: LLM + SERP (SerpAPI)
├── analysis.py                     # NER brand, URL, Jaccard, metriche
├── fanout.py                       # Generazione query fan-out via Claude
├── engine.py                       # Motore di esecuzione run
├── sheets_export.py                # Export su Google Sheets (gspread)
├── supabase_schema.sql             # Schema DDL per Supabase
├── requirements.txt                # Dipendenze Python
├── .env.example                    # Template variabili d'ambiente (locale)
├── .streamlit/
│   ├── config.toml                 # Tema dark Streamlit
│   └── secrets.toml.example        # Template Secrets (Streamlit Cloud)
└── GUIDA_DEPLOY.md                 # Questa guida
```

---

## Moduli in Dettaglio

### `llm_api.py` — Chiamate API

Ogni piattaforma ha la sua funzione dedicata:

- `call_chatgpt()` → OpenAI API (`gpt-4o`)
- `call_claude()` → Anthropic API (`claude-sonnet-4-20250514`)
- `call_gemini()` → Google Generative AI (`gemini-2.0-flash`)
- `call_perplexity()` → Perplexity API (`sonar`)
- `call_ai_overview()` → SerpAPI, engine `google` → token → `google_ai_overview`
- `call_ai_mode()` → SerpAPI, engine `google_ai_mode`
- `fetch_paa()` → SerpAPI, `related_questions` da Google Search

Il dispatcher `call_platform(platform, query, lang)` smista automaticamente.

### `analysis.py` — Estrazione & Metriche

- **Brand extraction**: dual-mode (pattern markdown bold + regex su maiuscole), con stopword IT/EN
- **URL extraction**: regex standard
- **Jaccard intra-platform**: media pairwise tra iterazioni dello stesso prompt (ripetibilità)
- **Jaccard cross-platform**: pairwise tra piattaforme (accordo)
- Tutte le metriche vengono salvate in `lvm_run_metrics`

### `engine.py` — Esecuzione Run

Itera su `query × piattaforma × iterazione`, con:
- Salvataggio in tempo reale su Supabase (ogni risposta, brand, fonte)
- Progress callback per la UI
- Rate limiting (`DELAY_BETWEEN_CALLS = 2s`)
- Gestione errori per singola chiamata (non blocca il run)
- Calcolo metriche aggregate a fine run

### `fanout.py` — Generazione Fan-out

Usa Claude Sonnet per generare query fan-out dalle keyword seed. Il prompt chiede domande reali che un utente cercherebbe, coprendo intenti informativi, comparativi e transazionali.

### Scheduler

Lo scheduler usa APScheduler (BackgroundScheduler) per verificare ogni ora se ci sono configurazioni attive con run giornalieri da eseguire. Funziona su Streamlit Cloud finché l'app è attiva (cioè con almeno un utente connesso). Per scheduling più robusto, valuta un cron esterno o GitHub Actions.

---

## Costi API Stimati

Per un run tipico (50 query × 6 piattaforme × 3 iterazioni = 900 chiamate):

| Servizio | Costo stimato per run |
|----------|-----------------------|
| OpenAI (GPT-4o) | ~$2-4 |
| Anthropic (Claude Sonnet) | ~$1-3 |
| Google (Gemini Flash) | ~$0.10 |
| Perplexity (Sonar) | ~$1-2 |
| SerpAPI (AIO + AI Mode + PAA) | ~$5-15 (dipende dal piano) |
| **Totale** | **~$10-25 per run** |

---

## Note Tecniche

- **Thread-safety**: ogni operazione DB nei thread usa `make_supabase()` (client non-cached)
- **Paginazione**: `fetch_all()` pagina automaticamente oltre le 1000 righe di Supabase
- **Secrets**: lo script legge prima da `st.secrets`, poi fallback su `os.getenv` (sviluppo locale)
- **Copilot**: la struttura è pronta per l'integrazione futura — basta aggiungere `call_copilot()` in `llm_api.py` e il valore `"copilot"` nelle opzioni config
