"""
llm_api.py — Chiamate API a LLM (ChatGPT, Claude, Gemini, Perplexity)
                e SERP (AI Overview, AI Mode) via SerpAPI

Tutte le funzioni ricevono un dict `api_keys` con le chiavi,
caricate dai Secrets e passate dall'engine/app.
"""
import time
import re
import requests
import logging
from typing import Dict, Any, Optional, Tuple

log = logging.getLogger(__name__)

# ─── Modelli di default ──────────────────────────────────────────────────────
MODELS = {
    "chatgpt":    "gpt-4o",
    "claude":     "claude-sonnet-4-20250514",
    "gemini":     "gemini-2.0-flash",
    "perplexity": "sonar",
}

SYSTEM_PROMPT_IT = (
    "Sei un esperto del settore. Rispondi in modo dettagliato e completo alla domanda, "
    "menzionando brand, aziende e servizi specifici quando rilevante. "
    "Includi fonti e URL quando possibile."
)

SYSTEM_PROMPT_EN = (
    "You are an industry expert. Answer the question in detail, "
    "mentioning specific brands, companies and services when relevant. "
    "Include sources and URLs when possible."
)


def get_system_prompt(lang: str = "it") -> str:
    return SYSTEM_PROMPT_IT if lang == "it" else SYSTEM_PROMPT_EN


def _require_key(api_keys: Dict[str, str], key_name: str) -> str:
    """Estrae una chiave dal dict, raise se mancante."""
    val = api_keys.get(key_name, "")
    if not val:
        raise RuntimeError(
            f"API key '{key_name}' non configurata. "
            f"Vai nella tab Configurazione → Chiavi API."
        )
    return val


# ─── ChatGPT ─────────────────────────────────────────────────────────────────

def call_chatgpt(prompt: str, api_keys: Dict[str, str],
                 lang: str = "it", model: str | None = None) -> Tuple[str, float]:
    api_key = _require_key(api_keys, "openai")
    model = model or MODELS["chatgpt"]
    t0 = time.time()
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": get_system_prompt(lang)},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 2000,
        },
        timeout=60,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    return text, time.time() - t0


# ─── Claude ──────────────────────────────────────────────────────────────────

def call_claude(prompt: str, api_keys: Dict[str, str],
                lang: str = "it", model: str | None = None) -> Tuple[str, float]:
    api_key = _require_key(api_keys, "anthropic")
    model = model or MODELS["claude"]
    t0 = time.time()
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 2000,
            "system": get_system_prompt(lang),
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    text = resp.json()["content"][0]["text"]
    return text, time.time() - t0


# ─── Gemini ──────────────────────────────────────────────────────────────────

def call_gemini(prompt: str, api_keys: Dict[str, str],
                lang: str = "it", model: str | None = None) -> Tuple[str, float]:
    api_key = _require_key(api_keys, "google")
    model = model or MODELS["gemini"]
    t0 = time.time()
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": f"{get_system_prompt(lang)}\n\n{prompt}"}]}],
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 2000},
        },
        timeout=60,
    )
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    return text, time.time() - t0


# ─── Perplexity ──────────────────────────────────────────────────────────────

def call_perplexity(prompt: str, api_keys: Dict[str, str],
                    lang: str = "it", model: str | None = None) -> Tuple[str, float]:
    api_key = _require_key(api_keys, "pplx")
    model = model or MODELS["perplexity"]
    t0 = time.time()
    resp = requests.post(
        "https://api.perplexity.ai/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": get_system_prompt(lang)},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 2000,
        },
        timeout=60,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    return text, time.time() - t0


# ─── SerpAPI: Google AI Overview ─────────────────────────────────────────────

