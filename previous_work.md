# previous_work.md

## 2026-11-02 — Single-task replies skip compose; compose only reorganises messy multi-task replies
- Issue: a greeting took 6–8 s: preprocess LLM + conversation LLM + a compose LLM rewrite, triggered whenever the specialist's reply contained a single `*` (markdown check in `_needs_composition`).
- Changes: `state.answer()` now cleans every specialist reply (`clean_reply`: markdown -> plain text, `price_as_of` field name -> "giá áp dụng từ ngày", leading greeting stripped unless the customer greeted); `strip_greeting`/`MARKDOWN` moved from compose to state. Compose: one answer is sent as-is; several answers are joined, and the LLM runs only when `_messy` (more than one part ends with a question, or a line repeats across parts). STYLE_RULES (used by all specialists) now require plain text and no internal field names; sales prompt no longer says "price_as_of".
- Live LLM (gpt-5.4-nano, DB `garage`, sessions `trace4-*`, script `/tmp/trace_run/greet.py`): compose made 0 LLM calls for greeting, catalog search, maintenance question and a sales+appointment turn; greeting total 6.4–6.7 s (was 8.2 s; preprocess first call ~4 s is cold start), replies plain text. Test suite not run (user request).
- Not addressed: `_messy` LLM path was not exercised in the live run.

## 2026-11-02 — Running summary + recent window, preprocess prompt cleanup, policy guard gating
- Issue: every LLM call got the full session history (`conversation._history` has no limit); preprocess sent canonical_slots/available_actions/empty state as a JSON dump each turn; policy guard called the LLM for any HIGH complexity (a multi-intent "search + book" turn was handed to a human as COMPLEXITY_HIGH).
- Changes: new `app/agent/memory.py` (`windowed_history`): input node keeps the last `HISTORY_WINDOW_MESSAGES` (6, up to +`HISTORY_SUMMARY_BATCH` 4) verbatim and folds older messages into `conversation_summary` (checkpointed, with `summarized_messages` cursor). The summary goes first in `state["history"]` as `{"role": "summary"}`; `llm._past` sends it as a system message (all nodes). `state["turn"]` = full history length; `appointment._turn` uses it (confirmation turn matching no longer depends on the window). Preprocess: static `SYSTEM_PROMPT` (slots/actions as text lines, built once), dynamic state JSON only with non-empty parts and fact values; prompt says multi-request/greeting/price turns are complexity LOW. Policy guard: wants_human handed over in code; LLM only for complaint, safety_risk, or HIGH complexity on KNOWLEDGE/MAINTENANCE tasks.
- Live LLM (gpt-5.4-nano, DB `garage`, script `/tmp/trace_run/run.py`, results `/tmp/trace_run/results2.json`, last trace `/tmp/trace_run/trace2_last_turn.json`): fresh single/multi/greeting -> policy guard 0 LLM calls; reused session `trace-long-ad8f5d` (6 more turns) -> history sent = summary + 6–10 msgs, summary calls on turns 1 and 4 (2.8 s, 4.1 s), phone from an old turn recalled via summary, brake turn -> SAFETY handover via LLM. Test suite not run (user request).
- Not addressed: the summary LLM call runs synchronously in the input node (adds ~3–4 s every second turn); an old bot reply (msg 16) claimed a booking that does not exist in `appointments`, and the summary carried that claim forward.

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

## Session 6 (2026-11-01) — Appointment agent rewritten as tool-calling agent (create/reschedule/cancel/details)
- Design: Appointment Agent -> LLM picks one of 4 tools (`create_appointment`, `reschedule_appointment`, `cancel_appointment`, `get_appointment_details`) -> `DbBookingService` (Postgres) -> tool result -> reply. Old slot/confirm/recap/contact-update branches removed.
- Rules in code, not prompt: writes need all required slots; a write only runs when the customer accepts (ACCEPT_OFFER/accepts_offer) the CONFIRMATION opened on the immediately previous turn, with the same action and args (stored in pending metadata `action/args/turn`). Otherwise the tool returns NEEDS_CONFIRMATION or MISSING. Reschedule/cancel only touch the session's own PENDING_APPROVAL/CONFIRMED rows. Each write runs at most once per turn.
- Service: `list_appointments`, `reschedule_appointment` (rewrites note time, releases slot, back to PENDING_APPROVAL), `cancel_appointment` (CANCELLED, releases slot); row lock + commit. New `AppointmentInfo` schema; protocol in interfaces.py updated.
- Routing: new actions `reschedule_appointment`, `cancel_appointment`, `appointment_details` (input.py); preprocess prompt maps "hủy lịch hẹn của tôi" to cancel_appointment, not CANCEL_TASK; router sends these actions to appointment and excludes them from the generic cancel path.
- Live LLM via /chat (port 8077), DB garage, session in /tmp/appt_live/sid (`livetest-appt-...`), output /tmp/appt_live/part{1..4}.txt: booking -> confirm -> appointments.id=6 MAINTENANCE PENDING_APPROVAL; "lúc mấy giờ" and "đã xác nhận chưa" answered from DB (pending, no fixed hour); "đổi sang ngày mai" -> confirm -> note became "ngày mai" (committed); "hủy" -> confirm -> status CANCELLED (committed).
- Issue found and fixed during run: the LLM kept asking for an hour/session ("sáng hay chiều?") instead of calling reschedule; prompt rule 7 added (a date alone is enough).
- Not run: pytest (per user). Tests that mock the old appointment node/booking interface may fail.

