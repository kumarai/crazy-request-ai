# Support Chat — End-to-End Flow

Reverse-engineered from code. Covers every path a turn can take, every
agent/tool that runs, and every SSE event the frontend consumes.

Source-of-truth files this is based on:

- Frontend page: `frontend/src/pages/SupportChatPage.tsx`
- SSE client: `frontend/src/api/sse.ts`
- Transcript renderer: `frontend/src/components/chat/Transcript.tsx`
- Query API: `backend/app/api/routes/support.py`
- Action API: `backend/app/api/routes/support_actions.py`
- Auth API: `backend/app/api/routes/auth.py`
- Cost API: `backend/app/api/routes/support_cost.py`
- Orchestrator: `backend/app/support/orchestrator.py`
- Router agent: `backend/app/support/agents/router_agent.py`
- Specialist registry: `backend/app/support/agents/registry.py`
- Auth helpers: `backend/app/support/auth.py`
- FAQ cache: `backend/app/support/faq_cache.py`
- Action registry: `backend/app/support/action_registry.py`
- Proposal helpers: `backend/app/support/agents/proposal_tools.py`
- MCP client: `backend/app/support/mcp_client.py`
- MCP server: `backend/mcp_server/server.py`
- MCP adapters: `backend/mcp_server/adapters/*.py`

## Top-level architecture

```
  ┌──────────────────────────────┐
  │  Frontend (React + Vite)     │
  │  SupportChatPage             │
  │  - Transcript                │
  │  - CardGrid                  │
  │  - InteractiveActions        │
  │  - SessionBanner/LoginDialog │
  └────┬─────────────────────────┘
       │  streamSupport()     login()/whoami()/logout()
       │  POST /v1/support/query     POST /v1/login
       │  (SSE)                      POST /v1/logout
       │                             POST /v1/support/action
       ▼
  ┌──────────────────────────────┐
  │  FastAPI backend (web)       │
  │  Middleware:                 │
  │   - CORS (allow credentials) │
  │   - APIKeyMiddleware         │
  │     (skips /login, /whoami,  │
  │      /support/* paths)       │
  │   - RateLimit / Logging      │
  │  Routes:                     │
  │   - /v1/login /logout /whoami│
  │   - /v1/support/query (SSE)  │
  │   - /v1/support/action       │
  │   - /v1/support/cost_summary │
  │   - /v1/support/conversations│
  └────┬─────────────────┬───────┘
       │                 │
       │ call_tool()     │ SQLAlchemy
       ▼                 ▼
  ┌────────────┐    ┌────────────┐
  │  MCP       │    │ Postgres   │
  │  Server    │    │ conversations
  │ (FastMCP   │    │ messages,   │
  │  HTTP)     │    │ tool_calls) │
  │            │    └────────────┘
  │  SQLite    │    ┌────────────┐
  │  adapter   │    │  Redis     │
  │  (billing, │    │ - session  │
  │  orders,   │    │   cache    │
  │  appts,    │    │ - FAQ cache│
  │  outage,   │    │ - actions  │
  │  payments, │    │   registry │
  │  writelog) │    └────────────┘
  └────────────┘
```

## Request entrypoint

```
Browser
  └── POST /v1/support/query   (SSE, credentials included)
       │
       └── support_query_endpoint()  [support.py]
             │
             ├── Identity resolution (precedence):
             │     1. signed cookie  `support_session`  ──► read_session()
             │     2. X-Customer-Id header (service-to-service)
             │     3. mint guest-<uuid>, set cookie on response
             │
             ├── Customer context:
             │     resolve_customer_context(customer_id, is_guest)
             │
             ├── Conversation resolution:
             │     - X-New-Conversation header: force a new row
             │     - X-Conversation-Id provided:
             │         - owner matches          → reuse
             │         - owner was guest, now authed → REBIND customer_id
             │         - cross-customer         → 403
             │     - Neither provided: create a new conversation row
             │
             └── SupportOrchestrator.stream(query, customer, conversation_id,
                                            source_ids, provider)
```

Response headers set on the SSE stream:

- `X-Conversation-Id: <uuid>`
- `X-Is-Guest: true|false`
- `X-Conversation-Rebound: true` (only if a guest→authed rebind happened)

## Orchestrator — per-turn flow

