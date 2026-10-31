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

## Findings (confirmed with trace + code)
1. Invalid phone accepted as a slot. `preprocess._canonical_value` has no `customer_phone` branch, so "12345" is stored. T5 then asked the user to confirm booking; validation happens only after confirmation in `appointment.py`. T8 the LLM echoed "12345" back.
2. Cancel drops sibling tasks. `router._cancel` returns a single conversation plan, discarding the MAINTENANCE task in T6. Reply claimed it had no maintenance data and offered staff, while T7 (same question alone) answered from RAG.
3. Invalid phone survives cancel. `_cancel` clears only `BOOKING_FACTS`; the bad phone carried into the next booking (T8).
4. Web UI renders raw markdown. `web/index.html` uses `textContent`; LLM replies contain `**bold**` and lists.
5. Minor: test-drive vehicle (entity 37) is not passed to `BookingService.create_appointment`, so staff won't see which car. T3 shows `price_as_of` raw key text in reply.

## Session 2 (2026-10-27) — fixes applied, uncommitted
- `identity.sync_contact`: customers row follows latest phone/name; corrections overwrite unverified data; never renames a pre-existing other customer; verified phone never changed by chat. Used by `conversation.handle_message`.
- `preprocess`: phone validated at extraction (`normalize_phone`); rejected keys -> `rejected_slots` (state, reset per turn in runner); explicit CORRECT on name allowed; prompt: keep vague `appointment_when`, `deal_change` signal.
- `appointment`: tells customer the phone is invalid; booking note includes vehicle/service detail (`_booking_detail`; `BookingService.create_appointment(detail=)`).
- `router._cancel`: cancel no longer discards other tasks in the same message.
- `contract`: keyword ESCALATE list replaced by LLM signal `signals.deal_change` (schemas.PolicySignals).
- `web/index.html`: escaped light-markdown rendering.
- Live replays: `livetest-fix-1793065159` (/tmp/live2), `livetest-fix2-1793065508` (/tmp/live3). Invalid phone, cancel+question, name/phone correction -> DB all confirmed fixed.

### SIDE EFFECT (needs user decision)
Replay T4 sent all booking fields in one message; existing code books immediately on an explicit request, so a real row was written: `appointments.id=1` (PENDING_APPROVAL, session livetest-fix2-1793065508). Test customers `customers.id` 2 and 3 were also created. Not deleted — awaiting user permission.

### New bugs found in replay (not fixed yet)
- After a PENDING_APPROVAL booking, booking facts are not cleared and workflow stays WAITING_FOR_APPROVAL; a later phone/name correction re-enters appointment and asks to confirm again -> would create a duplicate booking; the existing appointment's `contact_phone` keeps the old number.
- Reply after booking was only "Lịch hẹn đang chờ nhân viên xác nhận." (service/time missing).
- Policy question: explicit request with all fields books without a confirmation turn.

## Remaining work / next actions
- Proposed fixes awaiting user approval: validate phone in `_canonical_value` via `identity.normalize_phone`; make `_cancel` keep non-cancelled tasks in the plan; render safe markdown (escape then format) in web UI; pass product to booking note.
- After fixing: replay the same 8-turn script in a fresh session, compare traces.

## Safety notes
- Never reset `garage`. Test bookings would write `appointments` rows (PENDING_APPROVAL) if a user confirms — avoid confirming with a valid phone in live tests, or mock `BookingService`.

## Session 3 (2026-10-28) — node cleanup + durable checkpoint, uncommitted
- Design check: graph matches the flowchart node-for-node; each node has its own file. Gap fixed: "Save Workflow State" now durable.
- `nodes/common.py` removed: `answer` + new shared `fact_value`/`open_pending` -> `agent/state.py`; `STYLE_RULES`/`strip_greeting` -> `nodes/compose.py` (Response Composer); `step()` replaced by literal `["<node>"]` trace entries.
- `appointment.py` rewritten 229 -> 161 lines, same logic (merged ASK/FIELD_LABELS into REQUIRED, `reply()`/`waiting()` helpers, phone check folded into missing-slot path).
- `contract.py` 170 -> 79 lines: dropped re-application of slot_updates (preprocess already writes known_facts) and duplicate pending helpers.
- Duplicate fact/pending helpers removed from knowledge, sales, conversation, human_handover.
- `graph.py`: PostgresSaver in the app DB (MemorySaver for non-Postgres URLs). Added `langgraph-checkpoint-postgres==3.2.0` (pulled langgraph-checkpoint 4.3.1, psycopg-pool 3.3.3).
- Live LLM via /chat, DB garage: `livetest-arch-1793151923` (8 turns: greet, search, spec, test-drive booking, invalid phone, valid phone, confirm, phone correction) all correct; replies in /tmp/live4. Created `appointments.id=2` (PENDING_APPROVAL, phone corrected to new number, note has vehicle). `livetest-restart-1793152085`: booking state survived a server kill/restart.
- Not run: pytest suite (per user instruction).

