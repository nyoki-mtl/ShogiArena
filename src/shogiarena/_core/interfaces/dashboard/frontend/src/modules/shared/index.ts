export { installDashboardCore } from './services/core';
export {
    installDashboardApi,
    resolveApiBase,
} from './services/api';
export { installDashboardDom } from './utils/dom';
export { installDashboardNavigation } from './services/navigation';
export { installDashboardTabs } from './services/tabs';
export { installDashboardNotices } from './components/notices';
export { installShogiBoardGlobals } from './components/shogi-board';
export { installHeaderProgress } from './services/headerProgress';
export { installDashboardTheme } from './services/theme';
export {
    DASHBOARD_MODE_CHANGED_EVENT,
    applyTabConfiguration,
    getDashboardMode,
    getModeConfig,
    resolveRuntimeModeFromSummary,
    setDashboardMode,
} from './services/runtimeMode';
export { getLiveViewSnapshotStore } from './stores/liveViewSnapshot';
export { createDeferredHydrator } from './utils/hydration';
