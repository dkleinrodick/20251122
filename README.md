# WildFares | The Pro Tool for GoWild!™

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![Python: 3.10+](https://img.shields.io/badge/Python-3.10%2B-yellow.svg)
![Framework: FastAPI](https://img.shields.io/badge/Framework-FastAPI-green.svg)
![Frontend: Vue 3 + Tailwind](https://img.shields.io/badge/Frontend-Vue%203%20%7C%20Tailwind-blueviolet.svg)

**WildFares** is the ultimate, unofficial search engine designed to unlock the full potential of the Frontier Airlines **GoWild!™ All-You-Can-Fly Pass**.

Standard booking tools aren't built for the chaos of GoWild. WildFares is. It aggregates real-time availability, visualizes the network, builds impossible multi-hop routes, and automates the hunt for open seats.

---

## 🚀 Features Overview

### 1. 🌍 Visual Explore Mode
*The default landing experience.*
- **Interactive Map:** Instantly see every valid destination you can reach from your home airport.
- **Smart Filters:** Filter by **"No Rain"** or **"Warm Weather (60°+)"** using integrated live weather data.
- **Dual Modes:** Switch between "Departing From" (Where can I go?) and "Arriving To" (How do I get home?).
- **List View:** A clean, chunked grid view of destinations with inline expansion for flight details.

### 2. 🔀 Advanced Route Builder
*When direct flights are sold out.*
- **Multi-Hop Logic:** Automatically stitches together itineraries via major hubs (DEN, MCO, ATL, etc.) to get you to your destination.
- **Self-Transfer Support:** Clearly highlights where you need to exit security, claim bags, and re-check them.
- **Rich Cards:** Horizontal timeline visualization showing leg prices, layover durations, and total trip cost.
- **Customizable:** Filter by Max Connection Time, Total Duration, and Max Stops (1-3). Sort by Price, Duration, or Stops.

### 3. ⚡ Day Trips
*For the spontaneous adventurer.*
- Finds **Same-Day Round Trips** (out in the morning, back at night).
- Configurable minimum "Hours at Destination" slider.
- Perfect for mileage runs or quick dinner-and-back trips.

### 4. 🤖 Intelligent Automation
- **Midnight Scraper:** A specialized high-frequency job that monitors timezones. As soon as it hits **00:00 local time** at an airport, the scraper wakes up to cache the newly released flights for the next day (GoWild! booking window opens 24h in advance).
- **Auto-Scraper:** Periodically refreshes active routes to keep the cache warm.
- **Self-Healing:** Automatically deletes old cache entries before adding new ones to prevent duplicates.
- **Weather Updater:** Fetches 7-day forecasts for all airports on startup and daily schedules.

### 5. 🛡️ Admin & System
- **Live Status Popup:** Real-time overlay showing scraper progress, flights added/deleted, and errors.
- **Proxy Rotation:** Support for authenticated proxies to bypass rate limits.
- **Periodic Reset:** Configurable "nuclear option" to wipe the cache every X days (default 2) and rebuild from scratch to ensure data hygiene.

---

## 🛠️ Installation & Setup

### Prerequisites
- **Python 3.10** or higher.
- **pip** (Python package manager).

### Quick Start (Windows)

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/wildfares.git
   cd wildfares
   ```

2. **Run the Launcher:**
   Double-click **`run_locally.bat`**.
   *This script automates the entire setup: virtual environment creation, dependency installation, and server startup.*

3. **Access the App:**
   - **Search Interface:** [http://localhost:8000](http://localhost:8000)
   - **Admin Panel:** [http://localhost:8000/admin](http://localhost:8000/admin) (Default Password: `admin`)

---

## 🧩 How It Works

### Timezone Logic
GoWild! tickets typically become available at midnight in the **origin airport's timezone**.
- WildFares is timezone-aware. If you are in Chicago (CST) searching for a flight from New York (EST), the "Today/Tomorrow" dropdown will adjust to reflect that the booking window for New York might already be open for the next day.

### Route Deduplication
The Route Builder is smart enough to know when a "connection" is actually just a standard ticket Frontier sells directly. It filters these out to show you *true* hacker fares that you wouldn't find otherwise.

### Caching Strategy
- **On-Demand:** Searching for a route triggers a live scrape (if cache is stale).
- **Background:** The Scheduler keeps popular routes fresh.
- **Periodic Reset:** Every 2 days (configurable), the system wipes the slate clean to remove phantom inventory.

---

## ⚙️ Configuration

Navigate to the **Admin Panel** (`/admin`) to configure:
- **Scraper Mode:** Switch between `ondemand` (real-time) and `automatic` (background only).
- **Concurreny:** Set max concurrent requests (default: 2).
- **Proxies:** Add standard HTTP proxies (`user:pass@host:port`).
- **Timers:** Adjust how often the auto-scraper and midnight scraper run.

---

## ⚠️ Disclaimer

**WildFares is an unofficial tool.** It is not affiliated with, endorsed by, or connected to Frontier Airlines.

- **Booking:** All "Book" buttons redirect you to the official `flyfrontier.com` website to complete your purchase. WildFares handles no money.
- **Risks:** Automated scraping can lead to IP blocks. Use proxies and conservative settings.
- **Data:** Flight availability is volatile. Always verify before making plans.

---

*Unlock the World. Embrace the Chaos.*