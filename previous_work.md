# previous_work.md

## 2026-10-30 — Pylance warnings (human_handover, appointment) + duplicate pronoun code
- human_handover: `state["session_id"]`/`state["user_text"]` -> `state.get(..., "")` (AgentState is total=False).
- appointment: `waiting()` now closes over a non-None workflow; the missing-slot branch also checks `not phone`, so `create_appointment` always gets a `str`.
- Duplicates removed: four identical `_get_pronouns` copies (appointment, compose, contract, conversation) and three copies of the pronoun-instruction if/else -> `state.pronouns`, `state.pronoun_vars`, `state.pronoun_instruction`. Appointment's repeated REQUIRED/CONFIRM/BOOKING_FAILED formatting -> `_required()` + `TEMPLATE.format(**p)`. Removed unused `conversation._format_fallback` and a debug `print` in compose.
- Verification: Pylance clean for both files; graph imports. Live LLM session `livetest-refactor-1793377826` (maintenance booking, anh/em pronouns) created appointment MAINTENANCE / PENDING_APPROVAL. Test suite not run (user request).
- Not addressed: Pylance error in `router.py:91` (dict assigned to AgentState).

## 2026-10-30 — Only full customer names are saved to the DB
- Issue: a given name alone ("Khang") was written to `customers.full_name`.
- Fix: `identity.is_full_name` (at least two words). `sync_contact` saves a name only if it is a full name, and a full name may replace a stored short name. The contract workflow treats a short name as missing and asks for "Họ và tên đầy đủ". In preprocess, completing a short name with a full name is no longer discarded as an overwrite. The short name stays in session facts, where it is used for addressing the customer.
- Data: cleared the artifact `customers.id=1 full_name='Khang'` in dev DB `garage`.
- Live LLM via /chat, session `livetest-name-1793377027`: "Mình tên Khang, sđt ..." -> DB full_name NULL; contract asked for the full name; "Họ tên đầy đủ của mình là Dương Định Khang" -> DB full_name 'Dương Định Khang', contract summary correct. Test suite not run (user request).

## 2026-10-30 — Removed test drive (lái thử) service
- Issue: bot offered/booked "lái thử", but the garage has no test-drive service.
- Changes: dropped `ServiceType.TEST_DRIVE` (`app/contracts/schemas.py`) and `TEST_DRIVE` from `SLOT_TYPES`/`SERVICE_TYPES` (`app/db/models.py`, `docs/schema.dbml`); removed lái thử from prompts/templates in `preprocess.py`, `appointment.py`, `conversation.py`, `compose.py`, `input.py`, and added explicit "no test-drive service" lines to the preprocess, conversation and compose prompts. An LLM-returned `TEST_DRIVE` is now rejected by `preprocess._canonical_value`.
- Migration `b2d4f6a8c0e1_remove_test_drive.py` narrows the two check constraints; applied to dev DB `garage` (no TEST_DRIVE rows existed). Not yet applied to `garage_test`.
- Tests/eval: test-drive phrasing replaced with showroom-visit/repair phrasing; added `test_test_drive_is_not_a_service_type`.
- Verification: import check + `_canonical_value` assertion passed. Test suite and live-LLM run skipped at user request. `tests/test_preprocess_canonical.py` and `tests/test_conversation_interpretation.py` already failed to import before this change (`validate_interpretation` no longer exists in `preprocess.py`).
- Also installed missing `pgvector` into `.venv` (listed in requirements.txt).

## Last updated
2026-10-27 — Design-conformance check + live web/LLM bug hunt (investigation only, no code changes)

## Current objective
Check the agent graph against the target flowchart (Input → Preprocess → Policy Guard → Router/Coordinator → specialists → Validation → Composer → Output Guard → Respond → Save) and find bugs via a live LLM session on the web endpoint with the `garage` DB.

## Repository state
- Branch `testing`, working tree clean at start. No source files changed this session.
- New untracked: `trace.pre-web-session.json` (copy of trace.json before this run), this file.

## Relevant architecture (confirmed)
- Graph: `app/agent/graph.py`; runner `app/agent/runner.py`; DB persistence `app/conversation.py::handle_message`.
- Nodes: `app/agent/nodes/{input,preprocess,policy_guard,router,coordinator,validation,compose,output_guard,human_handover,respond}.py` + specialists `sales, knowledge, after_sales, appointment, contract, conversation`.
- Trace: `preprocess._write_preprocess_trace` overwrites `trace.json` (repo root) every turn; preprocess output only.
- Workflow state lives in LangGraph `MemorySaver` (in-process); only messages/session/customer go to Postgres.
- Web: `docker start garage-pg-dev garage-web-dev`; UI at http://localhost:8000, API `POST /chat`.

## Design conformance
Matches the flowchart node-for-node, including validation → preprocess retry (max 1) and output_guard → human_handover. Gap: "Save Workflow State" is not durable (MemorySaver; TODO in graph.py) — a uvicorn `--reload` or restart loses active workflow/pending/known_facts while messages remain.

