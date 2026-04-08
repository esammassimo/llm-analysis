"""
db.py — Supabase client, env helper, pagination
"""
import os
import time
import streamlit as st
from supabase import create_client, Client
from typing import Any, Dict, List


def get_env(name: str) -> str:
    """Read from st.secrets first, then os.environ."""
    try:
        v = st.secrets.get(name)
        if v:
            return str(v)
    except Exception:
        pass
    v = os.getenv(name)
    if v:
        return v
    raise RuntimeError(f"Variabile '{name}' mancante. Configurala in Secrets o .env")


def _create_client() -> Client:
    """Crea un nuovo client Supabase."""
    return create_client(get_env("SUPABASE_URL"), get_env("SUPABASE_SERVICE_ROLE_KEY"))


def get_supabase() -> Client:
    """
    Ritorna un client Supabase. Se il client cached ha perso la connessione,
    ne crea uno nuovo.
    """
    if "sb_client" not in st.session_state:
        st.session_state.sb_client = _create_client()
    return st.session_state.sb_client


def refresh_supabase() -> Client:
    """Forza la ricreazione del client (dopo un errore di connessione)."""
    st.session_state.sb_client = _create_client()
    return st.session_state.sb_client


def make_supabase() -> Client:
    """Non-cached: per thread separati."""
    return _create_client()


def sb_query(fn, max_retries: int = 2):
    """
    Wrapper con retry per le query Supabase.
    Se la connessione cade (httpx.ReadError), ricrea il client e riprova.

    Uso:
        data = sb_query(lambda sb: sb.table("x").select("*").execute()).data
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            sb = get_supabase()
            return fn(sb)
        except Exception as e:
            last_error = e
            error_str = str(e).lower()
            if any(k in error_str for k in ["readtimeout", "readerror", "connection", "closed", "reset"]):
                refresh_supabase()
                time.sleep(0.5)
            else:
                raise
    raise last_error


PAGE_SIZE = 1000


def get_api_keys() -> Dict[str, str]:
    """
    Carica tutte le API keys dai Secrets / .env.
    Ritorna dict: {openai, anthropic, google, pplx, serpapi}
    """
    keys = {}
    mapping = {
        "openai":    "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google":    "GOOGLE_API_KEY",
        "pplx":      "PPLX_API_KEY",
        "serpapi":   "SERPAPI_KEY",
    }
    for short_name, env_name in mapping.items():
        try:
            keys[short_name] = get_env(env_name)
        except RuntimeError:
            pass  # chiave non configurata, ok
    return keys


# ─── User & project helpers ──────────────────────────────────────────────────

def upsert_user(email: str, display_name: str = "", avatar_url: str = "",
                sb: Client | None = None):
    """Crea o aggiorna l'utente al login."""
    if sb is None:
        sb = get_supabase()
    sb.table("lvm_users").upsert({
        "email": email,
        "display_name": display_name,
        "avatar_url": avatar_url,
        "last_login": "now()",
    }, on_conflict="email").execute()


def get_user_projects(email: str, sb: Client | None = None) -> List[Dict]:
    """Ritorna i progetti assegnati all'utente."""
    if sb is None:
        sb = get_supabase()
    resp = sb.table("lvm_user_projects").select(
        "project_id, lvm_projects(id, name, slug, language, created_at)"
    ).eq("user_email", email).execute()
    projects = []
    for row in (resp.data or []):
        p = row.get("lvm_projects")
        if p:
            projects.append(p)
    return projects


def assign_user_to_project(email: str, project_id: str, sb: Client | None = None):
    """Assegna un utente a un progetto."""
    if sb is None:
        sb = get_supabase()
    try:
        sb.table("lvm_user_projects").insert({
            "user_email": email,
            "project_id": project_id,
        }).execute()
    except Exception:
        pass  # già assegnato


def get_all_users(sb: Client | None = None) -> List[Dict]:
    """Ritorna tutti gli utenti registrati."""
    if sb is None:
        sb = get_supabase()
    return sb.table("lvm_users").select("*").order("last_login", desc=True).execute().data or []


def fetch_all(table: str, sb: Client, filters: Dict[str, Any] | None = None,
              select: str = "*", order: str | None = None) -> List[Dict]:
    """Paginated fetch from Supabase table."""
    all_rows: List[Dict] = []
    offset = 0
    while True:
        q = sb.table(table).select(select)
        if filters:
            for k, v in filters.items():
                q = q.eq(k, v)
        if order:
            q = q.order(order)
        resp = q.range(offset, offset + PAGE_SIZE - 1).execute()
        batch = resp.data or []
        all_rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return all_rows
