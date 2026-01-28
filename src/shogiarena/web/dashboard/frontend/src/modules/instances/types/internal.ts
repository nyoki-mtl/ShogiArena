// Instances module internal types

export interface InstanceFormElements {
    readonly form: HTMLFormElement;
    readonly title: HTMLElement | null;
    readonly type: HTMLSelectElement;
    readonly name: HTMLInputElement;
    readonly slots: HTMLInputElement;
    readonly host: HTMLInputElement;
    readonly user: HTMLInputElement;
    readonly port: HTMLInputElement;
    readonly identity: HTMLInputElement;
    readonly projectRoot: HTMLInputElement;
    readonly tags: HTMLInputElement;
    readonly strict?: HTMLInputElement | null;
    readonly sshFields?: HTMLElement | null;
    readonly submit: HTMLButtonElement | null;
}
