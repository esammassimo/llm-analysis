"""
analysis.py — Brand extraction (NER + regex), URL extraction, Jaccard, sentiment
"""
import re
from collections import Counter, defaultdict
from typing import Dict, List, Set, Tuple
from itertools import combinations

# ─── Stopwords IT + EN (compact) ─────────────────────────────────────────────
_SW = {
    "the","a","an","in","on","at","to","for","of","and","or","but","is","are","was","were",
    "be","been","have","has","had","do","does","did","will","would","could","should","may",
    "might","can","not","with","by","from","as","it","its","this","that","these","those",
    "i","we","you","he","she","they","their","our","your","my","also","more","most","best",
    "top","new","good","high","low","first","last","some","any","all","other","well","just",
    "very","much","many","each","both","only","than","then","when","where","which","who",
    "what","how","if","while","about","into","through","before","after","between","same",
    "few","less","here","there","up","down","out","no","yes","per","vs","etc",
    "il","lo","la","i","gli","le","un","una","del","della","dei","delle","degli","al","alla",
    "ai","alle","nel","nella","nei","nelle","sul","sulla","sui","sulle","dal","dalla","dai",
    "dalle","col","con","per","tra","fra","che","chi","cui","non","ma","se","come","quando",
    "dove","però","quindi","così","anche","già","ancora","sempre","mai","molto","poco",
    "tutto","niente","nulla","essere","avere","fare","dire","andare","venire","vedere",
    "sapere","potere","volere","stare","dare","questo","questa","questi","queste",
    "prestito","prestiti","finanziaria","finanziarie","tasso","tassi","interessi","interesse",
    "rata","rate","importo","durata","offerta","offerte","banca","banche","personale",
    "personali","conveniente","velocemente","veloce","rapido","basso","bassi","migliori",
    "migliore","oggi","mercato","prodotto","prodotti","euro","annuo","annuale","mensile",
}


def extract_brands_regex(text: str) -> List[str]:
    """Extract brand-like capitalized phrases from text."""
    pattern = r'\b([A-Z][a-zA-ZÀ-ÖØ-öø-ÿ0-9&\-\'\.]{1,}(?:\s+[A-Z][a-zA-ZÀ-ÖØ-öø-ÿ0-9&\-\'\.]+){0,3})\b'
    raw = re.findall(pattern, text)
    brands = []
    for b in raw:
        b = b.strip().rstrip(".")
        tokens = b.split()
        if len(tokens) > 4:
            continue
        if all(t.lower() in _SW for t in tokens):
            continue
        if len(b) < 3:
            continue
        # filter common false positives
        if b.lower() in {"http", "https", "www", "com", "org", "net", "url", "api"}:
            continue
        brands.append(b)
    return brands


def extract_brands_from_bold(text: str) -> List[str]:
    """Extract brands from markdown bold patterns **Brand** or *Brand*."""
    bold = re.findall(r'\*\*([A-Z][A-Za-zÀ-ÿ\s\'&\-\.]+?)\*\*', text)
    italic = re.findall(r'(?<!\*)\*([A-Z][A-Za-zÀ-ÿ\s\'&\-\.]+?)\*(?!\*)', text)
    return [b.strip() for b in bold + italic if len(b.strip()) >= 3]


def extract_brands(text: str) -> List[str]:
    """Combined brand extraction: bold + regex, deduplicated."""
    bold_brands = extract_brands_from_bold(text)
    regex_brands = extract_brands_regex(text)
    seen = set()
    result = []
    for b in bold_brands + regex_brands:
        key = b.lower().strip()
        if key not in seen:
            seen.add(key)
            result.append(b)
    return result


def extract_urls(text: str) -> List[str]:
    """Extract URLs from text."""
    return re.findall(r'https?://[^\s\)\]\>\"\']+', text)


def normalize_domain(url: str) -> str:
    """Extract domain from URL, removing www."""
    match = re.search(r'https?://(?:www\.)?([^/\s]+)', url)
    return match.group(1).lower() if match else url.lower()


def jaccard(set_a: Set, set_b: Set) -> float:
    """Jaccard similarity coefficient."""
    if not set_a and not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def jaccard_intra_platform(brand_sets: List[Set[str]]) -> float:
    """Average pairwise Jaccard within a platform's iterations."""
    if len(brand_sets) < 2:
        return 1.0
    scores = []
    for a, b in combinations(range(len(brand_sets)), 2):
        scores.append(jaccard(brand_sets[a], brand_sets[b]))
    return sum(scores) / len(scores) if scores else 0.0


def jaccard_cross_platform(platform_brands: Dict[str, Set[str]]) -> Dict[str, float]:
    """Pairwise Jaccard between platforms."""
    platforms = sorted(platform_brands.keys())
    result = {}
    for a, b in combinations(platforms, 2):
        key = f"{a} vs {b}"
        result[key] = jaccard(platform_brands[a], platform_brands[b])
    return result


def compute_run_metrics(responses_by_platform: Dict[str, List[Dict]]) -> Dict:
    """
    Compute all metrics for a run.
    responses_by_platform: {platform: [{response_text, brands, urls, iteration, query_text}, ...]}
    """
    metrics = {}

    for platform, responses in responses_by_platform.items():
        all_brands = set()
        all_urls = set()
        by_query: Dict[str, List[Set[str]]] = defaultdict(list)

        for r in responses:
            brands = set(b.lower() for b in r.get("brands", []))
            all_brands |= brands
            all_urls |= set(r.get("domains", []))
            by_query[r["query_text"]].append(brands)

        # Intra-platform Jaccard (ripetibilità)
        jaccard_scores = []
        for query, brand_sets in by_query.items():
            if len(brand_sets) >= 2:
                jaccard_scores.append(jaccard_intra_platform(brand_sets))

        metrics[platform] = {
            "brand_count": len(all_brands),
            "source_count": len(all_urls),
            "jaccard_intra": sum(jaccard_scores) / len(jaccard_scores) if jaccard_scores else 0,
        }

    # Cross-platform Jaccard
    platform_all_brands = {}
    for platform, responses in responses_by_platform.items():
        brands = set()
        for r in responses:
            brands |= set(b.lower() for b in r.get("brands", []))
        platform_all_brands[platform] = brands

    cross = jaccard_cross_platform(platform_all_brands)
    metrics["_cross_platform"] = cross

    return metrics
