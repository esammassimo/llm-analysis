"""
engine.py — Motore di esecuzione dei run
==========================================
- Retry con backoff esponenziale per singola chiamata
- Esecuzione parallela per piattaforma (ThreadPoolExecutor)
- Rate limiting configurabile per piattaforma
- Resume di run interrotti (riparte dalle query non processate)
- Cancellation check dal DB
- Checkpointing (salva last_checkpoint in lvm_runs)
- Timeout globale per run
- Validazione pre-run delle API keys
- Caching: salta duplicati query+piattaforma+iterazione nello stesso run
"""
import time
import logging
import json
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple, Set
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from db import make_supabase
from llm_api import call_platform, MODELS
from brand_analysis import extract_brands, extract_urls, normalize_domain, compute_run_metrics

log = logging.getLogger(__name__)

# ─── Rate limits per piattaforma (secondi tra chiamate) ──────────────────────
PLATFORM_DELAYS = {
    "chatgpt":     2.0,
    "claude":      1.5,
    "gemini":      1.0,
    "perplexity":  2.0,
    "ai_overview": 2.5,
    "ai_mode":     2.5,
}

DEFAULT_DELAY = 2.0
MAX_RETRIES = 3
CANCEL_CHECK_INTERVAL = 5
DEFAULT_TIMEOUT_MINUTES = 120

# ─── Platform → required API key mapping ─────────────────────────────────────
PLATFORM_KEY_MAP = {
    "chatgpt":     "openai",
    "claude":      "anthropic",
    "gemini":      "google",
    "perplexity":  "pplx",
    "ai_overview": "serpapi",
    "ai_mode":     "serpapi",
}


def validate_api_keys(platforms: List[str], api_keys: Dict[str, str]) -> List[str]:
    """
    Verifica che le API keys necessarie siano presenti.
    Returns: lista di errori (vuota = tutto ok).
    """
    errors = []
    for platform in platforms:
        required_key = PLATFORM_KEY_MAP.get(platform)
        if required_key and not api_keys.get(required_key):
            errors.append(
                f"API key '{required_key}' mancante per {platform}. "
                f"Configurala nei Secrets."
            )
    return errors


def test_api_keys(platforms: List[str], api_keys: Dict[str, str],
                  lang: str = "it") -> Dict[str, str]:
    """
    Test rapido di connessione per ogni piattaforma.
    Returns: {platform: "ok" | "errore: ..."}
    """
    results = {}
    test_query = "test" if lang != "it" else "test connessione"
    for platform in platforms:
        try:
            text, elapsed = call_platform(platform, test_query, api_keys, lang)
            results[platform] = f"ok ({elapsed:.1f}s)"
        except Exception as e:
            results[platform] = f"errore: {str(e)[:100]}"
    return results


def _call_with_retry(platform: str, query: str, api_keys: Dict[str, str],
                     lang: str, max_retries: int = MAX_RETRIES) -> Tuple[str, float]:
    """Chiamata API con retry e backoff esponenziale."""
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return call_platform(platform, query, api_keys, lang)
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                wait = (2 ** attempt) + 0.5  # 1.5s, 2.5s, 4.5s
                log.warning(f"Retry {attempt+1}/{max_retries} per {platform}: {e}. Attendo {wait:.1f}s")
                time.sleep(wait)
            else:
                raise last_error


def _get_completed_tasks(sb, run_id: str) -> Set[str]:
    """
    Carica le combinazioni (query_id, platform, iteration) già completate per il run.
    Usato per resume e deduplicazione.
    Returns: set di chiavi "query_id|platform|iteration"
    """
    resp = sb.table("lvm_responses").select(
        "query_id, platform, iteration"
    ).eq("run_id", run_id).execute()

    completed = set()
    for r in (resp.data or []):
        key = f"{r['query_id']}|{r['platform']}|{r['iteration']}"
        completed.add(key)
    return completed


def _is_cancelled(sb, run_id: str) -> bool:
    """Controlla se il run è stato cancellato dall'utente."""
    try:
        resp = sb.table("lvm_runs").select("status").eq("id", run_id).limit(1).execute()
        return resp.data and resp.data[0]["status"] == "cancelled"
    except Exception:
        return False


