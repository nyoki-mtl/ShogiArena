"""Tests for SPSA gradient update logic and quantization behavior."""

from shogiarena.arena.tuning.param_io import ParamEntry, quantize_value


def test_quantize_value_int_boundary_clamping():
    """Test that integer quantization clamps properly at boundaries."""
    p = ParamEntry(
        name="TestParam",
        type="int",
        v=5.0,
        min=0.0,
        max=10.0,
        step=1.0,
        delta=1.0,
        comment="Test integer parameter",
        not_used=False,
    )

    # Test boundary clamping
    assert quantize_value(p, -1.0) == 0.0  # Below min
    assert quantize_value(p, 11.0) == 10.0  # Above max
    assert quantize_value(p, 0.0) == 0.0  # At min
    assert quantize_value(p, 10.0) == 10.0  # At max

    # Test rounding to step
    assert quantize_value(p, 2.3) == 2.0
    assert quantize_value(p, 2.7) == 3.0
    assert quantize_value(p, 2.5) == 2.0  # Python's round() uses banker's rounding


def test_quantize_value_int_preserve_fractional():
    """When requested, integer params should retain fractional increments."""
    p = ParamEntry(
        name="FracParam",
        type="int",
        v=5.0,
        min=0.0,
        max=10.0,
        step=1.0,
        delta=1.0,
        comment="Integer value allowed to accumulate",
        not_used=False,
    )

    # Without rounding, fractional increments are preserved but still clamped.
    assert quantize_value(p, 5.2, round_int=False) == 5.2
    assert quantize_value(p, -3.0, round_int=False) == 0.0
    assert quantize_value(p, 15.0, round_int=False) == 10.0


def test_quantize_value_int_with_step():
    """Step magnitude should not affect integer quantization."""
    p = ParamEntry(
        name="StepParam",
        type="int",
        v=10.0,
        min=0.0,
        max=20.0,
        step=3.0,
        delta=1.0,
        comment="Integer with step 3",
        not_used=False,
    )

    # Should round to nearest integer regardless of step size
    assert quantize_value(p, 1.0) == 1.0
    assert quantize_value(p, 2.0) == 2.0
    assert quantize_value(p, 4.0) == 4.0
    assert quantize_value(p, 5.0) == 5.0

    # Test boundaries still clamp correctly
    assert quantize_value(p, 19.0) == 19.0
    assert quantize_value(p, 20.5) == 20.0  # Above max, clamp to max


def test_quantize_value_float_no_step():
    """Test float quantization without step snapping."""
    p = ParamEntry(
        name="FloatParam",
        type="float",
        v=5.0,
        min=0.0,
        max=10.0,
        step=0.1,
        delta=1.0,
        comment="Float parameter",
        not_used=False,
    )

    # Float should only clamp, not snap to step by default
    assert quantize_value(p, 2.3456) == 2.3456
    assert quantize_value(p, -0.5) == 0.0  # Clamp to min
    assert quantize_value(p, 12.0) == 10.0  # Clamp to max


def test_spsa_gain_schedule():
    """Test SPSA gain schedule calculations."""

    # Test standard SPSA gain schedules
    def compute_a_k(k: int, a0: float, A: float, alpha: float) -> float:
        return a0 / (A + k) ** alpha

    def compute_c_k(k: int, c0: float, gamma: float) -> float:
        return c0 / k**gamma

    # Standard parameters
    a0, A, alpha = 1.0, 10.0, 0.602
    c0, gamma = 1.0, 0.101

    # Test that gains decrease over time
    a1 = compute_a_k(1, a0, A, alpha)
    a10 = compute_a_k(10, a0, A, alpha)
    a100 = compute_a_k(100, a0, A, alpha)

    assert a1 > a10 > a100, "a_k should decrease with k"

    c1 = compute_c_k(1, c0, gamma)
    c10 = compute_c_k(10, c0, gamma)
    c100 = compute_c_k(100, c0, gamma)

    assert c1 > c10 > c100, "c_k should decrease with k"

    # Test specific values match expected SPSA behavior
    assert abs(a1 - a0 / (A + 1) ** alpha) < 1e-10
    assert abs(c1 - c0) < 1e-10  # c_k at k=1 should equal c0


def test_spsa_gradient_sign_effect():
    """Test that gradient sign affects parameter update direction."""

    def compute_param_update(current_val: float, grad: float, a_k: float, delta_i: float) -> float:
        """Simulate SPSA parameter update rule."""
        return current_val - a_k * delta_i * grad

    current_val = 5.0
    a_k = 0.1
    delta_i = 1.0

    # Positive gradient should decrease parameter (gradient descent)
    positive_grad = 2.0
    new_val_pos = compute_param_update(current_val, positive_grad, a_k, delta_i)
    assert new_val_pos < current_val, "Positive gradient should decrease parameter"

    # Negative gradient should increase parameter
    negative_grad = -2.0
    new_val_neg = compute_param_update(current_val, negative_grad, a_k, delta_i)
    assert new_val_neg > current_val, "Negative gradient should increase parameter"

    # Larger gradient magnitude should cause larger change
    large_grad = 10.0
    new_val_large = compute_param_update(current_val, large_grad, a_k, delta_i)
    assert abs(new_val_large - current_val) > abs(new_val_pos - current_val), (
        "Larger gradient should cause larger parameter change"
    )


