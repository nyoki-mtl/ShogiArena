// OffscreenCanvas renderer for live board.
// This is a Worker-port of `ShogiBoardRenderer` (shared/components/shogi-board.ts),
// keeping visuals/functionality close while avoiding DOM dependencies.

type InitMessage = { type: 'init'; canvas: OffscreenCanvas };
type ResizeMessage = {
    type: 'resize';
    width: number;
    height: number;
    cssWidth?: number;
    cssHeight?: number;
    dpr?: number;
};
type PositionMessage = { type: 'position'; sfen?: string };
type MovesMessage = { type: 'moves'; moves: string[] };
type AppendMovesMessage = { type: 'append_moves'; moves: string[] };
type GoToMessage = { type: 'goto'; ply: number };
type HighlightMessage = { type: 'highlight'; squares: number[] };
type FlipMessage = { type: 'flip'; enabled: boolean };
type ThemeMessage = { type: 'theme'; theme: Partial<BoardTheme> };

type WorkerMessage =
    | InitMessage
    | ResizeMessage
    | PositionMessage
    | MovesMessage
    | AppendMovesMessage
    | GoToMessage
    | HighlightMessage
    | FlipMessage
    | ThemeMessage;

enum PieceType {
    EMPTY = 0,
    PAWN = 1,
    LANCE = 2,
    KNIGHT = 3,
    SILVER = 4,
    GOLD = 5,
    BISHOP = 6,
    ROOK = 7,
    KING = 8,
    PRO_PAWN = 9,
    PRO_LANCE = 10,
    PRO_KNIGHT = 11,
    PRO_SILVER = 12,
    HORSE = 13,
    DRAGON = 14,
}

enum Color {
    BLACK = 0,
    WHITE = 1,
}

interface BoardPiece {
    type: PieceType;
    color: Color;
}

interface ParsedMove {
    from: { file: number; rank: number } | null;
    to: { file: number; rank: number };
    piece: PieceType;
    promote: boolean;
}

interface BoardTheme {
    boardColor: string;
    gridColor: string;
    blackPieceColor: string;
    whitePieceColor: string;
    lastMoveColor: string;
    highlightColor: string;
    coordinateColor: string;
}

const DEFAULT_THEME: BoardTheme = {
    boardColor: '#f0d9b5',
    gridColor: '#8b7355',
    blackPieceColor: '#000',
    whitePieceColor: '#000',
    lastMoveColor: 'rgba(50, 200, 50, 0.3)',
    highlightColor: 'rgba(255, 255, 0, 0.3)',
    coordinateColor: '#666',
};

class BoardState {
    board: (BoardPiece | null)[][];
    hands: Map<Color, Map<PieceType, number>>;
    currentTurn: Color;

    constructor() {
        this.board = Array.from({ length: 9 }, () => Array<BoardPiece | null>(9).fill(null));
        this.hands = new Map<Color, Map<PieceType, number>>([
            [Color.BLACK, new Map<PieceType, number>()],
            [Color.WHITE, new Map<PieceType, number>()],
        ]);
        this.currentTurn = Color.BLACK;
    }

