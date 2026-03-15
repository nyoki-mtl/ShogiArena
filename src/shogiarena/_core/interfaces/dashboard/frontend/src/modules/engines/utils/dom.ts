import type { EnginesWindow } from '../types/internal';

interface EnginesDomRefs {
    table: HTMLTableElement | null;
    tableBody: HTMLTableSectionElement | null;
    summaryLabel: HTMLElement | null;
}

export interface EngineRowDomRefs {
    row: HTMLTableRowElement;
    nameCell: HTMLTableCellElement;
    instanceCell: HTMLTableCellElement;
    timeCell: HTMLTableCellElement;
}

export const engineRowRegistry = new Map<string, EngineRowDomRefs>();
export const detailRowRegistry = new Map<string, HTMLTableRowElement>();

export function createDomRefs(): EnginesDomRefs {
    return {
        table: null,
        tableBody: null,
        summaryLabel: null,
    };
}

export function clearRowRegistry(): void {
    engineRowRegistry.clear();
    detailRowRegistry.clear();
}

export function cacheDomRefs(dom: EnginesDomRefs, owner: EnginesWindow): void {
    const table = owner.document.getElementById('enginesTable') as HTMLTableElement | null;
    const tableBody = table ? (table.querySelector('tbody') as HTMLTableSectionElement | null) : null;
    const summaryLabel = owner.document.getElementById('enginesSummary');

    if (!table || !tableBody || !summaryLabel) {
        throw new Error('Required Engines dashboard DOM elements are unavailable');
    }

    dom.table = table;
    dom.tableBody = tableBody;
    dom.summaryLabel = summaryLabel;
}
