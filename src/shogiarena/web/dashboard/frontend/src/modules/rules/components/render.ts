import type {
    RuleCard,
    RulesListEntry,
    RulesSummary,
    SpsaAlgorithmConfig,
    SpsaLtcPassCriteria,
    SpsaLtcRulesConfig,
    WarnSoftFailure,
} from '@/modules/rules/types/internal';
import type { ParseTimeControlSpec } from '@/modules/rules/utils/helpers';
import {
    formatPercentage,
    formatTimeControl,
    getRepetitionValue,
    normalizeAdjudication,
    normalizeInitialPositions,
} from '@/modules/rules/utils/helpers';
import type { JsonObject } from '@/types/shared';

function createActionButton(doc: Document, entry: RulesListEntry): HTMLButtonElement {
    const button = doc.createElement('button');
    button.type = 'button';
    button.className = 'rules-action-link';
    button.textContent = entry.value ?? '-';
    button.dataset.rulesAction = entry.action?.type ?? '';
    if (entry.action?.type === 'focus-engine') {
        button.dataset.rulesEngine = entry.action.engine;
    }
    if (entry.action?.type === 'open-tab') {
        button.dataset.rulesTab = entry.action.tab;
        if (entry.action.selector) {
            button.dataset.rulesSelector = entry.action.selector;
        }
    }
    return button;
}

function createDefinitionList(doc: Document, pairs: ReadonlyArray<RulesListEntry>): HTMLElement {
    if (!pairs || !pairs.length) {
        const empty = doc.createElement('p');
        empty.className = 'subtle';
        empty.textContent = 'No data available.';
        return empty;
    }

    const list = doc.createElement('dl');
    list.className = 'rules-list';

    for (const entry of pairs) {
        const row = doc.createElement('div');
        row.className = 'rule-row';

        if (entry.label) {
            const dt = doc.createElement('dt');
            dt.textContent = entry.label;
            row.appendChild(dt);
        }

        const dd = doc.createElement('dd');
        if (entry.action) {
            dd.appendChild(createActionButton(doc, entry));
        } else {
            dd.textContent = entry.value ?? '-';
        }
        row.appendChild(dd);

        list.appendChild(row);
    }

    return list;
}

function createMessage(doc: Document, text: string, className = 'subtle'): HTMLElement {
    const paragraph = doc.createElement('p');
    paragraph.className = className;
    paragraph.textContent = text;
    return paragraph;
}

function createSection(doc: Document, title: string, body: Node): HTMLElement {
    const section = doc.createElement('section');
    section.className = 'rules-section';

    const heading = doc.createElement('h4');
    heading.className = 'rules-section__title';
    heading.textContent = title;
    section.appendChild(heading);

    const content = doc.createElement('div');
    content.className = 'rules-section__body';
    content.appendChild(body);
    section.appendChild(content);

    return section;
}

interface RenderContext {
    readonly doc: Document;
    readonly summary: RulesSummary;
    readonly options: RenderOptions;
}

function buildTimeControlSection({ doc, summary, options }: RenderContext): HTMLElement | null {
    const rules = summary.rules;
    const defaultSpec = summary.defaultTimeControl || (rules?.time_control_spec as string | undefined);
    const formatSpec = (spec: unknown) => formatTimeControl(spec, options.parseTimeControlSpec);
    const rows: RulesListEntry[] = [{ label: 'Default spec', value: formatSpec(defaultSpec) }];

    if (summary.engineTimeControls && typeof summary.engineTimeControls === 'object') {
        const overrides = Object.entries(summary.engineTimeControls)
            .filter(([, spec]) => spec && spec !== defaultSpec)
            .sort(([a], [b]) => a.localeCompare(b));
        if (overrides.length) {
            overrides.forEach(([engine, spec]) => {
                rows.push({
                    label: engine,
                    value: formatSpec(spec),
                    action: { type: 'focus-engine', engine },
                });
            });
        }
    }

    return createSection(doc, 'Time Control', createDefinitionList(doc, rows));
}

