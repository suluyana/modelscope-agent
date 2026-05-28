import asyncio
from typing import Any, Dict, Tuple

from ms_agent.self_improve.adapters.base import RunAdapter
from ms_agent.self_improve.orchestrator import SelfImproveOrchestrator

class LLMAgentApiAdapter(RunAdapter):
    def __init__(self, agent_or_engine, query: str, output_dir: str):
        self.agent = agent_or_engine
        self.query = query
        self._output_dir = output_dir

    @property
    def name(self) -> str:
        return "llmagent_api_run"

    @property
    def output_dir(self) -> str:
        return self._output_dir

    def run_target(self) -> Tuple[bool, Dict[str, Any]]:
        # In a real implementation, we would wrap the async call and catch exceptions
        # to record them into exception.txt in the output_dir.
        try:
            # Simplistic synchronous wrapper for demonstration
            asyncio.run(self.agent.run(self.query))
            return True, {"exit_code": 0, "reward": None}
        except Exception as e:
            # We would write this to exception.txt
            return False, {"exit_code": 1, "reward": None, "exception": str(e)}

    def get_context(self) -> Dict[str, str]:
        return {"query": self.query}

def run_with_self_improve(agent_or_engine, query: str, config: Dict[str, Any]) -> Any:
    """
    Python API Wrapper for Self-Improve.
    If self_improve is disabled, just runs normally.
    """
    si_config = config.get("self_improve", {})
    if not si_config.get("enabled", False):
        return asyncio.run(agent_or_engine.run(query))

    import uuid
    run_id = f"si_{uuid.uuid4().hex[:8]}"
    
    # We would pull output_dir from config or default
    output_dir = f"outputs/self_improve/{run_id}"
    
    adapter = LLMAgentApiAdapter(agent_or_engine, query, output_dir)
    orchestrator = SelfImproveOrchestrator(run_id, adapter, si_config)
    
    # Run the self-improve loop
    orchestrator.run_loop()
    
    # After the loop finishes (and presumably fixes things if possible), we can return a final run or status.
    # For now, we return None as the loop itself handled the execution.
    return None