    setPositionFromSFEN(sfen: unknown): void {
        const STARTPOS_SFEN = 'lnsgkgsnl/1r5b1/ppppppppp/9/9/9/PPPPPPPPP/1B5R1/LNSGKGSNL b - 1';

        const normalizeToken = (input: unknown): string => {
            const raw = String(input ?? '').trim();
            if (!raw) return 'startpos';
            let text = raw;
            if (text.startsWith('position ')) {
                text = text.slice('position '.length).trim();
            }
            if (text.startsWith('startpos')) {
                return 'startpos';
            }
            if (text.startsWith('sfen ')) {
                const tokens = text.split(/\\s+/);
                if (tokens.length >= 5) {
                    return tokens.slice(1, 5).join(' ');
                }
                return STARTPOS_SFEN;
            }
            const tokens = text.split(/\\s+/);
            const movesIdx = tokens.indexOf('moves');
            if (movesIdx > -1) {
                text = tokens.slice(0, movesIdx).join(' ');
            }
            return text || 'startpos';
        };

        const applyToken = (token: string): boolean => {
            const normalized = token === 'startpos' || token === '' ? STARTPOS_SFEN : token;
            const parts = normalized.split(' ');
            if (parts.length < 3) {
                return false;
            }

            const blackHands = this.hands.get(Color.BLACK);
            const whiteHands = this.hands.get(Color.WHITE);
            if (!blackHands || !whiteHands) {
                return false;
            }

            this.board = Array.from({ length: 9 }, () => Array<BoardPiece | null>(9).fill(null));
            blackHands.clear();
            whiteHands.clear();

            const rankSpecs = parts[0]?.split('/') ?? [];
            if (rankSpecs.length !== 9) {
                return false;
            }

            for (let rank = 0; rank < rankSpecs.length; rank++) {
                const row = rankSpecs[rank] ?? '';
                let file = 0;
                let promoteNext = false;
                for (let idx = 0; idx < row.length; idx++) {
                    const char = row[idx];
                    if (char === '+') {
                        if (promoteNext) {
                            return false;
                        }
                        promoteNext = true;
                        continue;
                    }
                    if (char >= '1' && char <= '9') {
                        file += parseInt(char, 10);
                        if (file > 9) {
                            return false;
                        }
                        promoteNext = false;
                        continue;
                    }
                    let piece = this.charToPiece(char);
                    if (!piece) {
                        return false;
                    }
                    if (promoteNext) {
                        piece = { type: this.promoteType(piece.type), color: piece.color };
                        promoteNext = false;
                    }
                    if (file >= 9) {
                        return false;
                    }
                    this.board[rank][file] = piece;
                    file += 1;
                }
                if (file !== 9) {
                    return false;
                }
            }

            const turnPart = parts[1];
            if (turnPart !== 'b' && turnPart !== 'w') {
                return false;
            }
            this.currentTurn = turnPart === 'b' ? Color.BLACK : Color.WHITE;

            const handsStr = parts[2];
            if (handsStr && handsStr !== '-') {
                let count = 0;
                for (let i = 0; i < handsStr.length; i++) {
                    const ch = handsStr[i];
                    if (ch >= '0' && ch <= '9') {
                        count = count * 10 + parseInt(ch, 10);
                        continue;
                    }
                    const pieceType = this.handCharToPieceType(ch);
                    if (pieceType == null) {
                        return false;
                    }
                    const color = ch === ch.toUpperCase() ? Color.BLACK : Color.WHITE;
                    const target = this.hands.get(color);
                    if (!target) {
                        return false;
                    }
                    target.set(pieceType, count || 1);
                    count = 0;
                }
                if (count !== 0) {
                    return false;
                }
            }
            return true;
        };

        const token = normalizeToken(sfen);
        if (applyToken(token)) {
            return;
        }

        throw new Error(`[arena-ui] Invalid SFEN token encountered: ${String(sfen)}`);
    }

    charToPiece(char: string): BoardPiece | null {
        const isBlack = char === char.toUpperCase();
        const color = isBlack ? Color.BLACK : Color.WHITE;
        const lowerChar = char.toLowerCase();
        const pieceMap: Record<string, PieceType> = {
            p: PieceType.PAWN,
            l: PieceType.LANCE,
            n: PieceType.KNIGHT,
            s: PieceType.SILVER,
            g: PieceType.GOLD,
            b: PieceType.BISHOP,
            r: PieceType.ROOK,
            k: PieceType.KING,
        };
        const type = pieceMap[lowerChar] ?? null;
        return type != null ? { type, color } : null;
    }

    promoteType(type: PieceType): PieceType {
        switch (type) {
            case PieceType.PAWN:
                return PieceType.PRO_PAWN;
            case PieceType.LANCE:
                return PieceType.PRO_LANCE;
            case PieceType.KNIGHT:
                return PieceType.PRO_KNIGHT;
            case PieceType.SILVER:
                return PieceType.PRO_SILVER;
            case PieceType.BISHOP:
                return PieceType.HORSE;
            case PieceType.ROOK:
                return PieceType.DRAGON;
            default:
                return type;
        }
    }

