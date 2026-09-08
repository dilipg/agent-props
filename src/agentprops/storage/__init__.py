"""Storage adapters behind the Store Protocol.

Filled at M3 (SQLite via SQLAlchemy 2.x Core plus Alembic) and M7
(Postgres, Mongo). All access goes through the Protocol in storage/base.py,
never through an adapter directly.
"""