def call_ai_overview(query: str, api_keys: Dict[str, str],
                     lang: str = "it") -> Tuple[str, float]:
    """Fetches AI Overview via SerpAPI (two-step: google search → ai_overview token → fetch)."""
    api_key = _require_key(api_keys, "serpapi")
    gl = "it" if lang == "it" else "us"
    hl = lang
    t0 = time.time()

    # Step 1: Google search to get ai_overview token
    resp1 = requests.get(
        "https://serpapi.com/search",
        params={"engine": "google", "q": query, "gl": gl, "hl": hl, "api_key": api_key},
        timeout=30,
    )
    resp1.raise_for_status()
    data1 = resp1.json()

    ai_ov = data1.get("ai_overview")
    if not ai_ov:
        return "", time.time() - t0

    # Try to get page_token for detailed fetch
    page_token = ai_ov.get("page_token")
    if page_token:
        resp2 = requests.get(
            "https://serpapi.com/search",
            params={
                "engine": "google_ai_overview",
                "page_token": page_token,
                "api_key": api_key,
            },
            timeout=30,
        )
        resp2.raise_for_status()
        data2 = resp2.json()
        text_blocks = data2.get("text_blocks", [])
    else:
        text_blocks = ai_ov.get("text_blocks", [])

    # Reconstruct text
    parts = []
    for block in text_blocks:
        if isinstance(block, dict):
            snippet = block.get("snippet") or block.get("text") or ""
            parts.append(snippet)
        elif isinstance(block, str):
            parts.append(block)
    text = "\n".join(parts)

    # Append references if any
    refs = (data2 if page_token else ai_ov).get("references", [])
    if refs:
        text += "\n\nFonti:\n"
        for r in refs:
            link = r.get("link", "")
            title = r.get("title", "")
            text += f"- {title}: {link}\n"

    return text, time.time() - t0


# ─── SerpAPI: Google AI Mode ────────────────────────────────────────────────

def call_ai_mode(query: str, api_keys: Dict[str, str],
                 lang: str = "it") -> Tuple[str, float]:
    api_key = _require_key(api_keys, "serpapi")
    gl = "it" if lang == "it" else "us"
    hl = lang
    t0 = time.time()

    resp = requests.get(
        "https://serpapi.com/search",
        params={"engine": "google_ai_mode", "q": query, "gl": gl, "hl": hl, "api_key": api_key},
        timeout=45,
    )
    resp.raise_for_status()
    data = resp.json()

    # Extract text from text_blocks
    parts = []
    for block in data.get("text_blocks", []):
        if isinstance(block, dict):
            snippet = block.get("snippet") or block.get("text") or ""
            parts.append(snippet)
        elif isinstance(block, str):
            parts.append(block)
    text = "\n".join(parts)

    # Reconstruct markdown if available
    if not text and "reconstructed_markdown" in data:
        text = data["reconstructed_markdown"]

    # Append references
    refs = data.get("references", [])
    if refs:
        text += "\n\nFonti:\n"
        for r in refs:
            link = r.get("link", "")
            title = r.get("title", "")
            text += f"- {title}: {link}\n"

    return text, time.time() - t0


# ─── SerpAPI: People Also Ask ────────────────────────────────────────────────

def fetch_paa(keyword: str, api_keys: Dict[str, str],
              lang: str = "it", max_questions: int = 10) -> list[str]:
    """Fetch People Also Ask questions for a keyword via SerpAPI."""
    api_key = _require_key(api_keys, "serpapi")
    gl = "it" if lang == "it" else "us"
    hl = lang

    resp = requests.get(
        "https://serpapi.com/search",
        params={"engine": "google", "q": keyword, "gl": gl, "hl": hl, "api_key": api_key},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    questions = []
    for rq in data.get("related_questions", [])[:max_questions]:
        q = rq.get("question", "")
        if q:
            questions.append(q)
    return questions


# ─── Dispatcher ──────────────────────────────────────────────────────────────

CALLERS = {
    "chatgpt":     call_chatgpt,
    "claude":      call_claude,
    "gemini":      call_gemini,
    "perplexity":  call_perplexity,
    "ai_overview": call_ai_overview,
    "ai_mode":     call_ai_mode,
}


def call_platform(platform: str, query: str, api_keys: Dict[str, str],
                  lang: str = "it", model: str | None = None) -> Tuple[str, float]:
    """Dispatch to the right API caller."""
    fn = CALLERS.get(platform)
    if not fn:
        raise ValueError(f"Piattaforma sconosciuta: {platform}")
    if platform in ("ai_overview", "ai_mode"):
        return fn(query, api_keys, lang)
    return fn(query, api_keys, lang, model)
