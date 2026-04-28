#!/usr/bin/env python3
import argparse
import datetime as dt
import fnmatch
import glob
import hashlib
import shutil
import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse
from xml.sax.saxutils import escape

import requests

DEFAULT_CONFIG: Dict[str, Any] = {
    "base_url": "https://www.pcfactory.cl",
    "output_dir": "output",
    "sitemap_base_url": "",
    "publish_dir": "",
    "strip_query": True,
    "limits": {
        "max_urls_per_sitemap": 50000,
        "max_bytes_per_sitemap": 52428800,
    },
    "filters": {
        "include": [],
        "exclude": [],
    },
    "change_detection": {
        "enabled": True,
        "state_file": "sitemap_state.json",
        "hash_fields": ["loc"],
    },
    "sources": {
        "products": {
            "enabled": True,
            "feed_url": "https://api.pcfactory.cl/pcfactory-services-catalogo/v1/catalogo/productos/feed",
            "changefreq": "daily",
            "priority": 0.8,
            "lastmod": "today",
        },
        "categories": {
            "enabled": True,
            "endpoint": "https://api.pcfactory.cl/api-dex-catalog/v1/catalog/category/PCF",
            "changefreq": "daily",
            "priority": 0.8,
            "lastmod": "today",
        },
        "modyo": {
            "enabled": True,
            "endpoint": "https://ww3.pcfactory.cl/api/admin/sites/1/layout_pages",
            "cookie_env": "MODYO_COOKIE",
            "per_page": 30,
            "changefreq": "monthly",
            "priority": 0.8,
            "lastmod": "updated_at",
        },
    },
}


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def load_config(path: str) -> Dict[str, Any]:
    cfg = {}
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    merged = deep_merge(DEFAULT_CONFIG, cfg)
    if not merged.get("sitemap_base_url"):
        merged["sitemap_base_url"] = merged.get("base_url", "")
    return merged


def build_session(extra_headers: Optional[Dict[str, str]] = None) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "accept": "application/json",
        "user-agent": "pcfactory-sitemap-generator/1.0",
    })
    if extra_headers:
        session.headers.update(extra_headers)
    return session


def today_date() -> str:
    return dt.date.today().isoformat()


def parse_iso_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        clean = value.replace("Z", "+00:00")
        parsed = dt.datetime.fromisoformat(clean)
        return parsed.date().isoformat()
    except Exception:
        return None


def normalize_url(url: str, base_url: str, strip_query: bool = True) -> str:
    url = (url or "").strip()
    if not url:
        return ""

    parsed = urlparse(url)
    if not parsed.scheme:
        base = base_url.rstrip("/")
        url = f"{base}/{url.lstrip('/')}"
        parsed = urlparse(url)

    scheme = parsed.scheme or "https"
    netloc = parsed.netloc
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query = "" if strip_query else parsed.query
    fragment = ""
    return urlunparse((scheme, netloc, path, "", query, fragment))


