from geopulse.agents.geo import GeoMapper
from geopulse.config import Config


def mapper() -> GeoMapper:
    return GeoMapper(Config())


def test_location_match_yields_municipio():
    geo = mapper()
    post = {"raw_id": "1", "text": "Venta de uvas", "location_text": "Neiba, Bahoruco", "hashtags": []}
    result = geo.map_post(post)
    assert result["municipality"] == "Neiba"
    assert result["confidence"] >= 0.6


def test_hashtag_match():
    geo = mapper()
    post = {"raw_id": "2", "text": "Feria agricola", "location_text": "", "hashtags": ["tamayo"]}
    result = geo.map_post(post)
    assert result["municipality"] == "Tamayo"


def test_province_fallback_when_no_municipio():
    geo = mapper()
    post = {"raw_id": "3", "text": "Noticias de la provincia Bahoruco", "location_text": "", "hashtags": []}
    result = geo.map_post(post)
    assert result["municipality"] is None
    assert result["province"] == "Bahoruco"
    assert result["method"] == "province"


def test_source_prior_used():
    geo = mapper()
    post = {"raw_id": "4", "text": "Acto de entrega de obras", "location_text": "", "hashtags": []}
    result = geo.map_post(post, {"municipio": "Galván"})
    assert result["municipality"] == "Galván"
