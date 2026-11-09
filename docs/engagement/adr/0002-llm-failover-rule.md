# ADR 0002: LLM failover rule

Status: accepted.

## Context

A hosted model can be slow or down. A case run must not hang, and it must not fail only because one provider has a fault. The platform has two chat adapters. It needs a rule to move calls from one to the other, and a rule to move back.

## Options

1. **No failover.** Simple. A provider problem becomes a customer-visible error.
2. **Retry only.** Helps with short faults. A long outage makes every call wait for each retry.
3. **Primary with a breaker and a secondary.** Failed calls go to the second adapter. The breaker stops calls to a failing primary.
4. **Hedged requests.** Send the call to both adapters and use the first answer. This cuts tail latency but doubles the cost and the token use.

## Decision

Use option 3.

- **Switch rule.** The primary is retried once when the retry budget allows it. The budget allows about 20 percent of attempts in a 60 second window, with a minimum of three. If the call still fails, for example after two timeouts, the secondary answers. After five failures in a row the breaker opens. While the breaker is open, the platform skips the primary.
- **Switch back rule.** After 30 seconds the breaker turns half open. One probe call goes to the primary. If the probe succeeds, the breaker closes and the primary serves again. If the probe fails, the breaker opens again.
- **Visibility.** The counter `aip_llm_failover_total` has two labels, `from` and `to`. They hold the provider names only.

## Consequences

- A call can cost more time: one failed try, one retry and then the secondary call. The call timeout limits the time of each try.
- The secondary can give answers of lower quality or a different style. Cases that reach the approval step still need a team lead, so a weaker proposal does not skip a human check.
- The two adapters may have different prices. Token use and cost per run are recorded separately.
- Hedged requests are not used. They double the cost for every call to protect against a rare slow call. Reconsider them if latency becomes the main problem.
- The state of the breaker is in process memory. Each replica learns about a bad provider on its own.
