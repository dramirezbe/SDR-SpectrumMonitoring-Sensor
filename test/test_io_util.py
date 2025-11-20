import pytest
import json
from pathlib import Path
from unittest.mock import MagicMock

# Imports work due to the setup in conftest.py
from app.utils.io_util import (
    atomic_write_bytes, 
    get_persist_var, 
    modify_persist, 
    CronHandler, 
    CronTab  # Import CronTab here to use it for MagicMock spec
)


# =========================================================================
# 1. FILE I/O TESTS (Using pytest's built-in tmp_path fixture)
# =========================================================================

def test_atomic_write_bytes_success(tmp_path: Path):
    """Test successful atomic write."""
    target_path = tmp_path / "test.bin"
    data = b"hello world"
    
    atomic_write_bytes(target_path, data)

    assert target_path.read_bytes() == data
    assert len(list(tmp_path.iterdir())) == 1

def test_modify_persist_create_new_file(tmp_path: Path):
    """Test creating a new JSON file with the key."""
    path = tmp_path / "vars.json"
    
    modify_persist("key_a", "value_a", path)

    assert path.exists()
    data = json.loads(path.read_text())
    assert data == {"key_a": "value_a"}

def test_get_persist_var_read_existing(tmp_path: Path):
    """Test reading a variable from an existing JSON file."""
    path = tmp_path / "vars.json"
    
    initial_data = {"key_a": 100, "key_b": "value"}
    path.write_text(json.dumps(initial_data))

    result = get_persist_var("key_a", path)
    assert result == 100
    
    result_missing = get_persist_var("key_c", path)
    assert result_missing is None


# =========================================================================
# 2. CRON HANDLER TESTS (Using pytest-mock fixture 'mocker')
# =========================================================================

@pytest.fixture
def mock_cron_instance():
    """Returns a mock instance that simulates a CronTab object."""
    return MagicMock(spec=CronTab)


def test_cron_handler_initialization_success(mocker, mock_cron_instance):
    """
    Test successful initialization and dependency injection.
    We patch the CronTab class itself (the constructor).
    """
    mock_get_time = MagicMock(return_value=1000)
    
    # 💡 CORRECT PATCH LOCATION: Patch the CronTab class as imported in io_util.
    # We store the resulting mock object (the mock of the class/constructor).
    mock_crontab_class = mocker.patch(
        'app.utils.io_util.CronTab', 
        return_value=mock_cron_instance # Set the return value to be our mock instance
    )

    # Action: This line internally calls CronTab(user=True)
    handler = CronHandler(get_time_ms=mock_get_time)
    
    # Assert the mock CLASS (constructor) was called
    mock_crontab_class.assert_called_once_with(user=True)
    
    # Assert the handler's internal 'cron' attribute is our mocked instance
    assert handler.cron is mock_cron_instance
    assert handler.crontab_changed is False


def test_cron_handler_add_and_save(mocker, mock_cron_instance):
    """Test adding a job, verifying crontab_changed flag, and saving."""
    mock_get_time = MagicMock(return_value=1000)
    
    # Set up the patch for the constructor
    mocker.patch('app.utils.io_util.CronTab', return_value=mock_cron_instance)
    handler = CronHandler(get_time_ms=mock_get_time)
    
    # 1. Test the add method
    handler.add(command="echo hi", comment="test_job", minutes=5)
    
    # Check job creation method calls on the mock instance
    job = mock_cron_instance.new.return_value
    job.setall.assert_called_once_with("*/5 * * * *")
    assert handler.crontab_changed is True
    
    # 2. Test the save method
    handler.save()
    mock_cron_instance.write.assert_called_once()
    assert handler.crontab_changed is False

def test_cron_handler_time_check():
    """Test the is_in_activate_time logic with a 10s guard window."""
    
    mock_get_time = MagicMock(return_value=20000) # Current time 20 seconds (20,000 ms)
    handler = CronHandler(get_time_ms=mock_get_time) 
    
    start_time = 10000 
    end_time = 30000 

    # start_guard: 0s, end_guard: 40s. Current time 20s is in range.
    assert handler.is_in_activate_time(start_time, end_time) is True

    # Test just outside the end guard (41s)
    mock_get_time.return_value = 41001 
    assert handler.is_in_activate_time(start_time, end_time) is False