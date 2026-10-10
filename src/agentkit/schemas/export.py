"""Write the JSON Schema files (for editor autocomplete and CI): uv run python -m agentkit.schemas.export"""

import json
from pathlib import Path

from agentkit.schemas.agent_definition import AgentDefinition

OUT = Path(__file__).resolve().parents[3] / "schemas"

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    (OUT / "agent-definition.schema.json").write_text(json.dumps(AgentDefinition.model_json_schema(), indent=2) + "\n")
