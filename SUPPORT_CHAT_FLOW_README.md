# Support Chat Flow README

This document traces the current support-chat runtime flow end to end and lists every agent involved in that flow.

It is based on the code paths wired today, not on older agent files that still exist in the repo.

## Key entrypoints

- Frontend page: `frontend/src/pages/SupportChatPage.tsx`
- Frontend SSE client: `frontend/src/api/sse.ts`
- Support query API: `backend/app/api/routes/support.py`
- Main orchestration: `backend/app/support/orchestrator.py`
- Specialist registry: `backend/app/support/agents/registry.py`
- Post-click action API: `backend/app/api/routes/support_actions.py`

## One-line summary

The support chat flow is orchestrator-driven. Agents do not call each other directly. The orchestrator decides which agent runs next, whether a handoff happens, and whether post-processing agents run.

## Runtime call graph

```text
SupportChatPage
  -> streamSupport()
  -> POST /v1/support/query
  -> SupportOrchestrator.stream()
     -> classify_intent()
        -> intent_agent (unless smalltalk hard-rule matched)

     Branch A: unsupported language
       -> no further agent
       -> static bilingual rejection

     Branch B: smalltalk
       -> smalltalk_agent

     Branch C: off-topic
       -> support_off_topic_agent

     Branch D: capabilities
       -> no agent
       -> static capabilities reply

     Branch E: summarize current conversation
       -> summarize_agent

     Branch F: real support request
       -> route_query()
          -> router_agent (unless router hard-rule matched)
       -> FAQ cache check
          -> cache hit: no specialist agent
       -> auth gate
          -> guest blocked: no specialist agent
       -> retrieval + scope gate
          -> out-of-scope KB refusal: no specialist agent
       -> specialist agent
          -> optional orchestrator-mediated handoff
          -> support_faithfulness_agent
          -> support_followups_actions_agent
       -> proposal extraction
          -> interactive buttons
          -> POST /v1/support/action on click
          -> no agent on click, MCP write tool only
```

## Active agents in the support flow

| Agent                             | File                                              | Called by                                               | When it runs                                      | What happens next                                                                                  |
| --------------------------------- | ------------------------------------------------- | ------------------------------------------------------- | ------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `intent_agent`                    | `backend/app/agents/intent_agent.py`              | `classify_intent()` from `SupportOrchestrator.stream()` | First step for almost every turn                  | Branches into smalltalk, off-topic, capabilities, summarize, unsupported language, or real support |
| `smalltalk_agent`                 | `backend/app/agents/smalltalk_agent.py`           | `SupportOrchestrator._stream_cheap_reply()`             | If intent is `smalltalk`                          | Turn ends after cheap reply                                                                        |
| `support_off_topic_agent`         | `backend/app/support/agents/off_topic_agent.py`   | `SupportOrchestrator._stream_cheap_reply()`             | If intent is `off_topic`                          | Turn ends after cheap reply                                                                        |
| `summarize_agent`                 | `backend/app/agents/summarize_agent.py`           | `SupportOrchestrator._stream_summarize_reply()`         | If intent is `summarize`                          | Turn ends after summary                                                                            |
| `router_agent`                    | `backend/app/support/agents/router_agent.py`      | `route_query()` from `SupportOrchestrator.stream()`     | Only for real support turns after history load    | Chooses a specialist                                                                               |
| `technical_agent`                 | `backend/app/support/agents/technical_agent.py`   | `SupportOrchestrator.stream()` via registry             | Routed technical/service/device issues            | May hand off to billing via orchestrator regex                                                     |
| `billing_agent`                   | `backend/app/support/agents/billing_agent.py`     | `SupportOrchestrator.stream()` via registry             | Routed billing-info issues                        | May hand off to technical via orchestrator regex                                                   |
| `general_agent`                   | `backend/app/support/agents/general_agent.py`     | `SupportOrchestrator.stream()` via registry             | Catch-all support specialist                      | Can trigger orchestrator handoff if it names another specialist                                    |
| `outage_agent`                    | `backend/app/support/agents/outage_agent.py`      | `SupportOrchestrator.stream()` via registry             | Routed outage checks                              | Can structured-handoff to technical                                                                |
| `order_agent`                     | `backend/app/support/agents/order_agent.py`       | `SupportOrchestrator.stream()` via registry             | Order, catalog, shipping flows                    | Can emit order proposals for button clicks                                                         |
| `bill_pay_agent`                  | `backend/app/support/agents/bill_pay_agent.py`    | `SupportOrchestrator.stream()` via registry             | Payment and autopay flows                         | Can emit payment/autopay proposals for button clicks                                               |
| `appointment_agent`               | `backend/app/support/agents/appointment_agent.py` | `SupportOrchestrator.stream()` via registry             | Appointment scheduling flows                      | Can emit appointment proposals for button clicks                                                   |
| `support_faithfulness_agent`      | `backend/app/support/agents/validator_agent.py`   | `SupportOrchestrator.stream()`                          | After specialist reply, if response is verifiable | Runs in parallel with followups/actions agent                                                      |
| `support_followups_actions_agent` | `backend/app/support/agents/validator_agent.py`   | `SupportOrchestrator.stream()`                          | After specialist reply, if response is verifiable | Produces follow-up questions and action-link topics                                                |

