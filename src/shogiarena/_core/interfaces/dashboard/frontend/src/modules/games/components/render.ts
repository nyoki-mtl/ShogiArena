import { canOpenLiveView } from '@/modules/games/utils';
import type { DashboardGamesRender, GamesSortDirection, GamesSortKey, NormalizedGameRow } from '@/modules/games/types';
import type { DashboardNavigationApi } from '@/types/globals';
import type { TournamentDashboardAPI } from '@/modules/tournament/types';
import { buildGameRowViewModel, sortRows as sortViewRows, type GameRowViewModel } from '@/modules/games/viewmodel';

interface GamesRenderWindow extends Window {
    DashboardGamesRender?: DashboardGamesRender;
    DashboardTournament?: TournamentDashboardAPI;
}

const defaultWindow = window as GamesRenderWindow;

type SideKey = 'black' | 'white';

function formatOpeningLabel(spec: unknown): string {
    const sfen = typeof spec === 'string' && spec ? spec : 'startpos';
    const tournament = defaultWindow.DashboardTournament;
    if (tournament && typeof tournament.formatOpeningLabel === 'function') {
        return tournament.formatOpeningLabel(sfen) as string;
    }
    return sfen;
}

function createEngineLink(engineName: unknown): HTMLElement {
    const label = typeof engineName === 'string' ? engineName.trim() : '';
    if (!label) {
        throw new Error('createEngineLink: engineName is required and must be a non-empty string');
    }
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'games-engine-link';
    btn.dataset.engineName = label;
    btn.textContent = label;
    btn.setAttribute('aria-label', `Show matchups for ${label}`);
    return btn;
}

function sanitizeInstanceLabel(value: unknown): string | null {
    if (typeof value !== 'string') return null;
    const trimmed = value.trim();
    return trimmed || null;
}

function createInstancePill(instanceName: unknown, kind: unknown, gameId: unknown, side: SideKey): HTMLElement | null {
    const nameLabel = sanitizeInstanceLabel(instanceName);
    const kindLabel = sanitizeInstanceLabel(kind);
    if (!nameLabel && !kindLabel) return null;
    const pill = document.createElement('span');
    pill.className = 'games-instance-pill';
    pill.setAttribute('role', 'button');
    pill.setAttribute('tabindex', '0');
    if (nameLabel) {
        pill.dataset.instanceId = nameLabel;
    }
    if (typeof gameId === 'string') {
        pill.dataset.gameId = gameId;
    }
    pill.dataset.side = side;
    pill.textContent = nameLabel ?? kindLabel ?? '';
    return pill;
}

function createOpeningBadge(openingSfen: string): HTMLButtonElement {
    const safeSfen = openingSfen || 'startpos';
    const openingBadge = document.createElement('button');
    openingBadge.type = 'button';
    openingBadge.className = 'games-opening-badge';
    openingBadge.dataset.sfen = safeSfen;
    openingBadge.title = safeSfen;
    openingBadge.textContent = '📍';
    openingBadge.setAttribute('aria-label', `Show opening: ${formatOpeningLabel(safeSfen)}`);
    const tournament = window.DashboardTournament;
    if (tournament && typeof tournament.focusOpeningBySfen === 'function') {
        openingBadge.addEventListener('click', () => {
            const navigation = window.DashboardNavigation as DashboardNavigationApi | undefined;
            navigation?.openTab('openings');
            tournament.focusOpeningBySfen?.(safeSfen);
        });
    } else {
        openingBadge.disabled = true;
    }
    return openingBadge;
}

function buildGameCell(normalized: NormalizedGameRow): HTMLTableCellElement {
    const gameCell = document.createElement('td');
    gameCell.className = 'games-game-cell table-group-id';
    const wrapper = document.createElement('div');
    wrapper.className = 'games-game-wrapper';
    gameCell.appendChild(wrapper);

    const openingSfen =
        typeof normalized.initial_sfen === 'string' && normalized.initial_sfen ? normalized.initial_sfen : 'startpos';
    const liveReady = canOpenLiveView(normalized.status);
    const displayId = normalized.game_id ?? '-';
    if (liveReady && normalized.game_id) {
        const gameButton = document.createElement('button');
        gameButton.type = 'button';
        gameButton.className = 'games-game-link';
        gameButton.dataset.gameId = normalized.game_id;
        gameButton.dataset.status = normalized.status ?? '';
        gameButton.textContent = displayId;
        gameButton.setAttribute('aria-label', `Open Live View for game ${displayId}`);
        wrapper.appendChild(gameButton);
    } else {
        const label = document.createElement('span');
        label.className = 'games-game-label';
        label.textContent = displayId;
        wrapper.appendChild(label);
    }

    const badge = createOpeningBadge(openingSfen);
    wrapper.appendChild(badge);

    return gameCell;
}

