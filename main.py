import os
import re
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# =========================
# SETTINGS
# =========================
PERSON_GROUPS = [1, 2]          # Send results for 1-person AND 2-person
BASE_BUDGET = 900               # € per person
TOP_RESULTS = 5                 # max items per section
SEEN_FILE = "seen_listings.txt" # memory file (committed by GH Actions)

# Furnished detection keywords
FURNISHED_YES = {
    "furnished", "fully furnished", "gemeubileerd", "volledig gemeubileerd"
}
FURNISHED_NO = {
    "unfurnished", "not furnished", "ongemeubileerd", "kaal", "shell", "gestoffeerd"
}

# =========================
# UTILITIES
# =========================
def is_furnished(text: str) -> bool:
    t = text.lower()
    if any(w in t for w in FURNISHED_NO):
        return False
    if any(w in t for w in FURNISHED_YES):
        return True
    return False  # be strict if unclear

def infer_bedrooms(title: str, text: str) -> int | None:
    """Return 0 for studio, N for detected bedrooms, or None if unknown."""
    t = (title + " " + text).lower()
    if "studio" in t:
        return 0
    m = re.search(r"\b(\d+)\s*-\s*bed|\b(\d+)\s*bed(room)?s?\b|\b(\d+)\s*slaapkamers?\b|\b(\d+)\s*kamers?\b", t)
    if m:
        for g in m.groups():
            if g and g.isdigit():
                return int(g)
    if "room" in t or "kamer" in t:
        return 0
    return None

def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f)

def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        for item in seen:
            f.write(item + "\n")

def remove_seen(listings, people):
    """Deduplicate using people + source + url so groups/sources don’t block each other."""
    seen = load_seen()
    new_items = []
    for it in listings:
        key = f"{people}|{it['source']}|{it['url']}"
        if key not in seen:
            new_items.append(it)
            seen.add(key)
    save_seen(seen)
    return new_items

def send_message(message: str):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("CHAT_ID")
    if not token or not chat_id:
        print("❌ Missing TELEGRAM_TOKEN or CHAT_ID environment variables")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, json={"chat_id": chat_id, "text": message})
    print("Telegram status:", r.status_code)
    if not r.ok:
        print("Response:", r.text)

# =========================
# SCRAPERS
# =========================
def fetch_pararius_listings():
    url = "https://www.pararius.com/apartments/rotterdam"
    headers = {"User-Agent": "Mozilla/5.0 (HousingBot)"}
    resp = requests.get(url, headers=headers, timeout=25)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    out = []
    for card in soup.select("section.listing-search-item"):
        title_el = card.select_one("a.listing-search-item__link--title")
        price_el = card.select_one("div.listing-search-item__price")
        if not title_el or not price_el:
            continue

        title = title_el.get_text(strip=True)
        digits = "".join(c for c in price_el.get_text(strip=True) if c.isdigit())
        if not digits:
            continue
        price_num = int(digits)

        rel = title_el.get("href")
        if not rel or isinstance(rel, list):
            continue
        full_url = urljoin(url, rel)

        text = card.get_text(" ", strip=True)
        furnished = is_furnished(text)
        bedrooms = infer_bedrooms(title, text)

        out.append({
            "title": title,
            "price": price_num,
            "url": full_url,
            "furnished": furnished,
            "bedrooms": bedrooms,
            "source": "Pararius",
        })
    return out

def _parse_price_from_text(txt: str) -> int | None:
    m = re.search(r"€\s*([\d\.\,]+)", txt)
    if not m:
        # fallback: grab any number that looks like rent
        m = re.search(r"\b(\d{3,4})\b", txt.replace(".", "").replace(",", ""))
        if not m:
            return None
        return int(m.group(1))
    val = m.group(1).replace(".", "").replace(",", "")
    try:
        return int(val)
    except ValueError:
        return None

