import cshogi


def sfen_parser(sfen: str) -> cshogi.Board:
    """SFEN文字列から盤面を作成

    startposから始まる形式と、sfenから始まる形式の両方に対応しています。
    movesキーワードの後に指し手のリストがある場合は、それらの手も適用します。

    Args:
        sfen: SFEN形式の文字列

    Returns:
        解析された盤面

    Raises:
        ValueError: 不正なSFEN文字列や指し手が渡された場合
    """
    text = sfen.strip()
    if not text:
        raise ValueError("SFEN string must not be empty")

    if text.startswith("position "):
        text = text[len("position ") :].strip()

    tokens = text.split()
    if not tokens:
        raise ValueError("SFEN string must contain tokens")

    board = cshogi.Board()
    move_tokens: list[str] = []
    head = tokens[0]

    if head == "startpos":
        remainder = tokens[1:]
        board.set_sfen(cshogi.STARTING_SFEN)
    elif head == "sfen":
        if len(tokens) < 5:
            raise ValueError(f"Invalid SFEN (expected 'sfen <board> <turn> <hand> <ply>'): {sfen}")
        try:
            board.set_sfen(" ".join(tokens[1:5]))
        except (ValueError, RuntimeError) as exc:
            raise ValueError(f"Invalid SFEN position: {sfen}") from exc
        remainder = tokens[5:]
    else:
        if len(tokens) < 4:
            raise ValueError(f"Invalid SFEN token: {sfen}")
        board_token = tokens[0]
        if board_token.count("/") != 8:
            raise ValueError(f"Invalid SFEN board layout: {sfen}")
        if any(not part for part in board_token.split("/")):
            raise ValueError(f"Invalid SFEN board layout: {sfen}")
        turn_token = tokens[1]
        if turn_token not in {"b", "w"}:
            raise ValueError(f"Invalid SFEN turn token: {sfen}")
        try:
            board.set_sfen(" ".join(tokens[:4]))
        except (ValueError, RuntimeError) as exc:
            raise ValueError(f"Invalid SFEN position: {sfen}") from exc
        remainder = tokens[4:]

    if remainder:
        if remainder[0] != "moves":
            raise ValueError(f"Unexpected token sequence in SFEN: {sfen}")
        move_tokens = remainder[1:]

    for move_usi in move_tokens:
        try:
            move = board.move_from_usi(move_usi)
        except ValueError as exc:
            raise ValueError(f"Invalid move '{move_usi}' in SFEN sequence: {sfen}") from exc
        if not board.is_legal(move):
            raise ValueError(f"Illegal move '{move_usi}' in SFEN sequence: {sfen}")
        board.push(move)

    return board


def normalize_sfen(s: str) -> str:
    """Normalize position strings to a canonical SFEN token or "startpos".

    Supported inputs (examples):
    - "sfen <...>" (with or without "moves ...")
    - "position sfen <...>" (with or without "moves ...")
    - "startpos" or "position startpos" (with or without "moves ...")
    - Canonical SFEN token (e.g., "lnsgkgsnl/... b - 1")

    Behavior:
    - When "moves ..." is present (startpos/sfen forms), applies the moves and returns
      the resulting canonical SFEN (board.sfen()).
    - When representing the initial starting position with no moves, returns "startpos".
    - For plain SFEN tokens, returns the token unchanged.
    """
    raw = s.strip()
    # Strip optional "position " prefix
    if raw.startswith("position "):
        raw = raw[len("position ") :].strip()

    # startpos (with or without moves)
    has_moves_keyword = " moves " in raw

    if raw.startswith("startpos") or raw.startswith("sfen "):
        board = sfen_parser(raw)
        sfen_token = str(board.sfen())
        if sfen_token == cshogi.STARTING_SFEN and not has_moves_keyword:
            return "startpos"
        return sfen_token

    if raw == "":
        return "startpos"

    board = sfen_parser(raw)
    sfen_token = str(board.sfen())
    if sfen_token == cshogi.STARTING_SFEN and not has_moves_keyword:
        return "startpos"
    return sfen_token