Every branch below is EXCLUSIVE. Once a branch takes over, the turn ends
with a `DONE` SSE event and `return`. No branch falls through to another.

```
stream(query, customer, conversation_id, ...)
  │
  │ (0) SSE: THINKING stage=loading
  │
  ├── Step 0: Intent + language classification
  │     classify_intent(query)  [intent_agent.py]
  │        - hard-rule regex for English smalltalk (no LLM)
  │        - else: `intent` LLM slot returns {intent, language}
  │     intent ∈ {smalltalk, off_topic, capabilities, summarize,
  │               support, unknown}
  │     language ∈ {en, es, unsupported}
  │
  ├── Branch A — language == "unsupported"
  │     → _stream_unsupported_language_reply()
  │     → SSE: SPECIALIST_INFO(unsupported_language) + TEXT + DONE
  │     → static bilingual rejection, no LLM, no persistence beyond message rows
  │
  ├── Branch B — intent ∈ {smalltalk, off_topic}
  │     → _stream_cheap_reply()
  │     → smalltalk_agent  OR  support_off_topic_agent
  │     → SSE: SPECIALIST_INFO + stream TEXT + DONE
  │     → skip history load + cache write (values unchanged)
  │
  ├── Branch C — intent == "capabilities"
  │     → _stream_capabilities_reply()
  │     → static text (English or Spanish), no LLM
  │     → SSE: SPECIALIST_INFO(capabilities) + TEXT + DONE
  │
  │ (for all remaining branches): load + maybe_compact history
  │
  ├── Branch D — intent == "summarize"
  │     → _stream_summarize_reply()
  │     → summarize_agent streams over the loaded history
  │     → SSE: SPECIALIST_INFO(summarize) + TEXT + DONE
  │     → does NOT overwrite last_specialist
  │
  │ ─────────────  everything below is the real support flow  ─────────────
  │
  ├── Step 1: Route
  │     route(query, customer_plan, customer_services, history_summary,
  │           last_specialist, model)   [router_agent.py]
  │
  │     IF _has_active_context(history_summary, last_specialist):
  │       → skip hard-rule regex, go straight to router LLM
  │         (keyword snap-in would break continuity mid-flow)
  │     ELSE (cold start):
  │       → apply_hard_rules(query):
  │           bill_pay  (pay my bill, autopay, add card)
  │           appointment (schedule, book, reschedule)
  │           outage    (outage, service down, area down)
  │           order     (order status, shipment, buy)
  │           billing   (refund, invoice, charge, balance, …)
  │         → on match: confidence 1.0, done
  │
  │     LLM classification (router slot):
  │       system prompt lists 7 specialists + continuity rule
  │       output JSON: {specialist, confidence}
  │       if confidence < 0.75:
  │         if last_specialist:  stick with last_specialist (raise to 0.8)
  │         else:                default → general
  │       on any exception: last_specialist OR general
  │
  │     SSE: THINKING stage=routing
  │
  ├── Step 1.5: FAQ cache
  │     FaqCache(redis).get(specialist, query, zip_code?)
  │     Cacheable specialists: {general, outage}
  │     Scope key: `general`  OR  `outage:<zip>`
  │     Normalized query: lowercase + collapsed whitespace + stripped .?!
  │     TTL: 1 hour
  │
  │     HIT → _stream_cached_reply()
  │       → SSE: SPECIALIST_INFO + TEXT + TOOL_CALL* + FOLLOWUPS + ACTIONS + DONE
  │       → persists user + assistant messages, zero LLM calls
  │
  ├── Step 2.0: Auth gate (guest → AUTH_REQUIRED)
  │     If customer.is_guest AND specialist ∈
  │         {billing, bill_pay, order, appointment}:
  │       → SSE: AUTH_REQUIRED{reason, message, allow_continue_as_guest} + DONE
  │       → allow_continue_as_guest = true only for 'order' (catalog browse)
  │       → persist user message, skip everything else
  │
  │     SSE: THINKING stage=retrieving
  │
  ├── Step 2: Retrieve
  │     effective_top_k = spec.top_k or settings.rag_top_k_final
  │     Retriever.retrieve(query, source_ids, language='text',
  │                        top_k=effective_top_k, include_wiki=True,
  │                        include_code=False, use_hyde=False,
  │                        use_query_expansion=False)
  │
  │     SSE: SOURCES(chunks[], total_searched)
  │
  ├── Step 2.5: Scope gate (KB-grounded specialists only)
  │     scope_passes = top_score ≥ rag_scope_threshold
  │                     AND coverage ≥ rag_min_coverage_chunks
  │                     (or one very-strong single hit ≥ 0.85)
  │
  │     IF spec.requires_kb_grounding AND NOT scope_passes:
  │       → _stream_out_of_scope_reply()
  │       → canned "I don't have that in our KB" + escalation contact
  │       → SSE: SPECIALIST_INFO + TEXT + DONE
  │     ELSE IF NOT spec.requires_kb_grounding AND NOT scope_passes:
  │       → log "running with empty KB — grounding via tools only"
  │       → continue (appointment/bill_pay/order/outage go through even
  │         when the KB is silent — they ground in MCP tool output)
  │
  ├── Step 3: Specialist runs
  │     Up to 2 hops (1 handoff allowed). See "Specialist loop" below.
  │
  ├── Step 4: Validation (parallel)
  │     is_verifiable_response(draft) determines whether to verify.
  │     _run_faithfulness():
  │        uses spec.faithfulness_model_slot (billing + bill_pay use
  │        `generation`, others use `followup`)
  │        validates against retrieved_chunks + tool_outputs + history
  │     _run_followups_actions():
  │        uses `followup` slot
  │        produces followup questions + action-link topics
  │
  │     If NOT grounded:
  │        spec.requires_kb_grounding → replace with KB refusal
  │        ELSE (tool-driven) → keep draft, log failure
  │
  ├── Step 5: Stream text + side events
  │     SSE: TEXT(final_response)
  │     SSE: TOOL_CALL(tool_name, status=success)   [one per tool]
  │     SSE: CARDS(kind, prompt, cards[])   [auto-derived from tool_outputs]
  │          order_list_catalog → product cards
  │          payment_method_list → payment-method cards
  │          appointment_list_slots → slot cards
  │     SSE: INTERACTIVE_ACTIONS(actions[])   [one per propose_* proposal]
  │          guest-gated kinds refused even here as belt-and-suspenders
  │     SSE: FOLLOWUPS(questions[])
  │     SSE: ACTIONS(actions[])   [static external links]
  │
  ├── Step 6: Persist turn
  │     - user message + assistant message → Postgres `messages`
  │     - tool_calls → Postgres `tool_calls`
  │     - conversations.last_specialist = current specialist
  │     - session_cache Redis → {summary, last_specialist, unresolved_facts}
  │
  ├── Step 7: Store in FAQ cache (guards)
  │     all must be true:
  │       - is_cacheable_specialist(specialist)  (general|outage)
  │       - response_is_grounded AND NOT grounding_fallback_used
  │       - all tools called are in the outage-read allowlist
  │       - reply does NOT contain the customer_id
  │       - no write proposals emitted
  │
  └── Step 8: SSE DONE + optional title generation
        DONE carries: latency_ms, retrieval_ms, generation_ms, validation_ms,
                      tokens (in/out/total/llm_requests), cost_usd,
                      specialist_used, router_confidence, conversation_id,
                      tools_called[], faithfulness_passed
        _maybe_generate_title(): once per conversation, `summary` slot
```

