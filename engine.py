"""
engine.py — Motore di esecuzione dei run
"""
import time
import logging
import traceback
from datetime import datetime
from typing import Dict, List, Any, Optional
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from db import make_supabase
from llm_api import call_platform, MODELS
from brand_analysis import extract_brands, extract_urls, normalize_domain, compute_run_metrics

log = logging.getLogger(__name__)

DELAY_BETWEEN_CALLS = 2.0


def execute_run(
    project_id: str,
    run_id: str,
    queries: List[Dict],      # [{id, query_text, query_type}]
    platforms: List[str],      # ["chatgpt", "claude", ...]
    api_keys: Dict[str, str],  # {"openai": "sk-...", "anthropic": "sk-ant-...", ...}
    iterations: int = 3,
    language: str = "it",
    progress_callback=None,    # fn(completed, total, detail_str)
) -> Dict:
    """
    Esegue un run completo: itera su query × piattaforme × iterazioni.
    Salva tutto su Supabase in tempo reale.
    api_keys viene passato dal session_state di Streamlit.
    """
    sb = make_supabase()
    total_calls = len(queries) * len(platforms) * iterations
    completed = 0

    # Update run status
    sb.table("lvm_runs").update({
        "status": "running",
        "started_at": datetime.utcnow().isoformat(),
        "total_calls": total_calls,
    }).eq("id", run_id).execute()

    responses_by_platform: Dict[str, List[Dict]] = defaultdict(list)
    errors = []
    _cancel_check_interval = 5  # controlla cancellazione ogni N chiamate

    for query in queries:
        for platform in platforms:
            for iteration in range(1, iterations + 1):
                # ─── Cancellation check ──────────────────────────
                if completed > 0 and completed % _cancel_check_interval == 0:
                    try:
                        status_check = sb.table("lvm_runs").select("status").eq("id", run_id).limit(1).execute()
                        if status_check.data and status_check.data[0]["status"] == "cancelled":
                            log.info(f"Run {run_id} cancellato dall'utente dopo {completed} chiamate.")
                            sb.table("lvm_runs").update({
                                "completed_at": datetime.utcnow().isoformat(),
                                "completed_calls": completed,
                                "error_log": f"Cancellato dall'utente dopo {completed}/{total_calls} chiamate.",
                            }).eq("id", run_id).execute()
                            return {
                                "total": total_calls,
                                "completed": completed,
                                "errors": len(errors),
                                "metrics": compute_run_metrics(responses_by_platform) if responses_by_platform else {},
                                "cancelled": True,
                            }
                    except Exception:
                        pass

                try:
                    text, elapsed = call_platform(
                        platform, query["query_text"], api_keys, language
                    )

                    # Extract brands and URLs
                    brands = extract_brands(text) if text else []
                    urls = extract_urls(text) if text else []
                    domains = [normalize_domain(u) for u in urls]

                    # Save response
                    resp_data = {
                        "run_id": run_id,
                        "project_id": project_id,
                        "query_id": query["id"],
                        "query_text": query["query_text"],
                        "platform": platform,
                        "model_used": MODELS.get(platform, platform),
                        "iteration": iteration,
                        "response_text": text[:50000] if text else "",
                        "response_time_s": round(elapsed, 2),
                    }
                    resp_result = sb.table("lvm_responses").insert(resp_data).execute()
                    response_id = resp_result.data[0]["id"] if resp_result.data else None

                    # Save brand mentions
                    if response_id and brands:
                        brand_counts = {}
                        for b in brands:
                            brand_counts[b] = brand_counts.get(b, 0) + 1

                        for brand, count in brand_counts.items():
                            pos = text.lower().find(brand.lower()) if text else None
                            sb.table("lvm_brand_mentions").insert({
                                "response_id": response_id,
                                "run_id": run_id,
                                "project_id": project_id,
                                "platform": platform,
                                "brand": brand,
                                "mention_count": count,
                                "position_first": pos if pos >= 0 else None,
                            }).execute()

                    # Save source citations
                    if response_id and urls:
                        for url, domain in zip(urls, domains):
                            sb.table("lvm_source_citations").insert({
                                "response_id": response_id,
                                "run_id": run_id,
                                "project_id": project_id,
                                "platform": platform,
                                "url": url[:2000],
                                "domain": domain,
                            }).execute()

                    # Track for metrics
                    responses_by_platform[platform].append({
                        "query_text": query["query_text"],
                        "iteration": iteration,
                        "brands": brands,
                        "domains": domains,
                        "response_text": text,
                    })

                except Exception as e:
                    error_msg = f"{platform}/{query['query_text'][:50]}/iter{iteration}: {str(e)}"
                    errors.append(error_msg)
                    log.error(error_msg)

                    # Save error response
                    sb.table("lvm_responses").insert({
                        "run_id": run_id,
                        "project_id": project_id,
                        "query_id": query["id"],
                        "query_text": query["query_text"],
                        "platform": platform,
                        "model_used": MODELS.get(platform, platform),
                        "iteration": iteration,
                        "response_text": "",
                        "error": str(e)[:1000],
                    }).execute()

                completed += 1
                if progress_callback:
                    progress_callback(
                        completed, total_calls,
                        f"{platform} — {query['query_text'][:40]}… (iter {iteration})"
                    )

                # Rate limiting
                time.sleep(DELAY_BETWEEN_CALLS)

    # Compute and save metrics
    try:
        metrics = compute_run_metrics(responses_by_platform)
        for platform, m in metrics.items():
            if platform == "_cross_platform":
                sb.table("lvm_run_metrics").insert({
                    "run_id": run_id,
                    "project_id": project_id,
                    "platform": "cross_platform",
                    "metric_type": "jaccard_cross",
                    "metric_value": sum(m.values()) / len(m) if m else 0,
                    "metric_detail": m,
                }).execute()
            else:
                for metric_type, value in m.items():
                    sb.table("lvm_run_metrics").insert({
                        "run_id": run_id,
                        "project_id": project_id,
                        "platform": platform,
                        "metric_type": metric_type,
                        "metric_value": value,
                    }).execute()
    except Exception as e:
        log.error(f"Errore calcolo metriche: {e}")

    # Update run status
    sb.table("lvm_runs").update({
        "status": "completed" if not errors else "completed",
        "completed_at": datetime.utcnow().isoformat(),
        "completed_calls": completed,
        "error_log": "\n".join(errors[:50]) if errors else None,
    }).eq("id", run_id).execute()

    return {
        "total": total_calls,
        "completed": completed,
        "errors": len(errors),
        "metrics": metrics,
    }
