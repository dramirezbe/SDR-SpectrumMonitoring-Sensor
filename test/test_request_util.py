import pytest
from unittest.mock import MagicMock
import requests
import json
import logging

# Imports work due to the setup in conftest.py
from app.utils.request_util import RequestClient

# =========================================================================
# Fixtures
# =========================================================================

@pytest.fixture
def mock_response():
    """Returns a mock requests.Response object."""
    mock_resp = MagicMock(spec=requests.Response)
    
    # Add attributes expected by the RequestClient
    mock_resp.status_code = 200
    mock_resp.text = '{"message": "OK"}'
    mock_resp.json.return_value = {"message": "OK"}
    
    return mock_resp

@pytest.fixture
def mock_logger():
    """Returns a mock logger object for the client."""
    return MagicMock(spec=logging.Logger)

@pytest.fixture
def mock_requests_request(mocker, mock_response):
    """
    Patches requests.request to return our mock_response.
    Returns the mock object so assertions can be made on it.
    """
    mock_req = mocker.patch(
        'app.utils.request_util.requests.request', 
        return_value=mock_response
    )
    return mock_req

# =========================================================================
# RequestClient Initialization Tests
# =========================================================================

def test_client_initialization():
    """Test client stores parameters correctly."""
    client = RequestClient(base_url="http://test.com/", verbose=True, logger="dummy")
    assert client.base_url == "http://test.com"
    assert client.timeout == (5, 15)
    assert client.verbose is True

# =========================================================================
# GET Method Tests
# =========================================================================

def test_get_success_200(mock_requests_request, mock_response):
    """Test successful GET request (rc=0) and correct arguments."""
    client = RequestClient(base_url="https://api.test.com")
    mock_response.status_code = 200
    
    rc, resp = client.get(endpoint="/v1/data", params={"key": "value"})

    # Assertions on the return value
    assert rc == 0
    assert resp is mock_response
    
    # Assertions on the patched function call
    mock_requests_request.assert_called_once()
    call_args, call_kwargs = mock_requests_request.call_args
    
    # FIX: Check positional arguments (method is at index 0, URL at index 1)
    assert call_args[0] == "GET" 
    assert call_args[1] == "https://api.test.com/v1/data"
    
    assert call_kwargs['headers']['Accept'] == "application/json"
    assert call_kwargs['params'] == {"key": "value"}
    assert call_kwargs['timeout'] == (5, 15)


def test_get_client_error_404(mock_requests_request, mock_response, mock_logger):
    """Test HTTP client error (404) results in rc=1."""
    client = RequestClient(base_url="https://api.test.com", logger=mock_logger)
    mock_response.status_code = 404
    
    rc, resp = client.get(endpoint="missing")

    assert rc == 1
    assert resp is mock_response
    
    mock_logger.error.assert_called_once()
    assert "[HTTP] client error rc=404" in mock_logger.error.call_args[0][0]

# =========================================================================
# POST Method Tests
# =========================================================================

def test_post_json_success_201(mock_requests_request, mock_response):
    """Test successful POST request (rc=0) and correct data/headers."""
    client = RequestClient(base_url="https://api.test.com", timeout=(1, 5))
    mock_response.status_code = 201
    
    test_data = {"id": 1, "status": "new"}
    rc, resp = client.post_json(endpoint="create", json_dict=test_data)

    assert rc == 0
    
    mock_requests_request.assert_called_once()
    call_args, call_kwargs = mock_requests_request.call_args
    
    # FIX: Get method from positional arguments (index 0)
    assert call_args[0] == "POST" 
    
    assert call_kwargs['data'] == json.dumps(test_data).encode("utf-8")
    assert call_kwargs['headers']['Content-Type'] == "application/json"


def test_post_json_serialization_error(mock_requests_request, mock_logger):
    """Test failure during JSON serialization results in rc=2."""
    client = RequestClient(base_url="https://api.test.com", logger=mock_logger)
    
    # Use a value that cannot be serialized to JSON (e.g., a set)
    non_serializable_data = {"key": {1, 2}} 
    
    rc, resp = client.post_json(endpoint="create", json_dict=non_serializable_data)

    assert rc == 2
    assert resp is None
    
    mock_requests_request.assert_not_called()
    
    mock_logger.error.assert_called_once()
    assert "[HTTP] JSON serialization error" in mock_logger.error.call_args[0][0]

# =========================================================================
# Internal Error Handling Tests (_send_request)
# =========================================================================

def test_request_timeout(mock_requests_request, mock_logger):
    """Test network timeout results in rc=1."""
    client = RequestClient(base_url="http://test.com", logger=mock_logger)
    
    mock_requests_request.side_effect = requests.exceptions.Timeout("Read timed out.")
    
    rc, resp = client.get(endpoint="/data")

    assert rc == 1
    assert resp is None
    mock_logger.error.assert_called_once_with("[HTTP] timeout")

def test_request_connection_error(mock_requests_request, mock_logger):
    """Test connection error results in rc=1."""
    client = RequestClient(base_url="http://test.com", logger=mock_logger)
    
    mock_requests_request.side_effect = requests.exceptions.ConnectionError("DNS lookup failed.")
    
    rc, resp = client.get(endpoint="/data")

    assert rc == 1
    assert resp is None
    mock_logger.error.assert_called_once()
    assert "[HTTP] connection error:" in mock_logger.error.call_args[0][0]
    
def test_request_unexpected_exception(mock_requests_request, mock_logger):
    """Test generic unexpected Python exception results in rc=2."""
    client = RequestClient(base_url="http://test.com", logger=mock_logger)
    
    mock_requests_request.side_effect = ZeroDivisionError("Math went wrong")
    
    rc, resp = client.get(endpoint="/data")

    assert rc == 2
    assert resp is None
    mock_logger.error.assert_called_once()
    assert "[HTTP] unexpected error: Math went wrong" in mock_logger.error.call_args[0][0]