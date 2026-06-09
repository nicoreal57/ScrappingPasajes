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

# Google Flights usa códigos de ciudad, no aeropuerto
# BUE = Buenos Aires (ambos aeropuertos), MAD = Madrid
ORIGINS = ["EZE", "AEP"]
DESTINATION = "MAD"

# Gmail
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

# ─── URL builder ──────────────────────────────────────────────────────────────

def build_url(origin: str) -> str:
    """
    Google Flights URL para round-trip.
    Formato: /flights/search?tfs=...
    Usamos la URL directa con parámetros legibles.
    """
    # Convertir fechas de YYYY-MM-DD a formato Google (YYYY-MM-DD es igual)
    dep = DEPARTURE_DATE
    ret = RETURN_DATE
    # URL de Google Flights para round-trip
    url = (
        f"https://www.google.com/travel/flights/search?"
        f"tfs=CBwQAhoeEgoyMDI2LTA4LTE5agcIARIDRVpFcgcIARIDTUFEGh4SCjIwMjYtMDktMDZqBwgBEgNNQURyBwgBEgNFWkUoAUABSAE"
    )
    # URL más simple y legible que Google también acepta
    url = (
        f"https://www.google.com/travel/flights?"
        f"q=flights+from+{origin}+to+{DESTINATION}+"
        f"{dep}+return+{ret}"
    )
    return url

def build_direct_url(origin: str) -> str:
    """URL directa de Google Flights con parámetros estructurados."""
    dep = DEPARTURE_DATE.replace("-", "")  # 20260819
    ret = RETURN_DATE.replace("-", "")     # 20260906
    # Google Flights acepta esta estructura de URL
    url = (
        f"https://www.google.com/flights#flt="
        f"{origin}.{DESTINATION}.{DEPARTURE_DATE}*"
        f"{DESTINATION}.{origin}.{RETURN_DATE};c:USD;e:1;sd:1;t:f"
    )
    return url

# ─── Scraper ──────────────────────────────────────────────────────────────────

def scrape_google_flights(origin: str) -> list[Flight]:
    # URL canónica de Google Flights para round-trip
    url = (
        f"https://www.google.com/travel/flights/search?"
        f"tfs=CBwQAhoqEgoyMDI2LTA4LTE5KAFqBwgBEgN"
        f"{origin}yBwgBEgNNQUQaKhIKMjAyNi0wOS0wNigBagcIARIDTUFEcgcIARID"
        f"{origin}SAFAAWoCVVNE"
    )
    # URL más robusta usando el formato de búsqueda de texto
    url = (
        f"https://www.google.com/travel/flights?"
        f"hl=en&gl=us&curr=USD"
        f"&tfs=CBwQAhoqEgoyMDI2LTA4LTE5agcIARID{origin}yBwgBEgNNQUQ"
        f"aKhIKMjAyNi0wOS0wNmoHCAESA01BRHIHARID{origin}SAFIAUABSAE"
    )
    # La URL más simple que funciona consistentemente
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
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            timezone_id="America/Argentina/Buenos_Aires",
        )

        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = context.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            time.sleep(random.uniform(3, 5))

            # Esperar a que carguen los resultados de vuelos
            # Google Flights usa varios selectores posibles
            selectors = [
                "[data-gs]",           # contenedor de vuelo con data
                ".YMlIz",              # precio en Google Flights
                "[jsname='IWWDBc']",   # lista de resultados
                "li[data-gs]",         # item de vuelo
            ]

            loaded = False
            for selector in selectors:
                try:
                    page.wait_for_selector(selector, timeout=20_000)
                    log.info(f"  → Resultados cargados con selector: {selector}")
                    loaded = True
                    break
                except PlaywrightTimeout:
                    continue

            if not loaded:
                log.warning(f"  → No se cargaron resultados para {origin}. Guardando HTML.")
                with open(f"debug_{origin}.html", "w", encoding="utf-8") as f:
                    f.write(page.content())
                return flights

            # Scroll para cargar más resultados
            page.evaluate("window.scrollTo(0, 600)")
            time.sleep(random.uniform(1, 2))

            flights = parse_google_flights(page, origin, url)
            log.info(f"  → {len(flights)} vuelos encontrados para {origin}")

        except PlaywrightTimeout:
            log.warning(f"  → Timeout general para {origin}.")
            with open(f"debug_{origin}.html", "w", encoding="utf-8") as f:
                f.write(page.content())
        except Exception as e:
            log.error(f"  → Error scraping {origin}: {e}")
        finally:
            browser.close()

    return flights