def test_spsa_delta_perturbation():
    """Test SPSA Rademacher perturbation vector properties."""
    import random

    def generate_rademacher_deltas(params: list[str], seed: int = 42) -> dict[str, float]:
        """Generate ±1 Rademacher perturbations."""
        random.seed(seed)
        return {param: 1.0 if random.random() > 0.5 else -1.0 for param in params}

    params = ["param1", "param2", "param3", "param4"]
    deltas = generate_rademacher_deltas(params)

    # All deltas should be ±1
    for delta in deltas.values():
        assert delta in [-1.0, 1.0], "Rademacher deltas should be ±1"

    # Test different seeds produce different perturbations
    deltas2 = generate_rademacher_deltas(params, seed=123)
    assert deltas != deltas2, "Different seeds should produce different perturbations"


def test_gradient_estimation_formula():
    """Test SPSA gradient estimation formula."""

    def estimate_gradient(s_plus: float, s_minus: float, c_k: float, delta_i: float) -> float:
        """SPSA gradient estimate: g_i = (s+ - s-) / (2 * c_k * delta_i)."""
        if delta_i == 0:
            return 0.0  # Parameter not used
        return (s_plus - s_minus) / (2 * c_k * delta_i)

    c_k = 0.5
    delta_i = 1.0

    # Better performance at plus perturbation should give negative gradient
    s_plus, s_minus = 0.8, 0.4
    grad = estimate_gradient(s_plus, s_minus, c_k, delta_i)
    assert grad > 0, "Better s+ should give positive gradient"

    # Better performance at minus perturbation should give positive gradient
    s_plus, s_minus = 0.4, 0.8
    grad = estimate_gradient(s_plus, s_minus, c_k, delta_i)
    assert grad < 0, "Better s- should give negative gradient"

    # Equal performance should give zero gradient
    s_plus, s_minus = 0.6, 0.6
    grad = estimate_gradient(s_plus, s_minus, c_k, delta_i)
    assert abs(grad) < 1e-10, "Equal performance should give zero gradient"

    # Zero delta should give zero gradient (not_used parameter)
    grad_unused = estimate_gradient(0.8, 0.4, c_k, 0.0)
    assert grad_unused == 0.0, "Unused parameter should have zero gradient"


def test_boundary_detection():
    """Test detection of parameters near boundaries."""

    def is_near_boundary(value: float, min_val: float, max_val: float, threshold_pct: float = 0.05) -> tuple[bool, str]:
        """Check if parameter is near min/max boundary."""
        range_val = max_val - min_val
        threshold = range_val * threshold_pct

        if value <= min_val + threshold:
            return True, "min"
        elif value >= max_val - threshold:
            return True, "max"
        else:
            return False, ""

    # Test boundary detection
    min_val, max_val = 0.0, 100.0

    # Near minimum
    is_boundary, flag = is_near_boundary(2.0, min_val, max_val)
    assert is_boundary and flag == "min", "Should detect near minimum boundary"

    # Near maximum
    is_boundary, flag = is_near_boundary(98.0, min_val, max_val)
    assert is_boundary and flag == "max", "Should detect near maximum boundary"

    # In middle
    is_boundary, flag = is_near_boundary(50.0, min_val, max_val)
    assert not is_boundary and flag == "", "Should not detect boundary in middle"

    # Edge cases
    is_boundary, flag = is_near_boundary(0.0, min_val, max_val)
    assert is_boundary and flag == "min", "Should detect exact minimum"

    is_boundary, flag = is_near_boundary(100.0, min_val, max_val)
    assert is_boundary and flag == "max", "Should detect exact maximum"


def test_mini_batch_averaging():
    """Test that mini-batch averaging reduces gradient variance."""
    import statistics

    # Simulate noisy gradient estimates
    def noisy_gradient_estimate(true_gradient: float = 1.0, noise_std: float = 0.5) -> float:
        """Simulate a single noisy gradient estimate."""
        import random

        return true_gradient + random.gauss(0, noise_std)

    # Test that averaging reduces variance
    true_gradient = 2.0
    noise_std = 1.0
    n_trials = 100
    batch_sizes = [1, 2, 4, 8]

    variances = []
    for batch_size in batch_sizes:
        estimates = []
        for _ in range(n_trials):
            # Simulate mini-batch average
            batch_estimates = [noisy_gradient_estimate(true_gradient, noise_std) for _ in range(batch_size)]
            batch_average = sum(batch_estimates) / len(batch_estimates)
            estimates.append(batch_average)

        variance = statistics.variance(estimates)
        variances.append(variance)

    # Variance should decrease with batch size (approximately as 1/batch_size)
    assert variances[1] < variances[0], "Batch size 2 should have lower variance than batch size 1"
    assert variances[2] < variances[1], "Batch size 4 should have lower variance than batch size 2"
    assert variances[3] < variances[2], "Batch size 8 should have lower variance than batch size 4"


