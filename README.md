# ✈️ Despegar Flight Scraper — BUE → MAD

Agente que monitorea Despegar.com y te notifica por WhatsApp cuando aparece un vuelo
round-trip Buenos Aires → Madrid por debajo del umbral de precio configurado.

---

## Stack

| Componente | Tecnología |
|---|---|
| Browser automation | Playwright (Chromium headless) |
| Scheduler | GitHub Actions (cron cada 1 hora) |
| Notificaciones | CallMeBot WhatsApp API |
| Hosting | 100% en GitHub — sin servidor |

---

## Setup en 5 pasos

### 1. Fork / clonar este repositorio en tu cuenta de GitHub

```bash
git clone https://github.com/TU_USUARIO/flight-scraper.git
cd flight-scraper
```

### 2. Activar tu número en CallMeBot

Cada persona que quiera recibir alertas debe hacer esto **una sola vez**:

1. Agregar el número de CallMeBot a tus contactos de WhatsApp: **+34 644 59 78 08**
2. Enviar el mensaje: `I allow callmebot to send me messages`
3. Recibirás un mensaje con tu **API Key** personal. Guardala.

Repetir para cada número (vos + tu amigo/a).

### 3. Configurar GitHub Secrets

En tu repositorio → **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Valor |
|---|---|
| `WA_PHONE_1` | Tu número con código de país, sin `+`. Ej: `5491165432100` |
| `WA_APIKEY_1` | Tu API key de CallMeBot |
| `WA_PHONE_2` | Número de tu amigo/a (mismo formato) |
| `WA_APIKEY_2` | API key de tu amigo/a |

### 4. Configurar parámetros del vuelo

En **Settings → Secrets and variables → Actions → Variables** (no Secrets):

| Variable | Ejemplo |
|---|---|
| `DEPARTURE_DATE` | `2025-07-15` |
| `RETURN_DATE` | `2025-07-30` |
| `PRICE_THRESHOLD` | `1000` |

> Las fechas y el umbral están en Variables (no Secrets) porque no son datos sensibles
> y así los podés editar fácilmente desde la UI sin hacer commit.

### 5. Habilitar GitHub Actions

1. Ir a la pestaña **Actions** de tu repositorio
2. Si aparece un banner "Workflows disabled", hacer clic en **"I understand my workflows, go ahead and enable them"**
3. El workflow se activará automáticamente cada hora

---

## Correr manualmente

En **Actions → Flight Price Scraper → Run workflow** podés triggear una corrida
on-demand y sobrescribir fechas/umbral sin cambiar la configuración.

---

## Ajustar el horario

El cron `0 * * * *` corre a las :00 de cada hora UTC (= cada hora, las 24 horas).
Para correr cada 3 horas: `0 */3 * * *`
Para solo en horario laboral AR (12:00–22:00 UTC): `0 12-22 * * *`

Editar en `.github/workflows/scraper.yml`.

---

## Actualizar selectores (si Despegar cambia su frontend)

Despegar actualiza su HTML con cierta frecuencia. Si el scraper deja de encontrar
precios, el paso de debug sube el HTML completo de la página como artefacto
(`debug_EZE.html`, `debug_AEP.html`) para que puedas inspeccionar los selectores actuales.

1. Descargar el artefacto desde la pestaña **Actions → run fallido → Artifacts**
2. Abrir el HTML en browser → DevTools → buscar el precio
3. Actualizar los selectores en `src/scraper.py` → función `parse_flights()`

---

## Estructura del proyecto

```
flight-scraper/
├── .github/
│   └── workflows/
│       └── scraper.yml       # Workflow de GitHub Actions
├── src/
│   └── scraper.py            # Lógica principal
├── requirements.txt
└── README.md
```

---

## Limitaciones conocidas

- **Cloudflare / bot detection**: Despegar puede bloquear el scraper ocasionalmente.
  Si falla consistentemente, considera añadir rotación de user-agent o un servicio
  de proxies residenciales (Bright Data, Oxylabs).
- **Cambios de selectores**: El frontend de Despegar es dinámico. Los selectores CSS
  pueden necesitar actualización periódica.
- **GitHub Actions gratuito**: El plan free ofrece 2.000 minutos/mes. Con corridas de
  ~3 min cada hora = ~90 min/día = ~2.700 min/mes. Se puede quedar sin minutos
  a fin de mes. Solución: cambiar el cron a cada 2-3 horas.
- **CallMeBot rate limit**: Máximo ~100 mensajes/día por número.