function buildSideCell(normalized: NormalizedGameRow, side: SideKey): HTMLTableCellElement {
    const sideCell = document.createElement('td');
    sideCell.className = 'games-side table-group-engine games-side-with-instance';
    const wrapper = document.createElement('div');
    wrapper.className = 'games-side-wrapper';
    sideCell.appendChild(wrapper);

    const engineLink = createEngineLink(normalized[side]);
    wrapper.appendChild(engineLink);

    const pill = createInstancePill(
        normalized[`${side}_instance`],
        normalized[`${side}_instance_kind`],
        normalized.game_id,
        side,
    );
    if (pill) {
        wrapper.appendChild(pill);
    } else {
        const placeholder = document.createElement('span');
        placeholder.className = 'games-instance-placeholder';
        placeholder.textContent = '—';
        wrapper.appendChild(placeholder);
    }

    return sideCell;
}

function buildResultCell(view: GameRowViewModel): {
    resultCell: HTMLTableCellElement;
    outcome: GameRowViewModel['outcome'];
} {
    const resultCell = document.createElement('td');
    resultCell.className = 'games-result-cell';

    const outcome = view.outcome;
    const { result } = view;
    const badge = document.createElement('span');
    badge.className = 'games-result-badge';
    badge.dataset.resultVariant = result.variant;
    badge.dataset.outcome = outcome.outcome;

    if (result.shape === 'disc') {
        badge.classList.add('games-result-badge-disc');
        badge.dataset.shape = 'disc';
        if (result.symbol) {
            delete badge.dataset.symbolEmpty;
            setBadgeLabel(badge, result.symbol);
        } else {
            badge.dataset.symbolEmpty = 'true';
            setBadgeLabel(badge, '');
        }
    } else if (result.shape === 'triangle') {
        badge.classList.add('games-result-badge-triangle');
        badge.dataset.shape = 'triangle';
        setBadgeLabel(badge, result.display);
        delete badge.dataset.symbolEmpty;
    } else {
        setBadgeLabel(badge, result.display);
        delete badge.dataset.symbolEmpty;
        delete badge.dataset.shape;
    }
    badge.title = result.tooltip;
    badge.setAttribute('aria-hidden', 'true');
    resultCell.appendChild(badge);

    const srText = document.createElement('span');
    srText.className = 'games-visually-hidden';
    srText.textContent = result.tooltip;
    resultCell.appendChild(srText);

    resultCell.title = result.tooltip;

    resultCell.classList.add(`result-outcome-${outcome.outcome}`);
    resultCell.dataset.outcome = outcome.outcome;
    resultCell.dataset.variant = result.variant;

    return { resultCell, outcome };
}

function buildMovesCell(view: GameRowViewModel): HTMLTableCellElement {
    const movesCell = document.createElement('td');
    movesCell.className = 'games-moves-cell numeric';
    movesCell.textContent = view.movesLabel;
    return movesCell;
}

function buildStartedCell(view: GameRowViewModel): HTMLTableCellElement {
    const startedCell = document.createElement('td');
    startedCell.className = 'games-started-cell';
    startedCell.textContent = view.started.label;
    if (view.started.isEmpty) {
        startedCell.classList.add('games-time-empty');
    }
    if (view.started.title) {
        startedCell.dataset.timestampIso = view.started.title;
        startedCell.title = view.started.title;
    } else {
        delete startedCell.dataset.timestampIso;
        startedCell.removeAttribute('title');
    }
    return startedCell;
}