def fetch_funda_listings():
    base = "https://www.funda.nl"
    list_url = f"{base}/en/huur/rotterdam/"
    headers = {"User-Agent": "Mozilla/5.0 (HousingBot)"}
    resp = requests.get(list_url, headers=headers, timeout=25)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    links = []
    for a in soup.select('a[href^="/en/huur/rotterdam/"]'):
        href = a.get("href") or ""
        if "/appartement-" in href or "/huis-" in href or "/kamer-" in href or "/studio-" in href:
            full = urljoin(base, href.split("?")[0])
            if full not in links:
                links.append(full)

    results = []
    for detail_url in links[:20]:
        try:
            time.sleep(1.0)  # be polite
            r = requests.get(detail_url, headers=headers, timeout=25)
            r.raise_for_status()
            page = BeautifulSoup(r.text, "html.parser")
            text = page.get_text(" ", strip=True)

            title_el = page.find(["h1", "h2"])
            title = title_el.get_text(strip=True) if title_el else "Funda listing"

            price_num = _parse_price_from_text(text) or 10**9
            furnished = is_furnished(text)
            bedrooms = infer_bedrooms(title, text)

            results.append({
                "title": title,
                "price": price_num,
                "url": detail_url,
                "furnished": furnished,
                "bedrooms": bedrooms,
                "source": "Funda",
            })
        except Exception as e:
            print(f"[Funda] Skip {detail_url}: {e}")
            continue

    return results

def fetch_huurwoningen_listings():
    base = "https://www.huurwoningen.nl"
    list_url = f"{base}/in/rotterdam/"
    headers = {"User-Agent": "Mozilla/5.0 (HousingBot)"}

    try:
        resp = requests.get(list_url, headers=headers, timeout=25)
        resp.raise_for_status()
    except Exception as e:
        print("Huurwoningen list error:", e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    links = []
    for a in soup.select('a[href^="/huren/rotterdam/"]'):
        href = a.get("href") or ""
        if len(href) > 20:
            full = urljoin(base, href.split("?")[0])
            if full not in links:
                links.append(full)

    results = []
    for detail_url in links[:20]:
        try:
            time.sleep(1.0)
            r = requests.get(detail_url, headers=headers, timeout=25)
            r.raise_for_status()
            page = BeautifulSoup(r.text, "html.parser")
            text = page.get_text(" ", strip=True)

            title_el = page.find(["h1", "h2"])
            title = title_el.get_text(strip=True) if title_el else "Huurwoningen listing"

            price_num = _parse_price_from_text(text) or 10**9
            furnished = is_furnished(text)
            bedrooms = infer_bedrooms(title, text)

            results.append({
                "title": title,
                "price": price_num,
                "url": detail_url,
                "furnished": furnished,
                "bedrooms": bedrooms,
                "source": "Huurwoningen",
            })
        except Exception as e:
            print(f"[Huurwoningen] Skip {detail_url}: {e}")
            continue

    return results

def fetch_all_listings():
    items = []
    try:
        items += fetch_pararius_listings()
    except Exception as e:
        print("Pararius error:", e)
    try:
        items += fetch_funda_listings()
    except Exception as e:
        print("Funda error:", e)
    try:
        items += fetch_huurwoningen_listings()
    except Exception as e:
        print("Huurwoningen error:", e)
    return items

# =========================
# FILTERING & NOTIFY
# =========================
def filter_for_people(listings, people: int):
    max_rent = BASE_BUDGET * people
    out = []
    for it in listings:
        if it["price"] is None or it["price"] > max_rent:
            continue
        if not it["furnished"]:
            continue

        b = it.get("bedrooms")
        if people == 1:
            if b is not None and b > 1:
                continue
        else:  # 2 or more people
            if b is not None and b < 2:
                continue

        out.append(it)

    out.sort(key=lambda x: x["price"])
    return out

def notify_group(matches, people: int):
    if not matches:
        send_message(f"ℹ️ No new listings today for {people} person(s).")
        return

    max_rent = BASE_BUDGET * people
    msg = [f"🏠 Matches for {people} person(s) — Max budget €{max_rent}\n"]
    for it in matches[:TOP_RESULTS]:
        b = it.get("bedrooms")
        btxt = "studio" if b == 0 else (f"{b} bedrooms" if b is not None else "bedrooms?")
        msg.append(f"[{it['source']}] {it['title']} — €{it['price']} — {btxt}\n{it['url']}\n")
    send_message("\n".join(msg))

# =========================
# MAIN
# =========================
def main():
    listings = fetch_all_listings()
    for people in PERSON_GROUPS:
        filtered = filter_for_people(listings, people)
        new_only = remove_seen(filtered, people)
        notify_group(new_only, people)

if __name__ == "__main__":
    main()