## Session 4 (2026-10-28) — preprocess cleanup + LLM compose agent, uncommitted
- preprocess: removed rule-based name parser (`_valid_name`, `_NON_NAME_WORDS`, `_PLACE_NAMES`); the LLM now writes `customer_name` in proper Vietnamese form (slot description + prompt rule), value checked by evidence only (`customer_name` in `_NORMALISED_VALUE_KEYS`). Removed `validate_interpretation` wrapper, optional `rejected`, unused `complexity` state key.
- schemas: dropped unused fields `depends_on`, `clarification_question`, `unresolved_ambiguities`, `answers_pending_question`, `referenced_product_name`, `product_reference_ambiguous`, `catalog_request` (no readers). Tests in tests/ still reference some (not run, per user).
- compose: LLM agent with `submit_reply` tool; code rejects markdown, new numbers, FORBIDDEN wording, unexecuted booking/contract claims; fallback = joined answers with markdown stripped. Single plain answer skips the LLM. Output Guard unchanged after it.
- graph: checkpoint serializer allowlists schema types (LangGraph warned it will block unregistered types).
- Live: livetest-name1-1793153383 ("dương định khang" -> Dương Định Khang, saved to customers; multi-part reply plain text; booking appointments.id=3), livetest-name2-1793153383 (given name "Khang" then correction -> Trần Văn Nam USER_CORRECTED; no customer row since no phone).

## Session 5 (2026-10-31) — sales tool merge, spec routing, profile without DB, uncommitted
- Redundant budget tool: `search_by_budget` tool + `ProductService.search_by_budget/count_by_budget` removed. `ProductService.search(query, limit, fallback, max_budget_vnd)` now filters by name words and/or budget; budget is a hard cap (fallback never exceeds it; budget-only results sorted closest-to-budget first). Sales tool `search_products` got optional `max_budget_vnd`; `query` no longer required. Interface updated.
- Spec questions misrouted to KNOWLEDGE: root cause was preprocess had no action for catalog specs (only `product_detail` = "previously displayed product") and `answer_knowledge` = "technical questions". Added action `product_specs` (SALES) in `input.AVAILABLE_ACTIONS`, narrowed `answer_knowledge` description, preprocess prompt rule: specs/price of a catalog model -> SALES product_specs. Sales prompt: answer only from `specs`, say data missing when spec absent.
- Conversation node: `_get_customer_profile` (2 DB queries per message) replaced by `_customer_profile(state)` reading `known_facts` customer_name/customer_phone from the checkpoint. customers rows are only written from these same facts (`handle_message` -> `sync_contact`), so the data is the same.
- Env: dev DB container was stopped (started `docker compose -f docker-compose.dev.yml up -d db`); `.venv` lacked pinned `langgraph-checkpoint-postgres==3.2.0` -> installed (pulled langgraph-checkpoint 4.3.1).
- Live LLM via /chat (port 8077), DB garage: `livetest-sales-1793412217`, output /tmp/live5/out.txt. Engine of FI170 -> sales, correct spec; "hộp số của nó" -> sales, correct; 400tr budget -> 3 cars <=400tr; size of cargo box -> "chưa có dữ liệu"; name recall from known_facts correct.
- Not run: pytest (per user). tests/test_agent_boundaries.py still mocks `search_by_budget` (will fail).
- Observed, not fixed: "chassis dưới 300 triệu" returns non-chassis (THACO TF230 thùng lửng) because "chassis" is in GENERIC_WORDS so the query becomes budget-only. Greeting "Chào shop, anh tên ..." replied with bạn/mình (pronoun not detected on greeting turn).
