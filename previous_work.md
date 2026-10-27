# previous_work.md

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
