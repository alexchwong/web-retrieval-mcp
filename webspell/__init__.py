"""webSpell: web search, evidence passages and page text, as a library and an MCP server."""
from ._version import INTERFACE_VERSION, __version__
from .api import TOOLS, call, evidence_passages, page_text, result_bytes, search_results
from .settings import Settings, load_settings

__all__ = [
    "__version__",
    "INTERFACE_VERSION",
    "TOOLS",
    "search_results",
    "evidence_passages",
    "page_text",
    "call",
    "result_bytes",
    "Settings",
    "load_settings",
]
