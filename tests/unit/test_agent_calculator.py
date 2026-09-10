import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.agent_calculator import calculator


@pytest.mark.parametrize("store,tax,purchase", [("rimili", "usn", 200.123), ("gogol", "osno", 200), ("rimili", "usn", 0), ("rimili", "usn", None)])
def test_matches_actual_browser_calculator(store, tax, purchase):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for parity with actual frontend code")
    product = {"article": "test", "store_slug": store,
        "price": {"current": 1234.567, "with_spp": 1012.123, "with_wallet": 950.456},
        "details": {"commission_percent": 24.111, "acquiring": 1.5, "team_commission_percent": 3,
            "storage_days": 7.2, "vat_percent": 5, "tax_system": tax, "usn_percent": 6,
            "osno_percent": 20, "purchase_cost": purchase, "fulfillment_cost": 40.123,
            "delivery_with_returns": 65.347},
        "advertising": {"drr": 10, "buyout_percent": 87, "spend_per_order": 15.245},
        "product_settings": {"storage_wb_rub": 1.235}}
    source = (Path(__file__).resolve().parents[2] / "static/unit-economics-1c.js").read_text(encoding="utf-8")
    names = ("finite", "productTaxSystem", "walletDiscountPercent", "databaseCalculatorValues", "calculatePrice")
    functions = [re.search(r"    function " + name + r"\(.*?\n    }", source, re.S).group() for name in names]
    script = "\n".join(functions) + "\nvar p=JSON.parse(process.argv[1]); var v=databaseCalculatorValues(p); Object.keys(v).forEach(k=>{if(v[k]!==null)v[k]=Math.round(Number(v[k])*100)/100;}); console.log(JSON.stringify({inputs:v,results:calculatePrice(p,v)}));"
    expected = json.loads(subprocess.check_output([node, "-e", script, json.dumps(product)], text=True, encoding="utf-8"))
    actual = calculator(product)
    assert actual["inputs"] == expected["inputs"]
    for key, value in expected["results"].items():
        assert actual["results"][key] == (pytest.approx(value) if isinstance(value, (int, float)) else value)


def test_missing_costs_do_not_become_zero_profit():
    row = calculator({"article": "test", "price": {"current": 100}})
    assert row["results"]["margin"] is None
    assert row["results"]["roi"] is None
    assert not row["calculation_complete"]
