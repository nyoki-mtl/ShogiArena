import './style.css';

import { showFatalDashboardError } from '@/modules/shared/components/errorBanner';
import { crash } from '@/modules/shared/utils/errors';
import { isTestEnvironment } from '@/modules/shared/utils/env';
import { loadInitialDashboardData, resolveProfileConfiguration } from '@/modules/shared/services/bootstrap';

import logoDark from './assets/logo-dark.png';
import faviconPng from './assets/logo-favicon.png';

import type { DashboardBootstrapOptions, DashboardInitializationResult } from './bootstrap';
import { initializeDashboard } from './bootstrap';
import type { ArenaDashboardWindow } from '@/types/globals';

function normalizeAssetPath(asset: string): string {
    if (!import.meta.env.PROD) {
        return asset;
    }

    if (/^https?:\/\//i.test(asset) || asset.startsWith('data:')) {
        return asset;
    }

    const cleaned = asset.replace(/^\.?\/*/, '');
    if (cleaned.startsWith('static/dist/')) {
        return cleaned;
    }

    return `static/dist/${cleaned}`;
}

function installBrandingAssets(documentRef: Document | null | undefined): void {
    if (!documentRef) {
        return;
    }

    const logo = documentRef.querySelector<HTMLImageElement>('.dashboard-logo');
    if (!logo) {
        return;
    }
    logo.src = normalizeAssetPath(logoDark);

    const favicon = documentRef.querySelector<HTMLLinkElement>("link[rel~='icon']");
    if (favicon) {
        favicon.setAttribute('href', normalizeAssetPath(faviconPng));
        favicon.setAttribute('type', 'image/png');
    }
}

let bootstrapped = false;
let lastInitialization: DashboardInitializationResult | null = null;

// test-only reset hook to clear bootstrap cache between vitest runs
export function __resetDashboardBootstrapCache(): void {
    bootstrapped = false;
    lastInitialization = null;
}

function runBootstrap(options?: DashboardBootstrapOptions): DashboardInitializationResult {
    bootstrapped = true;
    lastInitialization = initializeDashboard(options);
    return lastInitialization;
}

export function bootstrapDashboard(options?: DashboardBootstrapOptions): DashboardInitializationResult {
    if (bootstrapped) {
        return lastInitialization ?? initializeDashboard(options);
    }
    return runBootstrap(options);
}

export function bootstrapDashboardOnReady(options?: DashboardBootstrapOptions): void {
    if (typeof document === 'undefined') {
        runBootstrap(options);
        return;
    }

    if (document.readyState === 'loading') {
        document.addEventListener(
            'DOMContentLoaded',
            () => {
                if (!bootstrapped) {
                    runBootstrap(options);
                }
            },
            { once: true },
        );
    } else if (!bootstrapped) {
        runBootstrap(options);
    }
}
if (typeof window !== 'undefined' && typeof document !== 'undefined' && !isTestEnvironment()) {
    installBrandingAssets(document);
    const owner = window as ArenaDashboardWindow;
    const profileConfig = resolveProfileConfiguration(document);
    void loadInitialDashboardData(owner, profileConfig.runtimeMode)
        .then((initialData) => {
            bootstrapDashboardOnReady({
                owner,
                initialData,
                features: profileConfig.features,
                runtimeMode: profileConfig.runtimeMode,
            });
        })
        .catch((error) => {
            const errorMessage = error instanceof Error ? error.message : String(error);
            const errorCause = error instanceof Error && error.cause ? ` (${error.cause})` : '';
            const fullMessage = `Failed to initialize dashboard: ${errorMessage}${errorCause}`;

            showFatalDashboardError(fullMessage);
            console.error('[Dashboard Initialization]', error);

            // Fail fast instead of continuing with a partially initialized UI
            throw crash('Dashboard initialization failed', error);
        });
}

export type { DashboardBootstrapOptions, DashboardInitializationResult };
