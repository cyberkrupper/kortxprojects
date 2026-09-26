"""Console entry point for the MCP stdio server (collaborator-mcp.exe).

MCP clients launch this with no arguments and speak JSON-RPC over stdio.
"""

import os
import sys


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    from collaborator.mcp_server import main as mcp_main
    return mcp_main()


if __name__ == "__main__":
    sys.exit(main())
