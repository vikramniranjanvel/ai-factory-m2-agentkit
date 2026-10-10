"""Write the JSON Schema files (for editor autocomplete and CI): uv run python -m agentkit.schemas.export"""

import json
from pathlib import Path

from pydantic import BaseModel

from agentkit.schemas.agent_definition import AgentDefinition
from agentkit.schemas.evaluation_config import EvaluationConfig

OUT = Path(__file__).resolve().parents[3] / "schemas"
SCHEMAS: dict[str, type[BaseModel]] = {
    "agent-definition.schema.json": AgentDefinition,
    "evaluation-config.schema.json": EvaluationConfig,
}

if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for filename, model in SCHEMAS.items():
        (OUT / filename).write_text(json.dumps(model.model_json_schema(), indent=2) + "\n")
