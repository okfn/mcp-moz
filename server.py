import logging
from pathlib import Path
from typing import Literal

import pandas as pd
from mcp.server.apps import Apps, ResourceCsp
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class Dataset(BaseModel):
    label: str
    data: list[float]

class ChartData(BaseModel):
    labels: list[str]
    datasets: list[Dataset]

class ChartConfig(BaseModel):
    type: Literal["bar", "line", "pie", "doughnut", "radar", "polarArea", "bubble", "scatter"]
    data: ChartData


# Get absolute path of current script
current_file = Path(__file__).resolve()

# Get directory of current script
script_dir = current_file.parent

# Calculate path to another file relative to the script
target_file = script_dir / "vehicles-2025.csv"
df = pd.read_csv(target_file)
df.rename(columns={"Nombre": "Cantidad"}, inplace=True)
df["Cantidad"] = df["Cantidad"].astype(int)

CHART_HTML = """\
<!DOCTYPE html>
<html>
<head>
  <title>Chart.js Bar Chart</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body>

  <canvas id="myChart"></canvas>

  <script>
    window.addEventListener("message", (event) => {
      console.log(event);
      const chartConfig = event.data?.params?.structuredContent;
      if (chartConfig) {
        const ctx = document.getElementById('myChart');
        new Chart(ctx, chartConfig);
      }
    });
  </script>
</body>
</html>
"""

apps = Apps()

@apps.tool(resource_uri="ui://charts/bar.html", description="A simple bar chart.")
def get_barchart(ctx: Context) -> ChartConfig:
    chart = ChartConfig(
        type="bar",
        data=ChartData(
            labels=["Red", "Blue", "Yellow", "Green", "Purple", "Orange"],
            datasets=[
                Dataset(label="Votes", data=[12, 19, 3, 5, 2, 3])
            ]
        )
    )
    return chart

@apps.tool(resource_uri="ui://charts/bar.html", description="A simple bar chart.")
def vehiculos_por_tipo_de_propulsion_barchart(ctx: Context) -> ChartConfig:
    result = df.groupby("Tipus_Propulsio")["Cantidad"].sum()
    chart = ChartConfig(
        type="bar",
        data=ChartData(
            labels=result.index.tolist(),
            datasets=[
                Dataset(label="Cantidad", data=result.values.tolist())
            ]
        )
    )
    return chart

apps.add_html_resource(
        "ui://charts/bar.html",
        CHART_HTML,
        title="Bar Chart",
        csp=ResourceCsp(resource_domains=["https://cdn.jsdelivr.net"], connect_domains=["https://cdn.jsdelivr.net"])
        )

mcp = MCPServer("clock", extensions=[apps])

@mcp.tool()
def vehiculos_por_tipo_de_propulsion() -> dict[str, int]:
    """Returns a table with statistics of the amount of vehicles by propulsion system.

    It covers the city of Barcelona and the year 2025."""
    result = df.groupby("Tipus_Propulsio")["Cantidad"].sum()
    return result.to_dict()


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

if __name__ == "__main__":
    mcp.run()
