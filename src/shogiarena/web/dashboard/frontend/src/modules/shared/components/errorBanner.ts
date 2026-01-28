export function showFatalDashboardError(message: string): void {
    if (typeof document === 'undefined' || !document.body) {
        return;
    }

    const errorDiv = document.createElement('div');
    errorDiv.style.cssText =
        'position: fixed; top: 20px; left: 50%; transform: translateX(-50%); background: #dc2626; color: white; padding: 16px 24px; border-radius: 8px; z-index: 9999; max-width: 600px; box-shadow: 0 4px 6px rgba(0,0,0,0.1);';
    errorDiv.textContent = message;
    document.body.insertBefore(errorDiv, document.body.firstChild);
}
