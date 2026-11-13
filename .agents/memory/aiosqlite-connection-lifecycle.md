---
name: aiosqlite connection lifecycle
description: Avoid double-starting aiosqlite worker threads when opening SQLite connections.
---

Use a single async context manager to open each aiosqlite connection, apply per-connection PRAGMAs, and yield the connection. Do not await a connection before entering its async context manager.

**Why:** The bot's database helper awaited a connection and then entered it with `async with`, which tried to start the worker thread a second time and raised `RuntimeError: threads can only be started once`.

**How to apply:** When adding SQLite stores or changing connection helpers, keep connection creation, PRAGMA setup, and cleanup within one `@asynccontextmanager` helper and use it with `async with`.
