# 🚗 Personalized Chatbot for Automobile Garage Management

An AI-powered multi-agent chatbot system designed to automate customer support and operational workflows for automobile garages. It handles car sales inquiries, technical maintenance Q&A (via Hybrid RAG), multi-step appointment scheduling, policy enforcement, and seamless human handover. Staff get a web admin to read conversations, take over chats, and confirm appointments.

---

## 🚀 How to run it

### Prerequisites
Make sure you have **Docker** and **Docker Compose** installed on your machine.

---

### Step 1: Set Up Environment Variables
Create your `.env` file from the provided example:
```bash
cp .env.example .env
```
*(Optional)* If you want to use OpenAI models, open `.env` and fill in your `OPENAI_API_KEY`.
> **Tip:** You can run completely offline without an API key by setting `LLM_PROVIDER=none` and `EMBEDDING_PROVIDER=none`.

Set `ADMIN_SECRET` to a long random string (it signs admin login tokens). It is required in production:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

### Step 2: Run the Application

#### Development Mode (Hot-reload enabled)
Mounts local source code directly into the container so changes take effect immediately:
```bash
# Using Make:
make dev

# Or using Docker Compose directly:
docker compose -f docker-compose.dev.yml up --build
```
Once started, access the services:
- **Client:** http://localhost:8000/
- **Admin:** http://localhost:8000/admin
- **Appointments (admin):** http://localhost:8000/appointments.html
- **Interactive API Docs (Swagger):** http://localhost:8000/docs
- **PostgreSQL Database:** `localhost:5432` (`garage` / `garage`)

#### Production Mode (Background daemon)
Runs Uvicorn with multiple workers in detached mode:
```bash
# Using Make:
make prod

# Or using Docker Compose directly:
docker compose -f docker-compose.prod.yml up -d --build
```

---

### Step 3: Create an Admin Account
There is no default account. Create the first one (the migration runs automatically on startup):
```bash
# Development:
docker compose -f docker-compose.dev.yml exec web python -m app.manage create-manager <username> <password> --role manager

# Production:
docker compose -f docker-compose.prod.yml exec web python -m app.manage create-manager <username> <password> --role manager
```
Then open http://localhost:8000/admin and log in.

| Role | What they can see |
|---|---|
| `manager` | Everything, including the **Bot** tab (chats the AI handles alone) |
| `sales_admin`, `technician` | Only chats handed over to staff, and chats they joined themselves |

> **Tip:** For local development only, set `ADMIN_AUTH_REQUIRED=false` in `.env` to skip the login. Never do this on a public server.

After **3 wrong passwords in a row** an account is locked for **15 minutes** (`ADMIN_MAX_LOGIN_ATTEMPTS`, `ADMIN_LOCK_MINUTES`). Unlock it early with `python -m app.manage unlock <username>`.

---

### Useful Management Commands

| Action | Make Command | Docker Compose Equivalent |
|---|---|---|
| **View logs** | `make logs` | `docker compose -f docker-compose.prod.yml logs -f` |
| **Stop containers** | `make down` | `docker compose -f docker-compose.dev.yml down`<br>`docker compose -f docker-compose.prod.yml down` |
| **Reset / Clean all** | `make clean` | `docker compose -f docker-compose.dev.yml down -v`<br>`docker compose -f docker-compose.prod.yml down -v` |

> **Warning:** `make clean` deletes the database volume (all chats, appointments and admin accounts).

Account commands (run inside the `web` container, or locally with the database up):

| Action | Command |
|---|---|
| **Create / reset an account** | `python -m app.manage create-manager <username> <password> --role manager\|sales_admin\|technician` |
| **List accounts** | `python -m app.manage list-managers` |
| **Unlock a locked account** | `python -m app.manage unlock <username>` |

---

## 🧭 What you can do with it

- **Customers** chat at `/`: ask about trucks and prices, maintenance advice, book or change service appointments, or ask for a human.
- **Staff** work at `/admin`: read every conversation, filter by **All / Waiting / In progress / Done / Bot**, take over a chat and reply to the customer, then hand it back to the bot.
- **Staff** confirm bookings at `/appointments.html`. The bot creates and changes appointments on its own; staff press **Confirm**, and the customer is notified in the chat. The page refreshes automatically.

---

## 🧰 Testing multiple customers & booking slots

### Multiple customer tabs (one chat session per tab)
- `http://localhost:8000/?s=A`, `/?s=B`, ... each `s` value is a separate customer (id `khach_test_A`, ...). Reloading a tab keeps its session.
- `http://localhost:8000/multi.html` shows several customers side by side on one page ("+ Add customer" button). Open `/admin` in another tab to monitor them.
- The "+" button in the chat header and the car icon in `/admin` both open a new customer tab. Without `?s=`, there is one session per browser.

### Slots & capacity
On `/appointments.html` → "Slots & capacity" (only `manager` can edit):
- Set the *max bookings per slot*, *slot length (minutes)*, and opening/closing hours. Slots start from opening time, e.g. 08:00 + 90 min = 08:00-09:30, 09:30-11:00, ...
- `PENDING_APPROVAL` and `CONFIRMED` bookings both hold a slot. If a slot is full, the bot says so and suggests free times; if the customer already has a booking in that slot, it reports a conflict. Checks run on booking, rescheduling, and staff confirmation.
- Defaults come from `SLOT_CAPACITY` (maintenance/repair), `SHOWROOM_CAPACITY` (showroom visits and contract signing, counted separately), `SLOT_MINUTES`, `WORK_START`, `WORK_END`. Values set on the admin page (`app_settings` table) take precedence and apply only to future bookings and confirmations.
- API: `GET|PUT /admin/settings/booking`, `GET /admin/slots?date=YYYY-MM-DD`.

---

## 🧪 Tests

Tests need Postgres (they use a separate `garage_test` database, wiped on every run):
```bash
# Admin login lock, booking confirmation, slots / full capacity / conflicts
LLM_PROVIDER=none EMBEDDING_PROVIDER=none pytest tests/test_admin_security.py tests/test_booking_slots.py

# Agent trajectory eval
LLM_PROVIDER=none python -m eval.run_eval

# Retrieval quality
python -m eval.run_rag_eval
```
Some older tests are out of date; see **Known issues** in [GUIDE.md](GUIDE.md).

---

## 📚 Documentation

| File | What is inside |
|---|---|
| [GUIDE.md](GUIDE.md) | Start here: how the system works, the staff workflow, configuration, troubleshooting |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Architecture diagram mapped to code |
| [docs/RAG_GUIDE.md](docs/RAG_GUIDE.md) | Hybrid RAG (BM25 + pgvector) in detail |
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | User stories and the eval cases that cover them |
| [previous_work.md](previous_work.md) | Development log and open issues |
