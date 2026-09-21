from farq.media import listing_images


class _Body:
    def __init__(self, html: str, url: str):
        self._html = html.encode()
        self._url = url

    def geturl(self) -> str:
        return self._url

    def read(self, _limit: int) -> bytes:
        return self._html

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> bool:
        return False


def test_listing_images_keep_real_haraj_files_and_drop_logos():
    html = """
    <img src="https://v8-cdn.haraj.com.sa/logos/haraj-logo-markup-icon.png">
    <img src="https://postcdn.haraj.com.sa/userfiles30/2026-04-05/1350x1800_ABC.jpg-700.webp">
    <img src="https://thumbcdn.haraj.com.sa/1350x1800_ABC.jpg-140x140.webp">
    <img src="https://mimg6cdn.haraj.com.sa/userfiles30/2026-04-05/1350x1800_DEF.jpg">
    """

    def opener(_request, timeout=8):
        del timeout
        return _Body(html, "https://haraj.com.sa/111/post/")

    images = listing_images("https://haraj.com.sa/111/post/?t=images", opener=opener)
    assert images[0].endswith("1350x1800_ABC.jpg-700.webp")
    assert any(item.endswith("1350x1800_DEF.jpg") for item in images)
    assert all("logo" not in item for item in images)
    assert all("140x140" not in item for item in images)


def test_non_haraj_listing_url_is_rejected():
    try:
        listing_images("https://example.com/ad")
    except ValueError as exc:
        assert "haraj.com.sa" in str(exc)
    else:
        raise AssertionError("expected rejection")
