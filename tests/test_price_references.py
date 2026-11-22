import price_references as refs

CATALOG = [
    {"ean": "8056597875226", "brand": "Prada", "model": "PR 26ZS", "color": "16K08Z", "caliber": "55-16"},
    {"ean": "8056597875349", "brand": "Prada", "model": "PR 27ZS", "color": "16K08Z", "caliber": "54-21"},
    {"ean": "8056597877329", "brand": "Prada", "model": "PR 65ZS", "color": "ZVN09T", "caliber": "55-20"},
    {"ean": "0889652637075", "brand": "Bottega Veneta", "model": "BV1274S", "color": "005", "caliber": "63-13"},
    {"ean": "1000000000011", "brand": "Ray Ban", "model": "RB2140", "color": "901", "caliber": "50-22"},
    {"ean": "1000000000028", "brand": "Ray Ban", "model": "RB2140", "color": "901", "caliber": "54-22"},
]
BV = CATALOG[3]
PR27 = CATALOG[1]


def test_bottega_reference_variants_are_equivalent():
    index = refs.ReferenceIndex(CATALOG)
    for text in ("BOTTEGA VENETA BV1274S 005", "BV1274S 005", "bv1274s-005", "BV1274S/005", "bv1274s005"):
        level, found = index.lookup(text)
        assert [p["ean"] for p in found] == ["0889652637075"], text
        assert level
    assert index.lookup("BOTTEGA VENETA BV1274S 005")[0] == "brand_model_color"
    assert index.lookup("BV1274S 005")[0] == "model_color"
    assert index.lookup("bv1274s005")[0] == "normalized_variants"


def test_similar_models_and_colours_are_not_confused():
    index = refs.ReferenceIndex(CATALOG)
    assert index.lookup("PR 26ZS 16K08Z")[1][0]["ean"] == "8056597875226"
    assert index.lookup("PR27ZS-16K08Z")[1][0]["ean"] == "8056597875349"
    assert index.lookup("BV1274S 5")[1] == []
    assert index.lookup("PR 27ZS ZVN09T")[1] == []


def test_ean_lookup_first_and_caliber_variants_not_mixed():
    index = refs.ReferenceIndex(CATALOG)
    assert index.lookup("8056597877329")[0] == "ean"
    assert len(index.lookup("RB2140 901")[1]) == 2
    assert [p["ean"] for p in index.lookup("RB2140 901 54-22")[1]] == ["1000000000028"]


def test_search_plan_order():
    levels = [level for level, _ in refs.search_plan(BV)]
    assert levels[0] == "ean"
    assert levels[1:3] == ["brand_model_color", "model_color"]
    assert "normalized_variants" in levels
    assert ("normalized_variants", "BV1274S005") in refs.search_plan(BV)


def test_caliber_normalisation():
    assert refs.normalize_caliber("54\u25a121") == "54-21"
    assert refs.normalize_caliber("54/21") == "54-21"
    assert refs.normalize_caliber("63") == "63"
    assert refs.normalize_caliber("32-50") == "32-50"
    assert refs.extract_caliber("RB2140 901-32-50") == ""
    bassol_slug = "Ray-Ban RB2140 Wayfarer 901-32-50"
    parsed = refs.parse_reference(bassol_slug, brands=["Ray Ban"], models=["RB2140"])
    assert parsed.color_key == "90132" and parsed.caliber == "50"
    assert refs.classify(CATALOG[4], {"title": bassol_slug}).status == refs.REJECTED
    assert refs.compare_calibers("54-21", "54-20") == "different"
    assert refs.compare_calibers("63-13", "63") == "equal"
    assert refs.compare_calibers("", "63") == "unknown"


def test_invalid_size_like_tokens_cannot_hide_a_distinct_color():
    title = "Ray Ban RB2140 WAYFARER 901-32-50 54-18"
    parsed = refs.parse_reference(title, brands=["Ray Ban"], models=["RB2140"])
    assert parsed.color_key == "90132"
    assert refs.classify(CATALOG[4], {"title": title}).status == refs.REJECTED
    assert refs.ReferenceIndex(CATALOG).lookup("RB2140 901-32-50 54-18")[1] == []


