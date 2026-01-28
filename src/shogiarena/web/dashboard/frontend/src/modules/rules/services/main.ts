import type { DashboardNavigationApi, DashboardTabId } from '@/types/globals';
import type { DashboardRulesApi } from '@/modules/rules/types';
import type { TournamentDashboardAPI, NormalizedTournamentSummary } from '@/modules/tournament/types';
import type { DashboardSpsaPublicApi } from '@/modules/spsa/types';
import type { JsonObject } from '@/types/shared';
import { reportDashboardRecoverableFailure } from '@/modules/shared/utils/errors';

import { getRulesSummary } from '../utils/helpers';
import { createMessage, type RenderOptions, renderRuleCards } from '../components/render';
import { createInitialState } from '../state';
import type { RulesListEntryAction, RulesWindow, WarnSoftFailure } from '../types/internal';

const defaultWindow = window as RulesWindow;

let installedRulesApi: DashboardRulesApi | null = null;

function createRulesModule(owner: RulesWindow): DashboardRulesApi {
    const core = owner.DashboardCore;
    if (!core) {
        throw new Error('DashboardCore must be available before the rules module initialises');
    }

    const events = core.events;
    const warnSoftFailure: WarnSoftFailure = core.warnSoftFailure;
    const reportRecoverable = (scope: string, error: unknown, userMessage?: string): void => {
        reportDashboardRecoverableFailure(error, {
            scope: scope.startsWith('Rules.') ? scope : `Rules.${scope}`,
            userMessage,
        });
    };
    const doc = owner.document;

    const state = createInitialState();

    if (state.lastSummary == null) {
        const initial = owner.ARENA_SUMMARY;
        if (initial && typeof initial === 'object') {
            state.lastSummary = initial as JsonObject;
        }
    }

    function findContentRoot(): HTMLElement | null {
        const tab = doc.getElementById('rulesTab');
        if (!tab) return null;
        return tab.querySelector<HTMLElement>('#rulesContent');
    }

    function findContainer(): HTMLElement | null {
        const content = findContentRoot();
        if (!content) return null;
        return content.querySelector<HTMLElement>('.rules-grid');
    }

    function bindInteractions(container: HTMLElement): void {
        if (state.interactionBound) return;
        const handler = (event: MouseEvent) => {
            const interactiveRoot = (event.target as HTMLElement | null)?.closest<HTMLElement>('[data-rules-action]');
            if (!interactiveRoot || !container.contains(interactiveRoot)) {
                return;
            }
            event.preventDefault();
            const actionType = interactiveRoot.dataset.rulesAction as RulesListEntryAction['type'] | undefined;
            const navigation = owner.DashboardNavigation as DashboardNavigationApi | undefined;

            if (actionType === 'focus-engine') {
                const engine = interactiveRoot.dataset.rulesEngine;
                if (!engine) return;
                if (navigation?.focusEngine) {
                    navigation.focusEngine(engine, { tab: 'tournament', scroll: true });
                } else {
                    owner.DashboardTournament?.focusEngineMatchups?.(engine, { scroll: true });
                }
                return;
            }

            if (actionType === 'open-tab') {
                const tab = interactiveRoot.dataset.rulesTab as DashboardTabId | undefined;
                if (!tab) return;
                const selector = interactiveRoot.dataset.rulesSelector;
                if (navigation?.openTab) {
                    navigation.openTab(tab, selector ? { scrollIntoViewSelector: selector } : undefined);
                }
            }
        };
        container.addEventListener('click', handler);
        state.interactionBound = true;
        state.gridClickHandler = handler;
    }

    function requireContainer(): HTMLElement | null {
        const content = findContentRoot();
        if (!content) return null;

        const placeholders = content.querySelectorAll<HTMLElement>('.loading-state, .loading');
        placeholders.forEach((el) => {
            el.remove();
        });

        let grid = content.querySelector<HTMLElement>('.rules-grid');
        if (!grid) {
            grid = doc.createElement('div');
            grid.className = 'rules-grid';
            content.appendChild(grid);
        }
        bindInteractions(grid);
        return grid;
    }

    function normalizeForSignature(value: unknown): unknown {
        if (value === null || value === undefined) {
            return value;
        }
        if (typeof value !== 'object') {
            return value;
        }
        if (Array.isArray(value)) {
            return value.map((item) => normalizeForSignature(item));
        }
        const record = value as Record<string, unknown>;
        const sortedKeys = Object.keys(record).sort((a, b) => a.localeCompare(b));
        const normalized: Record<string, unknown> = {};
        for (const key of sortedKeys) {
            normalized[key] = normalizeForSignature(record[key]);
        }
        return normalized;
    }

    /** Keys within the `sprt` object that represent static configuration (not live status). */
    const SPRT_CONFIG_KEYS = [
        'elo0',
        'elo1',
        'alpha',
        'beta',
        'min_games',
        'max_games',
        'minGames',
        'maxGames',
        'engines',
        'num_parallel',
    ] as const;

    function extractSprtConfig(sprt: unknown): Record<string, unknown> | undefined {
        if (!sprt || typeof sprt !== 'object') return undefined;
        const record = sprt as Record<string, unknown>;
        const config: Record<string, unknown> = {};
        let found = false;
        for (const key of SPRT_CONFIG_KEYS) {
            if (key in record) {
                config[key] = record[key];
                found = true;
            }
        }
        return found ? config : undefined;
    }

    function buildRulesSignature(summarySource: JsonObject | NormalizedTournamentSummary): string {
        const keys = [
            'rules',
            'defaultTimeControl',
            'engineTimeControls',
            'spsaConfig',
            'adjudication',
            'initialPositions',
            'repetition',
        ] as const;
        const parts: string[] = [];
        for (const key of keys) {
            const value = (summarySource as Record<string, unknown>)[key];
            if (value !== undefined) {
                parts.push(`${key}:${JSON.stringify(normalizeForSignature(value))}`);
            }
        }
        // SPRT: only include config fields, not live status (llr, wins, draws, etc.)
        const sprtConfig = extractSprtConfig((summarySource as Record<string, unknown>).sprt);
        if (sprtConfig) {
            parts.push(`sprt:${JSON.stringify(normalizeForSignature(sprtConfig))}`);
        }
        return parts.join('|');
    }

    function renderCards(): void {
        const container = requireContainer();
        if (!container) return;

        const tournament: TournamentDashboardAPI | undefined = owner.DashboardTournament;
        const spsa: DashboardSpsaPublicApi | undefined = owner.DashboardSpsa;

        const rulesKeys = [
            'rules',
            'defaultTimeControl',
            'engineTimeControls',
            'sprt',
            'spsaConfig',
            'adjudication',
            'initialPositions',
            'repetition',
        ] as const;
        const hasRulesData = (obj: unknown): boolean => {
            if (!obj || typeof obj !== 'object') return false;
            const record = obj as Record<string, unknown>;
            return rulesKeys.some((key) => record[key] !== undefined);
        };

        let summarySource: JsonObject | NormalizedTournamentSummary | null = null;
        if (tournament && typeof tournament.getNormalizedSummary === 'function') {
            const normalized = tournament.getNormalizedSummary();
            if (normalized && hasRulesData(normalized)) {
                summarySource = normalized;
            }
        }

        if (!summarySource) {
            const latest = state.lastSummary;
            if (latest && hasRulesData(latest)) {
                summarySource = latest;
            }
        }

        if (!summarySource) {
            const arenaSummary = owner.ARENA_SUMMARY;
            if (
                arenaSummary &&
                typeof arenaSummary === 'object' &&
                !Array.isArray(arenaSummary) &&
                hasRulesData(arenaSummary)
            ) {
                summarySource = arenaSummary as JsonObject;
            }
        }

        if (!summarySource) {
            if (state.lastRenderSignature === null) {
                throw new Error('Rules summary is unavailable: no source contains rules-related data');
            }
            return;
        }

        const signature = buildRulesSignature(summarySource);
        if (signature === state.lastRenderSignature) {
            return;
        }
        state.lastRenderSignature = signature;

        const summary = getRulesSummary(summarySource);

        const parseTimeControlSpec =
            tournament && typeof tournament.parseTimeControlSpec === 'function'
                ? tournament.parseTimeControlSpec.bind(tournament)
                : spsa && typeof spsa.parseTimeControlSpec === 'function'
                  ? spsa.parseTimeControlSpec
                  : undefined;

        const options: RenderOptions = {
            parseTimeControlSpec,
            warnSoftFailure,
        };

        const cards = renderRuleCards(doc, summary, options);

        if (!cards.length) {
            container.replaceChildren(createMessage(doc, 'No rules information available.'));
            return;
        }

        const fragment = doc.createDocumentFragment();
        for (const card of cards) {
            const article = doc.createElement('article');
            article.className = 'rules-card';

            if (card.title) {
                const heading = doc.createElement('h3');
                heading.textContent = card.title;
                article.appendChild(heading);
            }

            article.appendChild(card.content);
            fragment.appendChild(article);
        }

        container.replaceChildren(fragment);
    }

    function handleSummaryEvent(event?: unknown): void {
        if (event && typeof event === 'object' && event !== null && 'summary' in event) {
            const summaryCandidate = (event as { summary?: unknown }).summary;
            if (summaryCandidate && typeof summaryCandidate === 'object' && !Array.isArray(summaryCandidate)) {
                state.lastSummary = summaryCandidate as JsonObject;
            }
        }
        if (!state.active) {
            return;
        }
        try {
            renderCards();
        } catch (error) {
            reportRecoverable('Render.Summary', error, 'Failed to render rules summary');
        }
    }

    function init(): void {
        if (state.initialized) return;
        state.initialized = true;

        if (events && typeof events.on === 'function') {
            state.unsubscribe = events.on('summary:update', handleSummaryEvent);
        }
    }

    const api: DashboardRulesApi = {
        refresh: () => {
            handleSummaryEvent();
        },
        setActive: (active: boolean): void => {
            state.active = Boolean(active);
            if (!state.initialized) {
                init();
            }
            if (state.active) {
                handleSummaryEvent();
            }
        },
        teardown: (): void => {
            state.active = false;
            state.lastRenderSignature = null;
            if (state.unsubscribe) {
                try {
                    state.unsubscribe();
                } catch (error) {
                    reportRecoverable('Subscription.Teardown', error, 'Failed to unsubscribe rules summary listener');
                }
                state.unsubscribe = null;
            }
            if (state.gridClickHandler) {
                const container = findContainer();
                container?.removeEventListener('click', state.gridClickHandler);
                state.gridClickHandler = null;
                state.interactionBound = false;
            }
        },
    };

    // Render immediately on load if the tab is already visible (e.g. standalone page)
    if (doc.readyState === 'loading') {
        doc.addEventListener(
            'DOMContentLoaded',
            () => {
                if (!state.initialized) init();
                state.active = true;
                handleSummaryEvent();
            },
            { once: true },
        );
    } else {
        init();
        state.active = true;
        handleSummaryEvent();
    }

    return api;
}

export function installRulesModule(owner: RulesWindow = defaultWindow): DashboardRulesApi {
    if (installedRulesApi) {
        owner.DashboardRules = installedRulesApi;
        return installedRulesApi;
    }

    const api = createRulesModule(owner);
    installedRulesApi = api;
    owner.DashboardRules = api;
    return api;
}
