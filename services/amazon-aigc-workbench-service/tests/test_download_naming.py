from amazon_aigc.delivery_download import archive_filename, safe_segment, suggested_short_name


def test_short_name_suggests_from_existing_name_without_inventing_and_sanitizes_paths():
    assert suggested_short_name("跨境 热销 新款 爱心项圈，黑白两条") == "爱心项圈"
    assert suggested_short_name("【生成测试】透明收纳托盘") == "透明收纳托盘"
    assert len(suggested_short_name("超长商品" * 20)) == 16
    assert safe_segment('../透明/收纳_盘') == "透明-收纳-盘"
    assert safe_segment(' .. ') == "商品"


def test_zip_entry_filenames_preserve_valid_names_and_cannot_escape_the_folder():
    assert archive_filename("原图_MAIN 01.png") == "原图_MAIN 01.png"
    assert "/" not in archive_filename("../../image.png")
    assert "\\" not in archive_filename("..\\image.png")
    name = archive_filename("图片" * 100 + ".png")
    assert len(name.encode("utf-8")) <= 180
    assert name.endswith(".png")
