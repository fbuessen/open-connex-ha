"""Validate installation metadata and user-facing error translations."""

import ast
import json
from pathlib import Path

from custom_components.open_connex_ha.const import DOMAIN, VERSION

ROOT = Path(__file__).parents[1] / "custom_components" / DOMAIN


def test_manifest_and_translations():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    assert manifest["domain"] == DOMAIN
    assert DOMAIN == "open_connex_ha"
    assert manifest["name"] == "Open Connex HA"
    assert manifest["version"] == VERSION
    assert manifest["config_flow"] is True
    assert manifest["iot_class"] == "local_polling"
    assert manifest["requirements"] == []
    strings = json.loads((ROOT / "strings.json").read_text())
    assert strings == json.loads((ROOT / "translations/en.json").read_text())
    for name in ["coordinator", "climate", "number"]:
        tree = ast.parse((ROOT / f"{name}.py").read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "action_error"
                and isinstance(node.args[0], ast.Constant)
            ):
                assert node.args[0].value in strings["exceptions"]