def test_exact_by_published_ean():
    result = refs.classify(PR27, {"ean_published": "8056597875349", "title": "Prada PR 27ZS 16K08Z"})
    assert (result.status, result.basis) == (refs.EXACT, "ean")


def test_exact_by_reference_without_ean():
    result = refs.classify(PR27, {"brand": "Prada", "model": "PR 27ZS", "color": "16K08Z", "caliber": "54-21"})
    assert (result.status, result.basis) == (refs.EXACT, "reference")


def test_reference_price_can_ignore_ean_only_with_exact_model_and_color():
    candidate = {"ean_published": "8056597875226", "brand": "Prada",
                 "model": "PR 27ZS", "color": "16K08Z"}
    assert refs.classify(PR27, candidate).status == refs.PROBABLE
    result = refs.classify(PR27, candidate, allow_reference_match=True)
    assert (result.status, result.basis) == (refs.EXACT, "reference")
    assert "EAN publicado distinto" in result.observations[0]

    candidate["color"] = "ZVN09T"
    assert refs.classify(PR27, candidate, allow_reference_match=True).status != refs.EXACT


def test_tiffany_brand_aliases_are_equivalent_but_explicit():
    product = {"ean": "8056262010730", "brand": "Tiffany & Co.", "model": "TF3104D", "color": "6216S4"}
    for brand, title in (
        ("Tiffany", "Tiffany TF3104D 6216S4"),
        ("Tiffany & Co.", "Tiffany & Co. TF3104D 6216S4"),
        ("TIFFANYCO", "TIFFANYCO TF3104D 6216S4"),
    ):
        result = refs.classify(product, {"brand": brand, "model": "TF3104D", "color": "6216S4",
                                         "title": title})
        assert result.status == refs.EXACT, (brand, result.reasons)


def test_tom_ford_commercial_names_are_scoped_to_their_complete_model():
    cases = (
        ({"ean": "0889214519788", "brand": "Tom Ford", "model": "FT1044", "color": "01V"},
         {"brand": "Tom Ford", "model": "Bronson", "title": "Tom Ford FT1044 01V Bronson"}),
        ({"ean": "0889214625625", "brand": "Tom Ford", "model": "FT1301", "color": "52A"},
         {"brand": "Tom Ford", "model": "Joey-02", "title": "Tom Ford Joey-02 FT1301 52A"}),
    )
    for product, candidate in cases:
        result = refs.classify(product, candidate)
        assert result.status == refs.EXACT, result.reasons

    unrelated = refs.classify(
        {"ean": "0889214625991", "brand": "Tom Ford", "model": "FT1310", "color": "52E"},
        {"brand": "Tom Ford", "model": "Joey-02", "title": "Tom Ford Joey-02 FT1310 52E"},
    )
    assert unrelated.status != refs.EXACT


def test_gucci_logotipo_descriptor_requires_exact_ean_group_title_and_color():
    product = {"ean": "0889652498768", "brand": "Gucci", "model": "GG1680S", "color": "002"}
    candidate = {
        "ean_published": "889652498768",
        "brand": "Gucci",
        "model": "Logotipo Gg",
        "title": "Gucci GG1680S 002 Logotipo Gg",
        "group_reference": "GG1680S-002",
    }
    assert refs.classify(product, candidate).status == refs.EXACT
    for change in (
        {"group_reference": "GG1680S-001"},
        {"title": "Gucci GG1680S 001 Logotipo Gg"},
        {"ean_published": "889652498760"},
        {"brand": "Other"},
    ):
        assert refs.classify(product, {**candidate, **change}).status != refs.EXACT, change


def test_hyphenated_tom_ford_codes_remain_complete_model_aliases():
    for model in ("FT5737-B", "FT6058-B", "FT6082-B"):
        assert refs.model_key(model) == refs.model_key(model.replace("-", ""))
    assert refs.model_key("FT6082-B") != refs.model_key("FT6082")


