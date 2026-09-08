from io import BytesIO
from unittest.mock import patch
import urllib.error

import pytest
from intelligence.storage.client import WorkerAPIClient, StorageClientError


def test_daily_quota_is_not_retried_as_transient_503():
    error = urllib.error.HTTPError('https://example.com', 503, 'quota', {},
        BytesIO(b'{"error":{"code":"d1_daily_read_limit"}}'))
    with patch('urllib.request.urlopen', side_effect=error) as request, patch('time.sleep') as sleep:
        with pytest.raises(StorageClientError, match='quota exhausted'):
            WorkerAPIClient('https://example.com', 'test').get_catalog()
    assert request.call_count == 1
    sleep.assert_not_called()
