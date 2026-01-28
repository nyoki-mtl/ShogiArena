"""Type stubs for cshogi.dlshogi - Deep Learning Shogi utilities

This module provides dlshogi-specific features and utilities for the cshogi library.
These features are used for deep learning models like AlphaZero-style neural networks.
"""

from typing import Any

import numpy as np
from cshogi import Board
from numpy.typing import NDArray

# Feature dimensions
# Number of features in the first feature plane (board position features)
FEATURES1_NUM: int

# Number of features in the second feature plane (auxiliary features)
# This value can change based on whether nyugyoku features are enabled
FEATURES2_NUM: int

def make_move_label(move: int, color: int) -> int:
    """Convert a move to a move label for policy network training.

    Args:
        move: A move value (typically from cshogi.Move)
        color: The color of the player (cshogi.BLACK or cshogi.WHITE)

    Returns:
        The corresponding move label index for the policy network
    """
    ...

def make_input_features(
    board: Board, features1: NDArray[np.floating[Any]], features2: NDArray[np.floating[Any]]
) -> None:
    """Extract input features from a board position for dlshogi models.

    This function fills the provided numpy arrays with feature representations
    of the current board state suitable for neural network input.

    Args:
        board: A Board object representing the current game state
        features1: A numpy array of shape (FEATURES1_NUM, 9, 9) to be filled
                  with the first set of features (board position features)
        features2: A numpy array of shape (FEATURES2_NUM, 9, 9) to be filled
                  with the second set of features (auxiliary features)

    Note:
        The arrays are modified in-place. Make sure they have the correct
        shape and dtype before calling this function.
    """
    ...

def use_nyugyoku_features(use: bool) -> None:
    """Set whether to use nyugyoku (entering king) features in the model.

    This function switches between different feature sets and updates
    the FEATURES2_NUM constant accordingly.

    Args:
        use: If True, use nyugyoku features; otherwise, use normal features

    Note:
        This function modifies the global FEATURES2_NUM constant.
        Call this before creating feature arrays or models that depend
        on the feature dimensions.
    """
    ...

__all__ = ["FEATURES1_NUM", "FEATURES2_NUM", "make_input_features", "make_move_label", "use_nyugyoku_features"]