function buildEndedCell(view: GameRowViewModel): HTMLTableCellElement {
    const endedCell = document.createElement('td');
    endedCell.className = 'games-ended-cell';
    endedCell.textContent = view.ended.label;
    if (view.ended.isEmpty) {
        endedCell.classList.add('games-time-empty');
    }
    if (view.ended.title) {
        endedCell.dataset.timestampIso = view.ended.title;
        endedCell.title = view.ended.title;
    } else {
        delete endedCell.dataset.timestampIso;
        endedCell.removeAttribute('title');
    }
    return endedCell;
}

function buildSettingsCell(normalized: NormalizedGameRow): HTMLTableCellElement {
    const settingsCell = document.createElement('td');
    settingsCell.className = 'games-settings-cell';
    const statusLower = String(normalized.status ?? '').toLowerCase();

    if (statusLower === 'pending' || statusLower === 'cancelled') {
        const settingsWrapper = document.createElement('div');
        settingsWrapper.className = 'games-settings';

        const trigger = document.createElement('button');
        trigger.type = 'button';
        trigger.className = 'games-settings-trigger';
        trigger.dataset.gameId = normalized.game_id ?? '';
        trigger.dataset.black = normalized.black ?? '';
        trigger.dataset.white = normalized.white ?? '';
        trigger.dataset.opening = normalized.initial_sfen ?? 'startpos';
        trigger.dataset.assignedOverride = (normalized.assigned_override as string | null) ?? '';
        trigger.dataset.assignedMode = normalized.assigned_mode;
        trigger.dataset.assignedOverrideBlack = normalized.assigned_override_black ?? '';
        trigger.dataset.assignedOverrideWhite = normalized.assigned_override_white ?? '';
        trigger.dataset.requireInstall = normalized.should_require_install ? 'true' : 'false';
        trigger.dataset.resolvedBlackInstance = normalized.resolved_instance_black ?? '';
        trigger.dataset.resolvedWhiteInstance = normalized.resolved_instance_white ?? '';
        trigger.dataset.displayOrder =
            typeof normalized.display_order === 'number' ? String(normalized.display_order) : '';
        trigger.dataset.status = statusLower;
        trigger.setAttribute('aria-label', `Game actions for ${normalized.game_id ?? 'game'}`);

        const icon = document.createElement('span');
        icon.className = 'games-settings-trigger-icon';
        icon.textContent = '⚙️';
        trigger.appendChild(icon);
        settingsWrapper.appendChild(trigger);

        settingsCell.appendChild(settingsWrapper);
    } else if (
        normalized.assigned_override ||
        normalized.assigned_override_black ||
        normalized.assigned_override_white ||
        normalized.assigned_mode === 'per_color'
    ) {
        const placeholder = document.createElement('span');
        placeholder.className = 'games-settings-placeholder';
        const formatInstance = (value: string | null): string => {
            if (!value || value.trim().length === 0) return 'auto';
            return value;
        };
        let label: string;
        if (normalized.assigned_mode === 'per_color') {
            const blackLabel = formatInstance(
                normalized.assigned_override_black ?? normalized.resolved_instance_black ?? null,
            );
            const whiteLabel = formatInstance(
                normalized.assigned_override_white ?? normalized.resolved_instance_white ?? null,
            );
            label = `B:${blackLabel} / W:${whiteLabel}`;
        } else if (normalized.assigned_mode === 'shared') {
            const shared =
                normalized.assigned_override_black ??
                normalized.assigned_override_white ??
                normalized.assigned_override ??
                normalized.resolved_instance_black ??
                normalized.resolved_instance_white;
            label = formatInstance(shared ?? null);
        } else {
            const fallback = normalized.assigned_override ? (normalized.assigned_override as string) : null;
            label = formatInstance(fallback);
        }
        if (normalized.should_require_install) {
            label = `${label} (install)`;
        }
        placeholder.textContent = label;
        placeholder.title = 'Preferred instance';
        settingsCell.appendChild(placeholder);
    }

    return settingsCell;
}