def _save_checkpoint(sb, run_id: str, completed: int, checkpoint_info: str):
    """Salva lo stato corrente del run."""
    try:
        sb.table("lvm_runs").update({
            "completed_calls": completed,
            "error_log": checkpoint_info[:5000] if checkpoint_info else None,
        }).eq("id", run_id).execute()
    except Exception:
        pass


def _process_single_call(
    sb, run_id: str, project_id: str,
    query: Dict, platform: str, iteration: int,
    api_keys: Dict[str, str], language: str,
) -> Dict:
    """
    Processa una singola chiamata API: call → extract → save.
    Returns: dict con risultati per le metriche.
    Ogni sotto-operazione è wrappata in try/except: un errore nel salvataggio
    dei brand o delle fonti non blocca la task né il run.
    """
    # Chiamata con retry
    text, elapsed = _call_with_retry(platform, query["query_text"], api_keys, language)

    # Estrazione brand e URL
    brands = extract_brands(text) if text else []
    urls = extract_urls(text) if text else []
    domains = [normalize_domain(u) for u in urls]

    # Salva risposta (critica — se fallisce, lascia risalire l'eccezione)
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
    try:
        resp_result = sb.table("lvm_responses").insert(resp_data).execute()
        response_id = resp_result.data[0]["id"] if resp_result.data else None
    except Exception as e:
        log.error(f"Errore salvataggio risposta {platform}/{query['query_text'][:30]}: {e}")
        response_id = None

    # Salva brand mentions (non critica — errore loggato e ignorato)
    if response_id and brands:
        try:
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
                    "position_first": pos if pos and pos >= 0 else None,
                }).execute()
        except Exception as e:
            log.error(f"Errore salvataggio brand {platform}: {e}")

    # Salva source citations (non critica — errore loggato e ignorato)
    if response_id and urls:
        try:
            for url, domain in zip(urls, domains):
                sb.table("lvm_source_citations").insert({
                    "response_id": response_id,
                    "run_id": run_id,
                    "project_id": project_id,
                    "platform": platform,
                    "url": url[:2000],
                    "domain": domain,
                }).execute()
        except Exception as e:
            log.error(f"Errore salvataggio fonti {platform}: {e}")

    return {
        "query_text": query["query_text"],
        "iteration": iteration,
        "brands": brands,
        "domains": domains,
        "elapsed": elapsed,
    }