    demoteType(type: PieceType): PieceType {
        switch (type) {
            case PieceType.PRO_PAWN:
                return PieceType.PAWN;
            case PieceType.PRO_LANCE:
                return PieceType.LANCE;
            case PieceType.PRO_KNIGHT:
                return PieceType.KNIGHT;
            case PieceType.PRO_SILVER:
                return PieceType.SILVER;
            case PieceType.HORSE:
                return PieceType.BISHOP;
            case PieceType.DRAGON:
                return PieceType.ROOK;
            default:
                return type;
        }
    }

    handCharToPieceType(char: string): PieceType | null {
        const lowerChar = char.toLowerCase();
        const pieceMap: Record<string, PieceType> = {
            p: PieceType.PAWN,
            l: PieceType.LANCE,
            n: PieceType.KNIGHT,
            s: PieceType.SILVER,
            g: PieceType.GOLD,
            b: PieceType.BISHOP,
            r: PieceType.ROOK,
        };
        return Object.hasOwn(pieceMap, lowerChar) ? pieceMap[lowerChar] : null;
    }

    getPiece(file: number, rank: number): BoardPiece | null {
        return this.board[rank - 1]?.[9 - file] ?? null;
    }

    getHands(color: Color): Map<PieceType, number> {
        const existing = this.hands.get(color);
        if (existing) return existing;
        const created = new Map<PieceType, number>();
        this.hands.set(color, created);
        return created;
    }

    getTurn(): Color {
        return this.currentTurn;
    }
}

let canvas: OffscreenCanvas | null = null;
let ctx!: OffscreenCanvasRenderingContext2D;
let pixelRatio = 1;
let logicalWidth = 0;
let logicalHeight = 0;

const boardState = new BoardState();
let isFlipped = false;
let highlightedSquares = new Set<string>();
let lastMove: ParsedMove | null = null;
let moves: string[] = [];
let initialSfen = '';
let theme: BoardTheme = { ...DEFAULT_THEME };

let reportedFatal = false;

function serializeError(error: unknown): Record<string, unknown> {
    if (error instanceof Error) {
        return { name: error.name, message: error.message, stack: error.stack ?? null };
    }
    if (!error || typeof error !== 'object') {
        return { message: String(error) };
    }
    const anyErr = error as Record<string, unknown>;
    const message = typeof anyErr.message === 'string' ? anyErr.message : String(error);
    const filename = typeof anyErr.filename === 'string' ? anyErr.filename : null;
    const lineno = typeof anyErr.lineno === 'number' ? anyErr.lineno : null;
    const colno = typeof anyErr.colno === 'number' ? anyErr.colno : null;
    return { message, filename, lineno, colno };
}

function reportFatal(context: string, error: unknown): void {
    if (reportedFatal) return;
    reportedFatal = true;
    try {
        // notify main thread so it can log a meaningful error and fallback
        // eslint-disable-next-line no-restricted-globals
        (self as unknown as { postMessage: (value: unknown) => void }).postMessage({
            type: '__error__',
            error: { context, ...serializeError(error) },
        });
    } catch {
        // ignore
    }
}

function ensureInit(): void {
    if (!ctx || !canvas) {
        throw new Error('Offscreen board worker is not initialized');
    }
}

function setHighlightSquares(indices: Iterable<number> | null | undefined): void {
    highlightedSquares = new Set<string>();
    if (!indices) return;
    for (const sq of indices) {
        const value = Number(sq);
        if (!Number.isFinite(value)) continue;
        const file = (value % 9) + 1;
        const rank = Math.floor(value / 9) + 1;
        highlightedSquares.add(`${file},${rank}`);
    }
}

