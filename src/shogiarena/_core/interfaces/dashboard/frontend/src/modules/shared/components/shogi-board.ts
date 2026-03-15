import type { LiveBoardAdapter } from '@/modules/live/types';

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
                const tokens = text.split(/\s+/);
                if (tokens.length >= 5) {
                    return tokens.slice(1, 5).join(' ');
                }
                return STARTPOS_SFEN;
            }
            const tokens = text.split(/\s+/);
            const movesIdx = tokens.indexOf('moves');
            if (movesIdx > -1) {
                text = tokens.slice(0, movesIdx).join(' ');
            }
            return text || 'startpos';
        };

        const applyToken = (token: string): boolean => {
            try {
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
            } catch (_error) {
                return false;
            }
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

class ShogiBoardRenderer {
    private container: HTMLElement;
    private canvas: HTMLCanvasElement;
    private ctx: CanvasRenderingContext2D;
    private boardState: BoardState;
    private isFlipped: boolean;
    private highlightedSquares: Set<string>;
    private lastMove: ParsedMove | null;
    private moves: string[];
    private initialSfen: string;
    private pixelRatio: number;
    private logicalWidth: number;
    private logicalHeight: number;
    private theme: BoardTheme;
    private readonly resizeHandler: () => void;

    constructor(container: HTMLElement) {
        if (!(container instanceof HTMLElement)) {
            throw new Error('ShogiBoardRenderer requires an HTMLElement container');
        }
        this.container = container;
        const doc = container.ownerDocument ?? document;
        this.canvas = doc.createElement('canvas');
        const context = this.canvas.getContext('2d');
        if (!context) {
            throw new Error('Failed to get canvas context');
        }
        this.ctx = context;
        this.container.appendChild(this.canvas);

        this.boardState = new BoardState();
        this.isFlipped = false;
        this.highlightedSquares = new Set<string>();
        this.lastMove = null;
        this.moves = [];
        this.initialSfen = '';
        this.pixelRatio = window.devicePixelRatio || 1;
        this.logicalWidth = 0;
        this.logicalHeight = 0;
        this.theme = { ...DEFAULT_THEME };

        this.resizeHandler = () => this.resize();
        this.resize();
        window.addEventListener('resize', this.resizeHandler);
    }

    resize(): void {
        const rect = this.container.getBoundingClientRect();
        const width = Math.max(1, Math.floor(rect.width || this.container.clientWidth || 0));
        const height = Math.max(1, Math.floor(rect.height || this.container.clientHeight || 0));
        const ratio = window.devicePixelRatio || 1;

        const nextCanvasWidth = Math.max(1, Math.floor(width * ratio));
        const nextCanvasHeight = Math.max(1, Math.floor(height * ratio));
        if (
            width === this.logicalWidth &&
            height === this.logicalHeight &&
            ratio === this.pixelRatio &&
            nextCanvasWidth === this.canvas.width &&
            nextCanvasHeight === this.canvas.height
        ) {
            return;
        }

        this.pixelRatio = ratio;
        this.logicalWidth = width;
        this.logicalHeight = height;
        this.canvas.width = nextCanvasWidth;
        this.canvas.height = nextCanvasHeight;
        this.canvas.style.width = `${width}px`;
        this.canvas.style.height = `${height}px`;
        this.render();
    }

    setPositionFromSFEN(sfen: string): void {
        this.initialSfen = String(sfen ?? '');
        this.boardState.setPositionFromSFEN(this.initialSfen);
        this.moves = [];
        this.lastMove = null;
        this.render();
    }

    setMoves(usiMoves: readonly string[] | null | undefined): void {
        this.moves = Array.isArray(usiMoves) ? [...usiMoves] : [];
    }

    goTo(ply: number): void {
        if (!Number.isFinite(ply)) return;
        const target = Math.max(0, Math.floor(ply));
        if (target > this.moves.length) return;

        this.lastMove = null;
        this.boardState.setPositionFromSFEN(this.initialSfen);
        for (let i = 0; i < target; i++) {
            const move = this.moves[i];
            if (typeof move === 'string') {
                this.applyUsiMove(move);
            }
        }
        this.render();
    }

    flip(enabled: boolean): void {
        this.isFlipped = Boolean(enabled);
        this.render();
    }

    setTheme(theme: Partial<BoardTheme> | null | undefined): void {
        if (!theme) return;
        this.theme = { ...this.theme, ...theme };
        this.render();
    }

    highlightSquares(squares: Iterable<number> | null | undefined): void {
        this.highlightedSquares.clear();
        if (squares) {
            for (const sq of squares) {
                const value = Number(sq);
                if (!Number.isFinite(value)) continue;
                const file = (value % 9) + 1;
                const rank = Math.floor(value / 9) + 1;
                this.highlightedSquares.add(`${file},${rank}`);
            }
        }
        this.render();
    }

    private applyUsiMove(usiMove: string): void {
        if (!usiMove || usiMove.length < 4) return;

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
            const piece = this.boardState.getPiece(fromFile, fromRank);
            if (piece) move.piece = piece.type;
        }

        const turn = this.boardState.getTurn();
        const idxFrom = move.from ? { r: move.from.rank - 1, c: 9 - move.from.file } : null;
        const idxTo = { r: move.to.rank - 1, c: 9 - move.to.file };
        if (idxTo.r < 0 || idxTo.r >= 9 || idxTo.c < 0 || idxTo.c >= 9) {
            return;
        }

        if (!move.from) {
            const pt = move.piece;
            this.boardState.board[idxTo.r][idxTo.c] = { type: pt, color: turn };
            const hands = this.boardState.getHands(turn);
            const current = (hands.get(pt) ?? 0) - 1;
            if (current > 0) {
                hands.set(pt, current);
            } else {
                hands.delete(pt);
            }
        } else if (idxFrom && idxFrom.r >= 0 && idxFrom.r < 9 && idxFrom.c >= 0 && idxFrom.c < 9) {
            const movingPiece = this.boardState.getPiece(move.from.file, move.from.rank);
            if (!movingPiece) {
                this.lastMove = move;
                return;
            }
            const captured = this.boardState.getPiece(move.to.file, move.to.rank);
            if (captured && captured.color !== movingPiece.color) {
                const capturedBase = this.boardState.demoteType(captured.type);
                const hands = this.boardState.getHands(movingPiece.color);
                hands.set(capturedBase, (hands.get(capturedBase) ?? 0) + 1);
            }
            const newType = move.promote ? this.boardState.promoteType(movingPiece.type) : movingPiece.type;
            this.boardState.board[idxFrom.r][idxFrom.c] = null;
            this.boardState.board[idxTo.r][idxTo.c] = { type: newType, color: movingPiece.color };
        }

        this.boardState.currentTurn = turn === Color.BLACK ? Color.WHITE : Color.BLACK;
        this.lastMove = move;
    }

    private render(): void {
        const ratio = this.pixelRatio || 1;
        const width = this.logicalWidth || Math.max(1, Math.floor(this.canvas.width / ratio));
        const height = this.logicalHeight || Math.max(1, Math.floor(this.canvas.height / ratio));
        this.ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        this.ctx.imageSmoothingEnabled = false;

        const sideMarginCells = 2;
        const verticalMarginCells = 3;
        const cellSizeByWidth = Math.floor(width / (9 + sideMarginCells));
        const cellSizeByHeight = Math.floor(height / (9 + verticalMarginCells));
        const cellSize = Math.max(1, Math.min(cellSizeByWidth, cellSizeByHeight));
        const boardSize = cellSize * 9;
        const offsetX = Math.floor((width - boardSize) / 2);
        const offsetY = Math.floor((height - boardSize) / 2);

        this.ctx.clearRect(0, 0, width, height);
        this.ctx.fillStyle = this.theme.boardColor;
        this.ctx.fillRect(0, 0, width, height);

        this.ctx.strokeStyle = this.theme.gridColor;
        const gridLineWidth = Math.max(1 / ratio, 0.5 / ratio);
        this.ctx.lineWidth = gridLineWidth;
        for (let i = 0; i <= 9; i++) {
            this.ctx.beginPath();
            this.ctx.moveTo(offsetX + i * cellSize, offsetY);
            this.ctx.lineTo(offsetX + i * cellSize, offsetY + boardSize);
            this.ctx.stroke();
            this.ctx.beginPath();
            this.ctx.moveTo(offsetX, offsetY + i * cellSize);
            this.ctx.lineTo(offsetX + boardSize, offsetY + i * cellSize);
            this.ctx.stroke();
        }

        if (this.lastMove?.to) {
            this.ctx.fillStyle = this.theme.lastMoveColor;
            if (this.lastMove.from) {
                const fromFile = this.isFlipped ? 10 - this.lastMove.from.file : this.lastMove.from.file;
                const fromRank = this.isFlipped ? 10 - this.lastMove.from.rank : this.lastMove.from.rank;
                const fromX = offsetX + (9 - fromFile) * cellSize;
                const fromY = offsetY + (fromRank - 1) * cellSize;
                this.ctx.fillRect(fromX, fromY, cellSize, cellSize);
            }
            const toFile = this.isFlipped ? 10 - this.lastMove.to.file : this.lastMove.to.file;
            const toRank = this.isFlipped ? 10 - this.lastMove.to.rank : this.lastMove.to.rank;
            const toX = offsetX + (9 - toFile) * cellSize;
            const toY = offsetY + (toRank - 1) * cellSize;
            this.ctx.fillStyle = 'rgba(50, 200, 50, 0.5)';
            this.ctx.fillRect(toX, toY, cellSize, cellSize);
        }

        this.ctx.font = `${Math.floor(cellSize * 0.7)}px serif`;
        this.ctx.textAlign = 'center';
        this.ctx.textBaseline = 'middle';
        for (let rank = 1; rank <= 9; rank++) {
            for (let file = 1; file <= 9; file++) {
                const piece = this.boardState.getPiece(file, rank);
                if (!piece) continue;
                const displayFile = this.isFlipped ? 10 - file : file;
                const displayRank = this.isFlipped ? 10 - rank : rank;
                const x = offsetX + (9 - displayFile) * cellSize + cellSize / 2;
                const y = offsetY + (displayRank - 1) * cellSize + cellSize / 2;

                if (this.highlightedSquares.has(`${file},${rank}`)) {
                    this.ctx.fillStyle = this.theme.highlightColor;
                    this.ctx.fillRect(x - cellSize / 2, y - cellSize / 2, cellSize, cellSize);
                }

                this.ctx.save();
                if (piece.color === Color.WHITE) {
                    this.ctx.translate(x, y);
                    this.ctx.rotate(Math.PI);
                    this.ctx.translate(-x, -y);
                }
                this.ctx.fillStyle =
                    piece.color === Color.BLACK ? this.theme.blackPieceColor : this.theme.whitePieceColor;
                this.ctx.fillText(this.getPieceChar(piece.type), x, y);
                this.ctx.restore();
            }
        }

        this.ctx.font = `${Math.floor(cellSize * 0.3)}px sans-serif`;
        this.ctx.fillStyle = this.theme.coordinateColor;
        for (let i = 1; i <= 9; i++) {
            const fileNum = this.isFlipped ? i : 10 - i;
            this.ctx.fillText(String(fileNum), offsetX + (i - 0.5) * cellSize, offsetY - cellSize * 0.3);
            const rankNum = this.isFlipped ? 10 - i : i;
            this.ctx.fillText(
                this.toKanjiNumber(rankNum),
                offsetX + boardSize + cellSize * 0.3,
                offsetY + (i - 0.5) * cellSize,
            );
        }

        const drawHandsRegion = (xLeft: number, yTop: number, regionW: number, regionH: number): void => {
            this.ctx.fillStyle = 'rgba(0,0,0,0.08)';
            this.ctx.fillRect(xLeft, yTop, regionW, regionH);
            this.ctx.strokeStyle = '#d8c7a0';
            this.ctx.lineWidth = gridLineWidth;
            this.ctx.strokeRect(xLeft, yTop, regionW, regionH);
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
            const hands = this.boardState.getHands(color);
            const slotW = Math.floor(regionW / order.length);
            const centerY = yTop + Math.floor(regionH / 2);
            const glyphSize = Math.max(14, Math.floor(cellSize * 0.7));
            const countSize = Math.max(9, Math.floor(cellSize * 0.32));
            this.ctx.textBaseline = 'middle';
            this.ctx.fillStyle = '#111';

            for (let i = 0; i < order.length; i++) {
                const pt = order[i];
                const cnt = hands.get(pt) ?? 0;
                if (cnt <= 0) continue;
                const centerX = xLeft + Math.floor(i * slotW + slotW / 2);
                this.ctx.save();
                this.ctx.font = `${glyphSize}px serif`;
                this.ctx.textAlign = 'center';
                this.ctx.translate(centerX, centerY + Math.floor(glyphSize * 0.06));
                if (color === Color.WHITE) {
                    this.ctx.rotate(Math.PI);
                }
                this.ctx.fillText(this.getPieceChar(pt), 0, 0);
                if (cnt > 1) {
                    this.ctx.font = `${countSize}px sans-serif`;
                    this.ctx.textAlign = 'left';
                    this.ctx.textBaseline = 'alphabetic';
                    const offsetX = Math.floor(slotW * 0.28);
                    const offsetY = Math.floor(regionH * 0.35);
                    this.ctx.fillText(String(cnt), offsetX, offsetY);
                }
                this.ctx.restore();
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

        if (this.lastMove && !this.lastMove.from) {
            const mover = this.boardState.getTurn() === Color.BLACK ? Color.WHITE : Color.BLACK;
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
            const pt = this.lastMove.piece;
            const idx = order.indexOf(pt);
            if (idx >= 0) {
                const centerX =
                    mover === Color.WHITE
                        ? regionXGote + Math.floor(idx * slotW + slotW / 2)
                        : regionXSente + Math.floor(idx * slotW + slotW / 2);
                const w = Math.floor(slotW * 0.9);
                const h = Math.floor(regionH * 0.9);
                this.ctx.fillStyle = this.theme.lastMoveColor;
                this.ctx.fillRect(centerX - Math.floor(w / 2), centerY - Math.floor(h / 2), w, h);
            }
        }
    }

    private getPieceChar(type: PieceType): string {
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

    private toKanjiNumber(value: number): string {
        const kanjiNumbers = ['', '一', '二', '三', '四', '五', '六', '七', '八', '九'];
        return kanjiNumbers[value] ?? '';
    }

    dispose(): void {
        window.removeEventListener('resize', this.resizeHandler);
        if (this.canvas.parentNode === this.container) {
            this.container.removeChild(this.canvas);
        }
    }
}

class ShogiBoardAdapter implements LiveBoardAdapter {
    [key: string]: unknown;
    private renderer: ShogiBoardRenderer | null = null;
    currentPly: number | undefined;

    mount(target: Element): void {
        const element = target instanceof HTMLElement ? target : null;
        if (!element) {
            throw new Error('ShogiBoardAdapter.mount expects an HTMLElement');
        }
        this.renderer?.dispose();
        this.renderer = new ShogiBoardRenderer(element);
    }

    resize(): void {
        this.renderer?.resize();
    }

    setPositionFromSFEN(sfen: string): void {
        this.renderer?.setPositionFromSFEN(sfen);
    }

    setMoves(moves: readonly string[]): void {
        this.renderer?.setMoves(moves);
    }

    goTo(ply: number): void {
        this.renderer?.goTo(ply);
        this.currentPly = ply;
    }

    flip(enabled: boolean): void {
        this.renderer?.flip(enabled);
    }

    highlightSquares(indices: Iterable<number>): void {
        this.renderer?.highlightSquares(indices);
    }

    setTheme(theme: Partial<BoardTheme>): void {
        this.renderer?.setTheme(theme);
    }

    destroy(): void {
        this.dispose();
    }

    dispose(): void {
        this.renderer?.dispose();
        this.renderer = null;
        this.currentPly = undefined;
    }
}

interface ShogiBoardWindow extends Window {
    ShogiBoardAdapter?: new () => ShogiBoardAdapter;
    ShogiBoardRenderer?: typeof ShogiBoardRenderer;
}

export function installShogiBoardGlobals(owner: ShogiBoardWindow = window as ShogiBoardWindow) {
    owner.ShogiBoardAdapter = ShogiBoardAdapter;
    owner.ShogiBoardRenderer = ShogiBoardRenderer;
    return { ShogiBoardAdapter };
}

export { ShogiBoardAdapter };
