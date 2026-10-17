from dataclasses import dataclass


@dataclass(frozen=True)
class Error:
    """One validation problem: a JSON path into the document and a human message."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


def join(*parts: str | int) -> str:
    """Build `flows[0].stages[2].tasks[0]`-style paths: ints become `[n]`, strings `.name`."""
    out = ""
    for p in parts:
        if isinstance(p, int):
            out += f"[{p}]"
        else:
            out += f".{p}" if out else p
    return out


def loc_to_path(loc: tuple[str | int, ...]) -> str:
    """Pydantic error location → path. Union/discriminator tags (`Task`, `str`, …) are dropped."""
    kept: list[str | int] = []
    for p in loc:
        if isinstance(p, str) and (p[:1].isupper() or p in {"str", "int"} or "[" in p):
            continue
        kept.append("from" if p == "from_" else p)
    return join(*kept) or "$"