function applyUsiMove(usiMove: string): void {
    const move: ParsedMove = {
        from: null,
        to: { file: 0, rank: 0 },
        piece: PieceType.PAWN,
        promote: false,
    };

    if (usiMove.includes('*')) {
        const [pieceChar, toPart] = usiMove.split('*');
        const toStr = toPart ?? '';
        move.to.file = Number.parseInt(toStr[0] ?? '0', 10);
        move.to.rank = (toStr[1]?.toLowerCase().charCodeAt(0) ?? 'a'.charCodeAt(0)) - 96;
        const dropPieceMap: Record<string, PieceType> = {
            P: PieceType.PAWN,
            L: PieceType.LANCE,
            N: PieceType.KNIGHT,
            S: PieceType.SILVER,
            G: PieceType.GOLD,
            B: PieceType.BISHOP,
            R: PieceType.ROOK,
        };
        move.piece = dropPieceMap[pieceChar as keyof typeof dropPieceMap] ?? PieceType.PAWN;
    } else {
        const fromFile = Number.parseInt(usiMove[0] ?? '0', 10);
        const fromRank = (usiMove[1]?.toLowerCase().charCodeAt(0) ?? 'a'.charCodeAt(0)) - 96;
        const toFile = Number.parseInt(usiMove[2] ?? '0', 10);
        const toRank = (usiMove[3]?.toLowerCase().charCodeAt(0) ?? 'a'.charCodeAt(0)) - 96;
        move.from = { file: fromFile, rank: fromRank };
        move.to = { file: toFile, rank: toRank };
        move.promote = usiMove.length > 4 && usiMove[4] === '+';
        const piece = boardState.getPiece(fromFile, fromRank);
        if (piece) move.piece = piece.type;
    }

    const turn = boardState.getTurn();
    const idxFrom = move.from ? { r: move.from.rank - 1, c: 9 - move.from.file } : null;
    const idxTo = { r: move.to.rank - 1, c: 9 - move.to.file };
    if (idxTo.r < 0 || idxTo.r >= 9 || idxTo.c < 0 || idxTo.c >= 9) {
        return;
    }

    if (!move.from) {
        const pt = move.piece;
        boardState.board[idxTo.r][idxTo.c] = { type: pt, color: turn };
        const hands = boardState.getHands(turn);
        const current = (hands.get(pt) ?? 0) - 1;
        if (current > 0) {
            hands.set(pt, current);
        } else {
            hands.delete(pt);
        }
    } else if (idxFrom && idxFrom.r >= 0 && idxFrom.r < 9 && idxFrom.c >= 0 && idxFrom.c < 9) {
        const movingPiece = boardState.getPiece(move.from.file, move.from.rank);
        if (!movingPiece) {
            lastMove = move;
            return;
        }
        const captured = boardState.getPiece(move.to.file, move.to.rank);
        if (captured && captured.color !== movingPiece.color) {
            const capturedBase = boardState.demoteType(captured.type);
            const hands = boardState.getHands(movingPiece.color);
            hands.set(capturedBase, (hands.get(capturedBase) ?? 0) + 1);
        }
        const newType = move.promote ? boardState.promoteType(movingPiece.type) : movingPiece.type;
        boardState.board[idxFrom.r][idxFrom.c] = null;
        boardState.board[idxTo.r][idxTo.c] = { type: newType, color: movingPiece.color };
    }

    boardState.currentTurn = turn === Color.BLACK ? Color.WHITE : Color.BLACK;
    lastMove = move;
}

function getPieceChar(type: PieceType): string {
    const pieceChars: Record<number, string> = {
        [PieceType.PAWN]: '歩',
        [PieceType.LANCE]: '香',
        [PieceType.KNIGHT]: '桂',
        [PieceType.SILVER]: '銀',
        [PieceType.GOLD]: '金',
        [PieceType.BISHOP]: '角',
        [PieceType.ROOK]: '飛',
        [PieceType.KING]: '王',
        [PieceType.PRO_PAWN]: 'と',
        [PieceType.PRO_LANCE]: '杏',
        [PieceType.PRO_KNIGHT]: '圭',
        [PieceType.PRO_SILVER]: '全',
        [PieceType.HORSE]: '馬',
        [PieceType.DRAGON]: '龍',
    };
    return pieceChars[type] ?? '';
}

function toKanjiNumber(value: number): string {
    const kanjiNumbers = ['', '一', '二', '三', '四', '五', '六', '七', '八', '九'];
    return kanjiNumbers[value] ?? '';
}

