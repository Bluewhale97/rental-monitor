title = "auto-hunder for rent"

CONFIG = {
    "max_price": 2800,
    "min_beds": 2,
    "target_urls": ["https://www.zillow.com/bridgewater-township-nj-08807/rentals/"],
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def check_target_urls():
    if not CONFIG["target_urls"]:
        print("No target URLs configured.")
        return

    try:
        import requests
    except ImportError:
        print("The 'requests' package is not installed. Run: pip install requests")
        return

    for url in CONFIG["target_urls"]:
        print(f"Checking: {url}")
        try:
            response = requests.get(url, headers=HEADERS, timeout=15)
            print(f"Status code: {response.status_code}")
            print(f"Accessible: {response.ok}")
            print(response.text[:200].replace("\n", " "))
        except Exception as exc:
            print(f"Error: {type(exc).__name__}: {exc}")
        print("-" * 50)


if __name__ == "__main__":
    print(title)
    check_target_urls()
