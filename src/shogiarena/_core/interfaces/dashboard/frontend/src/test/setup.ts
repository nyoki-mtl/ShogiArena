const suppressed = [
    '[SPSA] Initializing dashboard',
    '[SPSA] Dashboard initialized (lazy connect)',
    '[SPSA] renderCorrelationResult deferred: layout unavailable',
    '[SPSA] SpsaApi.fetchConvergenceAnalysis',
    'ShogiBoardAdapter constructor is not provided; skipping board mount',
];

const shouldSuppress = (args: unknown[]) => {
    if (!args.length) return false;
    const first = args[0];
    const message = typeof first === 'string' ? first : '';
    if (!message) return false;
    return suppressed.some((needle) => message.includes(needle));
};

const originalWarn = console.warn.bind(console);
const originalError = console.error.bind(console);
const originalInfo = console.info.bind(console);

console.warn = (...args: unknown[]) => {
    if (shouldSuppress(args)) return;
    originalWarn(...args);
};

console.error = (...args: unknown[]) => {
    if (shouldSuppress(args)) return;
    originalError(...args);
};

console.info = (...args: unknown[]) => {
    if (shouldSuppress(args)) return;
    originalInfo(...args);
};