## Specialist loop (inside Step 3)

```
for hop in 0, 1:    # max 1 handoff
  spec_config = get_specialist(specialist_name)
    (KeyError → fallback to "general")

  SSE: SPECIALIST_INFO(specialist, confidence)
  SSE: THINKING stage=generating

  Prefetch escalation contact for the specialist's domain.
  build_user_message(query, customer, history, retrieved_context,
                     escalation_contact, language_directive)
  deps = SupportAgentDeps(customer, history, retrieved_context, tool_outputs)

  IF spec_config.structured_handoff:   # (outage only, for now)
      run_result = await agent.run(user_message, model, deps)
      hop_output = run_result.output    # OutageOutput pydantic model
  ELSE:
      async with agent.run_stream(...) as stream:
          async for chunk in stream.stream_text(delta=True):
              accumulate hop_text
          hop_output = await stream.get_output()

  IF structured_handoff:
      draft = hop_output.reply
      handoff_target = hop_output.handoff_to   # typed field
  ELSE:
      draft = hop_output if isinstance(str) else hop_text
      handoff_target = _detect_handoff(draft, specialist_name)
         # regex: looks for "<target> specialist", "handled by <target>",
         # "transfer to <target>", "<target> team"

  IF handoff_target AND hop == 0:
      SSE: THINKING stage=routing (transferring)
      specialist_name = handoff_target
      continue  # re-enter loop for hop 1

  IF handoff_target AND hop >= 1:
      # 2 hops reached — append an escalation note instead
      draft += escalation_note (phone + hours)

  break
```

