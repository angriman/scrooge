// Encode and decode the transactions side-panel URL query parameter (`detail`), keeping it
// distinct from the filter fields (`category`, `merchant`).

export type DetailKind = 'category' | 'merchant';

export interface DetailTarget {
	kind: DetailKind;
	name: string;
}

/** Encode a category or merchant target for the `detail` query parameter. */
export function formatDetail(kind: DetailKind, name: string): string {
	return `${kind}:${name}`;
}

/** Parse a `detail` query parameter into its target, or null when missing or malformed. */
export function parseDetail(raw: string | null): DetailTarget | null {
	if (!raw) return null;
	const sep = raw.indexOf(':');
	if (sep <= 0) return null;
	const kind = raw.slice(0, sep);
	const name = raw.slice(sep + 1);
	if ((kind !== 'category' && kind !== 'merchant') || !name.trim()) return null;
	return { kind, name };
}
