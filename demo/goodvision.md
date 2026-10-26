# GoodVision SmartRouter — API notes for the demo (2026-10-20)

No public API reference exists for `goodvision.tech` (web search finds only the marketing site
goodvision.ai). Everything below comes from two verifiable sources, marked **[bundle]** = the
console's own "Integration guide" code shipped in `https://console-test.goodvision.tech/console/assets/index-*.js`
and **[live]** = responses of the API hosts probed without a key on 2026-10-20. Items marked
**[assumed]** are not verified and must be checked once we have a key.

## Hosts and base URLs

| | test | production |
|---|---|---|
| console | https://console-test.goodvision.tech/console/router-settings | https://console.goodvision.tech |
| API origin **[bundle]** | `https://api-test.goodvision.tech` | `https://api.goodvision.tech` |
| OpenAI-protocol base URL **[bundle]** | `https://api-test.goodvision.tech/v1` | `https://api.goodvision.tech/v1` |
| Anthropic-protocol base URL **[bundle]** | `https://api-test.goodvision.tech` (no `/v1`; the client appends `/v1/messages`) | `https://api.goodvision.tech` |

The console resolves the API origin from its own hostname: `*-test.goodvision.tech` → `api-test`,
`*.goodvision.tech` → `api`, `*.goodvision.internal` → `smartrouter.goodvision.internal` **[bundle]**.
Today the production host answers with New API's error format (`"type":"new_api_error"`, "Invalid
token"), while the test host answers with the SmartRouter's own codes (below) **[live]** — the two
backends differ; the demo defaults to **test**.

## Authentication

- `Authorization: Bearer <key>` **[bundle: "authorized with a standard Bearer key"; live: accepted]**.
  `x-api-key: <key>` is also accepted by the test host **[live]**.