## Specialists (SPECIALIST_REGISTRY)

All 7 specialists live in `backend/app/support/agents/` and are registered in
`registry.py`. Each has a system prompt in `agents/prompts/`.

| Specialist    | Model slot    | Domain      | KB?    | Auth req? | Top-K | Faithfulness slot | Structured handoff | Proposes writes           |
| ------------- | ------------- | ----------- | ------ | --------- | ----- | ----------------- | ------------------ | ------------------------- |
| `technical`   | `technical`   | technical   | yes    | no        | 8     | followup          | no                 | —                         |
| `billing`     | `billing`     | billing     | yes    | **yes**   | 5     | generation        | no                 | —                         |
| `general`     | `general`     | general     | yes    | no        | 4     | followup          | no                 | —                         |
| `outage`      | `outage`      | outage      | **no** | no        | 3     | followup          | **yes**            | —                         |
| `order`       | `order`       | order       | no     | no (\*)   | 6     | followup          | no                 | place_order, cancel_order |
| `bill_pay`    | `bill_pay`    | bill_pay    | no     | **yes**   | 4     | generation        | no                 | pay, enroll_autopay       |
| `appointment` | `appointment` | appointment | no     | **yes**   | 4     | followup          | no                 | book/cancel/reschedule    |

(\*) `order` allows guests to browse the catalog but blocks them at the
`/v1/support/action` endpoint when they try to commit a write.

### Per-specialist registered tools

Read tools live on the specialist agent; write tools are surfaced only as
`propose_*` tools that record a structured proposal (no mutation).

```
technical_agent
   voice_get_details          mobile_get_details       internet_get_details
   tv_get_details             list_devices             get_device
   get_outage_for_customer    get_recent_tickets
   (escalation_contact is pre-fetched & injected, not tool-called)

billing_agent
   billing_get_invoice
   billing_list_charges
   billing_get_balance

general_agent
   get_recent_tickets

outage_agent                   (structured output: {reply, handoff_to, reason})
   area_status_for_customer    area_status_by_zip
   scheduled_maintenance

order_agent
   list_catalog                quote
   list_orders                 order_status              shipment_status
   list_payment_methods
   propose_place_order         propose_cancel_order

bill_pay_agent
   get_balance                 list_payment_methods
   propose_payment             propose_autopay

appointment_agent
   list_slots
   propose_book_appointment    propose_cancel_appointment
   propose_reschedule_appointment
```

## propose\_\* → INTERACTIVE_ACTIONS → /v1/support/action

Writes never run from the specialist. The flow is:

```
1.  Specialist calls propose_pay(amount, pm_id, pm_label)
       record_proposal() pushes into deps.tool_outputs:
         {"tool": "propose_pay",
          "output": {"_proposal": {kind, label, confirm_text, payload}}}

2.  After specialist run, orchestrator:
       proposals = extract_proposals(tool_outputs)
       for p in proposals:
           if customer.is_guest AND p.kind ∈ AUTH_REQUIRED_KINDS:
               drop (belt-and-suspenders; auth gate usually catches first)
           action = ActionRegistry(redis).create(
               kind=p.kind,
               customer_id=customer.customer_id,
               conversation_id=conversation_id,
               payload=p.payload,
           )
           # Redis key: support:action:<uuid>, TTL = 10 min

       SSE: INTERACTIVE_ACTIONS(actions[{
           label, action_id, kind, confirm_text, payload, expires_at
       }])

3.  User clicks the button in InteractiveActions.tsx:
       POST /v1/support/action  {action_id}
         │
         └── support_action_endpoint() [support_actions.py]
               session cookie required (guests get 401)
               ActionRegistry.claim(action_id)   (GETDEL, one-shot)
               if action.customer_id != session.customer_id → 403
               tool_name = _MCP_TOOL_FOR_KIND[kind]
                   pay → bill_pay_make_payment
                   enroll_autopay → bill_pay_enroll_autopay
                   place_order → order_place
                   cancel_order → order_cancel
                   book_appointment → appointment_book
                   cancel_appointment → appointment_cancel
                   reschedule_appointment → appointment_reschedule
                   add_payment_method → payment_method_add
                   set_default_payment_method → payment_method_set_default
               args = {customer_id, conversation_id, ...action.payload}
               await mcp_client.call_tool(tool_name, args)
                   MCP server recomputes its OWN idempotency key
                   (sha256(conversation_id|tool|args)), hits write_log
                   table, replays if duplicate
               Persist role='action_result' message in Postgres
               return ActionResultEvent

4.  Frontend handleActionResult() appends ACTION_RESULT into the
    current assistant message's events → green/red banner renders inline.
```