## Agents that are in the repo but not on the active support-chat runtime path

| Agent/file                                                                                 | Status               | Notes                                                                                                                                                                                                        |
| ------------------------------------------------------------------------------------------ | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `support_agent` in `backend/app/support/agents/support_agent.py`                           | Not invoked directly | Legacy monolithic support agent from the pre-router phase. The file is still used for `SupportAgentDeps` and `build_user_message`, but the `support_agent` object is not called by the current orchestrator. |
| `support_followup_agent` in `backend/app/support/agents/followup_agent.py`                 | Unused               | Superseded by `support_followups_actions_agent` in `validator_agent.py`.                                                                                                                                     |
| `support_action_suggester_agent` in `backend/app/support/agents/action_suggester_agent.py` | Unused               | Its responsibilities were folded into `support_followups_actions_agent` in `validator_agent.py`.                                                                                                             |
| `support_faithfulness_agent` in `backend/app/support/agents/faithfulness_agent.py`         | Unused               | The live flow imports the different `support_faithfulness_agent` defined in `validator_agent.py`.                                                                                                            |

## Top-level support flow

### Frontend starts the stream

- `SupportChatPage` calls `streamSupport()` with the user query.
- `streamSupport()` posts to `/v1/support/query` and includes the support session cookie plus `X-Customer-Id`.
- The backend route resolves the customer, resolves or creates the conversation, instantiates `SupportOrchestrator`, and streams SSE events back.

### Intent and language classification

- `SupportOrchestrator.stream()` always starts with `classify_intent()`.
- `classify_intent()` first applies a hard-rule regex for obvious English smalltalk.
- If no hard rule matches, it runs `intent_agent`.
- That result controls the next branch:
  - `unsupported` language: static rejection, no more agents
  - `smalltalk`: `smalltalk_agent`
  - `off_topic`: `support_off_topic_agent`
  - `capabilities`: static reply, no more agents
  - `summarize`: `summarize_agent`
  - `support`: continue into router + specialist flow

### Support request routing

- The orchestrator loads conversation history and may compact it first.
- It then runs `route_query()`.
- `route_query()` first applies support-router hard rules:
  - bill pay keywords -> `bill_pay`
  - appointment keywords -> `appointment`
  - outage keywords -> `outage`
  - order keywords -> `order`
  - billing keywords -> `billing`
- If no hard rule matches, it runs `router_agent`.
- Low-confidence router results are forced to `general`.

### Pre-specialist exits

These branches end the turn before any specialist agent is called:

- FAQ cache hit: the cached answer is replayed and no specialist agent runs.
- Guest auth gate: for blocked guest flows, the server emits `AUTH_REQUIRED`.
- Retrieval failure: error path, no specialist reply.
- KB scope gate refusal: KB-grounded specialists can be stopped before generation if retrieval coverage is too weak.

### Specialist dispatch

- The orchestrator looks up the routed specialist in `SPECIALIST_REGISTRY`.
- It resolves the model slot for that specialist through `LLMClient.agent_model(...)`.
- It builds a single user message with:
  - response-language directive
  - customer header
  - rolling history summary
  - unresolved facts
  - recent turns
  - retrieved KB context
  - pre-fetched escalation contact
  - current customer question
