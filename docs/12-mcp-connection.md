# 12 · MCP Connection

**Model Context Protocol (MCP)** lets AI assistants (Claude, IDE agents, internal copilots) call tools in a standard way. ValueGraph uses MCP in two directions:

1. **Product:** a `valuegraph-mcp` server so an underwriter's AI assistant can ask *"value this property"* or *"why is this one flagged?"* and get the same audited result as the dashboard.
2. **Development:** MCP servers that help *you* build (read-only DB access, GitHub).

---

## 1. `valuegraph-mcp` server (product)

**Design rule:** the MCP server is a thin client of the REST API. It never touches the DB or model directly — so auth, validation, audit and rate limits all still apply.

### Tools

| Tool | Calls | Side effect |
|---|---|---|
| `request_valuation` | `POST /valuations` | Creates a valuation (audited) |
| `get_valuation` | `GET /valuations/{id}` | none |
| `explain_valuation` | `GET /valuations/{id}` | none — returns factors + reason codes as text |
| `find_comparables` | `GET /comparables` | none |
| `market_summary` | `GET /markets/{city}/summary` | none |
| `model_status` | `GET /models/status` | none |

No tools for overrides, deletes or admin — those stay human-only in the dashboard.

### Implementation: `src/valuegraph/mcp_server.py` (abridged; see the file for all 5 tools)

```python
import os
import httpx
from mcp.server.mcpserver import MCPServer  # MCP Python SDK v2 (FastMCP was renamed)

mcp = MCPServer("valuegraph")
API = os.environ["VALUEGRAPH_API_URL"]
KEY = os.environ["VALUEGRAPH_API_KEY"]  # scoped key: valuations:read/write, markets:read


def _client() -> httpx.Client:
    return httpx.Client(base_url=API, headers={"X-API-Key": KEY}, timeout=15)


@mcp.tool()
def get_valuation(valuation_id: str) -> dict:
    """Fetch a ValueGraph valuation by id (e.g. VAL-2026-000123)."""
    with _client() as c:
        r = c.get(f"/api/v1/valuations/{valuation_id}")
        r.raise_for_status()
        return r.json()


@mcp.tool()
def request_valuation(
    lat: float,
    lon: float,
    property_type: str,
    area_value: float,
    area_type: str,
    bedrooms: int | None = None,
    age_years: float | None = None,
) -> dict:
    """Value a property. area_type: carpet | built_up | super_built_up | plot."""
    # TODO(you): build the request body exactly as in docs/API.md, add an Idempotency-Key
    ...


# TODO(you): explain_valuation -> turn factors + reason_codes into a short readable string

if __name__ == "__main__":
    mcp.run()  # stdio transport
```

Write clear docstrings — the AI client reads them to decide when and how to call a tool.

### Connect from Claude Desktop (local)
`claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "valuegraph": {
      "command": "uv",
      "args": ["--directory", "/path/to/valuegraph-ai", "run", "--extra", "mcp", "python", "-m", "valuegraph.mcp_server"],
      "env": {
        "VALUEGRAPH_API_URL": "http://localhost:8000",
        "VALUEGRAPH_API_KEY": "vg_<prefix>_<secret> from `valuegraph create-api-key`"
      }
    }
  }
}
```
For remote use (pilot), run the server with the streamable HTTP transport behind auth instead of stdio.

### Test it
```bash
npx @modelcontextprotocol/inspector uv run python -m valuegraph.mcp_server
```
The Inspector lets you call each tool by hand before wiring an AI client.

### Safety
- Dedicated API key per MCP deployment, least scopes, short expiry.
- `request_valuation` should be confirmed by the user in the client.
- Treat everything returned by tools as data. Don't put free text from listings into tool descriptions.
- Every MCP call is visible in the audit log as actor `api_key:<prefix>:<owner email>`.

## 2. Development MCP servers

| Server | Use | Guardrail |
|---|---|---|
| Postgres MCP | Let your coding assistant inspect schema / run SELECTs | Connect as `vg_readonly` role only |
| GitHub MCP | Issues, PRs from the assistant | Fine-grained token, single repo |
| Filesystem | Read `docs/`, `data/sample/` | Don't expose `data/raw` or `.env` |

Add them in your assistant's MCP config the same way as above. Never give a dev MCP server write access to a database holding real lender data.
