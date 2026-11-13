# LCC Community Bot (`lcc-community-bot`)

Official community automation service and web dashboard for **Life Connection Church Orange County (LCC OC)**—designed to broadcast the **"Our World Stops"** daily prayer every day at **12:00 PM EST/EDT** across Discord, Instagram, and Facebook Messenger.

---

## Core Mission & Features

* **Automated Daily Prayer Broadcast (12:00 PM EST/EDT):** Synchronized scheduler that automatically handles Daylight Saving Time (`America/New_York`) to deliver daily prayers at noon sharp.
* **Multi-Platform Delivery:**
  * **Discord:** Posts formatted prayer embeds directly to the designated community prayer channel.
  * **Instagram:** Publishes daily prayer graphics and captions via the Meta Graph API.
  * **Messenger:** Delivers daily prayer broadcasts to opted-in community subscribers via the Meta Send API.
* **Web Admin Dashboard (HTML/CSS):**
  * Secure admin login for church coordinators and team leads.
  * Content manager to draft, preview, and schedule upcoming daily prayers.
  * Manual **"Send Now"** override and real-time connection status indicators for all three platforms.

---

## Tech Stack

* **Backend & Scheduler:** Python & Java
* **Web Interface:** HTML5 & CSS3
* **Integrations:** Discord API, Meta Graph API (Instagram & Messenger)
* **Version Control:** GitHub (`flpns-io/lcc-community-bot`)

---

## Project Structure

```text
lcc-community-bot/
├── bot-python/          # Core scheduler, Discord bot, and Meta API handlers
├── backend-java/        # Auxiliary backend services and API endpoints
├── web/                 # Admin dashboard frontend (HTML/CSS)
├── .env                 # Secret tokens & credentials (ignored by Git)
├── .gitignore           # Excludes secrets, virtual environments, and build files
└── README.md            # Project documentation
