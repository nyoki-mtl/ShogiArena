export interface ProgressDisplayInput {
    completed: number | null | undefined;
    total: number | null | undefined;
    indicator: string | undefined;
    isFinal: boolean;
    cancelled?: number | null | undefined;
}

export interface ProgressDisplayModel {
    completedLabel: string;
    totalLabel: string;
    widthPercent: number;
    displayText: string;
    isPaused: boolean;
    isDraining: boolean;
    isFinished: boolean;
    textTone: 'light' | 'dark';
    cancelledLabel: string | null;
}

type ProgressState = 'normal' | 'paused' | 'draining' | 'finished';

export interface HeaderProgressPayload {
    completed: number | null | undefined;
    total: number | null | undefined;
    cancelled?: number | null | undefined;
    isFinal?: boolean | null | undefined;
    state?: ProgressState | null | undefined;
    unitLabel?: string | null | undefined;
}

export function deriveProgressDisplay({
    completed,
    total,
    indicator,
    isFinal,
    cancelled,
}: ProgressDisplayInput): ProgressDisplayModel {
    const safeCompleted = Number.isFinite(completed) && completed !== null ? Number(completed) : 0;
    const safeTotal = Number.isFinite(total) && total !== null ? Number(total) : 0;
    const pct = safeTotal > 0 ? Math.min(100, Math.round((safeCompleted / safeTotal) * 100)) : 0;
    const state = indicator === 'paused' || indicator === 'draining' || indicator === 'finished' ? indicator : 'normal';
    const finished = isFinal || state === 'finished';
    const safeCancelled = Number.isFinite(cancelled) && cancelled !== null ? Math.max(0, Number(cancelled)) : 0;
    const cancelledLabel = safeCancelled > 0 ? `${safeCancelled} cancelled` : null;

    let textTone: 'light' | 'dark' = 'dark';
    if (safeTotal > 0 && safeCompleted > 0) {
        const threshold = state === 'draining' ? 65 : 45;
        if (pct >= threshold && state !== 'paused') {
            textTone = 'light';
        }
    }

    const annotations: string[] = [];
    if (finished) annotations.push('completed');
    else if (state === 'paused') annotations.push('paused');
    else if (state === 'draining') annotations.push('draining');
    if (cancelledLabel) annotations.push(cancelledLabel);

    const displayTextBase = `${safeCompleted} / ${safeTotal}`;
    const displayText = annotations.length ? `${displayTextBase} (${annotations.join(', ')})` : displayTextBase;

    return {
        completedLabel: String(safeCompleted),
        totalLabel: String(safeTotal),
        widthPercent: pct,
        displayText,
        isPaused: state === 'paused',
        isDraining: state === 'draining',
        isFinished: finished,
        textTone,
        cancelledLabel,
    };
}

export function renderHeaderProgress(doc: Document, payload: HeaderProgressPayload): void {
    const progressBar = doc.getElementById('progressBar');
    const progressFill = doc.getElementById('progressFill');
    const progressText = doc.getElementById('progressText');
    if (!progressBar && !progressFill && !progressText) return;

    const completed = payload.completed ?? 0;
    const total = payload.total ?? 0;
    const derivedFinal =
        typeof payload.isFinal === 'boolean'
            ? payload.isFinal
            : Number.isFinite(completed) && Number.isFinite(total) && total > 0 && completed >= total;
    const indicator = payload.state ?? 'normal';
    const model = deriveProgressDisplay({
        completed,
        total,
        indicator: indicator ?? undefined,
        isFinal: derivedFinal,
        cancelled: payload.cancelled ?? undefined,
    });
    const unitLabel = (payload.unitLabel ?? '').trim();
    const displayText =
        unitLabel && unitLabel !== 'games' ? `${model.displayText} ${unitLabel}`.trim() : model.displayText;
    const nextState = model.isPaused
        ? 'paused'
        : model.isDraining
          ? 'draining'
          : model.isFinished
            ? 'finished'
            : 'normal';

    if (progressFill) {
        progressFill.style.width = `${model.widthPercent}%`;
        progressFill.dataset.progressTone = model.textTone;
    }
    if (progressText) {
        progressText.dataset.progressState = nextState;
        progressText.dataset.progressTone = model.textTone;
        progressText.textContent = displayText;
    }
    if (progressBar) {
        progressBar.classList.toggle('paused', model.isPaused);
        progressBar.classList.toggle('draining', model.isDraining);
        progressBar.classList.toggle('finished', model.isFinished);
    }

    const completedNode = doc.getElementById('completedGames');
    if (completedNode) completedNode.textContent = model.completedLabel;
    const totalNode = doc.getElementById('plannedGames');
    if (totalNode) totalNode.textContent = model.totalLabel;
}