def matches_patterns(value: str, patterns: Iterable[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(value, pattern):
            return True
    return False


def apply_filters(urls: List[str], include: List[str], exclude: List[str]) -> List[str]:
    filtered: List[str] = []
    for url in urls:
        parsed = urlparse(url)
        path = parsed.path or "/"
        if include:
            if not (matches_patterns(url, include) or matches_patterns(path, include)):
                continue
        if exclude:
            if matches_patterns(url, exclude) or matches_patterns(path, exclude):
                continue
        filtered.append(url)
    return filtered


def clamp_priority(value: Any) -> str:
    try:
        num = float(value)
    except Exception:
        num = 0.5
    num = max(0.0, min(1.0, num))
    return f"{num:.1f}"


def build_entry(loc: str, lastmod: str, changefreq: str, priority: str) -> Dict[str, str]:
    return {
        "loc": loc,
        "lastmod": lastmod,
        "changefreq": changefreq,
        "priority": priority,
    }


def entry_to_xml(entry: Dict[str, str]) -> str:
    return (
        "  <url>\n"
        f"    <loc>{escape(entry['loc'])}</loc>\n"
        f"    <lastmod>{escape(entry['lastmod'])}</lastmod>\n"
        f"    <changefreq>{escape(entry['changefreq'])}</changefreq>\n"
        f"    <priority>{escape(entry['priority'])}</priority>\n"
        "  </url>\n"
    )


def compute_entries_hash(entries: List[Dict[str, str]], fields: List[str]) -> str:
    if not fields:
        fields = ["loc"]
    rows: List[str] = []
    for entry in entries:
        parts = [entry.get(field, "") for field in fields]
        rows.append("|".join(parts))
    rows.sort()
    digest = hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()
    return digest


def resolve_state_path(state_file: str, output_dir: str) -> str:
    if not state_file:
        state_file = "sitemap_state.json"
    if os.path.isabs(state_file):
        return state_file
    return os.path.join(output_dir, state_file)


def load_state(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_state(path: str, data: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def sitemap_output_exists(output_dir: str) -> bool:
    if not os.path.isdir(output_dir):
        return False
    sitemap_path = os.path.join(output_dir, "sitemap.xml")
    index_path = os.path.join(output_dir, "sitemap_index.xml")
    return os.path.exists(sitemap_path) or os.path.exists(index_path)


def split_entries(entries_xml: List[str], max_urls: int, max_bytes: int) -> List[List[str]]:
    header = "<?xml version='1.0' encoding='utf-8'?>\n<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n"
    footer = "</urlset>\n"
    header_size = len(header.encode("utf-8"))
    footer_size = len(footer.encode("utf-8"))

    parts: List[List[str]] = []
    current: List[str] = []
    current_size = header_size + footer_size

    for entry in entries_xml:
        entry_size = len(entry.encode("utf-8"))
        if current and (len(current) >= max_urls or current_size + entry_size > max_bytes):
            parts.append(current)
            current = []
            current_size = header_size + footer_size
        current.append(entry)
        current_size += entry_size

    if current:
        parts.append(current)
    return parts


def write_sitemap_file(path: str, entries_xml: List[str]) -> None:
    header = "<?xml version='1.0' encoding='utf-8'?>\n<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n"
    footer = "</urlset>\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(header)
        for entry in entries_xml:
            f.write(entry)
        f.write(footer)


def write_sitemap_index(path: str, sitemap_urls: List[str]) -> None:
    today = today_date()
    header = "<?xml version='1.0' encoding='utf-8'?>\n<sitemapindex xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">\n"
    footer = "</sitemapindex>\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(header)
        for url in sitemap_urls:
            f.write("  <sitemap>\n")
            f.write(f"    <loc>{escape(url)}</loc>\n")
            f.write(f"    <lastmod>{today}</lastmod>\n")
            f.write("  </sitemap>\n")
        f.write(footer)


def copy_outputs(output_dir: str, publish_dir: str) -> List[str]:
    if not publish_dir:
        return []
    os.makedirs(publish_dir, exist_ok=True)
    copied: List[str] = []
    pattern = os.path.join(output_dir, "sitemap*.xml")
    for path in glob.glob(pattern):
        filename = os.path.basename(path)
        dest = os.path.join(publish_dir, filename)
        shutil.copy2(path, dest)
        copied.append(dest)
    return copied


def walk_categories(nodes: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    stack = list(nodes or [])
    while stack:
        node = stack.pop()
        link = node.get("link") or node.get("Link")
        if link:
            out.append({
                "id": node.get("id"),
                "name": node.get("nombre") or node.get("Nombre"),
                "link": str(link),
            })
        children = node.get("childCategories") or node.get("childcategories") or []
        if isinstance(children, list) and children:
            stack.extend(children)
    seen: set = set()
    unique: List[Dict[str, Any]] = []
    for item in out:
        if item["link"] not in seen:
            seen.add(item["link"])
            unique.append(item)
    return unique


def fetch_products(cfg: Dict[str, Any], base_url: str, strip_query: bool) -> List[str]:
    url = cfg["feed_url"]
    print(f"[products] fetching feed: {url}")
    session = build_session()
    resp = session.get(url, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    items: List[Dict[str, Any]]
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("items") or data.get("data") or data.get("content") or []
    else:
        items = []

    urls: List[str] = []
    for item in items:
        link = item.get("link") or item.get("url")
        if not link:
            continue
        norm = normalize_url(str(link), base_url, strip_query)
        if norm:
            urls.append(norm)
    print(f"[products] urls: {len(urls)}")
    return urls


def fetch_categories(cfg: Dict[str, Any], base_url: str, strip_query: bool) -> List[str]:
    endpoint = cfg["endpoint"]
    print(f"[categories] fetching endpoint: {endpoint}")
    session = build_session()
    resp = session.get(endpoint, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    nodes = data if isinstance(data, list) else data.get("data", [])
    items = walk_categories(nodes)
    urls: List[str] = []
    for item in items:
        link = item.get("link")
        if not link:
            continue
        norm = normalize_url(str(link), base_url, strip_query)
        if norm:
            urls.append(norm)
    print(f"[categories] urls: {len(urls)}")
    return urls


def build_cookie_value(raw: str) -> str:
    if not raw:
        return ""
    if "_pcfactory_session=" in raw:
        return raw
    return f"_pcfactory_session={raw}"


def flatten_modyo_pages(pages: Iterable[Dict[str, Any]], rows: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    if rows is None:
        rows = []
    for page in pages or []:
        rows.append(page)
        children = page.get("children") or []
        if isinstance(children, list) and children:
            flatten_modyo_pages(children, rows)
    return rows


def fetch_modyo_pages(cfg: Dict[str, Any], base_url: str, strip_query: bool) -> List[Tuple[str, Optional[str]]]:
    endpoint = cfg["endpoint"]
    cookie_env = cfg.get("cookie_env") or "MODYO_COOKIE"
    raw_cookie = os.environ.get(cookie_env, "")
    if not raw_cookie:
        print(f"[modyo] missing cookie in env: {cookie_env} (skipping)")
        return []

    cookie_value = build_cookie_value(raw_cookie)
    headers = {
        "accept": "application/json",
        "cookie": cookie_value,
    }
    session = build_session(headers)
    per_page = int(cfg.get("per_page") or 30)
    params_base = {
        "states[]": ["unpublished", "published", "scheduled"],
        "per_page": per_page,
    }

    urls: List[Tuple[str, Optional[str]]] = []
    page = 1
    total_pages: Optional[int] = None
    while True:
        params = dict(params_base)
        params["page"] = page
        resp = session.get(endpoint, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        meta = data.get("meta", {})
        if total_pages is None:
            total_pages = meta.get("total_pages", 1)
        raw_pages = data.get("layout_pages", [])
        items = flatten_modyo_pages(raw_pages)

        for item in items:
            if not item.get("current_published"):
                continue
            if item.get("private"):
                continue
            raw_url = item.get("current_url") or item.get("full_path")
            if not raw_url:
                continue
            norm = normalize_url(str(raw_url), base_url, strip_query)
            if not norm:
                continue
            urls.append((norm, item.get("updated_at")))

        if total_pages is not None and page >= total_pages:
            break
        page += 1

    print(f"[modyo] urls: {len(urls)}")
    return urls


def build_entries(cfg: Dict[str, Any]) -> Tuple[List[Dict[str, str]], Dict[str, int]]:
    base_url = cfg["base_url"]
    strip_query = bool(cfg.get("strip_query", True))
    counts = {"products": 0, "categories": 0, "modyo": 0}
    entries: List[Dict[str, str]] = []

    sources = cfg.get("sources", {})
    today = today_date()

    if sources.get("products", {}).get("enabled"):
        src = sources["products"]
        urls = fetch_products(src, base_url, strip_query)
        counts["products"] = len(urls)
        for url in urls:
            entries.append(build_entry(
                url,
                today if src.get("lastmod") == "today" else today,
                src.get("changefreq", "daily"),
                clamp_priority(src.get("priority", 0.8)),
            ))

    if sources.get("categories", {}).get("enabled"):
        src = sources["categories"]
        urls = fetch_categories(src, base_url, strip_query)
        counts["categories"] = len(urls)
        for url in urls:
            entries.append(build_entry(
                url,
                today if src.get("lastmod") == "today" else today,
                src.get("changefreq", "daily"),
                clamp_priority(src.get("priority", 0.8)),
            ))

    if sources.get("modyo", {}).get("enabled"):
        src = sources["modyo"]
        urls = fetch_modyo_pages(src, base_url, strip_query)
        counts["modyo"] = len(urls)
        for url, updated_at in urls:
            lastmod = parse_iso_date(updated_at) or today
            entries.append(build_entry(
                url,
                lastmod,
                src.get("changefreq", "monthly"),
                clamp_priority(src.get("priority", 0.8)),
            ))

    return entries, counts


def dedupe_entries(entries: List[Dict[str, str]]) -> Tuple[List[Dict[str, str]], int]:
    seen = set()
    unique: List[Dict[str, str]] = []
    dupes = 0
    for entry in entries:
        loc = entry["loc"]
        if loc in seen:
            dupes += 1
            continue
        seen.add(loc)
        unique.append(entry)
    return unique, dupes


def main() -> int:
    parser = argparse.ArgumentParser(description="PCFactory sitemap generator")
    parser.add_argument("--config", default="config.json", help="Path to config json")
    parser.add_argument("--output-dir", default=None, help="Override output dir")
    parser.add_argument("--dry-run", action="store_true", help="Only fetch and report counts")
    parser.add_argument("--force", action="store_true", help="Force write even if no changes")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.output_dir:
        cfg["output_dir"] = args.output_dir

    entries, counts = build_entries(cfg)
    include = cfg.get("filters", {}).get("include", [])
    exclude = cfg.get("filters", {}).get("exclude", [])

    urls = [e["loc"] for e in entries]
    filtered_urls = apply_filters(urls, include, exclude)
    filtered_set = set(filtered_urls)
    filtered_entries = [e for e in entries if e["loc"] in filtered_set]

    deduped_entries, dupes = dedupe_entries(filtered_entries)
    print(f"[filters] include={len(include)} exclude={len(exclude)}")
    print(f"[dedupe] removed={dupes} kept={len(deduped_entries)}")

    output_dir = cfg["output_dir"]
    change_cfg = cfg.get("change_detection", {})
    if change_cfg.get("enabled", True):
        fields = change_cfg.get("hash_fields") or ["loc"]
        state_path = resolve_state_path(change_cfg.get("state_file", ""), output_dir)
        current_hash = compute_entries_hash(deduped_entries, fields)
        prev_state = load_state(state_path)
        prev_hash = prev_state.get("hash")
        if (not args.force and prev_hash == current_hash and sitemap_output_exists(output_dir)):
            print("[skip] no changes detected")
            return 0

    if args.dry_run:
        print("[dry-run] done")
        return 0

    os.makedirs(output_dir, exist_ok=True)

    entries_xml = [entry_to_xml(e) for e in deduped_entries]
    limits = cfg.get("limits", {})
    max_urls = int(limits.get("max_urls_per_sitemap", 50000))
    max_bytes = int(limits.get("max_bytes_per_sitemap", 52428800))
    parts = split_entries(entries_xml, max_urls, max_bytes)

    sitemap_base_url = cfg.get("sitemap_base_url", "").rstrip("/")
    publish_dir = cfg.get("publish_dir", "")
    sitemap_files: List[str] = []
    sitemap_urls: List[str] = []

    if len(parts) == 1:
        filename = "sitemap.xml"
        path = os.path.join(output_dir, filename)
        write_sitemap_file(path, parts[0])
        sitemap_files.append(path)
        if sitemap_base_url:
            sitemap_urls.append(f"{sitemap_base_url}/{filename}")
    else:
        for idx, part in enumerate(parts, start=1):
            filename = f"sitemap_{idx}.xml"
            path = os.path.join(output_dir, filename)
            write_sitemap_file(path, part)
            sitemap_files.append(path)
            if sitemap_base_url:
                sitemap_urls.append(f"{sitemap_base_url}/{filename}")

        if sitemap_urls:
            index_path = os.path.join(output_dir, "sitemap_index.xml")
            write_sitemap_index(index_path, sitemap_urls)
            sitemap_files.append(index_path)

    copied = copy_outputs(output_dir, publish_dir)

    if change_cfg.get("enabled", True):
        state_path = resolve_state_path(change_cfg.get("state_file", ""), output_dir)
        state = {
            "hash": compute_entries_hash(deduped_entries, change_cfg.get("hash_fields") or ["loc"]),
            "total_urls": len(deduped_entries),
            "counts": counts,
            "generated_at": dt.datetime.utcnow().isoformat() + "Z",
        }
        write_state(state_path, state)

    print("[done]")
    print(f"[counts] products={counts['products']} categories={counts['categories']} modyo={counts['modyo']}")
    print(f"[output] files={len(sitemap_files)}")
    for path in sitemap_files:
        print(f"  - {path}")
    if copied:
        print(f"[publish] copied={len(copied)}")
        for path in copied:
            print(f"  - {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
