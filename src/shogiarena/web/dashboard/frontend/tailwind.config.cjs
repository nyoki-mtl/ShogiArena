/** @type {import('tailwindcss').Config} */
module.exports = {
    content: [
        './index.html',
        './src/**/*.{ts,tsx,js,jsx,html}',
    ],
    theme: {
        extend: {
            colors: {
                dashboard: {
                    base: 'rgb(var(--color-dashboard-base) / <alpha-value>)',
                    surface: 'rgb(var(--color-dashboard-surface) / <alpha-value>)',
                    surfaceMuted: 'rgb(var(--color-dashboard-surface-muted) / <alpha-value>)',
                    border: 'rgb(var(--color-dashboard-border) / <alpha-value>)',
                    accent: 'rgb(var(--color-dashboard-accent) / <alpha-value>)',
                    accentMuted: 'rgb(var(--color-dashboard-accent-muted) / <alpha-value>)',
                    foreground: 'rgb(var(--color-dashboard-foreground) / <alpha-value>)',
                    heading: 'rgb(var(--color-dashboard-heading) / <alpha-value>)',
                    muted: 'rgb(var(--color-dashboard-muted) / <alpha-value>)',
                    inverse: 'rgb(var(--color-dashboard-inverse) / <alpha-value>)',
                    neutral: 'rgb(var(--color-dashboard-neutral) / <alpha-value>)',
                    success: 'rgb(var(--color-dashboard-success) / <alpha-value>)',
                    warning: 'rgb(var(--color-dashboard-warning) / <alpha-value>)',
                    danger: 'rgb(var(--color-dashboard-danger) / <alpha-value>)',
                },
                table: {
                    rowBg: 'rgb(var(--color-table-row-bg) / <alpha-value>)',
                    rowBorder: 'rgb(var(--color-table-row-border) / <alpha-value>)',
                    headerText: 'rgb(var(--color-table-header-text) / <alpha-value>)',
                    groupEngine: 'rgb(var(--color-table-group-engine) / <alpha-value>)',
                    groupTime: 'rgb(var(--color-table-group-time) / <alpha-value>)',
                    groupStats: 'rgb(var(--color-table-group-stats) / <alpha-value>)',
                    groupInstance: 'rgb(var(--color-table-group-instance) / <alpha-value>)',
                    groupMeta: 'rgb(var(--color-table-group-meta) / <alpha-value>)',
                    groupStatus: 'rgb(var(--color-table-group-status) / <alpha-value>)',
                    groupId: 'rgb(var(--color-table-group-id) / <alpha-value>)',
                },
            },
            boxShadow: {
                'dashboard-card': '0 22px 60px -30px rgba(14, 23, 43, 0.85)',
            },
            borderRadius: {
                xl: '1rem',
            },
        },
    },
    plugins: [],
};
