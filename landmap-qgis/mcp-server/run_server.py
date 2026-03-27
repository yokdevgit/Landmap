"""Launcher script for the landmap MCP server."""
import sys
import os

# Add the mcp-server directory to path
server_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, server_dir)

# Now import and run the server
from src.server import main
import asyncio

if __name__ == "__main__":
    asyncio.run(main())