def test_complete_tom_ford_b_models_parse_in_hyphenated_and_compact_forms():
    for compact, hyphenated in (("FT5737B", "FT5737-B"), ("FT6058B", "FT6058-B"),
                                ("FT6082B", "FT6082-B")):
        for published, catalogue in ((compact, hyphenated), (hyphenated, compact)):
            parsed = refs.parse_reference(f"Tom Ford {published} 0A0",
                                          brands=["Tom Ford"], models=[catalogue])
            assert parsed.brand == "Tom Ford"
            assert parsed.model_key == refs.model_key(compact)
            assert parsed.color_key == "0A0"
        assert refs.model_key(compact) != refs.model_key(compact[:-1])


def test_ray_ban_901_58_color_remains_distinct_from_901_and_901_32():
    product = {"ean": "0805289126591", "brand": "Ray Ban", "model": "RB2140", "color": "90158"}
    assert refs.classify(product, {"title": "Ray Ban RB2140 901/58 54-18"}).status == refs.EXACT
    for title in ("Ray Ban RB2140 901 54-18", "Ray Ban RB2140 901/32 50-22"):
        assert refs.classify(product, {"title": title}).status == refs.REJECTED


def test_miu_a06s_merged_opticalh_descriptor_requires_exact_brand_model_and_color():
    product = {"ean": "8056262407691", "brand": "Miu Miu", "model": "MU A06S", "color": "14L09Z"}
    candidate = {
        "brand": "Miu Miu",
        "model": "GAFASDESOLMIUMIUA06S",
        "color": "14L09Z",
        "title": "GAFASDESOLMIUMIUA06S 14L09Z",
    }
    assert refs.classify(product, candidate).status == refs.EXACT
    for changed in (
        {"color": "14L09X"},
        {"brand": "Other"},
        {"model": "GAFASDESOLMIUMIUA06S2"},
        {"ean_published": "8056262407684"},
    ):
        result = refs.classify(product, {**candidate, **changed})
        assert result.status != refs.EXACT, changed


def test_parse_reference_recognizes_tiffany_brand_alias_prefix():
    parsed = refs.parse_reference("Tiffany TF3104D 6216S4", brands=["Tiffany & Co."],
                                  models=["TF3104D"])
    assert parsed.brand == "Tiffany & Co."
    assert (parsed.model_key, parsed.color_key) == ("TF3104D", "6216S4")


def test_model_and_color_are_exact_without_ean_or_caliber():
    result = refs.classify(PR27, {"brand": "Prada", "model": "PR 27ZS", "color": "16K08Z"})
    assert result.status == refs.EXACT and result.comparable


def test_different_colour_is_rejected_but_caliber_does_not_change_identity():
    assert refs.classify(PR27, {"brand": "Prada", "model": "PR 27ZS", "color": "ZVN09T",
                                "caliber": "54-21"}).status == refs.REJECTED
    assert refs.classify(PR27, {"brand": "Prada", "model": "PR 27ZS", "color": "16K08Z",
                                "caliber": "55-21"}).status == refs.EXACT


def test_different_ean_never_matches_automatically_and_goes_to_review():
    assert refs.classify(PR27, {"ean_published": "8056597875226"}).status == refs.PROBABLE
    result = refs.classify(PR27, {"ean_published": "8056597875349", "model": "PR 27ZS", "color": "ZVN09T"})
    assert result.status == refs.PROBABLE


def test_exact_ean_allows_unpublished_identity_fields_but_not_contradictions():
    assert refs.classify(PR27, {"ean_published": "8056597875349"}).status == refs.EXACT
    assert refs.classify(PR27, {"ean_published": "8056597875349", "model": "PR 65ZS",
                                "color": "16K08Z"}).status == refs.PROBABLE


def test_title_parsing_and_brand_evidence():
    ok = refs.classify(BV, {"title": "Gafas Bottega Veneta BV1274S 005 63-13", "caliber": "63-13"})
    assert ok.status == refs.EXACT
    no_brand = refs.classify(BV, {"title": "BV1274S 005", "caliber": "63-13"})
    assert no_brand.status == refs.PROBABLE
    assert refs.classify(BV, {"title": "Gafas BV1274S 006", "caliber": "63-13"}).status == refs.REJECTED