function buildAdjudicationSection({ doc, summary }: RenderContext): HTMLElement | null {
    const adj = normalizeAdjudication(summary);
    if (!adj) {
        return createSection(doc, 'Adjudication', createMessage(doc, 'Adjudication rules not provided.'));
    }

    const rows: RulesListEntry[] = [];
    const maxPlies = Number(adj.max_plies);
    if (adj.enable_max_plies && Number.isFinite(maxPlies) && maxPlies > 0) {
        rows.push({ label: 'Max plies', value: `${maxPlies}` });
    }
    const maxMoves = Number(adj.max_moves_to_draw ?? adj.max_moves);
    if (Number.isFinite(maxMoves) && maxMoves > 0) {
        rows.push({ label: 'Max moves to draw', value: `${maxMoves}` });
    }
    const resignThreshold = Number(adj.resign_threshold_cp);
    if (Number.isFinite(resignThreshold) && resignThreshold > 0) {
        const confirmed = Number(adj.resign_confirm_count);
        const parts = [`${resignThreshold} cp`];
        if (Number.isFinite(confirmed) && confirmed > 0) {
            parts.push(`${confirmed} consecutive moves`);
        }
        rows.push({ label: 'Resign', value: parts.join(' · ') });
    }
    if (adj.hysteresis && Number(adj.hysteresis) > 0) {
        rows.push({ label: 'Hysteresis', value: `${adj.hysteresis}` });
    }
    const optionNames = adj.engine_max_ply_option_names as string[] | undefined;
    if (Array.isArray(optionNames) && optionNames.length) {
        rows.push({
            label: 'Engine max ply options',
            value: optionNames.join(', '),
        });
    }

    if (rows.length === 0) {
        rows.push({ label: '', value: 'No adjudication limits enforced.' });
    }

    return createSection(doc, 'Adjudication', createDefinitionList(doc, rows));
}

function buildInitialPositionsSection({ doc, summary }: RenderContext): HTMLElement | null {
    const ip = normalizeInitialPositions(summary);
    if (!ip) {
        return createSection(doc, 'Initial Positions', createMessage(doc, 'Initial positions are not configured.'));
    }

    const rows: RulesListEntry[] = [];
    rows.push({ label: 'Type', value: ip.type || '-' });
    rows.push({ label: 'Source', value: ip.source ? String(ip.source) : '-' });
    rows.push({ label: 'Flip policy', value: ip.flip_policy || '-' });
    rows.push({ label: 'Randomize', value: ip.randomize ? 'Yes' : 'No' });
    if (ip.count != null) rows.push({ label: 'Positions', value: String(ip.count) });

    return createSection(doc, 'Initial Positions', createDefinitionList(doc, rows));
}

function renderSprt({ doc, summary }: RenderContext): RuleCard[] {
    const rules = summary.rules;
    const sprt = summary.sprt ?? (rules?.sprt as JsonObject | undefined);
    const sprtData = sprt && typeof sprt === 'object' ? (sprt as JsonObject) : null;
    if (!sprtData) {
        return [];
    }
    const rows: RulesListEntry[] = [];
    if (sprtData.elo0 != null) rows.push({ label: 'Elo H0', value: String(sprtData.elo0) });
    if (sprtData.elo1 != null) rows.push({ label: 'Elo H1', value: String(sprtData.elo1) });
    if (sprtData.alpha != null) rows.push({ label: 'Alpha', value: String(sprtData.alpha) });
    if (sprtData.beta != null) rows.push({ label: 'Beta', value: String(sprtData.beta) });
    const minGames = sprtData.min_games ?? sprtData.minGames;
    if (minGames != null) rows.push({ label: 'Min games', value: String(minGames) });
    const maxGames = sprtData.max_games ?? sprtData.maxGames;
    rows.push({ label: 'Max games', value: maxGames != null ? String(maxGames) : '\u221e' });
    if (Array.isArray(sprtData.engines) && sprtData.engines.length) {
        const engines = sprtData.engines.map((engine) => String(engine));
        const label = engines.join(' vs ');
        const firstEngine = engines[0];
        rows.push({
            label: 'Engines',
            value: label,
            action: firstEngine?.trim() ? { type: 'focus-engine', engine: firstEngine.trim() } : undefined,
        });
    }
    if (!rows.length) return [];

    const section = createSection(doc, 'Configuration', createDefinitionList(doc, rows));
    const wrapper = doc.createElement('div');
    wrapper.className = 'rules-card__sections';
    wrapper.appendChild(section);

    return [{ title: 'SPRT', content: wrapper }];
}

