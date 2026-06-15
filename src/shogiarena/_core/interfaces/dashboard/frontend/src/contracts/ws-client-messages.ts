// Outbound dashboard WebSocket client messages (frontend -> backend).
//
// This is a pure wire layer: every object key here is a snake_case wire key that the
// backend `DashboardWsClientMessage` model parses.
//
// Enforcement of the snake_case contract here is twofold:
//   1. Each builder returns a CLOSED message type (no index signature), so adding a
//      stray camelCase property to a returned literal is a tsc excess-property error.
//   2. The N010 naming check scans `contracts/` for known legacy camelCase wire keys.
//
// Note: N010's TypeScript check is a known-key scan, not a generic camelCase detector
// (TS uses camelCase legitimately for functions/params/vars, so a generic token scan
// would be false-positive prone). A brand-new camelCase wire key is therefore guarded
// by the closed return types above + the Python producer-side generic N010 gate (the
// source of truth), not by a generic TS scan. See agent-docs/rules/convention-lint-spec.md.

export interface SubscribeMessage {
    readonly type: 'subscribe';
    readonly topics: string[];
    readonly include_analysis: boolean;
}

export interface RequestSnapshotMessage {
    readonly type: 'request_snapshot';
    readonly topic: string;
    readonly from_seq: number | undefined;
    readonly reason_code: number;
}

export interface SetWorkerFilterMessage {
    readonly type: 'set_worker_filter';
    readonly workers: number[];
}

export type WsClientMessage = SubscribeMessage | RequestSnapshotMessage | SetWorkerFilterMessage;

export function buildSetWorkerFilterMessage(workers: number[]): SetWorkerFilterMessage {
    return { type: 'set_worker_filter', workers };
}

export function buildSubscribeMessage(topics: string[], analysisEnabled: boolean): SubscribeMessage {
    return { type: 'subscribe', topics, include_analysis: analysisEnabled };
}

export function buildRequestSnapshotMessage(
    topic: string,
    seq: number | undefined,
    reasonCode: number,
): RequestSnapshotMessage {
    return { type: 'request_snapshot', topic, from_seq: seq, reason_code: reasonCode };
}
