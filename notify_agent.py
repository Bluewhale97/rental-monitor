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
    f"official 2 bedroom apartments near {SEARCH_LOCATION} 08807 in-unit laundry under ${MAX_PRICE} managed community",
    f"official 2 bedroom townhomes near {SEARCH_LOCATION} 08807 under ${MAX_PRICE} managed leasing office",
    f"Bridgewater NJ 08807 2 bedroom apartment official website in-unit laundry",
    f"Somerset County NJ 2 bedroom managed apartment official leasing office under ${MAX_PRICE}",
    f"2 bedroom apartment communities near {SEARCH_LOCATION} 08807 official website",
    f"2 bedroom rental communities Bridgewater NJ official leasing office under ${MAX_PRICE}",
]
GENERAL_MATCH_QUERIES = [
    f"{SEARCH_LOCATION} 08807 2 bedroom apartment official website in-unit laundry",
    f"managed 2 bedroom townhomes {SEARCH_LOCATION} 08807 official property website",
    f"Bridgewater NJ 2 bedroom apartment community with in-unit laundry official leasing office",
    f"Somerset County NJ apartment official website 2 bed in-unit laundry under ${MAX_PRICE}",
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


def extract_property_name_from_result(item):
    title = (item.get("title") or item.get("property") or "").strip()
    if title:
        return re.sub(r"\s*\|\s*.*$", "", title).strip()
    return ""


def ai_search_property_links(property_name):
    if not property_name or not OPENAI_API_KEY:
        return []
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
                    f"Find the official website and the best matching rental listing for '{property_name}' in Bridgewater, NJ or nearby Somerset County. "
                    "Return JSON only with a list of objects using 'name', 'link', 'source', 'reason'."
                ),
                "tools": [{"type": "web_search_preview"}],
            },
            timeout=60,
        )
        response.raise_for_status()
        content = response.json().get("output_text") or "[]"
        parsed = json.loads(content)
        if isinstance(parsed, list):
            return [entry.get("link") for entry in parsed if isinstance(entry, dict) and entry.get("link")]
    except Exception as exc:
        print(f"AI follow-up search for property '{property_name}' failed: {type(exc).__name__}: {exc!r}")
    return []


def ai_extract_from_page_text(property_name, url, page_text):
    if not page_text or not OPENAI_API_KEY:
        return {}
    try:
        prompt = (
            "You are reading one exact property website. Extract the rental listing information only from this page. "
            "Do not use the title or the search snippet as a substitute for page evidence. "
            "If a field is clearly visible on the page, use the exact value; if it is not visible, use 'Not listed'. "
            "Do not invent details or mark a visible property name as missing just because a field is not on the page. "
            "Return valid JSON only with keys: property, address, sq_ft, type, price, amenities, commute, contact, availability, action, link. "
            "property should be the actual community name if the page shows it. "
            "address should be the full street address if present, else 'Not listed'. "
            "sq_ft should be the number + square feet wording if present, else 'Not listed'. "
            "type should be a string like 'Apartment - 2b2b' or 'Townhouse - 2b2.5b' when visible. "
            "price should be the rent shown on the page, else 'Price not listed'. "
            "amenities should be exactly: 'In-unit laundry: Yes/No/Not listed; Garbage disposal: Yes/No/Not listed; Gym: Yes/No/Not listed; Pool: Yes/No/Not listed; Move-in special: Yes/No/Not listed; Mandatory fees: Yes/No/Not listed'. "
            "commute must be 'X min drive to Legend Biotech (08807)' only when the page clearly states it; otherwise 'Not listed'. "
            "contact must include a leasing office phone and/or email like 'Phone: ...; Email: ...', otherwise 'Not listed'. "
            f"availability must be 'Yes - {MOVE_IN_DATE}' / 'No - {MOVE_IN_DATE}' / 'Not confirmed - {MOVE_IN_DATE}'. "
            "action must be 'appointment'.\n\nPROPERTY NAME: "
            + property_name + "\nPAGE URL: " + url + "\nPAGE CONTENT:\n" + page_text[:20000]
        )
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": "Return valid JSON only. No markdown."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
            },
            timeout=45,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            parsed["link"] = url
            parsed["id"] = build_listing_id(url)
            parsed["property"] = parsed.get("property") or property_name or "Property name not listed"
            parsed["address"] = parsed.get("address") or "Not listed"
            parsed["sq_ft"] = parsed.get("sq_ft") or "Not listed"
            parsed["type"] = parsed.get("type") or "Apartment - 2b2b"
            parsed["price"] = parsed.get("price") or "Price not listed"
            parsed["amenities"] = parsed.get("amenities") or "In-unit laundry: Not listed; Garbage disposal: Not listed; Gym: Not listed; Pool: Not listed; Move-in special: Not listed; Mandatory fees: Not listed"
            parsed["commute"] = parsed.get("commute") or "Not listed"
            parsed["contact"] = parsed.get("contact") or "Not listed"
            parsed["availability"] = parsed.get("availability") or f"Not confirmed - {MOVE_IN_DATE}"
            parsed["action"] = parsed.get("action") or "appointment"
            return parsed
    except Exception as exc:
        print(f"OpenAI page extraction failed for {property_name}: {type(exc).__name__}: {exc!r}")
    return {}


