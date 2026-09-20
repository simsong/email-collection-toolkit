# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""Shared identity picker queries and immediate, durable manual decisions."""
from __future__ import annotations

import sqlite3
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class AddressRow(BaseModel):
    address_id: int
    address: str
    person_id: int
    canonical_name: str
    first_use: str | None
    last_use: str | None
    messages: int
    signature_messages: int = 0


class OrganizationRow(BaseModel):
    organization_id: int
    name: str
    domains: list[str]
    addresses: list[AddressRow]


class IdentityFilter(BaseModel):
    name: str = ""
    mailbox: str = ""
    domain: str = ""
    start: date | None = None
    end: date | None = None


class ManualDecision(BaseModel):
    operation: Literal["rename-person", "merge-person", "rename-organization", "affiliate", "move-address", "separate-address"]
    subject: int = Field(gt=0)
    target: int | None = Field(default=None, gt=0)
    name: str | None = None
    start: date | None = None
    end: date | None = None


def addresses(database: sqlite3.Connection, filters: IdentityFilter) -> list[AddressRow]:
    if filters.start and filters.end and filters.start > filters.end:
        raise ValueError("start date must not follow end date")
    start = filters.start.isoformat() if filters.start else None
    end = filters.end.isoformat() if filters.end else None
    rows = database.execute("""SELECT a.address_id,a.address,p.person_id,p.canonical_name,
        min(m.seen_at),max(m.seen_at),count(DISTINCT CASE WHEN m.kind='header' THEN m.message_id END),
        count(DISTINCT CASE WHEN m.kind='signature' THEN m.message_id END)
        FROM addresses a JOIN person_addresses USING(address_id) JOIN persons p USING(person_id)
        LEFT JOIN message_addresses m ON m.address_id=a.address_id
        WHERE instr(lower(p.canonical_name),lower(?))>0 AND instr(lower(a.mailbox),lower(?))>0
        AND instr(lower(a.domain),lower(?))>0 AND (? IS NULL OR date(m.seen_at)>=?)
        AND (? IS NULL OR date(m.seen_at)<=?) GROUP BY a.address_id ORDER BY lower(p.canonical_name),a.address""",
        (filters.name, filters.mailbox, filters.domain, start, start, end, end))
    return [AddressRow(address_id=row[0], address=row[1], person_id=row[2], canonical_name=row[3],
                       first_use=row[4], last_use=row[5], messages=row[6], signature_messages=row[7]) for row in rows]


def organizations(database: sqlite3.Connection, filters: IdentityFilter) -> list[OrganizationRow]:
    entries = addresses(database, filters.model_copy(update={"name": ""}))
    result: list[OrganizationRow] = []
    for organization_id, name in database.execute("SELECT organization_id,name FROM organizations WHERE instr(lower(name),lower(?))>0 ORDER BY lower(name)", (filters.name,)):
        domains = [row[0] for row in database.execute("SELECT domain FROM organization_domains WHERE organization_id=? ORDER BY domain", (organization_id,))]
        members = [entry for entry in entries if any(entry.address.rpartition("@")[2] == domain or entry.address.endswith("." + domain) for domain in domains)]
        if members or not (filters.mailbox or filters.domain or filters.start or filters.end):
            result.append(OrganizationRow(organization_id=organization_id, name=name, domains=domains, addresses=members))
    return result


def edit(database: sqlite3.Connection, decision: ManualDecision) -> None:
    """Caller holds the writer lease; every accepted edit and audit record commit together."""
    if decision.start and decision.end and decision.start > decision.end:
        raise ValueError("start date must not follow end date")
    table = ("organizations" if decision.operation == "rename-organization" else
             "addresses" if decision.operation in ("move-address", "separate-address") else "persons")
    key = {"organizations": "organization_id", "addresses": "address_id", "persons": "person_id"}[table]
    if database.execute(f"SELECT 1 FROM {table} WHERE {key}=?", (decision.subject,)).fetchone() is None:
        raise ValueError("unknown manual decision subject")
    with database:
        if decision.operation in ("move-address", "separate-address"):
            target = decision.target
            if decision.operation == "separate-address":
                address = database.execute("SELECT address FROM addresses WHERE address_id=?", (decision.subject,)).fetchone()[0]
                target = database.execute("INSERT INTO persons(canonical_name,manual) VALUES(?,1)", (address,)).lastrowid
            elif database.execute("SELECT 1 FROM persons WHERE person_id=?", (target,)).fetchone() is None:
                raise ValueError("move requires an existing target person")
            database.execute("UPDATE person_addresses SET person_id=?,manual=1 WHERE address_id=?", (target, decision.subject))
        elif decision.operation.startswith("rename-"):
            if not decision.name or not decision.name.strip():
                raise ValueError("a nonempty name is required")
            column = "name" if table == "organizations" else "canonical_name"
            database.execute(f"UPDATE {table} SET {column}=?,manual=1 WHERE {key}=?", (decision.name.strip(), decision.subject))
        elif decision.operation == "merge-person":
            if decision.target == decision.subject or database.execute("SELECT 1 FROM persons WHERE person_id=?", (decision.target,)).fetchone() is None:
                raise ValueError("merge requires a different existing target person")
            database.execute("INSERT OR IGNORE INTO person_aliases SELECT ?,canonical_name FROM persons WHERE person_id=?", (decision.target, decision.subject))
            database.execute("INSERT OR IGNORE INTO person_aliases SELECT ?,name FROM person_aliases WHERE person_id=?", (decision.target, decision.subject))
            database.execute("UPDATE person_addresses SET person_id=?,manual=1 WHERE person_id=?", (decision.target, decision.subject))
            database.execute("UPDATE affiliations SET person_id=? WHERE person_id=?", (decision.target, decision.subject))
            database.execute("UPDATE evidence SET person_id=? WHERE person_id=?", (decision.target, decision.subject))
            database.execute("DELETE FROM person_aliases WHERE person_id=?", (decision.subject,))
            database.execute("DELETE FROM persons WHERE person_id=?", (decision.subject,))
        else:
            if database.execute("SELECT 1 FROM organizations WHERE organization_id=?", (decision.target,)).fetchone() is None:
                raise ValueError("affiliation requires an existing organization")
            database.execute("INSERT INTO affiliations(person_id,organization_id,start_date,end_date,manual) VALUES(?,?,?,?,1)",
                             (decision.subject, decision.target, decision.start.isoformat() if decision.start else None,
                              decision.end.isoformat() if decision.end else None))
        database.execute("INSERT INTO manual_decisions(subject,operation,value) VALUES(?,?,?)", (str(decision.subject), decision.operation, decision.model_dump_json()))
