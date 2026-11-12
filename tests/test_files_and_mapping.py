import pytest

from core.errors import DuplicateOrderError, MappingError, MarketplaceMismatch
from core.mapping import REGIONS, load_mapping, sku_key
from core.order_files import check_marketplace, detect_marketplace, load_orders
from core.stock import parse_stock
from tests.helpers import (H, bigw_row, kogan_row, reverb_row, sears_row, write_mapping, write_xlsx)

US, AU = REGIONS["us"], REGIONS["au"]


def test_each_layout_parses(tmp_path):
    cases = [("reverb", [reverb_row()], US), ("sears", [sears_row()], US),
             ("kogan", [kogan_row()], AU), ("bigw", [bigw_row()], AU)]
    for name, rows, region in cases:
        p = write_xlsx(tmp_path / f"{name}.xlsx", H[name], rows)
        assert detect_marketplace(H[name]) == name
        lines = load_orders(p, name, region.marketplaces)
        assert len(lines) == 1 and lines[0].marketplace == name and lines[0].buyable
        assert lines[0].country == ("US" if region is US else "AU")


def test_reverb_normalisation(tmp_path):
    p = write_xlsx(tmp_path / "r.xlsx", H["reverb"], [reverb_row()])
    l = load_orders(p, "reverb", US.marketplaces)[0]
    assert (l.state_code, l.postcode, l.phone) == ("OH", "04301", "5550100001")


def test_wrong_marketplace_rejected(tmp_path):
    rev = write_xlsx(tmp_path / "r.xlsx", H["reverb"], [reverb_row()])
    kog = write_xlsx(tmp_path / "k.xlsx", H["kogan"], [kogan_row()])
    with pytest.raises(MarketplaceMismatch):          # reverb file into run_au
        check_marketplace(rev, "reverb", AU.marketplaces)
    with pytest.raises(MarketplaceMismatch):          # kogan file with --marketplace sears
        check_marketplace(kog, "sears", US.marketplaces)
    with pytest.raises(MarketplaceMismatch):          # right region, wrong file
        check_marketplace(rev, "sears", US.marketplaces)


def test_sears_two_skus_one_confirmation(tmp_path):
    p = write_xlsx(tmp_path / "s.xlsx", H["sears"], [sears_row(sku="A"), sears_row(sku="B", **{"Line Number": 2})])
    lines = load_orders(p, "sears", US.marketplaces)
    assert [l.sku for l in lines] == ["A", "B"] and {l.order_id for l in lines} == {"914219980"}
    assert lines[1].po_number == "PO1" and lines[1].line_number == "2"


def test_duplicate_order_and_sku_rejected(tmp_path):
    p = write_xlsx(tmp_path / "k.xlsx", H["kogan"], [kogan_row("K1", "X"), kogan_row("K1", "x")])
    with pytest.raises(DuplicateOrderError) as e:
        load_orders(p, "kogan", AU.marketplaces)
    assert e.value.keys == [("K1", "X")]


def test_sears_skip_rules(tmp_path):
    rows = [sears_row(sku="A", **{"Po Line Status": "SHIPPED"}), sears_row(sku="B", **{"New Item Quantity": 0}),
            sears_row(sku="C", **{"Po Status": "Closed"}), sears_row(sku="D")]
    p = write_xlsx(tmp_path / "s.xlsx", H["sears"], rows)
    assert [l.buyable for l in load_orders(p, "sears", US.marketplaces)] == [False, False, False, True]


def test_bigw_state_codes_and_status(tmp_path):
    rows = [bigw_row("B1", "A"), bigw_row("B2", "B", State="Victoria"), bigw_row("B3", "C", Status="Cancelled")]
    p = write_xlsx(tmp_path / "b.xlsx", H["bigw"], rows)
    lines = load_orders(p, "bigw", AU.marketplaces)
    assert [l.state_code for l in lines] == ["NSW", "VIC", "NSW"]
    assert [l.buyable for l in lines] == [True, True, False]


def test_mapping_case_insensitive_and_duplicates(tmp_path):
    m = load_mapping(write_mapping(tmp_path / "m.xlsx", [("amh-abc-new", "amazon", "https://www.amazon.com/dp/B0", "")]), US)
    assert sku_key("AMH-ABC-NEW") in m
    dup = write_mapping(tmp_path / "d.xlsx", [("A", "amazon", "https://www.amazon.com/dp/1", ""),
                                              (" a ", "amazon", "https://www.amazon.com/dp/2", "")])
    with pytest.raises(MappingError):
        load_mapping(dup, US)


def test_mapping_vendor_rules(tmp_path):
    au_in_us = write_mapping(tmp_path / "a.xlsx", [("A", "amazon_au", "https://www.amazon.com.au/dp/1", "")])
    with pytest.raises(MappingError):
        load_mapping(au_in_us, US)
    wrong_host = write_mapping(tmp_path / "b.xlsx", [("A", "amazon_au", "https://www.amazon.com/dp/1", "")])
    with pytest.raises(MappingError):
        load_mapping(wrong_host, AU)
    lookalike = write_mapping(tmp_path / "c.xlsx", [("A", "amazon", "https://evilamazon.com/dp/1", "")])
    with pytest.raises(MappingError):
        load_mapping(lookalike, US)
    ok = write_mapping(tmp_path / "d.xlsx", [("A", "costway_au", "https://au.costway.com/x", "V1"),
                                             ("B", "vevor_au", "https://www.vevor.com.au/p", "V2")])
    assert load_mapping(ok, AU)["a"].vendor_sku == "V1"


@pytest.mark.parametrize("text,n", [("Only 3 left in stock", 3), ("12 available", 12), ("More than 10 available", 10),
                                    ("Last one", 1), ("Currently unavailable", 0), ("In Stock", 99), ("In stock.", 99),
                                    ("Temporarily out of stock.", 0), ("Usually dispatched in 2 days", None), ("", None)])
def test_parse_stock(text, n):
    assert parse_stock(text) == n
