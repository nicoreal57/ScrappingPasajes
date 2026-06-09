"""
Despegar.com Flight Scraper
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

# Fechas: formato YYYY-MM-DD
DEPARTURE_DATE = os.getenv("DEPARTURE_DATE", "2025-07-15")
RETURN_DATE    = os.getenv("RETURN_DATE",    "2025-07-30")

# Aeropuertos de origen (se corren ambos)
ORIGINS = ["EZE", "AEP"]
DESTINATION = "MAD"

# Gmail — se leen desde GitHub Secrets
GMAIL_SENDER   = os.getenv("GMAIL_SENDER", "")    # tu cuenta Gmail
GMAIL_PASSWORD = os.getenv("GMAIL_PASSWORD", "")  # App Password de Gmail (no tu password normal)
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
    departure_time: str
    return_time: str
    url: str
    scraped_at: str

# ─── URL builder ──────────────────────────────────────────────────────────────

def build_url(origin: str) -> str:
    base = "https://www.despegar.com.ar/vuelos"
    return f"{base}/{origin}-{DESTINATION}/{DEPARTURE_DATE}/{RETURN_DATE}/1/0/0/"

# ─── Scraper ──────────────────────────────────────────────────────────────────

def scrape_despegar(origin: str) -> list[Flight]:
    url = build_url(origin)
    log.info(f"Scraping {origin} → {DESTINATION} | URL: {url}")
    flights = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="es-AR",
        )

        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = context.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)

            page.wait_for_selector(
                "[data-test='cluster-price'], .precio, [class*='Price'], [class*='price']",
                timeout=45_000,
            )

            time.sleep(random.uniform(2, 4))
            page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            time.sleep(random.uniform(1, 2))

            flights = parse_flights(page, origin, url)
            log.info(f"  → {len(flights)} vuelos encontrados para {origin}")

        except PlaywrightTimeout:
            log.warning(f"  → Timeout esperando resultados para {origin}.")
        except Exception as e:
            log.error(f"  → Error scraping {origin}: {e}")
        finally:
            browser.close()

    return flights


def parse_flights(page, origin: str, url: str) -> list[Flight]:
    flights = []
    now = datetime.utcnow().isoformat()

    price_selectors = [
        "[data-test='cluster-price']",
        "[class*='PriceSection'] [class*='amount']",
        "[class*='price-section'] span",
        ".price",
    ]

    price_elements = []
    for selector in price_selectors:
        price_elements = page.query_selector_all(selector)
        if price_elements:
            log.info(f"  → Selector activo: '{selector}' ({len(price_elements)} elementos)")
            break

    if not price_elements:
        log.warning("  → No se encontraron elementos de precio. Guardando HTML para debug.")
        with open(f"debug_{origin}.html", "w", encoding="utf-8") as f:
            f.write(page.content())
        return flights

    cluster_selectors = [
        "[data-test='result-cluster']",
        "[class*='ResultCluster']",
        "[class*='result-cluster']",
        "[class*='ClusterContainer']",
    ]

    clusters = []
    for selector in cluster_selectors:
        clusters = page.query_selector_all(selector)
        if clusters:
            break

    if not clusters:
        log.warning("  → No se encontraron clusters. Extrayendo solo precios.")
        for el in price_elements[:10]:
            price = parse_price(el.inner_text().strip())
            if price and price < PRICE_THRESHOLD_USD:
                flights.append(Flight(
                    origin=origin, destination=DESTINATION,
                    departure_date=DEPARTURE_DATE, return_date=RETURN_DATE,
                    price_usd=price, airline="Ver en Despegar",
                    stops="–", departure_time="–", return_time="–",
                    url=url, scraped_at=now,
                ))
        return flights

    for cluster in clusters[:20]:
        try:
            price_el = cluster.query_selector(
                "[data-test='cluster-price'], [class*='amount'], [class*='Price']"
            )
            if not price_el:
                continue
            price = parse_price(price_el.inner_text())
            if price is None:
                continue

            airline_el = cluster.query_selector(
                "[class*='airline'], [class*='Airline'], [data-test='airline-name']"
            )
            airline = airline_el.inner_text().strip() if airline_el else "–"

            stops_el = cluster.query_selector(
                "[class*='stops'], [class*='Stops'], [data-test='stops']"
            )
            stops = stops_el.inner_text().strip() if stops_el else "–"

            dep_el = cluster.query_selector("[class*='departure'], [data-test='departure-time']")
            ret_el = cluster.query_selector("[class*='arrival'], [data-test='arrival-time']")
            dep_time = dep_el.inner_text().strip() if dep_el else "–"
            ret_time = ret_el.inner_text().strip() if ret_el else "–"

            flights.append(Flight(
                origin=origin, destination=DESTINATION,
                departure_date=DEPARTURE_DATE, return_date=RETURN_DATE,
                price_usd=price, airline=airline, stops=stops,
                departure_time=dep_time, return_time=ret_time,
                url=url, scraped_at=now,
            ))

        except Exception as e:
            log.debug(f"  → Error parseando cluster: {e}")
            continue

    return flights


def parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    cleaned = text.replace("USD", "").replace("$", "").replace(" ", "").strip()
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
        log.warning("Faltan credenciales de Gmail o lista de destinatarios. Saltando envío.")
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
    """Devuelve (subject, html_body)."""
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
          <td style="padding:10px;border-bottom:1px solid #eee;">{f.departure_time}</td>
          <td style="padding:10px;border-bottom:1px solid #eee;"><a href="{f.url}" style="color:#0066cc;">Ver en Despegar</a></td>
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
            <th style="padding:10px;text-align:left;">Salida</th>
            <th style="padding:10px;text-align:left;">Link</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
      <p style="color:#999;font-size:12px;margin-top:20px;">
        Scrapeado: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
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
        flights = scrape_despegar(origin)
        cheap = [f for f in flights if f.price_usd < PRICE_THRESHOLD_USD]
        log.info(f"{origin}: {len(cheap)}/{len(flights)} vuelos bajo USD {PRICE_THRESHOLD_USD:,.0f}")
        all_cheap_flights.extend(cheap)

        if origin != ORIGINS[-1]:
            wait = random.uniform(8, 15)
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
