import logging

from mcp.server import MCPServer

mcp = MCPServer("Demo")

logger = logging.getLogger(__name__)

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    logger.info(f"Executing add tool with {a} and {b}...")
    return a + b


@mcp.resource("greeting://{name}")
def greeting(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"
