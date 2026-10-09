"""Print the Phase 5 LangGraph structure without starting services or models."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.service import AgentService  # noqa: E402


if __name__ == "__main__":
    graph = AgentService(None, None, None, None, None, None, None, None, None).graph
    print(graph.get_graph().draw_mermaid())
