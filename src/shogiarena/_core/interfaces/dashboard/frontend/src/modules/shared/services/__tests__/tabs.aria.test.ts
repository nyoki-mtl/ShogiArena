import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { installDashboardTabs } from '@/modules/shared/services/tabs';

type TabsWindow = Window & { DashboardTabs?: unknown };

function buildDashboardDom(): void {
    document.body.innerHTML = `
        <div class="tabs" role="tablist" aria-label="Dashboard sections">
            <button class="dashboard-tab" data-dashboard-tab="live" id="tabLive" aria-selected="true">Live</button>
            <button class="dashboard-tab" data-dashboard-tab="engines" id="tabEngines" aria-selected="false">Engines</button>
        </div>
        <div id="progressBar" role="progressbar" class="tab-content active" data-tab-content="live engines"></div>
        <section id="banner" class="tab-content active" data-tab-content="live engines"></section>
        <section id="liveSection" class="tab-content" data-tab-content="live"></section>
        <section id="enginesSection" class="tab-content" data-tab-content="engines" aria-label="Engines"></section>
    `;
}

describe('installDashboardTabs ARIA wiring', () => {
    beforeEach(() => {
        delete (window as TabsWindow).DashboardTabs;
        buildDashboardDom();
    });

    afterEach(() => {
        delete (window as TabsWindow).DashboardTabs;
        document.body.innerHTML = '';
    });

    it('marks tab buttons with role="tab" and a roving tabindex', () => {
        installDashboardTabs();
        const live = document.getElementById('tabLive') as HTMLButtonElement;
        const engines = document.getElementById('tabEngines') as HTMLButtonElement;
        expect(live.getAttribute('role')).toBe('tab');
        expect(engines.getAttribute('role')).toBe('tab');
        // 'live' is the default active tab → only it is in the Tab order.
        expect(live.tabIndex).toBe(0);
        expect(engines.tabIndex).toBe(-1);
    });

    it('wires per-tab panels with role="tabpanel" + aria-labelledby and tabs with aria-controls', () => {
        installDashboardTabs();

        const liveSection = document.getElementById('liveSection') as HTMLElement;
        const enginesSection = document.getElementById('enginesSection') as HTMLElement;
        expect(liveSection.getAttribute('role')).toBe('tabpanel');
        expect(liveSection.getAttribute('aria-labelledby')).toBe('tabLive');
        expect(enginesSection.getAttribute('role')).toBe('tabpanel');
        expect(enginesSection.getAttribute('aria-labelledby')).toBe('tabEngines');

        const live = document.getElementById('tabLive') as HTMLButtonElement;
        const engines = document.getElementById('tabEngines') as HTMLButtonElement;
        expect(live.getAttribute('aria-controls')).toBe('liveSection');
        expect(engines.getAttribute('aria-controls')).toBe('enginesSection');
    });

    it('leaves shared chrome alone (existing role and all-tab spanning regions)', () => {
        installDashboardTabs();

        // role="progressbar" must not be overwritten with tabpanel.
        const progress = document.getElementById('progressBar') as HTMLElement;
        expect(progress.getAttribute('role')).toBe('progressbar');
        expect(progress.hasAttribute('aria-labelledby')).toBe(false);

        // A region spanning every tab is cross-tab chrome, not a per-tab panel.
        const banner = document.getElementById('banner') as HTMLElement;
        expect(banner.hasAttribute('role')).toBe(false);
    });
});
