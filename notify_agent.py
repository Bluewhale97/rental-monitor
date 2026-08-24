import csv
import json
import os
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = os.getenv("RENT_SENDER_EMAIL", "stanley.z4r@gmail.com")
SENDER_PASSWORD = os.getenv("RENT_SENDER_PASSWORD", "").replace(" ", "")
RECEIVER_EMAILS = [
    email.strip()
    for email in os.getenv("RENT_RECEIVER_EMAILS", "stanley.z4r@gmail.com").split(",")
    if email.strip()
]

HISTORY_FILE = "sent_listings.json"
CSV_DATABASE = "rental_database.csv"
LIVE_LISTINGS_FILE = os.getenv("LIVE_LISTINGS_FILE", "live_listings.json")
SEARCH_LOCATION = os.getenv("SEARCH_LOCATION", "Bridgewater, NJ")
SEARCH_AREAS = os.getenv("SEARCH_AREAS", f"{SEARCH_LOCATION}; Somerset County, NJ")
MIN_BEDS = os.getenv("MIN_BEDS", "2")
MAX_PRICE = os.getenv("MAX_PRICE", "3200")
COMMUTE_LIMIT = int(os.getenv("COMMUTE_LIMIT", "30"))
MOVE_IN_DATE = os.getenv("MOVE_IN_DATE", "2026-10-31")
SEARCH_QUERY = os.getenv(
    "SEARCH_QUERY",
    f"current 2 bedroom apartment or townhouse rentals near {SEARCH_LOCATION} 08807 under ${MAX_PRICE} "
    "official property leasing office",
)
SEARCH_QUERIES = [
    SEARCH_QUERY,
    f"2 bedroom professionally managed apartments near {SEARCH_AREAS} under ${MAX_PRICE} leasing office",
    f"2 bedroom managed townhomes for rent near {SEARCH_AREAS} under ${MAX_PRICE} property website",
    f"new 2 bedroom rental communities near {SEARCH_AREAS} official leasing availability",
    f"2 bedroom apartments for rent Bridgewater Somerville Bound Brook South Bound Brook NJ under ${MAX_PRICE}",
    f"2 bedroom townhomes for rent Branchburg Hillsborough Franklin Township NJ under ${MAX_PRICE} leasing office",
    f"2 bedroom apartment community website Somerset County NJ in-unit laundry leasing office",
    f"2 bedroom rental community October 31 2026 Somerset County NJ official website",
]
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "tavily").lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
AI_LIVE_SEARCH_ENABLED = os.getenv("AI_LIVE_SEARCH_ENABLED", "true").lower() == "true"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
    "Connection": "keep-alive",
}


def load_sent_listings():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return []
    return []


def save_sent_listings(sent_list):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(sent_list, f, indent=4)


def build_search_urls():
    location_slug = SEARCH_LOCATION.lower().replace(" ", "-").replace(",", "")
    return [
        f"https://www.apartments.com/{location_slug}/{MIN_BEDS}-bedrooms/?price=0-{MAX_PRICE}",
        f"https://www.apartments.com/{location_slug}/{MIN_BEDS}-bedrooms/?price=0-{MAX_PRICE}&sort=price-asc",
    ]


def build_listing_id(url: str) -> str:
    match = re.search(r"https?://www\.apartments\.com/([^/]+)/?", url)
    if match:
        return match.group(1)
    slug = re.sub(r"[^a-z0-9]+", "_", url.lower()).strip("_")
    return slug or f"listing_{datetime.now().strftime('%Y%m%d%H%M%S')}"


def extract_price(text):
    matches = re.findall(
        r"\$\s?[0-9][0-9,]*(?:\s?[-+]\s?\$?[0-9][0-9,]*)?",
        text or "",
    )
    return matches[0].strip() if matches else ""


def extract_phone(text):
    match = re.search(r"(?:\+?1[\s.-]?)?\(?[2-9][0-9]{2}\)?[\s.-][0-9]{3}[\s.-][0-9]{4}", text or "")
    return match.group(0).strip() if match else ""


def extract_email(text):
    match = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text or "")
    return match.group(0).strip() if match else ""