Action kinds + their MCP tools:

| Frontend action kind         | MCP tool                     | Idempotent? |
| ---------------------------- | ---------------------------- | ----------- |
| `pay`                        | `bill_pay_make_payment`      | yes         |
| `enroll_autopay`             | `bill_pay_enroll_autopay`    | yes         |
| `place_order`                | `order_place`                | yes         |
| `cancel_order`               | `order_cancel`               | yes         |
| `book_appointment`           | `appointment_book`           | yes         |
| `cancel_appointment`         | `appointment_cancel`         | yes         |
| `reschedule_appointment`     | `appointment_reschedule`     | yes         |
| `add_payment_method`         | `payment_method_add`         | yes         |
| `set_default_payment_method` | `payment_method_set_default` | yes         |

## Auth flow (cookie-based, dev mock)

```
GET /v1/whoami
  if cookie present and valid → {customer_id, is_guest}
  else → mint guest-<uuid>, set cookie, return {customer_id=guest-…, is_guest=true}

POST /v1/login  {customer_id}
  reject if customer_id starts with 'guest-'
  otherwise: issue HMAC-signed cookie, return {customer_id, is_guest=false}
  No password (mock). Real auth slots in here later.

POST /v1/logout
  204 + delete_cookie

Session cookie:
  name: support_session
  body: base64(JSON({cid, iat})) + '.' + base64(HMAC_SHA256(body, secret))
  secret: settings.security_encryption_key
          OR env SUPPORT_SESSION_SECRET
          OR hard-coded dev fallback
  max_age: 7 days
  HttpOnly, SameSite=Lax, Secure only if SESSION_COOKIE_SECURE=true

Guest → Authed upgrade:
  1. Guest starts chatting, transcript persists under guest-<uuid>
  2. User logs in → new cookie with real customer_id
  3. Next /support/query call carries X-Conversation-Id
  4. support_query_endpoint sees conversation.customer_id starts with 'guest-'
     and request is now authed → calls conv_repo.rebind_customer_id(..., real_id)
  5. Response header: X-Conversation-Rebound=true
  Transcript survives the login.

APIKeyMiddleware skip list (public routes):
  /v1/health, /docs, /openapi.json, /redoc
  /v1/login, /v1/logout, /v1/whoami
  /v1/support/query, /v1/support/action
  Prefixes: /v1/support/conversations
```

## MCP server

```
backend/mcp_server/
  main.py                         entrypoint (python -m mcp_server.main)
  server.py                       FastMCP + transport_security + tools
  config.py                       env: MCP_BACKEND, MCP_SQLITE_PATH,
                                       MCP_HOST, MCP_PORT, MCP_SEED,
                                       MCP_ALLOWED_HOSTS,
                                       downstream URL overrides
  adapters/
    base.py                       Protocol types (BillingRepo, OrderRepo,
                                     AppointmentRepo, OutageRepo,
                                     PaymentMethodRepo, BillPayRepo,
                                     WriteLogRepo)
    sqlite_impl.py                aiosqlite-backed impl of each protocol
    http_impl.py                  stubs for downstream HTTP (not wired)
    factory.py                    picks impl from MCP_BACKEND env
  db/
    schema.sql                    customers, invoices, charges, balances,
                                  payment_methods, payments,
                                  catalog_items, orders, order_items,
                                  appointments, appointment_slots,
                                  outages, scheduled_maintenance,
                                  write_log
    seed.sql                      3 sample customers, invoices, catalog,
                                  slots (zip-scoped + national pool),
                                  outages

Transport: HTTP Streamable on :8765
Route: POST /mcp
Allowed hosts: localhost, 127.0.0.1, mcp-server, 0.0.0.0
               + MCP_ALLOWED_HOSTS env list
```

