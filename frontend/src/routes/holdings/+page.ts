import { onward } from '#lib/renamed.js';
import type { PageLoad } from './$types';

// Holdings is called Investments now: an old link to it still finds the page.
export const load: PageLoad = onward('/holdings');
