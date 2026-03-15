const domCache = new Map<string, HTMLElement | null>();

export function byId<T extends HTMLElement = HTMLElement>(id: string): T | null {
    if (!id) return null;
    const cached = domCache.get(id);
    if (cached?.isConnected) {
        return cached as T;
    }
    const element = document.getElementById(id) as T | null;
    domCache.set(id, element);
    return element;
}
