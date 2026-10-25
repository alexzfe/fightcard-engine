"""Live smoke checks. Run explicitly: pytest -m live pipeline/tests/images."""

import pytest

from fightcard.images.download import validate_image
from fightcard.images.http import HttpClient
from fightcard.images.sources import ImageFinder

pytestmark = pytest.mark.live


def test_live_known_fighter_resolves_to_real_image():
    client = HttpClient(min_interval=1.0)
    url = next(ImageFinder(client).candidates("Islam Makhachev"))
    resp = client.get(url)
    assert resp.ok and validate_image(resp.body) == "png"
