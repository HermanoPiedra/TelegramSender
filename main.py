import requests
from bs4 import BeautifulSoup
import os
from urllib.parse import urljoin
import re

# SETTINGS
PERSON_GROUPS = [1, 2]          # bot will send results for 1-person AND 2-person
BASE_BUDGET = 900               # budget per person (€)
TOP_RESULTS = 5                 # top listings per group
SEEN_FILE = "seen_listings.txt" # memory

# Furnished detection keywords
FURNISHED_YES = {
    "furnished", "fully furnished", "gemeubileerd", "volledig gemeubileerd"
}
FURNISHED_NO = {
    "unfurnished", "not furnished", "ongemeubileerd", "kaal", "shell", "gestoffeerd"
}

def is_furnished(text: str) -> bool:
    t = text.lower()
    if any(w in t for w in FURNISHED_NO):
        return False
    if any(w in t for w in FURNISHED_YES):
        return True
    return False

def infer_bedrooms(title: str, text: str) -> int | None:
    t = (title + " " + text).lower()
    if "studio" in t:
        return 0
    m = re.search(r"\b(\d+)\s*-\s*bed|\b(\d+)\s*bed(room)?s?\b|\b(\d+)\s*slaapkamers?\b", t)
    if m:
        for group in m.groups():
            if group and group.isdigit():
                return int(group)
    if "room" in t or "kamer" in t:
        return 0
    return None

# Memory system
def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r") as f:
        return set(line.strip() for line in f)

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        for item in seen:
            f.write(item + "\n")

def remove_seen(listings, people):
    seen = load_seen()
    new_items = []
    for it in listings:
        key = f"{people}|{it['url']}"
        if key not in seen:
            new_items.append(it)
            seen.add(key)
    save_seen(seen)
    return new_items

# Telegram sender
def send_message(message):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("CHAT_ID")
    if not token or not chat_id:
        print("❌ Missing TELEGRAM_TOKEN or CHAT_ID environment variables")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {"chat_id": chat_id, "text": message}
    r = requests.post(url, json=data)
    print("Telegram status:", r.status_code)
    if not r.ok:
        print("Response:", r.text)

# Website scraper
def fetch_pararius_listings():
    url = "https://www.pararius.com/apartments/rotterdam"
    headers = {"User-Agent": "Mozilla/5.0 (HousingBot)"}
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    listings = []
    for card in soup.select("section.listing-search-item"):
        title_el = card.select_one("a.listing-search-item__link--title")
        price_el = card.select_one("div.listing-search-item__price")
        if not title_el or not price_el:
            continue

        title = title_el.get_text(strip=True)
        price_raw = price_el.get_text(strip=True)
        digits = "".join(c for c in price_raw if c.isdigit())
        if not digits:
            continue
        price_num = int(digits)

        rel = title_el.get("href")
        if not rel or isinstance(rel, list):
            continue
        full_url = urljoin(url, rel)

        full_text = card.get_text(" ", strip=True)
        furnished = is_furnished(full_text)
        bedrooms = infer_bedrooms(title, full_text)

        listings.append({
            "title": title,
            "price": price_num,
            "url": full_url,
            "furnished": furnished,
            "bedrooms": bedrooms,
            "source": "Pararius"
        })

    return listings

# Group-based filtering
def filter_for_people(listings, people):
    max_rent = BASE_BUDGET * people
    out = []
    for it in listings:
        if it["price"] > max_rent:
            continue
        if not it["furnished"]:
            continue
        b = it.get("bedrooms")
        if people == 1:
            if b is not None and b > 1:
                continue
        else:
            if b is not None and b < 2:
                continue
        out.append(it)
    out.sort(key=lambda x: x["price"])
    return out

# Notification formatter
def notify_group(matches, people):
    if not matches:
        send_message(f"ℹ️ No new listings today for {people} person(s).")
        return
    max_rent = BASE_BUDGET * people
    msg = f"🏠 Matches for {people} person(s) — Max budget €{max_rent}\n\n"
    for it in matches[:TOP_RESULTS]:
        b = it.get("bedrooms")
        if b == 0:
            btxt = "studio"
        elif b is not None:
            btxt = f"{b} bedrooms"
        else:
            btxt = "bedrooms?"
        msg += f"{it['title']} — €{it['price']} — {btxt}\n{it['url']}\n\n"
    send_message(msg)

# MAIN PROCESS
def main():
    listings = fetch_pararius_listings()
    for people in PERSON_GROUPS:
        filtered = filter_for_people(listings, people)
        new_only = remove_seen(filtered, people)
        notify_group(new_only, people)

if __name__ == "__main__":
    main()
