# 🚗 Personalized Chatbot for Automobile Garage Management

An AI-powered multi-agent chatbot system designed to automate customer support and operational workflows for automobile garages. It handles car sales inquiries, technical maintenance Q&A (via Hybrid RAG), multi-step appointment scheduling, policy enforcement, and seamless human handover.

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
- **Interactive API Docs (Swagger)**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **PostgreSQL Database**: `localhost:5432` (`garage` / `garage`)

#### Production Mode (Background daemon)
Runs Uvicorn with multiple workers in detached mode:
```bash
# Using Make:
make prod

# Or using Docker Compose directly:
docker compose -f docker-compose.prod.yml up -d --build
```

---

### Useful Management Commands

| Action | Make Command | Docker Compose Equivalent |
|---|---|---|
| **View logs** | `make logs` | `docker compose -f docker-compose.prod.yml logs -f` |
| **Stop containers** | `make down` | `docker compose -f docker-compose.dev.yml down`<br>`docker compose -f docker-compose.prod.yml down` |
| **Reset / Clean all** | `make clean` | `docker compose -f docker-compose.dev.yml down -v`<br>`docker compose -f docker-compose.prod.yml down -v` |
