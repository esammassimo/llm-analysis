"""
db.py — Supabase client, env helper, pagination
"""
import os
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


@st.cache_resource
def get_supabase() -> Client:
    return create_client(get_env("SUPABASE_URL"), get_env("SUPABASE_SERVICE_ROLE_KEY"))


def make_supabase() -> Client:
    """Non-cached: per thread separati."""
    return create_client(get_env("SUPABASE_URL"), get_env("SUPABASE_SERVICE_ROLE_KEY"))


PAGE_SIZE = 1000


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
