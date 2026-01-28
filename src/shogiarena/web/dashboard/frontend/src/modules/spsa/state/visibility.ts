import { state } from './store';

let visibilityListenerAttached = false;
let visibilityChangeCallback: ((visible: boolean) => void) | null = null;

export function setVisibilityPaused(paused: boolean): void {
    state.visibility.refreshPaused = paused;
}

export function registerVisibilityHandler(doc: Document, callback: (visible: boolean) => void): void {
    visibilityChangeCallback = callback;
    if (visibilityListenerAttached) {
        return;
    }
    const handler = () => {
        const visible = !doc.hidden;
        state.visibility.pageHidden = !visible;
        state.visibility.refreshPaused = !visible;
        visibilityChangeCallback?.(visible);
    };
    doc.addEventListener('visibilitychange', handler, { passive: true });
    visibilityListenerAttached = true;
    handler();
}
