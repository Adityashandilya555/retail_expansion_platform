"""DEMO ONLY — deliberately bad code to show what SonarQube Cloud catches on a PR. Never merge.

Each block is a mistake that could slip into the Z-Matrix API; the comment names what Sonar should report.
"""
import hashlib
import random
import sqlite3

from fastapi import FastAPI

app = FastAPI()

# Security hotspot: hard-coded credential (rule "Credentials should not be hard-coded")
DB_PASSWORD = "admin123"


@app.get("/sites")
def find_site(name: str):
    conn = sqlite3.connect("demo.db")
    # Vulnerability / hotspot: SQL built from user input (SQL injection) — use a parameterised query instead
    rows = conn.execute(f"SELECT * FROM sites WHERE name = '{name}'").fetchall()
    # Bug: asserting a tuple is always true, so this check never fails
    assert (len(rows) > 0, "no site found")
    return rows


def hash_password(password: str) -> str:
    # Security hotspot: MD5 is a weak hash for passwords — use argon2/bcrypt
    return hashlib.md5(password.encode()).hexdigest()


def reset_token() -> str:
    # Security hotspot: `random` is not cryptographically secure — use `secrets.token_urlsafe()`
    return str(random.randint(100000, 999999))


@app.get("/calc")
def calc(expr: str):
    # Security hotspot / vulnerability: dynamic code execution of user input
    return eval(expr)


def approve(level, chain, comment):
    unused = "temp"  # Code smell: unused local variable
    if level == 1:
        if chain:
            if comment:
                if len(comment) > 3:  # Code smell: deeply nested, hard to follow
                    return "approved"
    return "approved"  # Code smell: both branches return the same value
