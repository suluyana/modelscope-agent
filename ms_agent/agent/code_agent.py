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
        
        # Capability gap fix: artifact_missing
        # Inject a system directive to force artifact verification before task completion
        self._artifact_verification_reminder = (
            "\n\n[SYSTEM DIRECTIVE - ARTIFACT VERIFICATION]: "
            "Before concluding your task or sending a final response, you MUST explicitly verify "
            "that all required output files/artifacts have been successfully created in the correct "
            "locations and contain the expected content. Use commands like 'ls -l', 'cat', or 'file' "
            "to confirm. Do NOT assume a command succeeded in creating a file just because it returned "
            "exit code 0. If a file is missing or incorrect, you must debug and fix it before finishing."
        )
        try:
            if hasattr(self.config, 'prompt'):
                self.config.prompt = str(self.config.prompt) + self._artifact_verification_reminder
            elif hasattr(self.config, 'system_prompt'):
                self.config.system_prompt = str(self.config.system_prompt) + self._artifact_verification_reminder
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
                inputs.append(Message(role='system', content=self._artifact_verification_reminder))
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