def extract_sq_ft(text):
    match = re.search(r"(?:[0-9][0-9,]*)\s*(?:sq\.?\s*ft\.?|square\s+feet)", text or "", re.IGNORECASE)
    return match.group(0).replace(" ", " ").strip() if match else ""


def extract_drive_time(text):
    match = re.search(r"(?:approximately\s+|about\s+)?(\d{1,2})(?:\s?[-\u2013]\s?(\d{1,2}))?\s+minutes?\s+(?:drive|driving)", text or "", re.IGNORECASE)
    if not match:
        return ""
    if match.group(2):
        return f"{match.group(1)}-{match.group(2)} min drive to 08807"
    return f"{match.group(1)} min drive to 08807"


def price_is_within_budget(price):
    amounts = [int(value.replace(",", "")) for value in re.findall(r"\$\s?([0-9][0-9,]*)", str(price))]
    return bool(amounts) and max(amounts) <= int(MAX_PRICE)


def price_is_supported(price, evidence):
    reported_amounts = {
        value.replace(",", "")
        for value in re.findall(r"\$\s?([0-9][0-9,]*)", str(price))
    }
    evidence_amounts = {
        value.replace(",", "")
        for value in re.findall(r"\$\s?([0-9][0-9,]*)", str(evidence))
    }
    return bool(reported_amounts) and reported_amounts.issubset(evidence_amounts)


def commute_is_within_limit(commute):
    match = re.search(r"(\d{1,2})(?:\s?[\-\u2013]\s?(\d{1,2}))?\s*[-]?\s*(?:min(?:ute)?s?|hour(?:s)?)", str(commute), re.IGNORECASE)
    if not match:
        return False
    if match.group(0).lower().find("hour") >= 0:
        return False
    return int(match.group(2) or match.group(1)) <= COMMUTE_LIMIT


def canonical_url(url):
    parsed = urlparse(str(url).strip())
    return parsed._replace(path=parsed.path.rstrip("/")).geturl()


def has_required_amenities(amenities):
    text = str(amenities).lower()
    return "in-unit laundry: yes" in text


def format_amenities(value):
    labels = [
        "In-unit laundry",
        "Garbage disposal",
        "Gym",
        "Pool",
        "Move-in special",
        "Mandatory fees",
    ]
    if isinstance(value, dict):
        normalized = {str(key).lower().replace("_", " "): str(item).lower() for key, item in value.items()}
        aliases = {
            "in-unit laundry": ("in-unit laundry", "laundry", "washer dryer"),
            "garbage disposal": ("garbage disposal", "disposal"),
            "gym": ("gym", "fitness center", "fitness"),
            "pool": ("pool", "swimming pool"),
            "move-in special": ("move-in special", "move in special", "move-in specials"),
            "mandatory fees": ("mandatory fees", "fees", "monthly fees"),
        }
        return "; ".join(
            f"{label}: {next((normalized[key].title() for key in aliases[label.lower()] if key in normalized), 'Not listed')}"
            for label in labels
        )
    return str(value or "Not listed")


def normalize_contact(value):
    if isinstance(value, dict):
        phone = value.get("phone")
        url = value.get("url")
        return " | ".join(part for part in (phone, url) if part) or "Not listed"
    return str(value or "Not listed")


PORTAL_DOMAINS = {
    "apartments.com",
    "zillow.com",
    "realtor.com",
    "redfin.com",
    "trulia.com",
    "rent.com",
    "forrent.com",
    "homes.com",
    "apartmenthomeliving.com",
    "roomster.com",
    "sulekha.com",
}


def is_portal_url(url):
    hostname = urlparse(url).netloc.lower().removeprefix("www.")
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in PORTAL_DOMAINS)


def find_official_property_site(property_hint, address):
    if not TAVILY_API_KEY:
        return []
    try:
        response = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": f'"{property_hint}" "{address}" official property website leasing office phone email rental availability commute to Legend Biotech 08807',
                "max_results": 5,
                "search_depth": "advanced",
                "include_raw_content": True,
            },
            timeout=45,
        )
        response.raise_for_status()
        return [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": (item.get("raw_content") or item.get("content", ""))[:3000],
            }
            for item in response.json().get("results", [])
            if item.get("url") and not is_portal_url(item["url"])
        ]
    except Exception as exc:
        print(f"Official property-site lookup failed: {type(exc).__name__}: {exc!r}")
        return []


