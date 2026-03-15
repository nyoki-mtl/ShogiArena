import { escapeHtml } from '@/modules/shared/utils/html';

export interface ApiErrorBannerParams {
    title: string;
    detail: string;
    timestamp: Date;
}

export function renderApiErrorBanner({ title, detail, timestamp }: ApiErrorBannerParams): string {
    const parts = [
        String(timestamp.getUTCHours()).padStart(2, '0'),
        String(timestamp.getUTCMinutes()).padStart(2, '0'),
        String(timestamp.getUTCSeconds()).padStart(2, '0'),
    ];
    return `
        <div class="api-error-banner__title">${escapeHtml(title)}</div>
        <pre class="api-error-banner__body">[${parts.join(':')}] ${escapeHtml(detail)}</pre>
    `;
}

export interface SprtBannerInput {
    isSprt: boolean;
    isFinal: boolean;
    conclusion?: string;
}

export interface SprtBannerModel {
    hidden: boolean;
    text: string | null;
}

export function deriveSprtBanner({ isSprt, isFinal, conclusion }: SprtBannerInput): SprtBannerModel {
    if (!isSprt && !isFinal) {
        return { hidden: true, text: null };
    }
    if (conclusion) {
        return { hidden: false, text: `SPRT: ${conclusion}. Tournament concluded.` };
    }
    if (isFinal) {
        return { hidden: false, text: 'Tournament completed.' };
    }
    return { hidden: false, text: null };
}