def test_inflight_factor_concurrency_limit():
    """Test that inflight_factor properly limits concurrent games."""

    def calculate_max_concurrent_games(num_workers: int, inflight_factor: int, batch_size: int) -> int:
        """Calculate maximum concurrent games based on SPSA configuration."""
        # Each batch item runs 2 games (plus and minus perturbations)
        # Limit based on inflight_factor and actual batch requirements
        return min(num_workers * inflight_factor, batch_size * 2)

    # Test various configurations
    test_cases = [
        # (num_workers, inflight_factor, batch_size, expected_max_concurrent)
        (4, 4, 1, 2),  # Limited by batch_size * 2
        (4, 4, 2, 4),  # Limited by batch_size * 2
        (4, 4, 8, 16),  # Limited by num_workers * inflight_factor
        (2, 2, 10, 4),  # Limited by num_workers * inflight_factor
        (8, 6, 5, 10),  # Limited by batch_size * 2
        (8, 6, 25, 48),  # Limited by num_workers * inflight_factor
    ]

    for num_workers, inflight_factor, batch_size, expected in test_cases:
        actual = calculate_max_concurrent_games(num_workers, inflight_factor, batch_size)
        assert actual == expected, (
            f"Workers={num_workers}, inflight={inflight_factor}, batch={batch_size}: expected {expected}, got {actual}"
        )


def test_batch_size_performance_scaling():
    """Test that batch size properly scales the number of games per update."""

    def games_per_update(batch_size: int) -> int:
        """Calculate total games per SPSA update."""
        # Each batch item runs 2 games (plus and minus perturbations)
        return batch_size * 2

    # Test scaling relationship
    assert games_per_update(1) == 2, "Batch size 1 should run 2 games"
    assert games_per_update(2) == 4, "Batch size 2 should run 4 games"
    assert games_per_update(4) == 8, "Batch size 4 should run 8 games"
    assert games_per_update(8) == 16, "Batch size 8 should run 16 games"

    # Verify linear scaling
    batch_sizes = [1, 2, 3, 4, 5]
    games = [games_per_update(bs) for bs in batch_sizes]

    for i in range(1, len(batch_sizes)):
        scaling_factor = games[i] / games[0]  # Relative to batch_size=1
        expected_factor = batch_sizes[i] / batch_sizes[0]
        assert abs(scaling_factor - expected_factor) < 0.001, "Games should scale linearly with batch size"


def test_batch_gradient_consistency():
    """Test that mini-batch gradient computation is mathematically consistent."""

    # Test that averaging individual gradients equals batch gradient
    def compute_individual_gradient(s_plus: float, s_minus: float, c_k: float, delta: float) -> float:
        """Compute SPSA gradient for a single game pair."""
        if delta == 0:
            return 0.0
        return (s_plus - s_minus) / (2 * c_k * delta)

    def compute_batch_gradient(score_pairs: list[tuple[float, float]], c_k: float, delta: float) -> float:
        """Compute SPSA gradient for a batch of game pairs."""
        if delta == 0:
            return 0.0

        # Average the scores first, then compute gradient
        avg_s_plus = sum(pair[0] for pair in score_pairs) / len(score_pairs)
        avg_s_minus = sum(pair[1] for pair in score_pairs) / len(score_pairs)
        return (avg_s_plus - avg_s_minus) / (2 * c_k * delta)

    # Test with sample data
    c_k = 0.5
    delta = 1.0
    score_pairs = [
        (0.7, 0.3),  # s_plus=0.7, s_minus=0.3
        (0.6, 0.4),  # s_plus=0.6, s_minus=0.4
        (0.8, 0.2),  # s_plus=0.8, s_minus=0.2
        (0.5, 0.5),  # s_plus=0.5, s_minus=0.5
    ]

    # Method 1: Average individual gradients
    individual_grads = [compute_individual_gradient(sp, sm, c_k, delta) for sp, sm in score_pairs]
    avg_individual_grad = sum(individual_grads) / len(individual_grads)

    # Method 2: Batch gradient (average scores first)
    batch_grad = compute_batch_gradient(score_pairs, c_k, delta)

    # They should be equal (within floating point precision)
    assert abs(avg_individual_grad - batch_grad) < 1e-10, "Individual and batch gradient methods should be equivalent"