function buildRepetitionSection({ doc, summary }: RenderContext): HTMLElement | null {
    const value = getRepetitionValue(summary);
    const content =
        value == null
            ? createMessage(doc, 'No repetition rule configured.')
            : createDefinitionList(doc, [{ label: 'Occurrences to draw', value: `${value}` }]);
    return createSection(doc, 'Repetition', content);
}

function buildSpsaAlgorithmSection({ doc }: RenderContext, cfg: SpsaAlgorithmConfig): HTMLElement | null {
    const rows: RulesListEntry[] = [];
    if (cfg.num_updates != null) rows.push({ label: 'Num updates', value: String(cfg.num_updates) });
    if (cfg.mobility != null) rows.push({ label: 'Mobility', value: String(cfg.mobility) });
    if (cfg.scale != null && cfg.scale !== 1.0) rows.push({ label: 'Scale', value: String(cfg.scale) });
    if (cfg.update_mode) rows.push({ label: 'Update mode', value: cfg.update_mode });
    if (cfg.crn_enabled != null) rows.push({ label: 'CRN', value: cfg.crn_enabled ? 'Enabled' : 'Disabled' });
    if (cfg.int_rounding && cfg.int_rounding !== 'none') {
        rows.push({ label: 'Int rounding', value: cfg.int_rounding });
        if (cfg.int_ck_floor != null) rows.push({ label: 'Int c\u2096 floor', value: String(cfg.int_ck_floor) });
    }
    if (cfg.snap_float_to_step) rows.push({ label: 'Snap float to step', value: 'Yes' });
    if (cfg.early_stop != null) rows.push({ label: 'Early stop', value: 'Enabled' });
    if (cfg.update_batch_size != null) rows.push({ label: 'Batch size', value: String(cfg.update_batch_size) });
    if (cfg.inflight_factor != null) rows.push({ label: 'Inflight factor', value: String(cfg.inflight_factor) });
    if (!rows.length) return null;
    return createSection(doc, 'Algorithm', createDefinitionList(doc, rows));
}

function buildSpsaGainScheduleSection({ doc }: RenderContext, cfg: SpsaAlgorithmConfig): HTMLElement | null {
    const rows: RulesListEntry[] = [];
    if (cfg.a0 != null) rows.push({ label: 'a\u2080', value: String(cfg.a0) });
    rows.push({ label: 'A', value: cfg.A != null ? String(cfg.A) : 'auto' });
    if (cfg.alpha != null) rows.push({ label: '\u03b1', value: String(cfg.alpha) });
    if (cfg.gamma != null) rows.push({ label: '\u03b3', value: String(cfg.gamma) });
    if (!rows.length) return null;
    return createSection(doc, 'Gain Schedule', createDefinitionList(doc, rows));
}

function buildSpsaLtcSection({ doc }: RenderContext, ltc: SpsaLtcRulesConfig): HTMLElement | null {
    if (!ltc.enabled) return null;
    const rows: RulesListEntry[] = [];
    if (ltc.every_n_updates != null) rows.push({ label: 'Frequency', value: `Every ${ltc.every_n_updates} updates` });
    if (ltc.total_pairs != null) rows.push({ label: 'Game pairs', value: String(ltc.total_pairs) });
    const pc = ltc.pass_criteria as SpsaLtcPassCriteria | null | undefined;
    if (pc) {
        if (pc.min_winrate != null) rows.push({ label: 'Min winrate', value: formatPercentage(pc.min_winrate) });
        if (pc.max_elo_drop != null) rows.push({ label: 'Max Elo drop', value: String(pc.max_elo_drop) });
        if (pc.sprt) {
            const sprt = pc.sprt as Record<string, unknown>;
            const h0 = sprt.elo0 ?? sprt.h0;
            const h1 = sprt.elo1 ?? sprt.h1;
            if (h0 != null && h1 != null) {
                rows.push({ label: 'SPRT', value: `H0=${h0} / H1=${h1}` });
            }
        }
    }
    if (!rows.length) return null;
    return createSection(doc, 'LTC Regression', createDefinitionList(doc, rows));
}

