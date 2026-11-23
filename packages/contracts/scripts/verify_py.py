"""Verify the generated Pydantic v2 models against the JSON Schemas (called by codegen.mjs).

For every contract:
  * the example document must validate with BOTH Pydantic and jsonschema (draft 2020-12),
  * every `<contract>.invalid.json` case must be rejected by BOTH,
  * a Pydantic round-trip (model_validate -> model_dump(mode="json")) must stay schema-valid,
  * the discriminated union on `params.type` must resolve to the right model class.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema
from pydantic import ValidationError

from revgate_contracts import AnalyticalMeta, DiffManifest, load_schema
from revgate_contracts import analytical_meta as am

MODELS = {"analytical_meta": AnalyticalMeta, "diff_manifest": DiffManifest}


def main(example_dir: Path) -> int:
    for name, model in MODELS.items():
        schema = load_schema(name)
        jsonschema.Draft202012Validator.check_schema(schema)
        validator = jsonschema.Draft202012Validator(schema)

        good = json.loads((example_dir / f"{name}.example.json").read_text("utf-8"))
        validator.validate(good)
        parsed = model.model_validate(good)
        dumped = parsed.model_dump(mode="json", exclude_unset=True)
        validator.validate(dumped)
        assert dumped == good, f"{name}: pydantic round-trip changed the document"

        bad_file = example_dir / f"{name}.invalid.json"
        bad_cases = json.loads(bad_file.read_text("utf-8")) if bad_file.exists() else []
        for case in bad_cases:
            doc = case["document"]
            schema_ok = validator.is_valid(doc)
            try:
                model.model_validate(doc)
                pyd_ok = True
            except ValidationError:
                pyd_ok = False
            if schema_ok or pyd_ok:
                raise AssertionError(
                    f"{name}: invalid case {case['name']!r} accepted "
                    f"(jsonschema={schema_ok}, pydantic={pyd_ok})"
                )
        print(f"[codegen] pydantic ✓ {name}: example valid + round-trip, {len(bad_cases)} invalid cases rejected")

    meta = AnalyticalMeta.model_validate(
        json.loads((example_dir / "analytical_meta.example.json").read_text("utf-8"))
    )
    kinds = {type(f.params).__name__ for f in meta.faces}
    expected = {am.PlaneParams.__name__, am.CylinderParams.__name__, am.ConeParams.__name__, am.OtherParams.__name__}
    assert kinds == expected, f"discriminated union resolved to {kinds}, expected {expected}"
    print("[codegen] pydantic ✓ FaceParams discriminated union resolves PLANE/CYLINDER/CONE/OTHER")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