def fetch_property_page_details(property_name, url):
    if not url:
        return {}
    try:
        response = requests.get(url, headers=HEADERS, timeout=25)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        text = " ".join(soup.stripped_strings)
        if not text:
            return {}

        ai_parsed = ai_extract_from_page_text(property_name, url, text)
        if ai_parsed:
            return ai_parsed

        match = {
            "property": property_name,
            "address": re.search(r"(?:\d+\s+[A-Za-z0-9.]+(?:\s+[A-Za-z0-9.]+)*,?\s*(?:Bridgewater|Somerset|Bound Brook|Somerville|Franklin|Hillsborough)[^\n]{0,120})", text, re.IGNORECASE),
            "price": re.search(r"\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{2})?", text),
            "sq_ft": re.search(r"(?:\d[\d,]*)\s*(?:sq\.?\s*ft\.?|square\s+feet)", text, re.IGNORECASE),
            "type": re.search(r"(?:Apartment|Townhouse|Condo|Unit)[^\n]{0,30}(?:\d\s*b\s*\d|\d\.\d\s*b)", text, re.IGNORECASE),
            "phone": extract_phone(text),
            "commute": extract_drive_time(text),
        }

        parsed = {
            "id": build_listing_id(url),
            "property": property_name,
            "address": match["address"].group(0).strip() if match["address"] else "Not listed",
            "sq_ft": match["sq_ft"].group(0).strip() if match["sq_ft"] else "Not listed",
            "type": match["type"].group(0).strip() if match["type"] else "Apartment - 2b2b",
            "price": match["price"].group(0).strip() if match["price"] else "Price not listed",
            "amenities": "In-unit laundry: Not listed; Garbage disposal: Not listed; Gym: Not listed; Pool: Not listed; Move-in special: Not listed; Mandatory fees: Not listed",
            "commute": match["commute"] or "Not listed",
            "contact": f"Phone: {match['phone']}" if match["phone"] else "Not listed",
            "availability": f"Not confirmed - {MOVE_IN_DATE}",
            "action": "appointment",
            "link": url,
        }
        if "laundry" in text.lower():
            parsed["amenities"] = parsed["amenities"].replace("In-unit laundry: Not listed", "In-unit laundry: Yes")
        if "garbage disposal" in text.lower() or "disposal" in text.lower():
            parsed["amenities"] = parsed["amenities"].replace("Garbage disposal: Not listed", "Garbage disposal: Yes")
        if "in-unit washer" in text.lower() or "washer and dryer" in text.lower() or "washer/dryer" in text.lower():
            parsed["amenities"] = parsed["amenities"].replace("In-unit laundry: Not listed", "In-unit laundry: Yes")
        if "gym" in text.lower() or "fitness center" in text.lower():
            parsed["amenities"] = parsed["amenities"].replace("Gym: Not listed", "Gym: Yes")
        return parsed
    except Exception as exc:
        print(f"Direct property fetch failed for {property_name} at {url}: {type(exc).__name__}: {exc!r}")
        return {}


def ai_enrich_listings(search_results):
    if not search_results:
        return []

    seen = set()
    cleaned = []
    tried_names = set()

    for result in search_results:
        property_name = extract_property_name_from_result(result)
        if not property_name or property_name.lower() in tried_names:
            continue
        tried_names.add(property_name.lower())

        candidate_urls = []
        title_url = result.get("link")
        if title_url:
            candidate_urls.append(title_url)

        if TAVILY_API_KEY:
            try:
                tavily_response = requests.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": TAVILY_API_KEY,
                        "query": f'"{property_name}" Bridgewater NJ 08807 official website 2 bedroom apartment in-unit laundry',
                        "max_results": 5,
                        "search_depth": "basic",
                        "include_answer": True,
                        "include_raw_content": True,
                    },
                    timeout=45,
                )
                tavily_response.raise_for_status()
                for item in tavily_response.json().get("results", []):
                    url = item.get("url")
                    if url and url not in candidate_urls:
                        candidate_urls.append(url)
            except Exception as exc:
                print(f"Tavily follow-up search failed for {property_name}: {type(exc).__name__}: {exc!r}")

        if OPENAI_API_KEY:
            for url in ai_search_property_links(property_name):
                if url not in candidate_urls:
                    candidate_urls.append(url)

        for candidate_url in candidate_urls:
            detail = fetch_property_page_details(property_name, candidate_url)
            if not detail:
                continue
            if detail["id"] in seen:
                continue
            if detail["address"] == "Not listed" and detail["price"] == "Price not listed":
                continue
            seen.add(detail["id"])
            cleaned.append(detail)
            break

    print(f"Property-name-first verification kept {len(cleaned)} records from official property pages or follow-up venue searches.")
    return cleaned


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
            queries = SEARCH_QUERIES + GENERAL_MATCH_QUERIES
            for search_query in queries:
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
                    url = item.get("url")
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        results.append(item)
            print(f"Tavily returned {len(results)} unique live search results across {len(queries)} searches.")
            candidates = []
            for item in results:
                source_content = item.get("raw_content") or item.get("content", "")
                content = f"{item.get('title', '')} {source_content}"
                url = item.get("url")
                if not url:
                    continue
                candidates.append({
                    "id": build_listing_id(url),
                    "title": item.get("title", ""),
                    "price": extract_price(content),
                    "content": source_content[:3000],
                    "phone_evidence": extract_phone(content),
                    "drive_evidence": extract_drive_time(content),
                    "link": url,
                })
            print(f"Sending up to 25 general-match candidates to OpenAI for official property verification.")
            enriched = ai_enrich_listings(candidates[:25])
            print(f"AI accepted {len(enriched)} verified property records from Tavily general-match results.")
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
