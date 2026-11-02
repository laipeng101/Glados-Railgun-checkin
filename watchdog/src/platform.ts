// Native Worker globals must keep their global receiver when injected into a client.
export const fetchFromWorker: typeof fetch = (input, init) => globalThis.fetch(input, init);
