import type { LiveTimeControlSpec, WorkerSnapshot } from './public';

export type MoveTimes = ReadonlyArray<number | null>;
export type ClockSide = 'black' | 'white';

export type ParsedTimeControlSpec = LiveTimeControlSpec;

export type EngineClockSnapshot = WorkerSnapshot & {
    time_control_black?: string | null;
    time_control_white?: string | null;
    move_times_ms?: MoveTimes;
};
