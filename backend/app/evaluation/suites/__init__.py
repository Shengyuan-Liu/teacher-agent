"""Built-in suites."""

from app.evaluation.suites.multi_agent_coordination import MultiAgentCoordinationSuite
from app.evaluation.suites.rag_retrieval import RagRetrievalSuite
from app.evaluation.suites.router_contract import RouterContractSuite
from app.evaluation.suites.structured_output import StructuredOutputSuite

__all__ = [
    "MultiAgentCoordinationSuite",
    "RagRetrievalSuite",
    "RouterContractSuite",
    "StructuredOutputSuite",
]
