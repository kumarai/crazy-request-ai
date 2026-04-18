"""Telecom customer-support MCP server.

Exposes read + write tools over streamable HTTP. Pluggable storage:
SQLite for development and fixtures, HTTP for calling real downstream
APIs in production (select via ``MCP_BACKEND`` env).
"""
