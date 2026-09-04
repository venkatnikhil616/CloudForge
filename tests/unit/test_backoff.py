from services.worker.executor import calculate_exponential_backoff


def test_exponential_backoff_calculation():
    # Base: 5 seconds
    # Attempt 1: 5 * 3^0 = 5
    assert calculate_exponential_backoff(1, base_seconds=5) == 5
    # Attempt 2: 5 * 3^1 = 15
    assert calculate_exponential_backoff(2, base_seconds=5) == 15
    # Attempt 3: 5 * 3^2 = 45
    assert calculate_exponential_backoff(3, base_seconds=5) == 45
    # Attempt 4: 5 * 3^3 = 135
    assert calculate_exponential_backoff(4, base_seconds=5) == 135
