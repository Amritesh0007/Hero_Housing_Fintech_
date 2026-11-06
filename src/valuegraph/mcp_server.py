"""MCP server: lets AI assistants use ValueGraph through the same audited REST API as everyone else.

Run: VALUEGRAPH_API_URL=... VALUEGRAPH_API_KEY=vg_... python -m valuegraph.mcp_server
No override / review / admin tools on purpose — those stay human-only in the web app.
"""

import os
import uuid

import httpx
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("valuegraph")


def _call(method: str, path: str, **kw) -> dict | list:
    with httpx.Client(
        base_url=os.environ.get("VALUEGRAPH_API_URL", "http://localhost:8000"),
        headers={"X-API-Key": os.environ["VALUEGRAPH_API_KEY"]},
        timeout=30,
    ) as c:
        r = c.request(method, path, **kw)
        if r.status_code >= 400:
            return {"error": r.status_code, "detail": r.json().get("detail") if r.content else ""}
        return r.json()


@mcp.tool()
def request_valuation(
    city: str,
    locality: str,
    area_sqft: float,
    area_type: str,
    bhk: int,
    transaction: str = "Resale",
    loan_amount_inr: int | None = None,
    society: str | None = None,
) -> dict:
    """Value an apartment in India. locality must match a locality ValueGraph knows for that city
    (e.g. "Gachibowli"). area_type: carpet | built_up | super_built_up. transaction: Resale | New Property. society (building /
    project name) is optional but sharpens the estimate when other flats in that building are on file.
    Returns value, low/high range, reliability grade, routing decision, top factors and comparables.
    Creates an audited valuation record — confirm with the user before calling."""
    body = {
        "city": city,
        "locality": locality,
        "area_sqft": area_sqft,
        "area_type": area_type,
        "bhk": bhk,
        "transaction": transaction,
        "loan_amount_inr": loan_amount_inr,
        "society": society,
    }
    return _call("POST", "/api/v1/valuations", json=body, headers={"Idempotency-Key": str(uuid.uuid4())})


@mcp.tool()
def get_valuation(valuation_id: str) -> dict:
    """Fetch an existing valuation by id, e.g. VAL-1A2B3C4D5E6F."""
    return _call("GET", f"/api/v1/valuations/{valuation_id}")


@mcp.tool()
def explain_valuation(valuation_id: str) -> str:
    """Plain-language explanation of why a valuation came out as it did and why it was routed where it was."""
    v = _call("GET", f"/api/v1/valuations/{valuation_id}")
    if "error" in v:
        return f"Could not load {valuation_id}: {v['detail']}"
    if not v["value"]:
        return f"No value issued ({', '.join(v['reason_codes'])}); routed to {v['route']}."
    lines = [
        f"Value ₹{v['value']:,} (range ₹{v['low']:,}–₹{v['high']:,}), reliability {v['reliability']}, "
        f"route {v['route']}. Basis: {v['basis']}."
    ]
    lines += [
        f"- {f['label']}: {'+' if f['contribution_inr'] >= 0 else '-'}₹{abs(f['contribution_inr']):,}"
        for f in v["factors"][:5]
    ]
    if v["reason_codes"]:
        lines.append("Flags: " + ", ".join(v["reason_codes"]))
    return "\n".join(lines)


@mcp.tool()
def market_summary(city: str) -> dict:
    """Median asking ₹/sqft for a city and its priciest localities (asking prices, not transactions)."""
    return _call("GET", f"/api/v1/markets/{city}/summary")


@mcp.tool()
def model_status() -> dict:
    """Deployed model version, supported cities and evaluation metrics (with what ground truth they use)."""
    return _call("GET", "/api/v1/models/status")


if __name__ == "__main__":
    mcp.run()
