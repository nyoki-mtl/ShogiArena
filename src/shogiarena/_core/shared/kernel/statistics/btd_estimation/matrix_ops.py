from __future__ import annotations

import copy
from collections.abc import Callable


def numerical_hessian(
    theta: list[float],
    gradient_at: Callable[[list[float]], list[float]],
    h: float = 1e-4,
) -> list[list[float]]:
    param_count = len(theta)
    hessian = [[0.0 for _ in range(param_count)] for _ in range(param_count)]
    for col in range(param_count):
        theta_plus = list(theta)
        theta_minus = list(theta)
        theta_plus[col] += h
        theta_minus[col] -= h
        grad_plus = gradient_at(theta_plus)
        grad_minus = gradient_at(theta_minus)
        for row in range(param_count):
            hessian[row][col] = (grad_plus[row] - grad_minus[row]) / (2.0 * h)
    for row in range(param_count):
        for col in range(param_count):
            sym = 0.5 * (hessian[row][col] + hessian[col][row])
            hessian[row][col] = sym
    return hessian


def invert_neg_def(hessian: list[list[float]]) -> list[list[float]] | None:
    """Compute covariance ≈ inv(-H), or None when the Hessian is singular.

    A singular Hessian (e.g. a disconnected comparison graph) has no well-defined covariance, so we
    return None instead of a zero matrix that would masquerade as perfectly precise estimates.
    """
    matrix = copy.deepcopy(hessian)
    size = len(matrix)
    for row in range(size):
        for col in range(size):
            matrix[row][col] = -matrix[row][col]

    ridge = 1e-8
    for row in range(size):
        matrix[row][row] += ridge

    identity = [[0.0] * size for _ in range(size)]
    for row in range(size):
        identity[row][row] = 1.0

    for col in range(size):
        pivot = col
        max_val = abs(matrix[pivot][col])
        for row in range(col + 1, size):
            value = abs(matrix[row][col])
            if value > max_val:
                max_val = value
                pivot = row
        if max_val < 1e-12:
            return None
        if pivot != col:
            matrix[col], matrix[pivot] = matrix[pivot], matrix[col]
            identity[col], identity[pivot] = identity[pivot], identity[col]

        pivot_value = matrix[col][col]
        inv_pivot = 1.0 / pivot_value
        for row in range(size):
            matrix[col][row] *= inv_pivot
            identity[col][row] *= inv_pivot

        for row in range(size):
            if row == col:
                continue
            factor = matrix[row][col]
            if factor == 0.0:
                continue
            for inner_col in range(size):
                matrix[row][inner_col] -= factor * matrix[col][inner_col]
                identity[row][inner_col] -= factor * identity[col][inner_col]

    return identity


__all__ = ["invert_neg_def", "numerical_hessian"]
