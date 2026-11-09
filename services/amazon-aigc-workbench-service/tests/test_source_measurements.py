import pytest

from amazon_aigc.product_facts import source_fact_hints
from amazon_aigc.source_measurements import measurement_projection, positive
from amazon_aigc.sync import supplier_urls


def test_markdown_source_links_do_not_include_wrappers_or_duplicates():
    from amazon_aigc.sync import reference_urls

    supplier = "https://detail.1688.com/offer/123456.html?lang=zh"
    reference = "https://www.amazon.co.uk/dp/B0TEST0003?th=1&psc=1"
    assert supplier_urls({"1688货源": f"[{supplier}]({supplier})"}) == [supplier]
    assert reference_urls({"参考链接": f"[{reference}]({reference})"}) == [reference]
    assert reference_urls({"参考链接": [{"type": "url", "link": reference, "text": reference}]}) == [reference]
from amazon_aigc.tmapi_collector import normalize


@pytest.mark.parametrize("value", [None, True, False, 0, -1, "NaN", "Infinity", "1e999", "1e-999", "unknown"])
def test_missing_or_invalid_measurements_remain_missing(value):
    assert positive(value) is None


def test_sku_packaging_keeps_identity_and_does_not_become_product_dimensions():
    url = "https://detail.1688.com/offer/123456789.html"
    data = {
        "item_id": 123456789,
        "title": "测试商品",
        "product_props": [{"产品尺寸": "12 × 8 × 2 cm"}, {"净重(g)": "45"}],
        "skus": [
            {
                "skuid": "small",
                "props_names": "小号",
                "package_info": {"weight": 0.05, "length": 13, "width": 9, "height": 3},
            },
            {
                "skuid": "big",
                "props_names": "大号",
                "package_info": {"weight": 0.1, "length": 20, "width": 10, "height": 0, "length_unit": "cm"},
            },
        ],
        "delivery_info": {"unit_weight": None},
    }
    result = normalize(data, url)
    hint = source_fact_hints(result)
    assert hint["dimensions"] == "12 × 8 × 2 cm" and hint["weight"] == "45 g"
    rows = result["measurements"]["package_by_sku"]
    assert rows["small"]["weight_kg"] == 0.05 and rows["small"]["dimension_unit"] == ""
    assert rows["big"]["height"] is None and rows["big"]["dimension_unit"] == "cm"
    assert "13" not in hint["dimensions"] and result["measurements"]["default_shipping_weight_kg"] is None


def test_package_defaults_and_fabric_weight_are_not_net_weight():
    result = measurement_projection({"delivery_info": {"unit_weight": 0}, "skus": []})
    assert result == {"package_by_sku": {}, "default_shipping_weight_kg": None}
    hint = source_fact_hints({"specifications": ["克重: 220", "包装重量: 50g", "包装尺寸: 1×1×1cm", "尺寸: 12×8×2"]})
    assert hint["weight"] == "" and hint["dimensions"] == "" and "单位未注明" in hint["unspecified_dimensions"]


def test_registered_supplier_rich_links_multiple_values_and_aliases():
    row = {
        "1688货源": [
            {"text": "采购链接", "link": "https://detail.1688.com/offer/1.html"},
            {"text": "https://detail.1688.com/offer/2.html"},
        ],
        "1688链接": "http://detail.1688.com/offer/1.html\nhttps://detail.1688.com/offer/3.html",
    }
    assert supplier_urls(row) == [f"https://detail.1688.com/offer/{i}.html" for i in (1, 2, 3)]