def parse_google_flights(page, origin: str, url: str) -> list[Flight]:
    flights = []
    now = datetime.utcnow().isoformat()

    # Google Flights renderiza vuelos en li elements con data-gs attribute
    # Intentar múltiples estrategias de extracción

    # Estrategia 1: buscar por el atributo data-gs (contiene datos del vuelo)
    flight_items = page.query_selector_all("li[data-gs]")

    if not flight_items:
        # Estrategia 2: buscar contenedores de precio directamente
        flight_items = page.query_selector_all("[jsname='IWWDBc'] li")

    if not flight_items:
        log.warning(f"  → No se encontraron items de vuelo para {origin}.")
        with open(f"debug_{origin}.html", "w", encoding="utf-8") as f:
            f.write(page.content())
        return flights

    log.info(f"  → {len(flight_items)} items de vuelo encontrados")

    for item in flight_items[:15]:
        try:
            full_text = item.inner_text()
            if not full_text.strip():
                continue

            # Extraer precio — Google muestra "USD 850" o "$850" o "850"
            price = extract_price_from_text(full_text)
            if price is None:
                continue

            # Extraer aerolínea (primera línea suele ser la aerolínea)
            lines = [l.strip() for l in full_text.split("\n") if l.strip()]
            airline = lines[0] if lines else "–"

            # Buscar duración (formato "14 hr 30 min" o similar)
            duration = "–"
            for line in lines:
                if "hr" in line or "min" in line:
                    duration = line
                    break

            # Buscar escalas
            stops = "–"
            for line in lines:
                if "stop" in line.lower() or "nonstop" in line.lower() or "direct" in line.lower():
                    stops = line
                    break

            flights.append(Flight(
                origin=origin,
                destination=DESTINATION,
                departure_date=DEPARTURE_DATE,
                return_date=RETURN_DATE,
                price_usd=price,
                airline=airline,
                stops=stops,
                duration=duration,
                url=url,
                scraped_at=now,
            ))

        except Exception as e:
            log.debug(f"  → Error parseando item: {e}")
            continue

    return flights