def find_commute_evidence(address):
    if not TAVILY_API_KEY or not address:
        return ""
    try:
        response = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": f'"{address}" to Legend Biotech 08807 driving time minutes',
                "max_results": 3,
                "search_depth": "advanced",
            },
            timeout=45,
        )
        response.raise_for_status()
        return " ".join(
            f"{item.get('title', '')} {item.get('content', '')}"
            for item in response.json().get("results", [])
        )[:3000]
    except Exception as exc:
        print(f"Commute lookup failed: {type(exc).__name__}: {exc!r}")
        return ""
    try:
        response = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": f'"{property_hint}" "{address}" official property website leasing office phone email rental availability commute to Legend Biotech 08807',
                "max_results": 5,
                "search_depth": "advanced",
                "include_raw_content": True,
            },
            timeout=45,
        )
        response.raise_for_status()
        return [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": (item.get("raw_content") or item.get("content", ""))[:3000],
            }
            for item in response.json().get("results", [])
            if item.get("url") and not is_portal_url(item["url"])
        ]
    except Exception as exc:
        print(f"Official property-site lookup failed: {type(exc).__name__}: {exc!r}")
        return []


def ai_enrich_listings(search_results):
    if not OPENAI_API_KEY or not search_results:
        print("Deep property extraction requires the OPENAI_API_KEY secret.")
        return []

    if not AI_LIVE_SEARCH_ENABLED:
        return []

    try:
        prompt = (
            "You are a meticulous rental research assistant. Extract only real, current, professionally managed "
            f"2-bedroom apartments or townhouses within a {COMMUTE_LIMIT}-minute drive of Legend Biotech, 08807, under ${MAX_PRICE}. "
            "Two bathrooms are preferred but not mandatory. A leasing office or property-management contact is mandatory; "
            "exclude private landlords, room rentals, generic search pages, unrelated articles, and properties without a "
            "verifiable full street address or current price. Do not infer or invent facts. Use only evidence in each result. "
            "In-unit laundry is mandatory and must be confirmed Yes; garbage disposal is preferred but optional, so report Yes, No, or Not listed. "
            f"The requested move-in date is {MOVE_IN_DATE}. Return a JSON array with exactly these fields: id, property, address, sq_ft, type, price, amenities, commute, contact, availability, action, link. "
            "property must be the actual community/property name, link must exactly match one supplied official-site URL, commute must say "
            "'<number> min drive to Legend Biotech (08807)' or be omitted if not supported, and contact must include a leasing "
            "office phone and/or email from the official site, formatted as 'Phone: ...; Email: ...', and the link must not be a listing portal. Use 'Not listed' for a contact method absent from evidence. "
            "sq_ft must contain the published area in square feet, or 'Not listed' when absent. type must contain the actual bedroom and bathroom count such as 'Apartment - 2b2b' or 'Townhouse - 2b2.5b'; "
            f"availability must be 'Yes - {MOVE_IN_DATE}' or 'No - {MOVE_IN_DATE}' when the source confirms that date; otherwise use 'Not confirmed - {MOVE_IN_DATE}' so the property can still be monitored. "
            "Set action exactly to 'appointment'. Format amenities as one plain string with these exact labels: "
            "In-unit laundry: Yes/No/Not listed; Garbage disposal: Yes/No/Not listed; Gym: Yes/No/Not listed; "
            "Pool: Yes/No/Not listed; Move-in special: Yes/No/Not listed; Mandatory fees: Yes/No/Not listed. "
            "Use Not listed when the source does not say; never guess.\n\nSEARCH RESULTS:\n"
            + json.dumps(search_results[:15], indent=2)
        )
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": "Return valid JSON only. Never use markdown fences."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=45,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        print(f"OpenAI returned {len(content)} characters for property extraction.")
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
        parsed = json.loads(content)
        if isinstance(parsed, list):
            allowed_links = {
                canonical_url(source.get("url")): source.get("url")
                for item in search_results
                for source in item.get("official_sources", [])
            }
            cleaned = []
            rejection_counts = {}
            for item in parsed:
                if not isinstance(item, dict) or canonical_url(item.get("link")) not in allowed_links:
                    rejection_counts["invalid or non-official link"] = rejection_counts.get("invalid or non-official link", 0) + 1
                    continue
                if not all(item.get(field) for field in ("property", "address", "type", "price", "commute", "contact", "availability")):
                    rejection_counts["missing required field"] = rejection_counts.get("missing required field", 0) + 1
                    continue
                area_match = re.search(r"[0-9][0-9,]*", str(item["sq_ft"]))
                official_evidence = " ".join(
                    source.get("content", "")
                    for result in search_results
                    for source in result.get("official_sources", [])
                    if canonical_url(source.get("url")) == canonical_url(item.get("link"))
                )
                candidate_evidence = " ".join(
                    f"{result.get('content', '')} {result.get('commute_evidence', '')}"
                    for result in search_results
                    if any(canonical_url(source.get("url")) == canonical_url(item.get("link")) for source in result.get("official_sources", []))
                )
                evidence = f"{candidate_evidence} {official_evidence}"
                if area_match and area_match.group(0).replace(",", "") not in official_evidence.replace(",", ""):
                    rejection_counts["square footage not supported"] = rejection_counts.get("square footage not supported", 0) + 1
                    continue
                if not re.match(rf"^(Yes|No|Not confirmed) - {re.escape(MOVE_IN_DATE)}$", str(item["availability"])):
                    rejection_counts["move-in date not supported"] = rejection_counts.get("move-in date not supported", 0) + 1
                    continue
                if not re.search(r"\b2b\d+(?:\.\d+)?b\b", str(item["type"]).lower()):
                    rejection_counts["bedroom or bathroom type invalid"] = rejection_counts.get("bedroom or bathroom type invalid", 0) + 1
                    continue
                if not price_is_within_budget(item["price"]):
                    rejection_counts["over budget"] = rejection_counts.get("over budget", 0) + 1
                    continue
                if not price_is_supported(item["price"], evidence):
                    rejection_counts["price not supported"] = rejection_counts.get("price not supported", 0) + 1
                    continue
                if not commute_is_within_limit(item["commute"]):
                    rejection_counts["commute invalid or over limit"] = rejection_counts.get("commute invalid or over limit", 0) + 1
                    continue
                contact = normalize_contact(item.get("contact"))
                if "Phone:" not in contact and "Email:" not in contact:
                    rejection_counts["phone or email missing"] = rejection_counts.get("phone or email missing", 0) + 1
                    continue
                phone = extract_phone(contact)
                email = extract_email(contact)
                if (phone and phone not in evidence) or (email and email not in evidence):
                    rejection_counts["phone or email not supported"] = rejection_counts.get("phone or email not supported", 0) + 1
                    continue
                item["id"] = build_listing_id(item["link"])
                item["link"] = allowed_links[canonical_url(item["link"])]
                item["action"] = "appointment"
                item["amenities"] = format_amenities(item.get("amenities"))
                item["contact"] = contact
                if not has_required_amenities(item["amenities"]):
                    rejection_counts["in-unit laundry not confirmed"] = rejection_counts.get("in-unit laundry not confirmed", 0) + 1
                    continue
                if is_portal_url(item["link"]):
                    rejection_counts["final link is a portal"] = rejection_counts.get("final link is a portal", 0) + 1
                    continue
                cleaned.append(item)
            print(f"AI accepted {len(cleaned)} verified property records; rejection details: {rejection_counts or 'none'}")
            return cleaned
    except Exception as exc:
        print(f"Deep property extraction failed: {type(exc).__name__}: {exc!r}")
    return []


