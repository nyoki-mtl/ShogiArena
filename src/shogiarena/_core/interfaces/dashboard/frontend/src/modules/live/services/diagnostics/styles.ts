import { LIVE_DIAGNOSTICS_PANEL_ID as PANEL_ID } from '../diagnostics/constants';

export function ensureDiagnosticsStyles(doc: Document): void {
    if (doc.getElementById(`${PANEL_ID}Styles`)) return;
    const style = doc.createElement('style');
    style.id = `${PANEL_ID}Styles`;
    style.textContent = `
        .live-diagnostics-panel {
            position: fixed;
            bottom: 1rem;
            right: 1rem;
            max-width: min(32rem, 90vw);
            max-height: calc(100vh - 2rem);
            background: rgba(17, 24, 39, 0.9);
            color: #f9fafb;
            border-radius: 0.75rem;
            padding: 0.5rem 1rem 1rem;
            box-shadow: 0 10px 30px rgba(15, 23, 42, 0.7);
            z-index: 2147483000;
            font: 12px/1.4 system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            overflow: hidden;
        }

        .live-diagnostics-panel[open] {
            display: flex;
            flex-direction: column;
        }

        .live-diagnostics-panel > summary {
            cursor: pointer;
            color: #fbbf24;
            font-weight: 600;
            outline: none;
            flex: 0 0 auto;
        }

        .live-diagnostics-content {
            margin-top: 0.75rem;
            display: grid;
            gap: 0.75rem;
            overflow-y: auto;
            -webkit-overflow-scrolling: touch;
            overscroll-behavior: contain;
            padding-right: 0.25rem;
        }

        .live-diagnostics-section {
            border: 1px solid rgba(148, 163, 184, 0.25);
            border-radius: 0.5rem;
            padding: 0.25rem 0.5rem 0.5rem;
            background: rgba(15, 23, 42, 0.25);
        }

        .live-diagnostics-section > summary {
            cursor: pointer;
            list-style: none;
            color: #e5e7eb;
            font-weight: 600;
            font-size: 0.75rem;
            margin: 0.25rem 0;
        }

        .live-diagnostics-section > summary::-webkit-details-marker {
            display: none;
        }

        .live-diagnostics-scroll {
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
        }

        .live-diagnostics-content table {
            width: 100%;
            border-collapse: collapse;
            table-layout: fixed;
            min-width: 24rem;
        }

        .live-diagnostics-content th,
        .live-diagnostics-content td {
            padding: 0.25rem 0.5rem;
            border-bottom: 1px solid rgba(148, 163, 184, 0.3);
            text-align: left;
            word-break: break-word;
            white-space: normal;
        }

        .live-diagnostics-content thead tr {
            background: rgba(30, 41, 59, 0.9);
        }

        .live-diagnostics-table tr.is-alert {
            background: rgba(239, 68, 68, 0.08);
        }

        .live-diagnostics-table tr.is-alert:hover {
            background: rgba(239, 68, 68, 0.16);
        }

        .live-diagnostics-table thead th {
            font-weight: 600;
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.03em;
            color: #e5e7eb;
        }

        .live-diagnostics-table tbody td,
        .live-diagnostics-table tbody th {
            font-size: 0.75rem;
        }

        .live-diagnostics-meta {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.25rem 0.75rem;
            margin-bottom: 0.5rem;
            font-size: 0.75rem;
            color: #e5e7eb;
        }

        .live-diagnostics-meta dt {
            font-weight: 500;
            opacity: 0.8;
        }

        .live-diagnostics-meta dd {
            margin: 0;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New',
                monospace;
        }

        .live-diagnostics-heatmap {
            display: flex;
            gap: 1px;
        }

        .live-diagnostics-heatmap__cell {
            width: 6px;
            height: 12px;
            border-radius: 1px;
        }

        .live-diagnostics-sparkline {
            width: 100%;
            height: 24px;
        }

        .live-diagnostics-actions {
            display: flex;
            justify-content: flex-end;
            gap: 0.5rem;
            margin-bottom: 0.5rem;
        }

        .live-diagnostics-actions button {
            cursor: pointer;
            border: 1px solid rgba(148, 163, 184, 0.5);
            background: rgba(30, 64, 175, 0.85);
            color: #e5e7eb;
            border-radius: 9999px;
            padding: 0.25rem 0.75rem;
            font-size: 0.75rem;
            display: inline-flex;
            align-items: center;
            gap: 0.25rem;
            transition: background-color 120ms ease-out, border-color 120ms ease-out, transform 80ms ease-out;
        }

        .live-diagnostics-actions button:hover {
            background: rgba(59, 130, 246, 0.9);
            border-color: rgba(96, 165, 250, 0.9);
        }

        .live-diagnostics-actions button:active {
            transform: translateY(1px);
        }

        .live-diagnostics-actions button:disabled {
            opacity: 0.6;
            cursor: not-allowed;
        }

        .live-diagnostics-table td:nth-child(1),
        .live-diagnostics-table th:nth-child(1) {
            max-width: 12rem;
        }

        .live-diagnostics-table td:nth-child(2),
        .live-diagnostics-table th:nth-child(2) {
            max-width: 10rem;
        }

        .live-diagnostics-table td:nth-child(3),
        .live-diagnostics-table th:nth-child(3) {
            max-width: 6rem;
        }

        .live-diagnostics-table td:nth-child(4),
        .live-diagnostics-table th:nth-child(4) {
            max-width: 6rem;
        }

        .live-diagnostics-table td:nth-child(5),
        .live-diagnostics-table th:nth-child(5) {
            max-width: 8rem;
        }

        .live-diagnostics-table td:nth-child(6),
        .live-diagnostics-table th:nth-child(6) {
            max-width: 8rem;
        }

        .live-diagnostics-table td:nth-child(7),
        .live-diagnostics-table th:nth-child(7) {
            max-width: 10rem;
        }

        .live-diagnostics-table td:nth-child(8),
        .live-diagnostics-table th:nth-child(8) {
            max-width: 8rem;
        }

        .live-diagnostics-table tbody tr:hover {
            background: rgba(31, 41, 55, 0.7);
        }

        .live-diagnostics-table tbody tr.is-alert.is-critical {
            background: rgba(239, 68, 68, 0.12);
        }

        .live-diagnostics-table tbody tr.is-alert.is-warning {
            background: rgba(234, 179, 8, 0.12);
        }

        .live-diagnostics-table tbody tr.is-alert.is-stable {
            background: rgba(34, 197, 94, 0.08);
        }
    `;
    doc.head.appendChild(style);
}