- The specialist then runs with `SupportAgentDeps`.

## Specialist-by-specialist flow details

### `technical_agent`

- File: `backend/app/support/agents/technical_agent.py`
- Typical use: internet, TV, voice, mobile, devices, service problems.
- Registered tools:
  - `voice_get_details`
  - `mobile_get_details`
  - `internet_get_details`
  - `tv_get_details`
  - `list_devices`
  - `get_device`
  - `get_outage_for_customer`
  - `get_recent_tickets`
- Handoff behavior:
  - The prompt tells it to point billing-like issues to the billing specialist.
  - The orchestrator then detects phrases like `billing specialist` and reroutes.

### `billing_agent`

- File: `backend/app/support/agents/billing_agent.py`
- Typical use: invoices, charges, balances, billing explanations.
- Registered tools:
  - `billing_get_invoice`
  - `billing_list_charges`
  - `billing_get_balance`
- Handoff behavior:
  - The prompt tells it to point service-quality, outage, or device issues to the technical specialist.
  - The orchestrator detects that wording and reroutes.

### `general_agent`

- File: `backend/app/support/agents/general_agent.py`
- Typical use: catch-all support questions, capabilities-ish support, general account/service info.
- Registered tools:
  - `get_recent_tickets`
- Handoff behavior:
  - It does not directly call another agent.
  - If it explicitly says another specialist should handle the issue, the orchestrator can reroute.

### `outage_agent`

- File: `backend/app/support/agents/outage_agent.py`
- Typical use: `is there an outage` or area-wide status checks.
- Registered tools:
  - `area_status_for_customer`
  - `area_status_by_zip`
  - `scheduled_maintenance`
- Special behavior:
  - This is the only specialist with structured output.
  - It returns `reply`, `handoff_to`, and `reason`.
  - If there is no outage and the issue looks like individual troubleshooting, it sets `handoff_to = "technical"`.
  - The orchestrator reads that field and reroutes to `technical_agent`.

### `order_agent`

- File: `backend/app/support/agents/order_agent.py`
- Typical use: browse catalog, quote items, order status, shipment tracking, order cancel.
- Registered tools:
  - `list_catalog`
  - `quote`
  - `list_orders`
  - `order_status`
  - `shipment_status`
  - `list_payment_methods`
  - `propose_place_order`
  - `propose_cancel_order`
- Runtime flow for placing an order:
  1. Browse catalog if needed.
  2. Quote selected SKU(s).
  3. List payment methods.
  4. Reply with summary.
  5. Emit a proposal, not a real write.
- Important:
  - The actual order is not placed by the agent.
  - The proposal becomes an interactive button.
  - `/v1/support/action` performs the real MCP write only after the customer clicks.

### `bill_pay_agent`

- File: `backend/app/support/agents/bill_pay_agent.py`
- Typical use: pay balance, enroll in autopay, choose payment method.
- Registered tools:
  - `get_balance`
  - `list_payment_methods`
  - `propose_payment`
  - `propose_autopay`
- Runtime flow:
  1. Get exact balance.
  2. List payment methods.
  3. Confirm amount and method in text.
  4. Emit a proposal button.
- Important:
  - The payment is not executed by the agent.
  - The click to `/v1/support/action` triggers the actual MCP payment write.

### `appointment_agent`

- File: `backend/app/support/agents/appointment_agent.py`
- Typical use: schedule, cancel, or reschedule appointments.
- Registered tools:
  - `list_slots`
  - `propose_book_appointment`
  - `propose_cancel_appointment`
  - `propose_reschedule_appointment`
- Runtime flow:
  1. Clarify appointment type if needed.
  2. Use zip code and fetch slots.
  3. Show options.
  4. Emit a booking/cancel/reschedule proposal button.
- Important:
  - The actual appointment mutation happens only after click through `/v1/support/action`.

## Handoff rules

### Important architectural rule

No agent directly invokes another agent. Every handoff is done by `SupportOrchestrator`.

### Handoff sources

- Structured handoff:
  - `outage_agent -> technical_agent`
