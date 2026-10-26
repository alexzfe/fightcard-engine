"""History store (stub)."""

from __future__ import annotations

TABLES = ("events", "fights", "fighters", "pages")


def is_ufc_event(name, listed):
    raise NotImplementedError


def connect(path):
    raise NotImplementedError


def upsert_event(conn, event, is_ufc, source):
    raise NotImplementedError


def upsert_fighter(conn, fighter_id, name, page=None, fetched_at=None):
    raise NotImplementedError


def dump(conn, out_dir):
    raise NotImplementedError


def rating_bouts(conn, as_of=None):
    raise NotImplementedError


def names(conn):
    raise NotImplementedError
