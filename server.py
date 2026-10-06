import logging
import json
import pandas as pd
import urllib.request

from mcp.server import MCPServer

mcp = MCPServer("Demo")

logger = logging.getLogger(__name__)

df = pd.DataFrame()

with urllib.request.urlopen('https://opendata-ajuntament.barcelona.cat/data/api/action/datastore_search?resource_id=5b3dc38e-5c04-40af-97fe-d16e5c161c0d&limit=6000') as f:
    data = json.loads(f.read().decode('utf-8'))
    df = pd.DataFrame(data["result"]["records"])
    df.rename(columns={"Nombre": "Cantidad"}, inplace=True)
    df["Cantidad"] = df["Cantidad"].astype(int)
    logger.info(df.head())

@mcp.tool()
def vehiculos_por_tipo_de_propulsion() -> dict[str, int]:
    """Returns a table with statistics of the amount of vehicles by propulsion system.

    It covers the city of Barcelona and the year 2025."""
    result = df.groupby("Tipus_Propulsio")["Cantidad"].sum()
    return result.to_dict()


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    logger.info(f"Executing add tool with {a} and {b}...")
    return a + b


@mcp.resource("greeting://{name}")
def greeting(name: str) -> str:
    """Greet someone by name."""
    return f"Hello, {name}!"