def call_live_search_provider(query):
    if not AI_LIVE_SEARCH_ENABLED:
        print("Live search is disabled: AI_LIVE_SEARCH_ENABLED is not true.")
        return []

    if SEARCH_PROVIDER == "tavily":
        if not TAVILY_API_KEY:
            print("Tavily live search is not configured. Add the TAVILY_API_KEY environment variable or GitHub secret.")
            return []
        try:
            results = []
            seen_urls = set()
            for search_query in SEARCH_QUERIES:
                response = requests.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": TAVILY_API_KEY,
                        "query": search_query + " Include current rent, full property address, leasing office phone, and driving time to 08807.",
                        "max_results": 20,
                        "search_depth": "advanced",
                        "include_answer": True,
                        "include_raw_content": True,
                    },
                    timeout=45,
                )
                response.raise_for_status()
                for item in response.json().get("results", []):
                    if item.get("url") and item["url"] not in seen_urls:
                        seen_urls.add(item["url"])
                        results.append(item)
            print(f"Tavily returned {len(results)} unique live search results across {len(SEARCH_QUERIES)} searches.")
            candidates = []
            for item in results:
                source_content = item.get("raw_content") or item.get("content", "")
                content = f"{item.get('title', '')} {source_content}"
                if not item.get("url"):
                    continue
                candidates.append({
                    "id": build_listing_id(item["url"]),
                    "title": item.get("title", ""),
                    "price": extract_price(content),
                    "content": source_content[:3000],
                    "phone_evidence": extract_phone(content),
                    "drive_evidence": extract_drive_time(content),
                    "link": item["url"],
                })
            official_candidates = []
            print(f"Tavily produced {len(candidates)} candidates with usable URLs; checking up to 25 official-site matches.")
            for candidate in candidates[:25]:
                official_sources = find_official_property_site(candidate["title"], candidate["content"][:500])
                if official_sources:
                    candidate["official_sources"] = official_sources
                    candidate["commute_evidence"] = find_commute_evidence(
                        f"{candidate['title']} {candidate['content'][:500]}"
                    )
                    official_candidates.append(candidate)
            print(f"Official-site verification found {len(official_candidates)} candidate matches.")
            enriched = ai_enrich_listings(official_candidates)
            print(f"AI accepted {len(enriched)} verified property records from Tavily results.")
            return enriched
        except Exception as exc:
            print(f"Tavily live search failed: {exc}")

    if SEARCH_PROVIDER in {"openai", "ai"} and OPENAI_API_KEY:
        try:
            response = requests.post(
                "https://api.openai.com/v1/responses",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "input": (
                        f"Search the web for the latest {MIN_BEDS}-bedroom apartment listings under ${MAX_PRICE} in {SEARCH_LOCATION}. "
                        "Return JSON only with a list of objects using 'name', 'price', 'link', 'source'."
                    ),
                    "tools": [{"type": "web_search_preview"}],
                },
                timeout=60,
            )
            response.raise_for_status()
            payload = response.json()
            content = payload.get("output_text") or "[]"
            parsed = json.loads(content)
            if isinstance(parsed, list):
                return parsed
        except Exception as exc:
            print(f"OpenAI live search failed: {exc}")

    return []