### MCP tools exposed

Read:

```
billing_get_invoice             billing_list_invoices
billing_list_charges            billing_get_balance
payment_method_list
order_list_catalog              order_quote
order_get                       order_list             order_shipment_status
appointment_list_slots
outage_area_status              outage_incident_lookup
outage_scheduled_maintenance
```

Write (idempotent, keyed by sha256(conversation_id|tool|args)):

```
payment_method_add              payment_method_set_default
bill_pay_make_payment           bill_pay_enroll_autopay
order_place                     order_cancel
appointment_book                appointment_cancel
appointment_reschedule
```

Idempotency layer:

- Every write tool calls `_with_idempotency()`.
- Key = sha256(conversation_id + tool + sorted inputs JSON).
- Hit on `write_log` → return stored response + `_replayed=true`.
- Miss → run the write, store the response.

## Backend MCP client

```
backend/app/support/mcp_client.py
  build_mcp_client() called from app lifespan
    probes MCP at MCP_SERVER_URL (default http://mcp-server:8765/mcp)
    success → McpClient  (per-call streamable-HTTP session)
    any failure → NullMcpClient (every call raises)

Why per-call and not a long-lived session:
  pydantic anyio cancel scopes are tied to the task that entered them.
  A persistent ClientSession opened during app startup can't be safely
  exited by a request-handling task during app shutdown.  ~50ms cost
  per tool call, acceptable at Phase A/B traffic.

Dev fallback:
  backend/app/support/tools/_mcp_bridge.py holds the client.
  Legacy shims in tools/billing.py + tools/outage.py route through the
  MCP client when live, otherwise return static stub dicts so the app
  stays demoable offline.
```

## Frontend transcript renderer

Each SSE event type maps to a renderer case in
`frontend/src/components/chat/Transcript.tsx::EventRenderer`.

```
thinking               ─► THINKING bar with stage message
sources                ─► Collapsible <details> with N chunks
code                   ─► Code block (rarely used)
wiki                   ─► External link
text                   ─► Accumulated text buffer (streamed, smooth)
specialist_info        ─► Badge + confidence %
tool_call              ─► "✓ tool_name" chip
scope_warn             ─► Warning alert
error                  ─► Error alert
followups              ─► Clickable follow-up chips
actions                ─► External-link buttons (ActionLink[])
cards                  ─► CardGrid (products / payment methods / slots)
interactive_actions    ─► InteractiveActions buttons (propose_* outcomes)
action_result          ─► ActionResult banner (green / red / amber)
auth_required          ─► Opens LoginDialog modal (not inline)
done                   ─► Stats badges: chunks, ms, tokens, cost, phase timings
```

Inside InteractiveActions.tsx:

```
  state: Map<action_id, "idle"|"pending"|"done"|"error">
  click:
    set status→pending
    POST /v1/support/action {action_id} (credentials: include)
    on success  → status→done, onResult(ActionResultEvent)
    on 4xx/5xx  → status→error, onResult(error event), inline error text
  disabled rules:
    - expired (past expires_at)
    - any sibling currently pending (one-of-N)
    - any sibling already done (consumed choice set)
    - this one is pending or done
```

## Caches, persistence, identifiers

```
Postgres (durable):
  conversations(id, customer_id, title, status, rolling_summary,
                unresolved_facts_json, last_specialist, last_handoff_json,
                metadata_json, created_at, updated_at)
  messages(id, conversation_id, role, content, specialist_used,
           citations_json, input_tokens, output_tokens, cost_usd,
           created_at)
  tool_calls(id, conversation_id, message_id, tool_name,
             input_json, output_json, created_at)

SQLite (MCP side, dev):
  write_log(idempotency_key PK, tool_name, response_json, created_at)
  plus all the domain tables (customers, invoices, orders, slots…)

Redis:
  support:session:<conversation_id>       rolling_summary + last_specialist
                                          + unresolved_facts
  support:faq:<scope>:<sha256>            cached FAQ answer, TTL 1h
  support:action:<uuid>                   PendingAction, TTL 10min
```

## SSE event glossary (payload shapes)

From `backend/app/streaming/events.py`:

