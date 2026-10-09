"""A minimal Starlette app acting as an MCP client for the local MCP server.

The server (server.py) already runs as streamable-http at http://localhost:8000/mcp.
This app is a tiny host that:

* GET  /            -> lists the server's tools and offers a form to call one.
* POST /call        -> calls the tool. If the tool carries ``_meta.ui.resourceUri``
                       (the MCP Apps extension, ``io.modelcontextprotocol/ui``), the
                       result page embeds the referenced ``ui://`` HTML resource in a
                       sandboxed iframe and delivers the tool result to it via
                       ``postMessage`` as a ``notifications/call_tool_result`` JSON-RPC
                       notification (the payload shape the resource expects:
                       ``event.data.params.structuredContent``).
* GET  /ui-resource -> proxies a ``ui://`` resource (read via resources/read) so the
                       iframe can load it by URL.

Run with:  uv run uvicorn app:app --port 8080
"""

from __future__ import annotations

import json
from html import escape
from urllib.parse import quote

import uvicorn
from mcp import Client
from mcp.types import CallToolResult
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Route

MCP_SERVER_URL = "http://localhost:8000/mcp"

PAGE = """<!DOCTYPE html>
<html>
<head><title>MCP Client</title></head>
<body>
{body}
</body>
</html>
"""


def page(body: str) -> HTMLResponse:
    return HTMLResponse(PAGE.format(body=body))


def error_page(message: str) -> HTMLResponse:
    return page(f"<h1>Error</h1><p>{escape(message)}</p><p><a href='/'>Back</a></p>")


async def index(request: Request) -> HTMLResponse:
    """List the server's tools and render a simple call form."""
    async with Client(MCP_SERVER_URL) as client:
        result = await client.list_tools()

    items = []
    options = []
    for tool in result.tools:
        resource_uri = (tool.meta or {}).get("ui", {}).get("resourceUri")
        ui_note = f" <em>(UI resource: {escape(resource_uri)})</em>" if resource_uri else ""
        items.append(f"<li><strong>{escape(tool.name)}</strong> &mdash; {escape(tool.description or '')}{ui_note}</li>")
        options.append(f"<option value='{escape(tool.name)}'>{escape(tool.name)}</option>")

    body = f"""
<h1>MCP Client</h1>
<h2>Tools</h2>
<ul>
{''.join(items)}
</ul>
<h2>Call a tool</h2>
<form method="post" action="/call">
  <label for="tool">Tool</label><br>
  <select id="tool" name="tool">{''.join(options)}</select><br><br>
  <label for="arguments">Arguments (JSON)</label><br>
  <textarea id="arguments" name="arguments" rows="5" cols="60">{{}}</textarea><br><br>
  <button type="submit">Call</button>
</form>
"""
    return page(body)


async def call(request: Request) -> HTMLResponse:
    """Call the selected tool and render text/structured output, plus the UI app."""
    form = await request.form()
    tool_name = str(form.get("tool", ""))
    raw_arguments = str(form.get("arguments") or "{}").strip() or "{}"

    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        return error_page(f"Arguments are not valid JSON: {exc}")

    try:
        async with Client(MCP_SERVER_URL) as client:
            tools = (await client.list_tools()).tools
            tool = next((t for t in tools if t.name == tool_name), None)
            if tool is None:
                return error_page(f"Unknown tool: {tool_name}")
            resource_uri = (tool.meta or {}).get("ui", {}).get("resourceUri")
            result: CallToolResult = await client.call_tool(tool_name, arguments)
    except Exception as exc:  # noqa: BLE001 - simple test client
        return error_page(f"MCP call failed: {exc}")

    # Spec-shaped CallToolResult params for notifications/call_tool_result.
    payload = {
        k: v
        for k, v in result.model_dump(by_alias=True, exclude_none=True, mode="json").items()
        if k in ("content", "structuredContent", "isError")
    }
    payload_json = json.dumps(payload).replace("</", "<\\/")

    sections = [f"<h1>Result: {escape(tool_name)}</h1>", "<p><a href='/'>Back</a></p>"]

    if result.structured_content is not None:
        sections.append(
            "<h2>Structured content</h2>"
            f"<pre>{escape(json.dumps(result.structured_content, indent=2, ensure_ascii=False))}</pre>"
        )
    else:
        sections.append(
            "<h2>Content</h2>"
            f"<pre>{escape(str(result.content))}</pre>"
        )

    if resource_uri:
        sections.append(f"<h2>UI resource: {escape(resource_uri)}</h2>")
        sections.append(
            f'<iframe id="app-frame" src="/ui-resource?uri={quote(resource_uri, safe="")}" '
            'sandbox="allow-scripts" width="100%" height="480" title="MCP App"></iframe>'
        )
        # Deliver the tool result to the sandboxed iframe, like an MCP Apps host does.
        sections.append(
            '<script type="application/json" id="call-result">' + payload_json + "</script>"
            "<script>\n"
            "  const frame = document.getElementById('app-frame');\n"
            "  const payload = JSON.parse(document.getElementById('call-result').textContent);\n"
            "  const deliver = () => frame.contentWindow.postMessage({\n"
            "    jsonrpc: '2.0',\n"
            "    method: 'notifications/call_tool_result',\n"
            "    params: payload,\n"
            "  }, '*');\n"
            "  let ready = false;\n"
            "  try { ready = frame.contentWindow.document.readyState === 'complete'; } catch (e) { /* sandboxed iframe: opaque origin */ }\n"
            "  if (ready) {\n"
            "    deliver();\n"
            "  } else {\n"
            "    frame.addEventListener('load', deliver);\n"
            "  }\n"
            "</script>"
        )

    return page("\n".join(sections))


async def ui_resource(request: Request) -> HTMLResponse:
    """Proxy a ui:// resource from the MCP server so the iframe can load it."""
    uri = request.query_params.get("uri", "")
    if not uri.startswith("ui://"):
        return error_page(f"Not a ui:// resource: {uri}")

    async with Client(MCP_SERVER_URL) as client:
        result = await client.read_resource(uri)

    html = next((c.text for c in result.contents if getattr(c, "text", None)), None)
    if html is None:
        return error_page(f"Resource {uri} has no text content")
    return HTMLResponse(html)


app = Starlette(
    routes=[
        Route("/", index, methods=["GET"]),
        Route("/call", call, methods=["POST"]),
        Route("/ui-resource", ui_resource, methods=["GET"]),
    ]
)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