## Session 6b (2026-11-01) — Mixed pronouns + booking without full name
- Reported session `e6288691-dfeb-4d4a-bda3-438ae2323468`: customer wrote "Tôi ..." but the reply used "Mình ... Chị ... em"; booking created `appointments.id=1` with customers.full_name NULL.
- Root cause (pronoun): checkpoint history shows preprocess returned customer_pronoun='chị' for "Tôi muốn sửa chữa xe ..." (the word "chị" never appeared); preprocess stored it and it stuck for the session. Fix: preprocess drops a detected anh/chị that is not a word in the current message. Appointment prompt rule 9: one pronoun pair only.
- Root cause (name): appointment REQUIRED had no customer_name. Fix: `customer_name` required and must pass `identity.is_full_name`; confirmation proposal now includes name and phone. Prompt rule 8: no extra questions (vehicle/plate) after a successful action.
- Live replay `livetest-name-...` (/tmp/appt_live/name{1,2}.txt): all replies bạn/mình; bot asked for full name, "Đúng rồi" without a name did not book; after "Tên tôi là Dương Định Khang" + confirm -> appointments.id=2 REPAIR PENDING_APPROVAL, customers.full_name 'Dương Định Khang'.
- Left in DB: appointments.id=1 of the reported session (created before the fix); its checkpoint still has customer_pronoun='chị'.

## Session 7 (2026-11-03) — Invented showroom date, blind "muốn mua" search, preprocess cleanup (uncommitted)
- Reported session `e6288691-dfeb-4d4a-bda3-438ae2323468` (msgs 31-32, 93-94).
- Root cause (date): preprocess correctly gave only service_type for "đặt lịch để đến xem trực tiếp" (no appointment_when). The appointment tool schema made `appointment_date` required and prompt rule 7 said "quy đổi ... rồi gọi công cụ ngay", so the LLM invented today's date and stated it in the reply (create() returned MISSING but the reply already had the date). Fix: `appointment_date` optional; context line "Thời gian khách đã nêu"; rule 7 forbids picking/mentioning a date the customer did not give; create() puts "ngày giờ hẹn" first in `missing` when appointment_when is absent.
- Root cause (purchase): no action existed for "tôi muốn mua X" -> preprocess mapped it to SALES product_detail, and sales re-listed specs. Fix: new action `purchase_intent` (input.AVAILABLE_ACTIONS + preprocess rule); sales adds PURCHASE_WITH_PRODUCT (confirm vehicle, ask next step: hợp đồng / xem xe / tư vấn) or PURCHASE_UNRESOLVED (ask which model, no search). Preprocess strips a domain prefix from task.action (model returned "SALES purchase_intent").
- Also fixed: output_guard compared amounts as strings, so "809.000.000 VNĐ" vs allowed "809.000.000 đồng" caused false handovers on catalog replies; now compares digits.
- Cleanup: preprocess builds the state JSON directly (removed `input.build_context` / ConversationContext round-trip); pronoun block reduced to: accept anh/chị only if typed in the message, else leave stored pair.
- Live LLM via web :8000, DB garage: livetest-visit-*, livetest-buy-*, livetest-buy2-*, livetest-final-* (outputs /tmp/kiro_dbg/*.txt). Showroom without time -> asks for date, no date invented; "Chiều thứ 7 tuần này" -> 2026-11-07. "muốn mua mẫu 2"/"muốn mua xe" -> confirms FI170L + asks next step. No appointment created (no phone given).
- Not run: pytest (per user). ConversationContext schema is now unused by app code.

## Session 7b (2026-11-03) — Tiered preprocessing inside the preprocess node (uncommitted)
- Graph unchanged (same nodes); tiers live in `preprocess.py::_interpret`: tier 0 rules (pure greeting; yes/no to an open CONFIRMATION/OFFER; bare phone to an open writer QUESTION), tier 1 small structured call `ShortReplyInterpretation` (short reply, <=12 words, no "?", to an open appointment/contract interaction; returns None on needs_full_analysis), tier 2 the full call. Validation retry always uses tier 2. All tiers go through `_apply_interpretation`. trace.json now has "tier".
- `ConversationContext` removed from schemas.py (no users left); `ShortReplyInterpretation` added.
- Appointment: INVALID_DATE tells the agent to convert the stated time itself (it was asking the customer for a YYYY-MM-DD date); reply that names a date when the customer gave none and no lookup ran falls back to the code reply.
- Sales: system prompt now formats the pronoun pair (STYLE_RULES placeholder was never filled before); purchase_intent instruction skipped when the message states search criteria without a resolved product.
- Live: livetest-tiers3-* (/tmp/kiro_dbg/tiers3.txt): greeting tier 0, need/search tier 2, "mua mẫu 2" asks next step, showroom without time asks for time (no invented date), "Chiều thứ 7 tuần này" tier 1 -> 07/11/2026, phone tier 0, "Đúng rồi" tier 0 -> appointment created PENDING_APPROVAL, status lookup from DB.
- Not run: pytest (per user).