def extract_price_from_text(text: str) -> Optional[float]:
    """Extrae el precio USD del texto completo de un resultado de vuelo."""
    import re
    # Patrones comunes en Google Flights
    patterns = [
        r'USD\s*([\d,]+)',           # USD 1,234
        r'\$\s*([\d,]+)',            # $1,234
        r'([\d,]+)\s*USD',           # 1,234 USD
        r'\b([1-9][\d]{2,3})\b',    # número de 3-4 dígitos standalone
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            try:
                value = float(match.replace(",", ""))
                if 200 <= value <= 5000:
                    return value
            except ValueError:
                continue
    return None


def parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    import re
    cleaned = re.sub(r'[^\d.,]', '', text).strip()
    if "." in cleaned and "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    elif "." in cleaned and cleaned.count(".") == 1:
        parts = cleaned.split(".")
        if len(parts[1]) == 3:
            cleaned = cleaned.replace(".", "")
    try:
        value = float(cleaned)
        if 200 <= value <= 5000:
            return value
        return None
    except ValueError:
        return None

# ─── Notificaciones Gmail ─────────────────────────────────────────────────────

def send_email(subject: str, body_html: str) -> bool:
    if not GMAIL_SENDER or not GMAIL_PASSWORD or not EMAIL_RECIPIENTS:
        log.warning("Faltan credenciales de Gmail. Saltando envío.")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_SENDER
        msg["To"]      = ", ".join(EMAIL_RECIPIENTS)
        msg.attach(MIMEText(body_html, "html", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_SENDER, GMAIL_PASSWORD)
            server.sendmail(GMAIL_SENDER, EMAIL_RECIPIENTS, msg.as_string())
        log.info(f"  ✓ Email enviado a: {', '.join(EMAIL_RECIPIENTS)}")
        return True
    except Exception as e:
        log.error(f"  ✗ Error enviando email: {e}")
        return False


def format_email_html(flights: list[Flight]) -> tuple[str, str]:
    best_price = min(f.price_usd for f in flights)
    subject = f"✈️ Vuelo BUE→MAD desde USD {best_price:,.0f} — ¡Oferta encontrada!"

    rows = ""
    for f in flights[:5]:
        rows += f"""
        <tr>
          <td style="padding:10px;border-bottom:1px solid #eee;">{f.origin} → {f.destination}</td>
          <td style="padding:10px;border-bottom:1px solid #eee;font-weight:bold;color:#1a7f37;">USD {f.price_usd:,.0f}</td>
          <td style="padding:10px;border-bottom:1px solid #eee;">{f.airline}</td>
          <td style="padding:10px;border-bottom:1px solid #eee;">{f.stops}</td>
          <td style="padding:10px;border-bottom:1px solid #eee;">{f.duration}</td>
          <td style="padding:10px;border-bottom:1px solid #eee;"><a href="{f.url}" style="color:#0066cc;">Ver en Google Flights</a></td>
        </tr>"""

    html = f"""
    <div style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto;">
      <h2 style="color:#1a1a2e;">✈️ Alerta de vuelo barato — BUE → MAD</h2>
      <p style="color:#555;">
        Ida: <strong>{DEPARTURE_DATE}</strong> &nbsp;|&nbsp;
        Vuelta: <strong>{RETURN_DATE}</strong> &nbsp;|&nbsp;
        Umbral: <strong>USD {PRICE_THRESHOLD_USD:,.0f}</strong>
      </p>
      <table style="width:100%;border-collapse:collapse;font-size:14px;">
        <thead>
          <tr style="background:#f0f4ff;">
            <th style="padding:10px;text-align:left;">Ruta</th>
            <th style="padding:10px;text-align:left;">Precio</th>
            <th style="padding:10px;text-align:left;">Aerolínea</th>
            <th style="padding:10px;text-align:left;">Escalas</th>
            <th style="padding:10px;text-align:left;">Duración</th>
            <th style="padding:10px;text-align:left;">Link</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
      <p style="color:#999;font-size:12px;margin-top:20px;">
        Fuente: Google Flights | Scrapeado: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
      </p>
    </div>
    """
    return subject, html


def notify_all(flights: list[Flight]):
    if not flights:
        return
    subject, html = format_email_html(flights)
    log.info(f"Enviando alerta: {len(flights)} vuelo(s) bajo umbral.")
    send_email(subject, html)

# ─── Log persistente ──────────────────────────────────────────────────────────

def save_to_log(flights: list[Flight]):
    existing = []
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r") as f:
                existing = json.load(f)
        except json.JSONDecodeError:
            pass
    existing.extend([asdict(f) for f in flights])
    existing = existing[-500:]
    with open(LOG_FILE, "w") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)
    log.info(f"Log actualizado: {len(existing)} registros en {LOG_FILE}")

# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    log.info("=" * 60)
    log.info(f"Iniciando scraper | {datetime.utcnow().isoformat()} UTC")
    log.info(f"Ruta: BUE → {DESTINATION} | {DEPARTURE_DATE} → {RETURN_DATE}")
    log.info(f"Umbral: USD {PRICE_THRESHOLD_USD:,.0f}")
    log.info("=" * 60)

    all_cheap_flights = []

    for origin in ORIGINS:
        flights = scrape_google_flights(origin)
        cheap = [f for f in flights if f.price_usd < PRICE_THRESHOLD_USD]
        log.info(f"{origin}: {len(cheap)}/{len(flights)} vuelos bajo USD {PRICE_THRESHOLD_USD:,.0f}")
        all_cheap_flights.extend(cheap)

        if origin != ORIGINS[-1]:
            wait = random.uniform(5, 10)
            log.info(f"Esperando {wait:.1f}s antes del siguiente origen...")
            time.sleep(wait)

    all_cheap_flights.sort(key=lambda f: f.price_usd)

    if all_cheap_flights:
        log.info(f"🎯 ¡{len(all_cheap_flights)} vuelo(s) bajo umbral encontrado(s)!")
        notify_all(all_cheap_flights)
        save_to_log(all_cheap_flights)
    else:
        log.info("Sin ofertas bajo umbral en esta corrida.")

    log.info("Scraper finalizado.")


if __name__ == "__main__":
    main()
