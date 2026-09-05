"""MCP failures classified for retry and operator action."""


class MCPError(RuntimeError):
    retryable = False


class MCPTransientError(MCPError):
    retryable = True


class MCPRateLimitError(MCPTransientError):
    pass


class MCPAuthenticationError(MCPError):
    pass


class MCPToolNotFoundError(MCPError):
    pass


class MCPContractError(MCPError):
    pass