## Live-LLM session
- Ran via web API, real configured OpenAI model, DB `garage`, session `livetest-1793062970`, 8 turns.
- Per-turn traces saved outside repo: `/tmp/live1/trace{1..8}.json`, replies `/tmp/live1/resp{N}.json`.
- Scenario: greeting → search 2-ton truck < 500M → "xe đầu tiên thông số" → book test drive Sat morning → phone "12345" → "hủy lịch + hỏi bảo dưỡng" → re-ask maintenance → "đặt lịch bảo dưỡng".
- DB after run: 16 new messages (with node traces), session BOT mode, 0 appointments, customers unchanged (1). No side effects.

## Session 5 (2026-10-31) — sales tool merge, spec routing, profile without DB, uncommitted
- Redundant budget tool: `search_by_budget` tool + `ProductService.search_by_budget/count_by_budget` removed. `ProductService.search(query, limit, fallback, max_budget_vnd)` now filters by name words and/or budget; budget is a hard cap (fallback never exceeds it; budget-only results sorted closest-to-budget first). Sales tool `search_products` got optional `max_budget_vnd`; `query` no longer required. Interface updated.
- Spec questions misrouted to KNOWLEDGE: root cause was preprocess had no action for catalog specs (only `product_detail` = "previously displayed product") and `answer_knowledge` = "technical questions". Added action `product_specs` (SALES) in `input.AVAILABLE_ACTIONS`, narrowed `answer_knowledge` description, preprocess prompt rule: specs/price of a catalog model -> SALES product_specs. Sales prompt: answer only from `specs`, say data missing when spec absent.
- Conversation node: `_get_customer_profile` (2 DB queries per message) replaced by `_customer_profile(state)` reading `known_facts` customer_name/customer_phone from the checkpoint. customers rows are only written from these same facts (`handle_message` -> `sync_contact`), so the data is the same.
- Env: dev DB container was stopped (started `docker compose -f docker-compose.dev.yml up -d db`); `.venv` lacked pinned `langgraph-checkpoint-postgres==3.2.0` -> installed (pulled langgraph-checkpoint 4.3.1).
- Live LLM via /chat (port 8077), DB garage: `livetest-sales-1793412217`, output /tmp/live5/out.txt. Engine of FI170 -> sales, correct spec; "hộp số của nó" -> sales, correct; 400tr budget -> 3 cars <=400tr; size of cargo box -> "chưa có dữ liệu"; name recall from known_facts correct.
- Not run: pytest (per user). tests/test_agent_boundaries.py still mocks `search_by_budget` (will fail).
- Observed, not fixed: "chassis dưới 300 triệu" returns non-chassis (THACO TF230 thùng lửng) because "chassis" is in GENERIC_WORDS so the query becomes budget-only. Greeting "Chào shop, anh tên ..." replied with bạn/mình (pronoun not detected on greeting turn).

## Session 5b (2026-10-31) — profile without DB in node, booking recall
- conversation node: `_get_customer_profile` (DB per message) actually removed this time (earlier edit had been lost on disk). `_customer_profile(state)` reads known_facts, then `state.customer_profile` filled by `handle_message` via `identity.customer_profile` in its existing DB session; `run_turn(..., profile)`.
- Bug: after booking, "nhắc lại giúp tôi xem xe gì" re-asked service type. Causes: (1) appointment clears booking facts after booking and the node had no path to answer from the stored request -> fell into missing-slot path; (2) vehicle never recorded: no car_model fact, appointment task had no entity_id; (3) preprocess sometimes skipped service_type for "xem xe trực tiếp" when a time was in the same sentence.
- Fixes: `appointment._booked_recap` answers from `active_workflow.collected_facts` (now includes `vehicle`) when a request is stored and no new booking slots arrive; new service/time after a stored request starts a separate workflow. New state `focused_product` set by sales (referenced product, or the single product whose full name the reply mentions); `_booking_detail` falls back to it; booking reply and note include the vehicle. Preprocess prompt: APPOINTMENT with a stated type must emit service_type ("xem xe trực tiếp", "đến showroom" -> SHOWROOM_VISIT).
- Live: livetest-recap-1793414829 (/tmp/live5/recap3.txt): booking created `appointments.id=5` SHOWROOM_VISIT, note "Xe: FUSO Canter FI170 (Thùng mui bạt)"; recall turn answered from stored request with vehicle. Earlier failed replays livetest-recap-1793414323/1793414549 created no appointment.
- Observed, not fixed: name correction after booking ("À thật ra tôi tên Phan Huy Anh") went to conversation, which asked "cập nhật lại lịch?" instead of the appointment contact-update reply. Turn "Tôi tên ..., số điện thoại ..." was routed to contract (start_contract) and asked for address/price.