- Regex-detected handoff:
  - After any non-structured specialist reply, the orchestrator scans the text for phrases like:
    - `<target> specialist`
    - `handled by <target>`
    - `transfer to <target>`
    - `<target> team`

### Practical handoff edges in current prompts

- `technical_agent -> billing_agent`
- `billing_agent -> technical_agent`
- `general_agent -> any dedicated specialist` if it phrases the reply that way
- `outage_agent -> technical_agent` through structured output

### Handoff limit

- The orchestrator allows only one specialist-to-specialist hop.
- If a second handoff would be needed, it stops routing and appends a human escalation note instead.

## Validation and follow-up stage

After the final specialist draft is produced:

- `support_faithfulness_agent` checks whether the answer is grounded in:
  - retrieved KB chunks
  - tool outputs
  - conversation history
- `support_followups_actions_agent` runs in parallel to generate:
  - follow-up questions
  - action-link topics from the fixed catalog

### Failure behavior

- If the response is not considered verifiable, faithfulness is skipped.
- If a KB-grounded specialist fails faithfulness, the orchestrator replaces the answer with a canned KB refusal.
- If a tool-driven specialist fails faithfulness, the orchestrator keeps the draft and records the failure signal, because the response may still be grounded in live tool output even if the checker was too strict.

## Interactive action flow

This is part of the support chat flow, but it is not agent-to-agent.

### Proposal stage

- `order_agent`, `bill_pay_agent`, and `appointment_agent` can call `propose_*` tools.
- Those proposal tools only append structured proposal data into `deps.tool_outputs`.
- The orchestrator extracts proposals, stores them in `ActionRegistry`, and emits `INTERACTIVE_ACTIONS`.

### Click stage

- The frontend button component posts `action_id` to `/v1/support/action`.
- `/v1/support/action`:
  - verifies session ownership
  - maps proposal kind to the real MCP write tool
  - runs the write tool
  - stores an `action_result` message in the transcript
- No LLM agent runs on action click.

## Non-agent branches worth knowing

These are part of the real support-chat flow but do not run an agent at that stage:

- Unsupported language reply
- Capabilities reply
- FAQ cache replay
- Guest `AUTH_REQUIRED` reply
- KB out-of-scope refusal
- Action-click execution

## Model slot mapping

The support flow uses named model slots through `LLMClient` in `backend/app/llm/client.py`.

| Agent/stage                       | Model slot                                                                             |
| --------------------------------- | -------------------------------------------------------------------------------------- |
| `intent_agent`                    | `intent`                                                                               |
| `smalltalk_agent`                 | `smalltalk`                                                                            |
| `support_off_topic_agent`         | `smalltalk`                                                                            |
| `summarize_agent`                 | `summary`                                                                              |
| `router_agent`                    | `router`                                                                               |
| `technical_agent`                 | `technical`                                                                            |
| `billing_agent`                   | `billing`                                                                              |
| `general_agent`                   | `general`                                                                              |
| `outage_agent`                    | `outage`                                                                               |
| `order_agent`                     | `order`                                                                                |
| `bill_pay_agent`                  | `bill_pay`                                                                             |
| `appointment_agent`               | `appointment`                                                                          |
| `support_faithfulness_agent`      | specialist-specific: usually `followup`, but `billing` and `bill_pay` use `generation` |
| `support_followups_actions_agent` | `followup`                                                                             |

## Final answer to "which agents get called to which?"

In the live code, the call sequence is:

1. `intent_agent`
2. One of:
   - `smalltalk_agent`
   - `support_off_topic_agent`
   - `summarize_agent`
   - no further agent for unsupported-language or capabilities
   - `router_agent` for real support
3. One routed specialist:
   - `technical_agent`
   - `billing_agent`
   - `general_agent`
   - `outage_agent`
   - `order_agent`
   - `bill_pay_agent`
   - `appointment_agent`
4. Optional single orchestrator-mediated handoff to another specialist
5. `support_faithfulness_agent` and `support_followups_actions_agent` in parallel
6. No agent on interactive action click; only the action endpoint and MCP write tool run

The most important architectural point is that the specialists do not call each other directly. The orchestrator is the only component that decides and performs agent-to-agent transitions.