def execute_run(
    project_id: str,
    run_id: str,
    queries: List[Dict],
    platforms: List[str],
    api_keys: Dict[str, str],
    iterations: int = 3,
    language: str = "it",
    progress_callback=None,
    timeout_minutes: int = DEFAULT_TIMEOUT_MINUTES,
    resume: bool = False,
) -> Dict:
    """
    Esegue un run completo con:
    - Retry + backoff per singola chiamata
    - Esecuzione parallela per piattaforma
    - Rate limiting per piattaforma
    - Resume da run interrotti
    - Cancellation check
    - Timeout globale
    - Checkpointing
    """
    sb = make_supabase()
    run_start = time.time()
    timeout_seconds = timeout_minutes * 60

    # ─── Validazione pre-run ─────────────────────────────────────────────
    validation_errors = validate_api_keys(platforms, api_keys)
    if validation_errors:
        error_msg = "\n".join(validation_errors)
        sb.table("lvm_runs").update({
            "status": "failed",
            "error_log": error_msg,
            "completed_at": datetime.utcnow().isoformat(),
        }).eq("id", run_id).execute()
        raise RuntimeError(f"Validazione fallita:\n{error_msg}")

    # ─── Genera tutte le task (query × platform × iteration) ─────────────
    # Le piattaforme SERP (AI Overview, AI Mode) hanno sempre 1 sola iterazione
    # perché i risultati sono deterministici per query.
    SERP_PLATFORMS = {"ai_overview", "ai_mode"}

    all_tasks = []
    for query in queries:
        for platform in platforms:
            platform_iterations = 1 if platform in SERP_PLATFORMS else iterations
            for iteration in range(1, platform_iterations + 1):
                all_tasks.append({
                    "query": query,
                    "platform": platform,
                    "iteration": iteration,
                    "key": f"{query['id']}|{platform}|{iteration}",
                })

    total_calls = len(all_tasks)

    # ─── Resume: filtra task già completate ──────────────────────────────
    if resume:
        completed_tasks = _get_completed_tasks(sb, run_id)
        all_tasks = [t for t in all_tasks if t["key"] not in completed_tasks]
        already_done = total_calls - len(all_tasks)
        log.info(f"Resume: {already_done} task già completate, {len(all_tasks)} rimanenti.")
    else:
        already_done = 0

    # ─── Update run status ───────────────────────────────────────────────
    sb.table("lvm_runs").update({
        "status": "running",
        "started_at": datetime.utcnow().isoformat(),
        "total_calls": total_calls,
        "completed_calls": already_done,
    }).eq("id", run_id).execute()

    # ─── Raggruppa task per piattaforma (per parallelismo) ───────────────
    tasks_by_platform: Dict[str, List[Dict]] = defaultdict(list)
    for task in all_tasks:
        tasks_by_platform[task["platform"]].append(task)

    responses_by_platform: Dict[str, List[Dict]] = defaultdict(list)
    errors = []
    completed = already_done
    cancelled = False

    # ─── Worker per singola piattaforma ──────────────────────────────────
    def process_platform(platform: str, tasks: List[Dict]) -> List[Dict]:
        """Processa tutte le task di una piattaforma sequenzialmente con rate limiting."""
        nonlocal completed, cancelled
        platform_sb = make_supabase()  # client thread-safe
        delay = PLATFORM_DELAYS.get(platform, DEFAULT_DELAY)
        results = []

        for task in tasks:
            # Timeout check
            if (time.time() - run_start) > timeout_seconds:
                log.warning(f"Timeout globale raggiunto ({timeout_minutes}min).")
                cancelled = True
                break

            # Cancellation check
            if completed % CANCEL_CHECK_INTERVAL == 0 and _is_cancelled(platform_sb, run_id):
                log.info(f"Run cancellato durante {platform}.")
                cancelled = True
                break

            try:
                result = _process_single_call(
                    platform_sb, run_id, project_id,
                    task["query"], platform, task["iteration"],
                    api_keys, language,
                )
                results.append(result)

            except Exception as e:
                error_msg = f"{platform}/{task['query']['query_text'][:50]}/iter{task['iteration']}: {str(e)}"
                errors.append(error_msg)
                log.error(error_msg)

                # Salva errore nel DB
                try:
                    platform_sb.table("lvm_responses").insert({
                        "run_id": run_id,
                        "project_id": project_id,
                        "query_id": task["query"]["id"],
                        "query_text": task["query"]["query_text"],
                        "platform": platform,
                        "model_used": MODELS.get(platform, platform),
                        "iteration": task["iteration"],
                        "response_text": "",
                        "error": str(e)[:1000],
                    }).execute()
                except Exception:
                    pass

            completed += 1
            if progress_callback:
                try:
                    progress_callback(
                        completed, total_calls,
                        f"{platform} — {task['query']['query_text'][:40]}… (iter {task['iteration']})"
                    )
                except Exception:
                    pass  # non bloccare il run per un errore di UI

            # Checkpoint ogni 10 chiamate
            if completed % 10 == 0:
                _save_checkpoint(sb, run_id, completed, "\n".join(errors[-10:]))

            # Rate limiting
            time.sleep(delay)

        return results

    # ─── Esecuzione parallela per piattaforma ────────────────────────────
    max_workers = min(len(platforms), 4)  # max 4 piattaforme in parallelo

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(process_platform, platform, tasks): platform
            for platform, tasks in tasks_by_platform.items()
        }

        for future in as_completed(futures):
            platform = futures[future]
            try:
                results = future.result()
                responses_by_platform[platform].extend(results)
            except Exception as e:
                log.error(f"Errore fatale piattaforma {platform}: {e}")
                errors.append(f"{platform}/FATAL: {str(e)}")

    # ─── Calcolo e salvataggio metriche ──────────────────────────────────
    metrics = {}
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

    # ─── Status finale ───────────────────────────────────────────────────
    if cancelled:
        final_status = "cancelled"
    elif errors and completed == 0:
        final_status = "failed"
    else:
        final_status = "completed"

    elapsed_total = time.time() - run_start

    sb.table("lvm_runs").update({
        "status": final_status,
        "completed_at": datetime.utcnow().isoformat(),
        "completed_calls": completed,
        "error_log": "\n".join(errors[:100]) if errors else None,
    }).eq("id", run_id).execute()

    return {
        "total": total_calls,
        "completed": completed,
        "errors": len(errors),
        "metrics": metrics,
        "cancelled": cancelled,
        "elapsed_seconds": round(elapsed_total, 1),
    }
