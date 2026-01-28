import type { MatchupRowViewModel } from '@/modules/tournament/viewmodels/matchups';
export type MatchupRowView = MatchupRowViewModel;

function escapeAttribute(value: string): string {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

export function buildMatchupPanelHtml(engineName: string): string {
    const safeName = escapeAttribute(engineName);
    return `
        <div class="matchup-panel" data-engine="${safeName}">
            <div class="matchup-header">
                <div class="matchup-title">${safeName} matchups</div>
                <div class="matchup-overview muted"></div>
            </div>
            <table class="matchup-table">
                <thead>
                    <tr>
                        <th>Opponent</th>
                        <th>W-D-L</th>
                        <th>Win Ratio</th>
                        <th>ΔR</th>
                        <th>LOS</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
    `;
}

export function renderMatchupRows(entries: readonly MatchupRowViewModel[]): string {
    if (!entries.length) {
        return '';
    }
    return entries
        .map((entry) => {
            const opponentRaw = String(entry.opponent ?? '');
            const opponentEncoded = encodeURIComponent(opponentRaw);
            const opponentEscaped = escapeAttribute(opponentRaw);
            return `
                <tr data-opponent="${opponentEncoded}">
                    <td>
                        <button class="matchup-opponent" type="button" data-action="matchup_select" data-engine="${opponentEscaped}" data-opponent="${opponentEncoded}">
                            ${opponentEscaped}
                        </button>
                    </td>
                    <td>${entry.wins}-${entry.draws}-${entry.losses}</td>
                    <td>${entry.winRatio}</td>
                    <td>${entry.delta}</td>
                    <td>${entry.los}</td>
                </tr>
            `;
        })
        .join('');
}
