# Copyright (c) ModelScope Contributors. All rights reserved.
from omegaconf import DictConfig
from typing import Any, List, Union

from ms_agent.llm import Message
from .base import Agent


class CodeAgent(Agent):
    """A code class can be executed in a `CodeAgent` in a workflow"""

    AGENT_NAME = 'CodeAgent'

    def __init__(self,
                 config: DictConfig,
                 tag: str,
                 trust_remote_code: bool = False,
                 **kwargs):
        super().__init__(config, tag, trust_remote_code, **kwargs)
        self.load_cache = kwargs.get('load_cache', False)
        
        # Capability gap fix: artifact_missing / task_implementation_incomplete
        # Inject a system directive to force artifact verification before task completion
        self._artifact_verification_reminder = (
            "\n\n[CRITICAL SYSTEM DIRECTIVE - ARTIFACT VERIFICATION]:\n"
            "1. You MUST execute any scripts you write to generate output files. Do not just write the code and stop.\n"
            "2. After execution, you MUST run terminal commands (e.g., `ls -l /app/`, `cat /app/output.toml`) "
            "to explicitly VERIFY that the required artifacts exist in the EXACT expected paths and contain valid data.\n"
            "3. Do NOT assume a file was created just because your script finished with exit code 0 or because you wrote code to save it. "
            "Hallucinating file creation will result in task failure.\n"
            "4. If verification fails, you must debug and fix the issue before concluding the task."
        )
        try:
            if hasattr(self.config, 'system_prompt'):
                self.config.system_prompt = str(self.config.system_prompt) + self._artifact_verification_reminder
            elif hasattr(self.config, 'prompt'):
                self.config.prompt = str(self.config.prompt) + self._artifact_verification_reminder
            elif isinstance(self.config, dict) and 'system_prompt' in self.config:
                self.config['system_prompt'] = str(self.config['system_prompt']) + self._artifact_verification_reminder
            elif isinstance(self.config, dict) and 'prompt' in self.config:
                self.config['prompt'] = str(self.config['prompt']) + self._artifact_verification_reminder
        except Exception:
            pass

    async def run(self, inputs: Union[str, List[Message]],
                  **kwargs) -> List[Message]:
        """Run the external code. Default implementation here does nothing.

        Args:
            inputs(`Union[str, List[Message]]`): The inputs can be a prompt string,
                or a list of messages from the previous agent

        Returns:
            The messages to output to the next agent
        """
        # Inject artifact verification reminder into inputs
        if hasattr(self, '_artifact_verification_reminder'):
            if isinstance(inputs, list):
                # Use 'user' role at the end to ensure it's seen as a final directive
                # without violating system message ordering constraints of many LLM APIs
                inputs.append(Message(role='user', content=self._artifact_verification_reminder))
            elif isinstance(inputs, str):
                inputs = inputs + self._artifact_verification_reminder

        _config = None
        _messages = None
        if self.load_cache:
            _config, _messages = self.read_history(inputs)
        if _config is not None and _messages is not None:
            self.config = _config
            return _messages
        messages = await self.execute_code(inputs, **kwargs)
        self.save_history(messages, **kwargs)
        return messages

    async def execute_code(self, inputs: Union[str, List[Message]],
                           **kwargs) -> List[Message]:
        return inputs