function render(): void {
    if (!ctx || !canvas) return;

    const ratio = pixelRatio || 1;
    const width = logicalWidth || Math.max(1, Math.floor(canvas.width / ratio));
    const height = logicalHeight || Math.max(1, Math.floor(canvas.height / ratio));
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.imageSmoothingEnabled = false;

    const sideMarginCells = 2;
    const verticalMarginCells = 3;
    const cellSizeByWidth = Math.floor(width / (9 + sideMarginCells));
    const cellSizeByHeight = Math.floor(height / (9 + verticalMarginCells));
    const cellSize = Math.max(1, Math.min(cellSizeByWidth, cellSizeByHeight));
    const boardSize = cellSize * 9;
    const offsetX = Math.floor((width - boardSize) / 2);
    const offsetY = Math.floor((height - boardSize) / 2);

    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = theme.boardColor;
    ctx.fillRect(0, 0, width, height);

    ctx.strokeStyle = theme.gridColor;
    const gridLineWidth = Math.max(1 / ratio, 0.5 / ratio);
    ctx.lineWidth = gridLineWidth;
    for (let i = 0; i <= 9; i++) {
        ctx.beginPath();
        ctx.moveTo(offsetX + i * cellSize, offsetY);
        ctx.lineTo(offsetX + i * cellSize, offsetY + boardSize);
        ctx.stroke();
        ctx.beginPath();
        ctx.moveTo(offsetX, offsetY + i * cellSize);
        ctx.lineTo(offsetX + boardSize, offsetY + i * cellSize);
        ctx.stroke();
    }

    if (lastMove?.to) {
        ctx.fillStyle = theme.lastMoveColor;
        if (lastMove.from) {
            const fromFile = isFlipped ? 10 - lastMove.from.file : lastMove.from.file;
            const fromRank = isFlipped ? 10 - lastMove.from.rank : lastMove.from.rank;
            const fromX = offsetX + (9 - fromFile) * cellSize;
            const fromY = offsetY + (fromRank - 1) * cellSize;
            ctx.fillRect(fromX, fromY, cellSize, cellSize);
        }
        const toFile = isFlipped ? 10 - lastMove.to.file : lastMove.to.file;
        const toRank = isFlipped ? 10 - lastMove.to.rank : lastMove.to.rank;
        const toX = offsetX + (9 - toFile) * cellSize;
        const toY = offsetY + (toRank - 1) * cellSize;
        ctx.fillStyle = 'rgba(50, 200, 50, 0.5)';
        ctx.fillRect(toX, toY, cellSize, cellSize);
    }

    ctx.font = `${Math.floor(cellSize * 0.7)}px serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (let rank = 1; rank <= 9; rank++) {
        for (let file = 1; file <= 9; file++) {
            const piece = boardState.getPiece(file, rank);
            if (!piece) continue;
            const displayFile = isFlipped ? 10 - file : file;
            const displayRank = isFlipped ? 10 - rank : rank;
            const x = offsetX + (9 - displayFile) * cellSize + cellSize / 2;
            const y = offsetY + (displayRank - 1) * cellSize + cellSize / 2;

            if (highlightedSquares.has(`${file},${rank}`)) {
                ctx.fillStyle = theme.highlightColor;
                ctx.fillRect(x - cellSize / 2, y - cellSize / 2, cellSize, cellSize);
            }

            ctx.save();
            if (piece.color === Color.WHITE) {
                ctx.translate(x, y);
                ctx.rotate(Math.PI);
                ctx.translate(-x, -y);
            }
            ctx.fillStyle = piece.color === Color.BLACK ? theme.blackPieceColor : theme.whitePieceColor;
            ctx.fillText(getPieceChar(piece.type), x, y);
            ctx.restore();
        }
    }

    ctx.font = `${Math.floor(cellSize * 0.3)}px sans-serif`;
    ctx.fillStyle = theme.coordinateColor;
    for (let i = 1; i <= 9; i++) {
        const fileNum = isFlipped ? i : 10 - i;
        ctx.fillText(String(fileNum), offsetX + (i - 0.5) * cellSize, offsetY - cellSize * 0.3);
        const rankNum = isFlipped ? 10 - i : i;
        ctx.fillText(toKanjiNumber(rankNum), offsetX + boardSize + cellSize * 0.3, offsetY + (i - 0.5) * cellSize);
    }

    const drawHandsRegion = (xLeft: number, yTop: number, regionW: number, regionH: number): void => {
        ctx.fillStyle = 'rgba(0,0,0,0.08)';
        ctx.fillRect(xLeft, yTop, regionW, regionH);
        ctx.strokeStyle = '#d8c7a0';
        ctx.lineWidth = gridLineWidth;
        ctx.strokeRect(xLeft, yTop, regionW, regionH);
    };

    const drawHandTiles = (color: Color, xLeft: number, yTop: number, regionW: number, regionH: number): void => {
        const orderSente: PieceType[] = [
            PieceType.ROOK,
            PieceType.BISHOP,
            PieceType.GOLD,
            PieceType.SILVER,
            PieceType.KNIGHT,
            PieceType.LANCE,
            PieceType.PAWN,
        ];
        const orderGote: PieceType[] = [
            PieceType.PAWN,
            PieceType.LANCE,
            PieceType.KNIGHT,
            PieceType.SILVER,
            PieceType.GOLD,
            PieceType.BISHOP,
            PieceType.ROOK,
        ];
        const order = color === Color.WHITE ? orderGote : orderSente;
        const hands = boardState.getHands(color);
        const slotW = Math.floor(regionW / order.length);
        const centerY = yTop + Math.floor(regionH / 2);
        const glyphSize = Math.max(14, Math.floor(cellSize * 0.7));
        const countSize = Math.max(9, Math.floor(cellSize * 0.32));
        ctx.textBaseline = 'middle';
        ctx.fillStyle = '#111';

        for (let i = 0; i < order.length; i++) {
            const pt = order[i];
            const cnt = hands.get(pt) ?? 0;
            if (cnt <= 0) continue;
            const centerX = xLeft + Math.floor(i * slotW + slotW / 2);
            ctx.save();
            ctx.font = `${glyphSize}px serif`;
            ctx.textAlign = 'center';
            ctx.translate(centerX, centerY + Math.floor(glyphSize * 0.06));
            if (color === Color.WHITE) {
                ctx.rotate(Math.PI);
            }
            ctx.fillText(getPieceChar(pt), 0, 0);
            if (cnt > 1) {
                ctx.font = `${countSize}px sans-serif`;
                ctx.textAlign = 'left';
                ctx.textBaseline = 'alphabetic';
                const offsetX = Math.floor(slotW * 0.28);
                const offsetY = Math.floor(regionH * 0.35);
                ctx.fillText(String(cnt), offsetX, offsetY);
            }
            ctx.restore();
        }
    };

    const mmToPx = (mm: number): number => Math.round((96 / 25.4) * mm);
    const regionW = Math.floor(cellSize * 8.0);
    const regionH = Math.floor(cellSize * 0.9);
    const regionXSente = offsetX + boardSize - regionW;
    const regionXGote = offsetX;
    const bandGap = Math.max(0, Math.floor(cellSize * 0.8) - mmToPx(3));
    const goteRegionY = Math.max(0, offsetY - bandGap - regionH);
    const senteRegionY = Math.min(height - regionH, offsetY + boardSize + bandGap);

    drawHandsRegion(regionXGote, goteRegionY, regionW, regionH);
    drawHandsRegion(regionXSente, senteRegionY, regionW, regionH);
    drawHandTiles(Color.WHITE, regionXGote, goteRegionY, regionW, regionH);
    drawHandTiles(Color.BLACK, regionXSente, senteRegionY, regionW, regionH);

    if (lastMove && !lastMove.from) {
        const mover = boardState.getTurn() === Color.BLACK ? Color.WHITE : Color.BLACK;
        const orderSente: PieceType[] = [
            PieceType.ROOK,
            PieceType.BISHOP,
            PieceType.GOLD,
            PieceType.SILVER,
            PieceType.KNIGHT,
            PieceType.LANCE,
            PieceType.PAWN,
        ];
        const orderGote: PieceType[] = [
            PieceType.PAWN,
            PieceType.LANCE,
            PieceType.KNIGHT,
            PieceType.SILVER,
            PieceType.GOLD,
            PieceType.BISHOP,
            PieceType.ROOK,
        ];
        const order = mover === Color.WHITE ? orderGote : orderSente;
        const slotW = Math.floor(regionW / order.length);
        const centerY =
            mover === Color.WHITE ? goteRegionY + Math.floor(regionH / 2) : senteRegionY + Math.floor(regionH / 2);
        const pt = lastMove.piece;
        const idx = order.indexOf(pt);
        if (idx >= 0) {
            const centerX =
                mover === Color.WHITE
                    ? regionXGote + Math.floor(idx * slotW + slotW / 2)
                    : regionXSente + Math.floor(idx * slotW + slotW / 2);
            const w = Math.floor(slotW * 0.9);
            const h = Math.floor(regionH * 0.9);
            ctx.fillStyle = theme.lastMoveColor;
            ctx.fillRect(centerX - Math.floor(w / 2), centerY - Math.floor(h / 2), w, h);
        }
    }
}

function handleInit(message: InitMessage): void {
    try {
        const context = message.canvas.getContext('2d', { alpha: false });
        if (!context) {
            throw new Error('Offscreen canvas context is unavailable');
        }
        canvas = message.canvas;
        ctx = context;
        render();
    } catch (error) {
        reportFatal('init', error);
        throw error;
    }
}

function handleResize(message: ResizeMessage): void {
    ensureInit();
    const w = Math.max(1, Math.floor(message.width));
    const h = Math.max(1, Math.floor(message.height));
    const activeCanvas = canvas;
    if (!activeCanvas) {
        throw new Error('Offscreen board worker is not initialized');
    }
    activeCanvas.width = w;
    activeCanvas.height = h;
    const dpr = typeof message.dpr === 'number' && Number.isFinite(message.dpr) ? Math.max(1, message.dpr) : 1;
    pixelRatio = dpr;
    if (typeof message.cssWidth === 'number' && Number.isFinite(message.cssWidth)) {
        logicalWidth = Math.max(1, Math.floor(message.cssWidth));
    } else {
        logicalWidth = Math.max(1, Math.floor(w / dpr));
    }
    if (typeof message.cssHeight === 'number' && Number.isFinite(message.cssHeight)) {
        logicalHeight = Math.max(1, Math.floor(message.cssHeight));
    } else {
        logicalHeight = Math.max(1, Math.floor(h / dpr));
    }
    render();
}

function handlePosition(message: PositionMessage): void {
    initialSfen = String(message.sfen ?? '');
    boardState.setPositionFromSFEN(initialSfen);
    moves = [];
    lastMove = null;
    render();
}

function handleMoves(message: MovesMessage): void {
    moves = Array.isArray(message.moves) ? [...message.moves] : [];
}

function handleAppendMoves(message: AppendMovesMessage): void {
    const appended = Array.isArray(message.moves) ? message.moves : [];
    if (!appended.length) return;
    for (const move of appended) {
        moves.push(String(move ?? ''));
    }
}

function handleGoTo(message: GoToMessage): void {
    ensureInit();
    if (!Number.isFinite(message.ply)) return;
    const target = Math.max(0, Math.floor(message.ply));
    if (target > moves.length) return;

    lastMove = null;
    boardState.setPositionFromSFEN(initialSfen);
    for (let i = 0; i < target; i++) {
        const move = moves[i];
        if (typeof move === 'string') {
            applyUsiMove(move);
        }
    }
    render();
}

function handleHighlight(message: HighlightMessage): void {
    setHighlightSquares(Array.isArray(message.squares) ? message.squares : []);
    render();
}

function handleFlip(message: FlipMessage): void {
    isFlipped = Boolean(message.enabled);
    render();
}

function handleTheme(message: ThemeMessage): void {
    theme = { ...theme, ...(message.theme ?? {}) };
    render();
}

self.onmessage = (event: MessageEvent<WorkerMessage>) => {
    try {
        const data = event.data;
        if (!data || typeof data !== 'object' || typeof (data as { type?: unknown }).type !== 'string') return;
        switch ((data as WorkerMessage).type) {
            case 'init':
                handleInit(data as InitMessage);
                break;
            case 'resize':
                handleResize(data as ResizeMessage);
                break;
            case 'position':
                handlePosition(data as PositionMessage);
                break;
            case 'moves':
                handleMoves(data as MovesMessage);
                break;
            case 'append_moves':
                handleAppendMoves(data as AppendMovesMessage);
                break;
            case 'goto':
                handleGoTo(data as GoToMessage);
                break;
            case 'highlight':
                handleHighlight(data as HighlightMessage);
                break;
            case 'flip':
                handleFlip(data as FlipMessage);
                break;
            case 'theme':
                handleTheme(data as ThemeMessage);
                break;
            default:
                break;
        }
    } catch (error) {
        reportFatal('onmessage', error);
        throw error;
    }
};