function buildRow(normalized: NormalizedGameRow): HTMLTableRowElement {
    const view = buildGameRowViewModel(normalized);
    const tr = document.createElement('tr');
    tr.className = 'games-row standings-row';
    tr.dataset.gameId = normalized.game_id ?? '';
    if (typeof normalized.order_index === 'number') {
        tr.dataset.orderIndex = String(normalized.order_index);
    } else {
        delete tr.dataset.orderIndex;
    }

    if (view.statusLower) {
        tr.dataset.status = view.statusLower;
        tr.classList.add(`games-row-status-${view.statusLower}`);
    }

    const orderCell = document.createElement('td');
    orderCell.className = 'games-order-cell table-group-meta';

    const statusIndicator = document.createElement('span');
    statusIndicator.className = 'games-status-indicator';
    statusIndicator.setAttribute('aria-hidden', 'true');
    orderCell.appendChild(statusIndicator);

    const orderLabel = document.createElement('span');
    orderLabel.className = 'games-order-label';
    if (typeof normalized.display_order === 'number') {
        orderLabel.textContent = String(normalized.display_order);
    } else {
        orderLabel.textContent = '-';
    }
    orderCell.appendChild(orderLabel);
    tr.appendChild(orderCell);

    const gameCell = buildGameCell(normalized);
    tr.appendChild(gameCell);

    const blackCell = buildSideCell(normalized, 'black');
    tr.appendChild(blackCell);

    const whiteCell = buildSideCell(normalized, 'white');
    tr.appendChild(whiteCell);

    const { resultCell, outcome } = buildResultCell(view);
    if (outcome.blackClass) {
        blackCell.classList.add(outcome.blackClass);
    }
    if (outcome.whiteClass) {
        whiteCell.classList.add(outcome.whiteClass);
    }
    tr.dataset.outcome = outcome.outcome;
    tr.classList.add(`games-row-outcome-${outcome.outcome}`);
    if (!['pending', 'running'].includes(outcome.outcome)) {
        tr.classList.add('games-row-final');
    }
    tr.appendChild(resultCell);

    const indicatorVariant = view.indicatorVariant;
    const indicatorLabel = view.indicatorLabel;

    if (indicatorVariant) {
        statusIndicator.dataset.statusIndicator = indicatorVariant;
        statusIndicator.style.removeProperty('background-color');
        statusIndicator.style.removeProperty('opacity');
    } else {
        delete statusIndicator.dataset.statusIndicator;
        statusIndicator.style.removeProperty('background-color');
        statusIndicator.style.removeProperty('opacity');
    }

    if (indicatorLabel) {
        orderCell.title = indicatorLabel;
        orderCell.setAttribute('aria-label', `${indicatorLabel} status`);
        orderCell.dataset.statusLabel = indicatorLabel;
    } else {
        orderCell.removeAttribute('title');
        orderCell.removeAttribute('aria-label');
        delete orderCell.dataset.statusLabel;
    }

    tr.appendChild(buildMovesCell(view));
    tr.appendChild(buildStartedCell(view));
    tr.appendChild(buildEndedCell(view));
    tr.appendChild(buildSettingsCell(normalized));

    return tr;
}
const sortRows = sortViewRows;

function updateSortHeaders(sortKey: GamesSortKey, sortDirection: GamesSortDirection): void {
    document.querySelectorAll<HTMLButtonElement>('.games-sort').forEach((btn) => {
        const key = btn.dataset.sort as GamesSortKey | undefined;
        const th = btn.closest('th');
        if (!key || !th) return;
        const isActive = key === sortKey;
        btn.classList.toggle('active', isActive);
        if (isActive) {
            btn.dataset.direction = sortDirection;
            th.setAttribute('aria-sort', sortDirection === 'desc' ? 'descending' : 'ascending');
        } else {
            btn.dataset.direction = '';
            th.setAttribute('aria-sort', 'none');
        }
    });
}

const exported: DashboardGamesRender = Object.freeze({
    buildRow,
    sortRows,
    updateSortHeaders,
});

let installedGamesRender: DashboardGamesRender | null = null;

export function installGamesRender(owner: GamesRenderWindow = defaultWindow): DashboardGamesRender {
    if (installedGamesRender) {
        owner.DashboardGamesRender = installedGamesRender;
        return installedGamesRender;
    }

    installedGamesRender = exported;
    owner.DashboardGamesRender = exported;
    return exported;
}

function setBadgeLabel(badge: HTMLSpanElement, text: string): void {
    badge.textContent = '';
    if (!text) return;
    const label = document.createElement('span');
    label.className = 'games-result-badge-label';
    label.textContent = text;
    badge.appendChild(label);
}
