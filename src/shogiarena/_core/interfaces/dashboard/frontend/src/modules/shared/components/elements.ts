export interface DetailsPaneOptions {
    readonly className?: string;
    readonly title?: string;
}

export function createDetailsPane(doc: Document, options: DetailsPaneOptions = {}): HTMLElement {
    const pane = doc.createElement('div');
    pane.className = options.className ? `details-pane ${options.className}`.trim() : 'details-pane';
    if (options.title) {
        const title = doc.createElement('div');
        title.className = 'title';
        title.textContent = options.title;
        pane.appendChild(title);
    }
    return pane;
}

export function createEmptyState(doc: Document, text: string, className = 'subtle'): HTMLElement {
    const wrapper = doc.createElement('div');
    wrapper.className = className;
    wrapper.textContent = text;
    return wrapper;
}
