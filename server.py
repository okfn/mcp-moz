import logging
from pathlib import Path
from typing import Literal

import pandas as pd
from mcp.server.apps import Apps, ResourceCsp
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from pydantic import BaseModel

logger = logging.getLogger(__name__)


### Get Sample Data for Analisis
### https://opendata-ajuntament.barcelona.cat/data/es/dataset/est_vehicles_propulsio/resource/5b3dc38e-5c04-40af-97fe-d16e5c161c0d
current_file = Path(__file__).resolve()
script_dir = current_file.parent
target_file = script_dir / "vehicles-2025.csv"
df = pd.read_csv(target_file)
df.rename(columns={"Nombre": "Cantidad"}, inplace=True)
df["Cantidad"] = df["Cantidad"].astype(int)


### Pydantic Models for returning Charts Configurations from tools.
class Dataset(BaseModel):
    label: str
    data: list[float]

class ChartData(BaseModel):
    labels: list[str]
    datasets: list[Dataset]

class ChartConfig(BaseModel):
    type: Literal["bar", "line", "pie", "doughnut", "radar", "polarArea", "bubble", "scatter"]
    data: ChartData


### Chart HTML to register as a UI Resource
CHART_HTML = """\
<!DOCTYPE html>
<html>
<head>
  <title>Chart</title>
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

### Define the App Extension with the tool and resources to return charts.
apps = Apps()

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

### Create the MCP Server, register the extension and other tools.
mcp = MCPServer("clock", extensions=[apps])

@mcp.tool()
def vehiculos_por_tipo_de_propulsion() -> dict[str, int]:
    """Returns a table with statistics of the amount of vehicles by propulsion system.

    It covers the city of Barcelona and the year 2025."""
    result = df.groupby("Tipus_Propulsio")["Cantidad"].sum()
    return result.to_dict()

