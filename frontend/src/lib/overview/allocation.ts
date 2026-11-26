// Screen-reader name for the overview allocation ring: what is owned, by type, with each share.

import { percent } from '#lib/format/number.js';
import { t } from '#lib/i18n/index.svelte.js';
import type { Part } from '#lib/types.js';

/** Screen-reader label for the allocation ring drawn from `held`. */
export function allocationChartLabel(
	held: readonly Part[],
	nameOf: (part: Part) => string
): string {
	if (!held.length) return t('overview.allocationChartAria');
	const parts = held
		.map((part) => (part.share === null ? nameOf(part) : `${nameOf(part)} ${percent(part.share)}`))
		.join(', ');
	return t('overview.allocationChartWithPartsAria', { parts });
}
