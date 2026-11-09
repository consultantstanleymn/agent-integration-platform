# ADR 0001: One MCP write tool for case actions

Status: accepted.

## Context

The case graph must change the outside world in one way only: it creates a return authorization. A model chooses the proposal. Code must decide what the write can do. A prompt-injection attack must not find a second write path.

## Options

1. **Direct database write in the graph node.** This is simple and fast. The write limits sit inside graph code. Any later node can add another write without a review step.
2. **REST call to an internal service.** This gives a clear boundary. It adds a service to deploy and a second set of credentials to manage.
3. **MCP tool server with one tool.** The server runs as its own process. It exposes one named tool with a JSON schema. The tenant is set when the server starts. The tool checks the amount limit and the order id pattern.

## Decision

Use option 3. The tool is `create_return_authorization`. The tenant id comes from the server start argument. It is never a tool input, and the server rejects an unknown argument such as `tenant_id`. The amount limit comes from the hard cap in `docs/engagement/AUTHORITY.md`. The idempotency key is `<thread_id>:act`, so a resume cannot create a second record.

The graph reaches the tool only through the `ActionPort` in `src/aip/graph/actions.py`. A static test reads every module in `src/aip/graph/`. It fails when a module imports an HTTP client, a write tool or the agent tool registry.

## Consequences

- Each call starts a server process. This costs time. It is acceptable for a case flow that already waits for a person.
- The MCP path adds one more failure point. The in-process adapter keeps unit tests fast. Both adapters call the same insert function.
- The tool call is a clear audit point. The server writes the audit row `mcp.create_return_authorization`.
- The static test checks the graph package. It does not prove that no other code path writes. Code review must keep that rule.
