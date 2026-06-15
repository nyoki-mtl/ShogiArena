export type LiveEnvelope<T = unknown> = {
    topic?: string;
    seq?: number;
    ts?: number;
    payload?: T;
};
