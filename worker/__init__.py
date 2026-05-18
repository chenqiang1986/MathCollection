"""Offline worker that drains the per-user raw-file queues by calling the
agent on each pending file. Long-running daemon; not invoked from the web
request path."""
