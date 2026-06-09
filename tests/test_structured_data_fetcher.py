from unittest.mock import patch, MagicMock

from structured_data_fetcher import get_structured_data_fetcher


def test_fetch_raw_returns_dict_on_mock_response():
    fetcher = get_structured_data_fetcher()
    fetcher._cache.pop("TCS", None)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = (
        '<html><table><tr><td>Sales</td><td>100</td></tr></table></html>'
    )

    with patch("requests.get", return_value=mock_response), \
         patch("requests.Session.get", return_value=mock_response):
        data = fetcher.fetch_raw("TCS")
        assert isinstance(data, dict)
