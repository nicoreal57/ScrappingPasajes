"""
Google Flights Scraper
Busca vuelos round-trip BUE → MAD bajo un precio umbral y notifica por email (Gmail).
"""

import os
import json
import time
import random
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ─── Configuración ────────────────────────────────────────────────────────────

PRICE_THRESHOLD_USD = float(os.getenv("PRICE_THRESHOLD", "1000"))

DEPARTURE_DATE = os.getenv("DEPARTURE_DATE", "2026-08-19")
RETURN_DATE    = os.getenv("RETURN_DATE",    "2026-09-06")

ORIGINS = ["EZE", "AEP"]
DESTINATION = "MAD"

GMAIL_SENDER     = os.getenv("GMAIL_SENDER", "")
GMAIL_PASSWORD   = os.getenv("GMAIL_PASSWORD", "")
EMAIL_RECIPIENTS = [
    r.strip()
    for r in os.getenv("EMAIL_RECIPIENTS", "").split(",")
    if r.strip()
]

LOG_FILE = "flights_log.json"

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# ─── Modelo de datos ──────────────────────────────────────────────────────────

@dataclass
class Flight:
    origin: str
    destination: str
    departure_date: str
    return_date: str
    price_usd: float
    airline: str
    stops: str
    duration: str
    url: str
    scraped_at: str

# ─── Scraper ──────────────────────────────────────────────────────────────────

def scrape_google_flights(origin: str) -> list[Flight]:
    url = f"https://www.google.com/flights?hl=en&curr=USD#flt={origin}.MAD.{DEPARTURE_DATE}*MAD.{origin}.{RETURN_DATE};c:USD;e:1;sd:1;t:f"

    log.info(f"Scraping Google Flights {origin} → {DESTINATION}")
    log.info(f"URL: {url}")
    flights = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