def fetch_live_listings_from_sources(urls=None):
    source_urls = urls or build_search_urls()
    listings = []
    seen_ids = set()

    for url in source_urls:
        try:
            response = requests.get(url, headers=HEADERS, timeout=20)
            response.raise_for_status()
        except Exception as exc:
            print(f"Failed to fetch {url}: {exc}")
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        cards = soup.select("article") or soup.select("li")
        for card in cards[:25]:
            link_tag = card.select_one("a[href*='apartments.com/']") or card.find("a", href=True)
            if not link_tag:
                continue
            link = link_tag.get("href", "").strip()
            if not link or "apartments.com" not in link:
                continue

            title = " ".join(link_tag.get_text(" ", strip=True).split())
            if not title or title.lower().startswith("favorites"):
                continue

            price_text = ""
            for candidate in card.stripped_strings:
                if "$" in candidate:
                    price_text = candidate.strip()
                    break
            if not price_text:
                match = re.search(r"\$\s?\d[0-9,]*\+?", response.text)
                if match:
                    price_text = match.group(0).strip()

            listing = {
                "id": build_listing_id(link),
                "name": title.split(",")[0][:80] or "Apartment Listing",
                "type": f"{MIN_BEDS} Bed / 2 Bath",
                "price": price_text or "$0+",
                "amenities": "In-unit laundry, Parking, Fitness center",
                "commute": SEARCH_LOCATION,
                "contact": "N/A",
                "link": link if link.startswith("http") else f"https://www.apartments.com{link}",
            }
            if listing["id"] not in seen_ids:
                seen_ids.add(listing["id"])
                listings.append(listing)

    if not listings:
        print("No structured live listing data was extracted from the configured search endpoints.")
        return []

    ranked = listings
    with open(LIVE_LISTINGS_FILE, "w", encoding="utf-8") as output_file:
        json.dump(ranked, output_file, indent=2)
    print(f"Saved {len(ranked)} live listings to {LIVE_LISTINGS_FILE}.")
    return ranked


