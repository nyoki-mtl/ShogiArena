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
