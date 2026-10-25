from fightcard.images.names import names_match, normalize_name


def test_normalize_strips_accents_case_and_punctuation():
    assert normalize_name("José  Aldo") == "jose aldo"
    assert normalize_name("Waldo Cortes-Acosta") == "waldo cortes acosta"
    assert normalize_name("Sean O'Malley") == "sean omalley"
    assert normalize_name("Lone'er Kavanagh") == "loneer kavanagh"


def test_normalize_drops_generational_suffix():
    assert normalize_name("Ronaldo Souza Jr.") == "ronaldo souza"
    assert normalize_name("Joaquin Buckley Sr") == "joaquin buckley"
    assert normalize_name("Dustin Jacoby II") == "dustin jacoby"


def test_normalize_empty():
    assert normalize_name("") == ""
    assert normalize_name(None) == ""


def test_exact_and_accent_insensitive_match():
    assert names_match("Jiří Procházka", "Jiri Prochazka")
    assert names_match("ALEXANDER VOLKANOVSKI", "alexander volkanovski")


def test_jr_on_one_side_still_matches():
    assert names_match("Manel Kape Jr.", "Manel Kape")


def test_token_order_insensitive():
    assert names_match("Cortes Acosta Waldo", "Waldo Cortes-Acosta")


def test_small_typo_matches_at_085():
    assert names_match("Abdul-Rakhman Yakhyaev", "Abdul-Rakhman Yakhyayev")


def test_short_first_name_prefix_matches():
    assert names_match("Alex Pereira", "Alexander Pereira")


def test_nickname_used_as_first_name_matches():
    # ESPN sometimes lists a fighter by nickname, e.g. "Shara Magomedov".
    assert names_match("Sharabutdin Magomedov", "Shara Magomedov", nickname="Shara Bullet")
    assert names_match("Sharabutdin Magomedov", "Bullet Magomedov", nickname="Bullet")


def test_different_people_do_not_match():
    assert not names_match("Alexander Volkanovski", "Alexander Volkov")
    assert not names_match("Petr Yan", "Song Yadong")
    assert not names_match("Abus Magomedov", "Islam Makhachev")


def test_same_surname_different_first_name_does_not_match():
    assert not names_match("Abus Magomedov", "Sharabutdin Magomedov")


def test_single_token_never_matches_full_name():
    assert not names_match("Al", "Al Iaquinta")
    assert not names_match("Magomedov", "Abus Magomedov")


def test_empty_never_matches():
    assert not names_match("", "Petr Yan")
