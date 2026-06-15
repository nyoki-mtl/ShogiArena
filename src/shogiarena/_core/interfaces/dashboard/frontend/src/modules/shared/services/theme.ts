type DashboardTheme = 'light' | 'dark';

interface ThemeAPI {
    getTheme(): DashboardTheme;
    setTheme(theme: DashboardTheme, options?: { persist?: boolean }): DashboardTheme;
    toggleTheme(): DashboardTheme;
    clearPreference(): void;
}

const THEME_STORAGE_KEY = 'dashboard:theme-preference';

function isDashboardTheme(value: unknown): value is DashboardTheme {
    return value === 'light' || value === 'dark';
}

function readStoredPreference(storage?: Storage): DashboardTheme | null {
    if (!storage) return null;
    try {
        const stored = storage.getItem(THEME_STORAGE_KEY);
        return isDashboardTheme(stored) ? stored : null;
    } catch (error) {
        console.warn('[Dashboard Theme] failed to read preference from storage', error);
        return null;
    }
}

function writeStoredPreference(theme: DashboardTheme | null, storage?: Storage): void {
    if (!storage) return;
    try {
        if (theme) {
            storage.setItem(THEME_STORAGE_KEY, theme);
        } else {
            storage.removeItem(THEME_STORAGE_KEY);
        }
    } catch (error) {
        console.warn('[Dashboard Theme] failed to persist preference', error);
    }
}

function detectSystemTheme(owner: Window & typeof globalThis): DashboardTheme {
    if (typeof owner.matchMedia !== 'function') {
        return 'light';
    }
    try {
        return owner.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    } catch (error) {
        console.warn('[Dashboard Theme] failed to read system preference', error);
        return 'light';
    }
}

function applyTheme(root: HTMLElement | null, theme: DashboardTheme): void {
    if (!root) return;
    root.dataset.theme = theme;
}

function updateToggleButton(button: HTMLButtonElement | null, theme: DashboardTheme): void {
    if (!button) return;

    const icon = button.querySelector<HTMLElement>('.theme-toggle__icon');
    const isDark = theme === 'dark';
    const nextThemeLabel = isDark ? 'light' : 'dark';

    button.dataset.theme = theme;
    button.setAttribute('aria-pressed', String(isDark));
    button.setAttribute(
        'aria-label',
        `Currently in ${isDark ? 'dark' : 'light'} mode. Click to switch to ${nextThemeLabel} mode.`,
    );

    if (icon) {
        icon.textContent = isDark ? '🌙' : '☀️';
    }
}

function resolveStorage(owner: Window & typeof globalThis): Storage | undefined {
    try {
        if (typeof owner.localStorage !== 'undefined') {
            return owner.localStorage;
        }
    } catch (error) {
        console.warn('[Dashboard Theme] localStorage is not accessible', error);
    }
    return undefined;
}

// Keyed by owner so distinct windows (tests, HMR, embedded views) each get their own theme binding;
// re-installing the same owner reuses its instance to avoid duplicate matchMedia/toggle listeners.
const themeRegistry = new WeakMap<Window, ThemeAPI>();

export function installDashboardTheme(owner: Window & typeof globalThis = window): ThemeAPI {
    if (owner?.document) {
        const existing = themeRegistry.get(owner);
        if (existing) {
            return existing;
        }
    }
    if (!owner || !owner.document) {
        let theme: DashboardTheme = 'light';
        return {
            getTheme: () => theme,
            setTheme: (next) => {
                theme = next;
                return theme;
            },
            toggleTheme: () => {
                theme = theme === 'dark' ? 'light' : 'dark';
                return theme;
            },
            clearPreference: () => {
                theme = detectSystemTheme(owner);
            },
        };
    }

    const documentRef = owner.document;
    const root = documentRef.documentElement;
    const storage = resolveStorage(owner);
    const toggleButton = documentRef.getElementById('themeToggle') as HTMLButtonElement | null;

    let storedPreference = readStoredPreference(storage);
    let currentTheme: DashboardTheme = storedPreference ?? detectSystemTheme(owner);

    applyTheme(root, currentTheme);
    updateToggleButton(toggleButton, currentTheme);

    const mediaQuery =
        typeof owner.matchMedia === 'function' ? owner.matchMedia('(prefers-color-scheme: dark)') : undefined;

    const setTheme = (theme: DashboardTheme, options: { persist?: boolean } = {}): DashboardTheme => {
        currentTheme = theme;
        applyTheme(root, currentTheme);
        updateToggleButton(toggleButton, currentTheme);

        if (options.persist !== false) {
            storedPreference = currentTheme;
            writeStoredPreference(currentTheme, storage);
        }

        return currentTheme;
    };

    const toggleTheme = (): DashboardTheme => {
        const nextTheme: DashboardTheme = currentTheme === 'dark' ? 'light' : 'dark';
        return setTheme(nextTheme, { persist: true });
    };

    const clearPreference = (): void => {
        storedPreference = null;
        writeStoredPreference(null, storage);
        const resolved = detectSystemTheme(owner);
        setTheme(resolved, { persist: false });
    };

    const handleMediaChange = (event: MediaQueryListEvent): void => {
        if (storedPreference) {
            return;
        }
        const nextTheme: DashboardTheme = event.matches ? 'dark' : 'light';
        setTheme(nextTheme, { persist: false });
    };

    if (mediaQuery) {
        try {
            mediaQuery.addEventListener('change', handleMediaChange);
        } catch (error) {
            if (typeof mediaQuery.addListener === 'function') {
                mediaQuery.addListener(handleMediaChange);
            } else {
                console.warn('[Dashboard Theme] failed to attach media listener', error);
            }
        }
    }

    if (toggleButton) {
        toggleButton.addEventListener('click', () => {
            storedPreference = currentTheme === 'dark' ? 'light' : 'dark';
            setTheme(storedPreference, { persist: true });
        });

        toggleButton.addEventListener('contextmenu', (event) => {
            event.preventDefault();
            clearPreference();
        });
    }

    const api: ThemeAPI = {
        getTheme: () => currentTheme,
        setTheme,
        toggleTheme,
        clearPreference,
    };
    themeRegistry.set(owner, api);
    return api;
}

export type { DashboardTheme, ThemeAPI };
