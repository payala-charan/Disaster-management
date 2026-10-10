"""Guard for design rule 9: the Pydantic schemas mirror frontend/src/types/index.ts field-for-field.

A deliberately simple parser: it reads each `export interface Name { field: ...; }` block and
compares the field names with the Pydantic model of the same name. If someone adds a field on one
side only, this test names the interface and the missing field.
"""

import re

import pytest
from pydantic import BaseModel

from app.core.config import BACKEND_DIR
from app.models import schemas

TS_FILE = BACKEND_DIR.parent / "frontend" / "src" / "types" / "index.ts"

INTERFACE_RE = re.compile(r"export interface (\w+)(?: extends (\w+))? \{(.*?)\n\}", re.S)
FIELD_RE = re.compile(r"^\s+(\w+)\??:", re.M)


def ts_interfaces() -> dict[str, set[str]]:
    source = TS_FILE.read_text()
    found = {name: (parent, set(FIELD_RE.findall(body))) for name, parent, body in INTERFACE_RE.findall(source)}

    def fields(name: str) -> set[str]:  # include inherited fields (interface X extends Y)
        parent, own = found[name]
        return own | (fields(parent) if parent else set())

    return {name: fields(name) for name in found}


INTERFACES = ts_interfaces()


def test_parser_found_the_contract():
    assert {"Incident", "Resource", "SituationSnapshot", "Plan", "AgentRun"} <= INTERFACES.keys()


@pytest.mark.parametrize("name", sorted(INTERFACES))
def test_pydantic_model_mirrors_ts_interface(name):
    model = getattr(schemas, name, None)
    assert isinstance(model, type) and issubclass(model, BaseModel), f"no Pydantic model for TS interface {name}"
    py_fields = set(model.model_fields)
    ts_fields = INTERFACES[name]
    assert py_fields == ts_fields, (
        f"{name}: only in TS {sorted(ts_fields - py_fields)}, only in Pydantic {sorted(py_fields - ts_fields)}"
    )
