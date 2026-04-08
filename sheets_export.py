"""
sheets_export.py — Export dati su Google Sheets via gspread
"""
import gspread
import json
from google.oauth2.service_account import Credentials
from typing import Dict, List, Any
import streamlit as st
from db import get_supabase, fetch_all


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_gspread_client() -> gspread.Client:
    """Create gspread client from Streamlit secrets."""
    try:
        creds_dict = dict(st.secrets["GOOGLE_SERVICE_ACCOUNT"])
    except Exception:
        import os, json as _json
        creds_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        if creds_path:
            with open(creds_path) as f:
                creds_dict = _json.load(f)
        else:
            raise RuntimeError(
                "Configura GOOGLE_SERVICE_ACCOUNT in Secrets (TOML dict) "
                "o GOOGLE_SERVICE_ACCOUNT_JSON come path al file JSON."
            )
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def export_run_to_sheets(
    project_name: str,
    run_id: str,
    spreadsheet_url: str | None = None,
) -> str:
    """
    Esporta i dati di un run su Google Sheets.
    Se spreadsheet_url è None, crea un nuovo spreadsheet.
    Returns: URL del foglio.
    """
    gc = get_gspread_client()
    sb = get_supabase()

    # Fetch data
    responses = fetch_all("lvm_responses", sb, {"run_id": run_id})
    brands = fetch_all("lvm_brand_mentions", sb, {"run_id": run_id})
    sources = fetch_all("lvm_source_citations", sb, {"run_id": run_id})
    metrics = fetch_all("lvm_run_metrics", sb, {"run_id": run_id})

    # Create or open spreadsheet
    if spreadsheet_url:
        sh = gc.open_by_url(spreadsheet_url)
    else:
        title = f"LVM — {project_name} — Run {run_id[:8]}"
        sh = gc.create(title)
        # Share with anyone with link
        sh.share("", perm_type="anyone", role="reader")

    # ─── Sheet 1: Responses ──────────────────────────────────────────────
    _write_sheet(sh, "Responses", responses, [
        "platform", "query_text", "iteration", "model_used",
        "response_time_s", "error", "created_at"
    ])

    # ─── Sheet 2: Brand Mentions ─────────────────────────────────────────
    _write_sheet(sh, "Brand Mentions", brands, [
        "platform", "brand", "mention_count", "position_first"
    ])

    # ─── Sheet 3: Source Citations ───────────────────────────────────────
    _write_sheet(sh, "Source Citations", sources, [
        "platform", "url", "domain"
    ])

    # ─── Sheet 4: Metrics ────────────────────────────────────────────────
    metrics_rows = []
    for m in metrics:
        row = {
            "platform": m.get("platform", ""),
            "metric_type": m.get("metric_type", ""),
            "metric_value": m.get("metric_value", ""),
            "metric_detail": json.dumps(m.get("metric_detail", "")) if m.get("metric_detail") else "",
        }
        metrics_rows.append(row)
    _write_sheet(sh, "Metrics", metrics_rows, [
        "platform", "metric_type", "metric_value", "metric_detail"
    ])

    # Remove default Sheet1 if exists
    try:
        default = sh.worksheet("Sheet1")
        sh.del_worksheet(default)
    except Exception:
        pass

    return sh.url


def _write_sheet(sh, sheet_name: str, rows: List[Dict], columns: List[str]):
    """Write data to a worksheet, creating or clearing it."""
    try:
        ws = sh.worksheet(sheet_name)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=sheet_name, rows=max(len(rows) + 1, 10), cols=len(columns))

    if not rows:
        ws.update("A1", [columns])
        return

    # Header
    data = [columns]
    for row in rows:
        data.append([str(row.get(c, "")) for c in columns])

    ws.update("A1", data)