```
THINKING              { message, stage }
SOURCES               { chunks: ChunkPreview[], total_searched }
TEXT                  { content }
SPECIALIST_INFO       { specialist, confidence }
TOOL_CALL             { tool_name, status }   status ∈ calling|success|error
SCOPE_WARN            { message }
ERROR                 { type, message, code }
FOLLOWUPS             { questions: { question, category }[] }
ACTIONS               { actions: { label, url, topic }[] }
CARDS                 { kind, prompt?, cards: CardItem[] }
                      CardItem = { kind, id, title, subtitle?, image_url?,
                                   badges[], metadata }
INTERACTIVE_ACTIONS   { actions: InteractiveAction[] }
                      InteractiveAction = { label, action_id, kind,
                                            confirm_text?, payload, expires_at }
ACTION_RESULT         { action_id, kind, status, message, detail }
AUTH_REQUIRED         { reason, message, allow_continue_as_guest, login_url }
DONE                  { total_chunks_used, sources_used[], faithfulness_passed,
                        latency_ms, retrieval_ms?, generation_ms?,
                        validation_ms?, specialist_used, router_confidence,
                        conversation_id, tools_called[], input_tokens,
                        output_tokens, total_tokens, llm_requests, cost_usd }
```

## Happy-path timelines

### 1. Authed customer asks "what's my balance?"

```
Frontend                   Backend                                LLMs     MCP
────────                   ───────                                ────     ───
POST /support/query  ─────► whoami (cookie) → cust_001
                            classify_intent                       intent
                            route (hard-rule: "balance" → billing)
                            FAQ cache miss (billing not cacheable)
                            (not guest, billing allowed)
                            retrieve (top_k=5) ────────────────────────► pgvector
                            scope gate passes
                            billing_agent.run_stream         ─────billing
                              calls billing_get_balance  ───────────────► mcp: billing_get_balance
                              streams "Your balance is $264.97…"
                            faithfulness (generation slot)    ────gen
                            followups+actions (followup slot) ────followup
              ◄─── SSE: THINKING, SOURCES, SPECIALIST_INFO, TEXT, TOOL_CALL,
                          FOLLOWUPS, ACTIONS, DONE
```

### 2. Guest asks "pay my bill"

```
Frontend                   Backend                                LLMs
────────                   ───────                                ────
POST /support/query  ─────► whoami → guest-abc (cookie minted)
                            classify_intent                       intent
                            route (hard-rule: "pay" → bill_pay)
                            FAQ cache skipped (not cacheable)
                            AUTH GATE: is_guest && bill_pay requires auth
              ◄─── SSE: AUTH_REQUIRED(reason=purchase, allow_continue_as_guest=false)
              ◄─── SSE: DONE (cost_usd = ~$0 for the intent call only)

Frontend:  opens LoginDialog (modal)
  User enters cust_001
POST /v1/login → sets signed cookie, cust_001

(User retries:) POST /support/query  with X-Conversation-Id=<prev convo>
                            cookie = cust_001
                            conversation.customer_id = guest-abc
                            guest→authed: rebind customer_id = cust_001
                            X-Conversation-Rebound: true
                            (normal bill_pay flow proceeds)
```

### 3. Customer books an appointment — full propose → click round-trip

