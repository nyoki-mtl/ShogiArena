import { createLiveCardsApi, type CardsWindow } from './cards-api';
import type { LiveCardsApi } from '@/types/live';
import { ensureLiveNamespace, registerLiveApi } from '@/modules/live/utils/liveNamespace';

const defaultWindow = window as unknown as CardsWindow;

export function installLiveCardsModule(owner: CardsWindow = defaultWindow): LiveCardsApi {
    if (!owner.DashboardCore) {
        throw new Error('DashboardCore must be installed before live cards module');
    }

    const namespace = ensureLiveNamespace(owner);
    const existing = namespace.get('cards');
    if (existing) {
        return existing;
    }

    const api = createLiveCardsApi(owner);
    registerLiveApi(owner, 'cards', api, { provider: 'live/components/cards' });

    return api;
}
