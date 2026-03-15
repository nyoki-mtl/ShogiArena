// Shared types used across multiple modules

export type JsonPrimitive = string | number | boolean | null;

export interface JsonObject {
    readonly [key: string]: JsonValue | undefined;
}

export type JsonArray = ReadonlyArray<JsonValue>;

export type JsonValue = JsonPrimitive | JsonArray | JsonObject;

export type MutableJsonObject = {
    [key: string]: JsonValue | undefined;
};

export interface IndexedPayload<TPayload = JsonValue> {
    readonly worker_idx: number;
    readonly payload: TPayload | null | undefined;
    readonly emitted_at?: string | null;
}

export type JsonRequestOptions = Omit<RequestInit, 'body'> & {
    readonly body?: BodyInit | JsonValue | MutableJsonObject | JsonArray;
    /**
     * Optional validator hook. Throw inside to fail fast on unexpected payloads.
     */
    readonly validate?: (payload: unknown) => void;
};

export interface RequestError extends Error {
    status?: number;
    payload?: unknown;
}