```
Turn 1:  "I need an appointment for internet"
  Frontend                 Backend                                LLMs     MCP
  ────────                 ───────                                ────     ───
  POST /support/query      classify_intent                        intent
                           hard-rule: "appointment" → appointment
                           auth gate: authed, allowed
                           retrieve (top_k=4, requires_kb=false — OK if empty)
                           appointment_agent.run_stream           appointment
                             asks for zip / type
           ◄──  SSE: TEXT "Which type (install or tech_visit)?…"
           ◄──  DONE

Turn 2:  "install, 80015"
  POST /support/query      classify_intent (active context → skip hard rule)
                           router_agent (LLM): previous=appointment, msg=short →
                             specialist=appointment, confidence=0.95
                           retrieve
                           appointment_agent.run_stream
                             calls appointment_list_slots(topic=install, zip=80015) ─► mcp
                             tool returns zip-scoped slots + national pool fallback
                             calls propose_book_appointment(slot_id, topic, …)
                               → records _proposal in tool_outputs (no mutation)

                           orchestrator:
                             SSE: TEXT "Here are available slots…"
                             SSE: TOOL_CALL appointment_list_slots
                             SSE: CARDS(kind=appointment_slot, prompt="Pick a time",…)
                             extract_proposals → 1 proposal
                             ActionRegistry.create → action_id in Redis, TTL 10 min
                             SSE: INTERACTIVE_ACTIONS([{label="Book install — …",
                                                        action_id, kind=book_appointment,
                                                        confirm_text, payload, expires_at}])
                             persist message + tool_calls
                             DONE
           ◄──  SSE: SPECIALIST_INFO, SOURCES, TEXT, TOOL_CALL, CARDS,
                     INTERACTIVE_ACTIONS, FOLLOWUPS, DONE

Turn 3:  user clicks the "Book install" button
  Frontend: handleClick → confirm() → spinner
  POST /v1/support/action {action_id}
                           session cookie → cust_001
                           ActionRegistry.claim(action_id)   (GETDEL, one-shot)
                           dispatch kind=book_appointment → appointment_book
                           mcp.call_tool(appointment_book, {customer_id, conversation_id, slot_id, topic})
                             MCP server: _with_idempotency → write_log miss →
                                         INSERT appointments, UPDATE slots SET booked=1
                           persist role='action_result' message in Postgres
                           return ActionResultEvent(status=success, message=…)
  Frontend:  handleActionResult → appends ACTION_RESULT event to the
             current assistant message → green banner renders inline.
             Other buttons in the group disable (one-of-N UX).
```

## Config + operator knobs (env vars)

```
Backend (web)
  MCP_SERVER_URL                  http://mcp-server:8765/mcp
  SESSION_COOKIE_SECURE           0/1
  SUPPORT_SESSION_SECRET          HMAC key (falls back to security_encryption_key)
  CORS_EXTRA_ORIGINS              comma-separated origins for prod CORS

MCP server
  MCP_BACKEND                     sqlite (default) | http
  MCP_SQLITE_PATH                 /data/mcp/mcp_data.db
  MCP_HOST, MCP_PORT              0.0.0.0, 8765
  MCP_SEED                        true (default) — apply seed.sql at start
  MCP_ALLOWED_HOSTS               extra Host headers to allow
  MCP_DOWNSTREAM_BILLING_URL      only used when MCP_BACKEND=http
  MCP_DOWNSTREAM_ORDERS_URL       …
  MCP_DOWNSTREAM_APPOINTMENTS_URL …
  MCP_DOWNSTREAM_OUTAGE_URL       …

RAG retrieval knobs (backend/config.toml)
  rag_scope_threshold, rag_top_k_final, rag_min_coverage_chunks
  per-specialist `top_k` override lives on SpecialistConfig in registry.py
```

## Known quirks + guardrails baked into code

```
1.  Smalltalk / off_topic / summarize / capabilities / unsupported_language
    never update conversations.last_specialist. That way follow-ups after
    those turns still see the prior topical specialist for continuity.

2.  Router's low-confidence fallback prefers sticking with last_specialist
    over defaulting to 'general'. Short follow-ups ("yes", "80015",
    "the Visa") are continuations.

3.  When a conversation has active context, the router SKIPS hard-rule
    regex and goes straight to LLM. Prevents a keyword like "payment"
    mid-appointment flow from snapping into bill_pay.

4.  FAQ cache is strict: only general+outage, only if tools called are
    outage-read allowlist, only if reply doesn't contain customer_id,
    only if grounded, only if no write proposals. Misses are safe.

5.  Interactive action buttons are single-use: GETDEL on claim, and the
    MCP-level idempotency key prevents double-mutations even if two
    clicks race.

6.  Guest→authed rebinding rejects cross-customer rebinds. The prior
    customer_id on the conversation must be a guest- prefix, OR must
    already match the authed id.

7.  The outage agent's structured output (OutageOutput) uses run() not
    run_stream() — pydantic-ai rejects stream_text on non-string outputs.
    Everyone else streams.

8.  Tool-driven specialists (appointment, bill_pay, order, outage) set
    requires_kb_grounding=False so the scope gate doesn't refuse them
    when the KB has no matching article. Their grounding is MCP tool
    output, not KB chunks.

9.  No agent calls another agent. All handoffs go through the
    orchestrator. Single hop max.

10. Every write tool runs at most once per (conversation_id, tool, args)
    tuple thanks to the write_log table. Client retries are safe.
```
