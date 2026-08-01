import { beforeEach, describe, expect, it } from 'vitest';
import { normalizeSpsaSummary } from '../services/normalizers';
import { renderOperationalStatus } from './hero';

beforeEach(() => {
    document.body.innerHTML = `
        <span id="operationalHealth"></span>
        <span id="operationalIdentity"></span>
        <span id="operationalCompletion"></span>
        <span id="operationalProgress"></span>
        <span id="operationalLtc"></span>
        <span id="operationalManifest"></span>
        <span id="operationalPreflight"></span>
        <span id="operationalTunable"></span>
        <span id="operationalArtifact"></span>
        <span id="operationalRemote"></span>
        <span id="operationalNodeMultiplier"></span>
    `;
});

describe('renderOperationalStatus', () => {
    it('renders authoritative healthy status and remote attribution', () => {
        const summary = normalizeSpsaSummary({
            wins: 0,
            losses: 0,
            draws: 0,
            operational_status: {
                run_id: 'run-1',
                session_id: 'session-2',
                completion: {
                    status: 'running',
                    last_committed_update: 4,
                    pending_update: 5,
                    pending_stage: 'LTC_RUNNING',
                },
                accepted_baseline: { update_idx: 3 },
                last_ltc_decision: { tested_update_idx: 4, decision: 'reverted' },
                manifest: { status: 'provenance_sealed', schema_version: 2, resume_hash: 'a'.repeat(64) },
                fixed_option_preflight: { status: 'passed', evidence_scopes: ['remote_runtime'] },
                tunable_manifest: { status: 'passed', runtime_scope: 'remote_runtime' },
                artifact_health: { status: 'healthy', revision: 12 },
                ledger: { schema_version: 'ledger-v1' },
                remote_execution: {
                    status: 'observed',
                    endpoint_identity: 'worker-a',
                    deployment_id: 'deploy-1',
                    job_id: 'job-9',
                },
                node_multiplier: { status: 'unknown', value: null },
            },
        });

        renderOperationalStatus(summary);

        expect(document.getElementById('operationalHealth')?.dataset.status).toBe('healthy');
        expect(document.getElementById('operationalIdentity')?.textContent).toContain('run run-1');
        expect(document.getElementById('operationalProgress')?.textContent).toBe(
            'committed 4 · pending 5 · LTC_RUNNING',
        );
        expect(document.getElementById('operationalRemote')?.textContent).toContain('job job-9');
        expect(document.getElementById('operationalNodeMultiplier')?.textContent).toBe('unknown');
    });

    it('does not present missing operational evidence as healthy', () => {
        const summary = normalizeSpsaSummary({ wins: 0, losses: 0, draws: 0 });

        renderOperationalStatus(summary);

        expect(document.getElementById('operationalHealth')?.dataset.status).toBe('unknown');
        expect(document.getElementById('operationalHealth')?.textContent).toBe('Unknown');
        expect(document.getElementById('operationalIdentity')?.textContent).toBe('Unknown');
        expect(document.getElementById('operationalRemote')?.textContent).toBe('Not observed');
    });

    it('labels failed and degraded authority explicitly', () => {
        const summary = normalizeSpsaSummary({
            wins: 0,
            losses: 0,
            draws: 0,
            operational_status: {
                completion: { status: 'failed', termination_reason: 'runtime_error', resumable: false },
                artifact_health: { status: 'degraded', revision: 8 },
            },
        });

        renderOperationalStatus(summary);

        expect(document.getElementById('operationalHealth')?.dataset.status).toBe('degraded');
        expect(document.getElementById('operationalCompletion')?.textContent).toBe('failed · runtime_error');
        expect(document.getElementById('operationalArtifact')?.textContent).toBe('degraded · revision 8');
    });
});