def load_live_listings():
    # An explicitly supplied JSON payload is useful for testing, but the normal
    # scheduled path must always perform a fresh search.
    raw_json = os.getenv("RENT_LIVE_LISTINGS_JSON")
    if raw_json:
        try:
            return json.loads(raw_json)
        except json.JSONDecodeError:
            print("Warning: RENT_LIVE_LISTINGS_JSON is not valid JSON.")

    provider_results = call_live_search_provider(SEARCH_QUERY)
    if provider_results:
        with open(LIVE_LISTINGS_FILE, "w", encoding="utf-8") as output_file:
            json.dump(provider_results, output_file, indent=2)
        return provider_results

    if SEARCH_PROVIDER in {"tavily", "openai", "ai"}:
        print(f"No results from the configured {SEARCH_PROVIDER} live-search provider.")
        return []

    return fetch_live_listings_from_sources(build_search_urls())


def update_csv_database(new_listings):
    file_exists = os.path.exists(CSV_DATABASE)
    fieldnames = [
        "id", "property", "address", "sq_ft", "type", "price", "amenities",
        "commute", "contact", "availability", "action", "link", "first_seen_date",
    ]
    existing_rows = {}
    if file_exists:
        with open(CSV_DATABASE, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if "name" in row and "property" not in row:
                    row["property"] = row.pop("name")
                if "apt_name" in row and "property" not in row:
                    row["property"] = row.pop("apt_name")
                row.setdefault("address", "")
                row.setdefault("sq_ft", "")
                row.setdefault("availability", "")
                row.setdefault("action", "appointment")
                if not row.get("property") or not row.get("address"):
                    continue
                row["amenities"] = format_amenities(row.get("amenities"))
                row["contact"] = normalize_contact(row.get("contact"))
                existing_rows[row["id"]] = row

    today = datetime.now().strftime("%Y-%m-%d")
    for item in new_listings:
        item_id = item["id"]
        if item_id in existing_rows:
            existing_rows[item_id].update({
                "property": item.get("property", item.get("name", existing_rows[item_id].get("property", ""))),
                "address": item.get("address", existing_rows[item_id].get("address", "")),
                    "sq_ft": item.get("sq_ft", existing_rows[item_id].get("sq_ft", "")),
                "type": item.get("type", existing_rows[item_id].get("type", "")),
                "price": item.get("price", existing_rows[item_id]["price"]),
                "amenities": item.get("amenities", existing_rows[item_id].get("amenities", "")),
                "commute": item.get("commute", existing_rows[item_id].get("commute", "")),
                "contact": item.get("contact", existing_rows[item_id].get("contact", "")),
                    "availability": item.get("availability", existing_rows[item_id].get("availability", "")),
                    "action": "appointment",
                "link": item.get("link", existing_rows[item_id]["link"]),
            })
        else:
            existing_rows[item_id] = {
                "id": item_id,
                "property": item.get("property", item.get("name")),
                "address": item.get("address"),
                "sq_ft": item.get("sq_ft"),
                "type": item.get("type"),
                "price": item.get("price"),
                "amenities": format_amenities(item.get("amenities")),
                "commute": item.get("commute"),
                "contact": normalize_contact(item.get("contact")),
                "availability": item.get("availability"),
                "action": "appointment",
                "link": item.get("link"),
                "first_seen_date": today,
            }

    with open(CSV_DATABASE, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in existing_rows.values():
            writer.writerow(row)


def send_email_notification(subject, html_content, dry_run=False):
    if dry_run:
        print(f"[Dry Run] Email would be sent to: {RECEIVER_EMAILS}")
        return True

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SENDER_EMAIL
        msg["To"] = ", ".join(RECEIVER_EMAILS)
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECEIVER_EMAILS, msg.as_string())
        print("Email sent successfully!")
        return True
    except Exception as exc:
        print(f"Failed to send email ({type(exc).__name__}): {exc!r}")
        return False


def run_scan(dry_run=False):
    listings_input = load_live_listings()
    if not listings_input:
        print("No live search listings found. Skipping CSV update and email send.")
        return

    update_csv_database(listings_input)
    sent_listings = load_sent_listings()
    unsent_listings = [item for item in listings_input if item["id"] not in sent_listings]
    if not unsent_listings:
        print("No new unnotified listings.")
        return

    table_rows = ""
    for item in unsent_listings:
        table_rows += f"""
        <tr style="border-bottom: 1px solid #eaeaea;">
            <td style="padding: 10px; font-weight: bold; color: #333; font-size: 13px;">{item.get('property', item.get('name'))}</td>
            <td style="padding: 10px; color: #555; font-size: 13px;">{item.get('address')}</td>
            <td style="padding: 10px; color: #555; font-size: 13px;">{item.get('sq_ft')}</td>
            <td style="padding: 10px; color: #555; font-size: 13px;">{item.get('type')}</td>
            <td style="padding: 10px; color: #2c7a7b; font-weight: bold; font-size: 13px;">{item.get('price')}</td>
            <td style="padding: 10px; color: #555; font-size: 13px;">{item.get('amenities', 'In-unit laundry, Disposal')}</td>
            <td style="padding: 10px; color: #555; font-size: 13px;">{item.get('commute')}</td>
            <td style="padding: 10px; color: #555; font-size: 13px;">{item.get('contact', 'N/A')}</td>
            <td style="padding: 10px; color: #555; font-size: 13px;">{item.get('availability')}</td>
            <td style="padding: 10px; text-align: right; font-size: 13px;">
                <a href="{item.get('link')}" style="background-color: #3182ce; color: white; padding: 5px 10px; text-decoration: none; border-radius: 4px;">Appointment</a>
            </td>
        </tr>
        """

    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background-color: #f7fafc; margin: 0; padding: 20px;">
        <div style="max-width: 900px; margin: auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.05);">
            <h2 style="color: #2d3748; margin-top: 0; border-bottom: 2px solid #edf2f7; padding-bottom: 10px; font-size: 18px;">
                🎯 [08807 Rental Radar] {len(unsent_listings)} New Match(es) Found
            </h2>
            <table style="width: 100%; border-collapse: collapse; margin-top: 14px; margin-bottom: 15px;">
                <thead>
                    <tr style="background-color: #edf2f7; text-align: left;">
                        <th style="padding: 10px; color: #4a5568; font-size: 12px;">Property</th>
                        <th style="padding: 10px; color: #4a5568; font-size: 12px;">Address</th>
                        <th style="padding: 10px; color: #4a5568; font-size: 12px;">Sq ft</th>
                        <th style="padding: 10px; color: #4a5568; font-size: 12px;">Type</th>
                        <th style="padding: 10px; color: #4a5568; font-size: 12px;">Price</th>
                        <th style="padding: 10px; color: #4a5568; font-size: 12px;">Amenities</th>
                        <th style="padding: 10px; color: #4a5568; font-size: 12px;">Commute</th>
                        <th style="padding: 10px; color: #4a5568; font-size: 12px;">Contact</th>
                        <th style="padding: 10px; color: #4a5568; font-size: 12px;">Availability</th>
                        <th style="padding: 10px; color: #4a5568; font-size: 12px; text-align: right;">Action</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>
            <p style="color: #a0aec0; font-size: 11px; text-align: center; margin-bottom: 0;">
                Automated Rental Intelligence System • Target: 08807 (Dec 1 Move-in)
            </p>
        </div>
    </body>
    </html>
    """

    subject = f"🎯 [08807 Rental Radar] {len(unsent_listings)} New Property Match(es) Found!"
    if send_email_notification(subject, html_content, dry_run=dry_run):
        for item in unsent_listings:
            sent_listings.append(item["id"])
        save_sent_listings(sent_listings)


if __name__ == "__main__":
    run_scan(dry_run=False)
