from geopulse.utils.text import (
    extract_hashtags,
    is_near_duplicate,
    normalize_key,
    simhash64,
)


def test_normalize_key_strips_accents_and_case():
    assert normalize_key("Neyba") == "neyba"
    assert normalize_key("LOS RÍOS") == "los rios"


def test_extract_hashtags():
    tags = extract_hashtags("Feria en #Neiba y #LagoEnriquillo este finde")
    assert "neiba" in tags
    assert "lagoenriquillo" in tags


def test_simhash_near_duplicate():
    a = simhash64("Feria de la uva en Neiba este sabado")
    b = simhash64("Feria de la uva en Neiba este sabado!")
    c = simhash64("Torneo de beisbol en Tamayo con mucho publico")
    assert is_near_duplicate(a, b)
    assert not is_near_duplicate(a, c)