function renderSpsa(context: RenderContext): RuleCard[] {
    const cfg = context.summary.spsaConfig;
    if (!cfg || typeof cfg !== 'object') return [];

    const sections: HTMLElement[] = [];

    const algo = buildSpsaAlgorithmSection(context, cfg);
    if (algo) sections.push(algo);

    const gain = buildSpsaGainScheduleSection(context, cfg);
    if (gain) sections.push(gain);

    const ltc = cfg.ltc_regression;
    if (ltc && typeof ltc === 'object') {
        const ltcSection = buildSpsaLtcSection(context, ltc);
        if (ltcSection) sections.push(ltcSection);
    }

    if (!sections.length) return [];

    const { doc } = context;
    const wrapper = doc.createElement('div');
    wrapper.className = 'rules-card__sections';
    for (const section of sections) {
        wrapper.appendChild(section);
    }

    return [{ title: 'SPSA Settings', content: wrapper }];
}

function renderCoreRules(context: RenderContext): RuleCard | null {
    const { doc } = context;
    const sections: HTMLElement[] = [];

    const timeControl = buildTimeControlSection(context);
    if (timeControl) sections.push(timeControl);

    const adjudication = buildAdjudicationSection(context);
    if (adjudication) sections.push(adjudication);

    const initialPositions = buildInitialPositionsSection(context);
    if (initialPositions) sections.push(initialPositions);

    const repetition = buildRepetitionSection(context);
    if (repetition) sections.push(repetition);

    if (!sections.length) {
        return null;
    }

    const wrapper = doc.createElement('div');
    wrapper.className = 'rules-card__sections';
    for (const section of sections) {
        wrapper.appendChild(section);
    }

    return {
        title: 'Core Rules',
        content: wrapper,
    };
}

function renderGenerateConfig({ doc, summary }: RenderContext): RuleCard[] {
    const generate = summary.generateConfig;
    const records = summary.recordsOutput;
    if (!generate && !records) {
        return [];
    }

    const sections: HTMLElement[] = [];

    if (generate && typeof generate === 'object') {
        const rows: RulesListEntry[] = [];
        const games = generate.games as number | undefined;
        const seed = generate.seed as number | undefined;
        const numParallel = generate.num_parallel ?? generate.numParallel;
        if (games != null) rows.push({ label: 'Games', value: String(games) });
        if (numParallel != null) rows.push({ label: 'Parallel', value: String(numParallel) });
        if (seed != null) rows.push({ label: 'Seed', value: String(seed) });
        sections.push(createSection(doc, 'Generate', createDefinitionList(doc, rows)));
    }

    if (records && typeof records === 'object') {
        const rows: RulesListEntry[] = [];
        if (records.format != null) rows.push({ label: 'Format', value: String(records.format) });
        if (records.output_dir != null) rows.push({ label: 'Output dir', value: String(records.output_dir) });
        if (records.file_prefix != null) rows.push({ label: 'File prefix', value: String(records.file_prefix) });
        if (records.max_positions_per_file != null) {
            rows.push({ label: 'Max positions', value: String(records.max_positions_per_file) });
        }
        if (records.max_games_per_file != null) {
            rows.push({ label: 'Max games', value: String(records.max_games_per_file) });
        }
        sections.push(createSection(doc, 'Records Output', createDefinitionList(doc, rows)));
    }

    if (!sections.length) {
        return [];
    }

    const wrapper = doc.createElement('div');
    wrapper.className = 'rules-card__sections';
    for (const section of sections) {
        wrapper.appendChild(section);
    }

    return [{ title: 'Generate', content: wrapper }];
}

export interface RenderOptions {
    parseTimeControlSpec?: ParseTimeControlSpec;
    warnSoftFailure?: WarnSoftFailure;
}

export function renderRuleCards(doc: Document, summary: RulesSummary, options: RenderOptions = {}): RuleCard[] {
    const context: RenderContext = { doc, summary, options };
    const cards: RuleCard[] = [];

    const coreRulesCard = renderCoreRules(context);
    if (coreRulesCard) {
        cards.push(coreRulesCard);
    }

    const mode = summary.tournamentType;
    if (mode === 'spsa') {
        const spsaCards = renderSpsa(context);
        if (spsaCards?.length) {
            cards.push(...spsaCards);
        }
    } else {
        // SPRT/Match/Tournament: show SPRT card when data is available
        const sprtCards = renderSprt(context);
        if (sprtCards?.length) {
            cards.push(...sprtCards);
        }
    }

    if (mode === 'generate') {
        const generateCards = renderGenerateConfig(context);
        if (generateCards?.length) {
            cards.push(...generateCards);
        }
    }

    return cards;
}

export { createMessage };