- Keys: console → *SmartRouter settings* → "Create API key" (a key "identifies requests for this
  ProductTenant and has its own routing policy"; the secret is shown once) **[bundle]**, or
  https://ai.goodvision.tech/keys ("Get API key") **[bundle]**. Their client snippets put the key in
  `GOODVISION_API_KEY` (OpenAI protocol) or `ANTHROPIC_AUTH_TOKEN` (Anthropic protocol) **[bundle]**.

Error bodies **[live, test host]** — OpenAI style, `{"error": {"code", "message", "param", "type"}}`:

| situation | HTTP | `code` |
|---|---|---|
| no credentials | 401 | `verified_identity_required` — "A verified credential identity is required" |
| bad / revoked key | 401 | `invalid_api_key` — "API key is invalid, revoked or expired" |
| unknown route | 404 | `route_not_found` |
| wrong method | 405 | `method_not_allowed` |

Response headers carry `x-gv-request-id`, `x-request-id`, `x-trace-id`, `traceparent` **[live]**.

## Endpoints (test host) **[live]**

| endpoint | status without a valid key | note |
|---|---|---|
| `POST /v1/chat/completions` | 401 `invalid_api_key` | what the harness actor (litellm `openai/<model>`), the note writer and the mem0 extractor use |
| `POST /v1/responses` | 401 `invalid_api_key` | the console's raw example: `{"model": "auto", "input": "Hello"}` **[bundle]**; Codex snippet sets `wire_api = "responses"` |
| `POST /v1/messages` | 401 (Anthropic-style error body) | Anthropic protocol |
| `GET /v1/models` | 401 `invalid_api_key` | used by the demo to list the model market once a key works |
| `POST /v1/embeddings`, `POST /v1/completions` | 404 `route_not_found` | not offered |

## Models and routing

- Model ids "must match the model market verbatim" **[bundle]**. Ids seen in the console's
  snippets: `gpt-5.6-sol`, `gpt-5.2`, `claude-sonnet-5`. `"model": "auto"` asks the SmartRouter to
  choose **[bundle, raw example]**.
- Each API key is bound to a *routing policy* (follow the tenant default, or a named policy)
  chosen in router-settings **[bundle]**; the request itself does not carry routing parameters.
- The console has pages *models*, *benchmarks*, *policies*, *request-logs*, *usage* (SmartRouter
  usage & billing) **[bundle]** — per-request cost is therefore accounted on their side; the response
  body is **[assumed]** standard OpenAI `usage` (`prompt_tokens`, `completion_tokens`, possibly
  `prompt_tokens_details.cached_tokens`), which is all `harness/ledger.py` needs.

## How the demo uses it

`demo/providers.py` defines the provider (`goodvision-test`, `goodvision-prod`, plus `local` vLLM).
A live run with a GoodVision provider sets, for `harness/mini_swe_agent.py`:

```
RSM_API_BASE=https://api-test.goodvision.tech/v1   RSM_API_KEY=$GOODVISION_API_KEY    # actor (litellm) + note writer
RSM_FACTS_API=https://api-test.goodvision.tech/v1  RSM_FACTS_API_KEY=$GOODVISION_API_KEY   RSM_FACTS_MODEL=<model>   # mem0 extraction
RSM_WRITER_MODEL=<model>   RSM_OPENAI_STRICT=1      # no vLLM-only params (chat_template_kwargs), max_completion_tokens, no forced temperature
```

and runs the actor as `--served-name <model>`. `RSM_ACTOR_NOTHINK` is *not* set (vLLM-only). The
key is read from the environment or from `demo/.env` (`GOODVISION_API_KEY=...`, git-ignored).

Harness changes, all env-gated (unset = byte-identical behaviour for the existing arms):
`harness/config.py` (`RSM_API_KEY`, `RSM_OPENAI_STRICT`), `memory/ours/fusednote.py` (Bearer header,
strict body), `memory/attribution/probes.py` (`RSM_FACTS_API_KEY`).

## First real key (2026-10-20 evening) **[live]**

Key format `gv_<16 hex>_<43 chars>`. On the **test** host it authenticates (`GET /v1/models` → 200 with
`x-gv-request-id`), but the market is empty (`{"data": []}`) and every request — `/v1/chat/completions`
with `auto`, `claude-sonnet-5`, `gpt-5.2`, `gpt-5.6-sol`, `gpt-4o-mini`; `/v1/responses` with `auto`;
`/v1/messages` with `claude-sonnet-5` — returns **422 `no_candidate`: "No model offer satisfies the
effective policy"**. That is the router saying the key's routing policy has no model offers enabled
(the console's matching message: "No models are available. Check the enterprise inference API key in
SmartRouter and your routing policy"). On the **production** host the same key is rejected
(`new_api_error` "Invalid token") — different backend, different credentials.

`demo/providers.py::check` now treats "authenticated + empty market" as not ready and surfaces the
router's code, so the UI's endpoint status reads `no_candidate: …` until the policy is fixed, and
live runs stay disabled (launching would make mini-swe-agent retry 422s in a loop).

## Second key (2026-10-20, working) **[live]**

`GET /v1/models` → 19 ids, `owned_by: "gv-router"`: `auto`, `claude-haiku-4-5`, `claude-opus-4-5`,
`claude-opus-4-6`, `claude-opus-4-7`, `claude-opus-4-8`, `claude-opus-5`, `claude-sonnet-4-5`,
`claude-sonnet-4-6`, `claude-sonnet-5`, `gpt-5.2`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.4-nano`, `gpt-5.5`,
`gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-6-astra`.
`POST /v1/chat/completions` with `model: "auto"` → 200; the response's `model` names the model the
router chose (`gpt-5.2-2025-12-11`), and `usage` is the standard `{prompt_tokens, completion_tokens,
total_tokens}` (no `prompt_tokens_details`, so the ledger's cache-aware meter equals raw here).
Still 401 on the production host — the key is for the test environment only.

## What the API exposes about the SmartRouter (2026-10-22) **[live + bundle]**

**Per request (inference API):** only response headers. Every `/v1/chat/completions` (streaming too)
answers with `x-gv-route-mode` (`auto` for `model: "auto"`), `x-gv-selected-model` (market id),
`x-gv-selected-offer` (`offer_chm_…`, the provider offer behind the model), `x-gv-decision-id`
(`dec_…`), `x-gv-decision-mode` (`initial` seen; the console vocabulary also has affinity/fallback
states), `x-gv-affinity-state` (`disabled`). The body is plain OpenAI JSON (`id, model, object, choices,
usage`) with `model` = the served model's dated id; no routing fields, no cost. Streaming chunks carry
empty `model`; usage arrives in the final chunk with `stream_options.include_usage`.

**Not exposed to API keys:** `GET /v1/decisions[/<id>]` exists but answers 401
`decision_service_authentication_required` — "Decision evidence requires an authenticated service".
No `/v1/policies`, `/v1/offers`, `/v1/benchmarks`, `/v1/usage`, per-model `/v1/models/<id>` or OpenAPI
document. **Requests cannot steer routing:** `routing`/`gv`/`metadata` body fields and `x-gv-route-mode`
/ `x-gv-policy` / `x-gv-affinity` headers are silently ignored; model aliases like `auto:cost` or `smart`
give `no_candidate` (`auto-fast` was accepted and treated like `auto`). Routing is decided by the
**policy bound to the key** (console → SmartRouter settings: "Bound API key to a custom routing policy";
default = the tenant's policy).

**Where the detail lives (console only, session login):** the console's request-logs page ("Inspect
recent customer requests and their routing outcomes. Prompt and response content are never stored
here"), the decision record ("Eligible models returned with this decision", "Execution activity after
the decision", `decision_duration_ms`), policies, benchmarks and usage pages. Its vocabulary shows what a
policy is made of and what the ranker uses: `weights` / scenario weights, `constraints`, `budget(s)`,
`cost_tier` (+ `cost_tier_mismatch`), `quality_score` / `quality_rank` (+ `quality_stale`,
`quality_sample_too_small`, `benchmark_evidence_unavailable`), `tiered_expr`, `affinity_hit`
("This session reused its established route, so ranking did not run again" — route continuity for
multi-turn Responses sessions), and fallback reasons (`fallback_timeout`, `fallback_rate_limited`,
`fallback_unavailable`, `fallback_invalid_json`, `fallback_empty_message`, `offer_denied`,
`offer_unavailable`, `routing_policy_rejected`, `routing_policy_not_active`).

**Observed behaviour under the default policy:** routing is content-aware, not load-based — a math
word problem and a summarisation prompt went to `gpt-5.2` (offer `offer_chm_9c477422c1a13475`); the
harness's agent prompt and a "write a Python function" prompt went to `claude-opus-5`
(`offer_chm_249c175709e97976`), repeatably. Prompt length alone (a 600-sentence filler) did not change
the choice. That is why the whole chain run below was served by `claude-opus-5`.

**Upstream availability is not masked:** on 2026-10-22 ~20:50 UTC every Anthropic id (and therefore
every `auto` coding request) failed with Bedrock's `ValidationException: Access to Anthropic models is
not allowed for this account` (HTTP 400 `aws_invoke_error`), passed straight through — no fallback to
another offer. GPT ids kept working. The demo's endpoint check (`/v1/models` + a trivial `auto` ping,
which routes to `gpt-5.2`) therefore reports "ok" while coding runs would fail; check a Claude id before
a demo.

## Tool calling is not offered to this key (2026-10-22) **[live]**

Any request that declares **tools** — `tools:[{type:function…}]` on `/v1/chat/completions` (even with
`tool_choice:"none"`), function or `web_search` tools on `/v1/responses` — returns 422 `no_candidate`
for every model id including `auto`; the same request without `tools` succeeds. So the effective
policy has no model offer that admits tool use. Consequences: **Codex CLI cannot run through the
router** (its first turn always declares the shell tool — verified with `codex exec --json`, provider
`wire_api = "responses"`: 422 on every retry), and neither can function-calling harnesses (SWE-Edit,
OpenHands). The ChainSWE harness works only because mini-swe-agent is text-based (one fenced bash
block per turn, no tools). `codex exec` itself works on this cluster (static musl binary
`/scratch/$USER/kv/codex/codex-x86_64-unknown-linux-musl`, v0.163.0; needs `< /dev/null` and
`-c approval_policy="never"` when scripted).

## Routing observed (2026-10-22) **[live]**

Response headers of every call carry the routing decision: `x-gv-route-mode: auto|…`,
`x-gv-selected-model`, `x-gv-selected-offer`, `x-gv-decision-id`, `x-gv-decision-mode`, plus the
upstream's own headers (`x-ms-served-model`, `azureai-fe-offer-type: paygo`, rate-limit headers keyed by
model). There is **no cost header**; the response body's `model` is the served model (dated id). The
ledger now records that served model per call (`harness/ledger.py`, 2026-10-22), so `auto` runs are
priced by what actually ran (`demo/model_prices.json`).

First `auto` chain run (tablib, Knowledge Vault arm): the router selected **`claude-opus-5` for every
call** (61/61), the most expensive model in the market. The chain resolved 2/3 (the only configuration
to solve task 3) but task 3 took 41 calls / 708K tokens / $4.12 and ended in `RepeatedFormatError`
(the model kept emitting two actions per turn). Routing policy is set per key in the console; a policy
that weighs cost would change this picture — worth asking GoodVision what the default policy optimises.

## Dry-run result (2026-10-20, dummy key)

`harness/mini_swe_agent.py --memory-system fusednote` with the environment above reaches the test
host: the actor's first `/v1/chat/completions` call carries the Bearer header and the strict body,
and the router answers `invalid_api_key`. litellm surfaces that as `BadRequestError`, which
mini-swe-agent 2.4.5 **retries with exponential backoff** instead of failing fast — so a run must
never be launched with an unverified key. The demo's `/api/live/start` therefore refuses to start
unless `GET /v1/models` succeeds with the configured key (`demo/providers.py::check`).

## Open questions to settle when the key arrives

1. Does `/v1/chat/completions` return `usage` for every call (and `prompt_tokens_details.cached_tokens`)?
2. Do the gpt-5.x ids accept `max_completion_tokens` and reject `temperature` (as upstream OpenAI does),
   and do the Claude ids accept `response_format: json_object` (used by the mem0 extractor)?
3. Which price row to use for USD: `harness/prices.json` is per model; the router's own usage page
   is the authoritative cost. Add a row per market model we run.
4. Whether `model: "auto"` should be a demo option (routing policy decides the model per call;
   the ledger then records `model` from the response, so cost per call stays attributable).
