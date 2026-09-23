"""Permission module — dual-layer permission control for tool calls.

Outer layer (PermissionEnforcer): user-intent based, configurable, overridable.
Inner layer (SafetyGuard): safety baseline, non-bypassable.
"""

from .ask_resolver import resolve_ask
from .config import PermissionConfig, SafetyConfig
from .enforcer import PermissionDecision, PermissionEnforcer
from .handler import (AutoPermissionHandler, CLIPermissionHandler,
                      PermissionAction, PermissionHandler, PermissionResponse,
                      WebPermissionHandler)
from .memory import PermissionMemory
from .approval import (ApprovalConflictError, ApprovalRequest, ApprovalStore,
                       FileApprovalStore, MemoryApprovalStore)
from .ledger import PermissionLedger
from .provider import (AgentDecisionProvider, LlmDecisionProvider,
                       PermissionDecisionProvider, ProviderDecision,
                       VerifierAgentDecisionProvider)
from .routes import DelegateRoute, select_delegate_route
from .safety import SafetyGuard

from .pattern_guard import UnsafePersistPattern

__all__ = [
    'resolve_ask',
    'PermissionConfig',
    'SafetyConfig',
    'PermissionDecision',
    'PermissionEnforcer',
    'AutoPermissionHandler',
    'CLIPermissionHandler',
    'PermissionAction',
    'PermissionHandler',
    'PermissionResponse',
    'WebPermissionHandler',
    'PermissionMemory',
    'PermissionLedger',
    'ApprovalConflictError',
    'ApprovalRequest',
    'ApprovalStore',
    'FileApprovalStore',
    'MemoryApprovalStore',
    'PermissionDecisionProvider',
    'ProviderDecision',
    'LlmDecisionProvider',
    'AgentDecisionProvider',
    'VerifierAgentDecisionProvider',
    'DelegateRoute',
    'select_delegate_route',
    'SafetyGuard',
    'UnsafePersistPattern',
]
