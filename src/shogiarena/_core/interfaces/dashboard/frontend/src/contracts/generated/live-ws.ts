import type { SseSummaryPayload } from '@/modules/live/types/public';
import type { JsonObject } from '@/types/shared';

export type LiveWsJsonObject = JsonObject;

export type LiveWsSummaryPayload = LiveWsJsonObject & SseSummaryPayload;

export type LiveWsGamesDeltaRow =
    | {
          op: 'remove';
          id: string;
      }
    | {
          op: 'add' | 'update';
          row: LiveWsJsonObject;
      };

export type LiveWsGamesPayload =
    | {
          kind: 'bulk';
          revision: number;
          base_revision: number | null;
          rows: LiveWsJsonObject[];
          snapshot_meta: LiveWsJsonObject;
      }
    | {
          kind: 'delta';
          revision: number;
          base_revision: number;
          rows: LiveWsGamesDeltaRow[];
          snapshot_meta: LiveWsJsonObject;
      };
